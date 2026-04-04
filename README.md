# RepricerUK — Amazon UK Buy Box Repricer

A self-hosted repricer for Amazon UK. Automatically matches the Buy Box price
for all your listings, with per-SKU floor and ceiling guardrails.

---

## Project structure

```
amazon-repricer/
├── .vscode/
│   ├── launch.json       ← F5 run & debug configs
│   ├── tasks.json        ← Setup / install / run tasks
│   ├── settings.json     ← Python interpreter, formatting
│   └── extensions.json   ← Recommended extensions
│
├── templates/
│   ├── index.html        ← Dashboard UI
│   └── login.html        ← Login page
│
├── main.py               ← FastAPI app, auth middleware, all routes
├── repricer.py           ← Buy Box repricing engine
├── amazon_api.py         ← SP-API wrapper (pricing + listings)
├── database.py           ← SQLite init & helpers
├── config.py             ← Reads .env
├── requirements.txt      ← Python dependencies
├── .env.example          ← Template — copy to .env and fill in
├── .gitignore
└── README.md
```

---

## Prerequisites

| Tool | Version | Download |
|------|---------|----------|
| Python | 3.10 or newer | https://www.python.org/downloads/ |
| VS Code | Any recent | https://code.visualstudio.com/ |

---

## Step 1 — Open the project in VS Code

1. Open VS Code.
2. **File → Open Folder…** and select the `amazon-repricer` folder.
3. VS Code will detect the project and may prompt:
   - *"Install recommended extensions?"* → click **Install**
   - *"We noticed a new environment…"* → click **Yes** (selects the venv)

---

## Step 2 — Install recommended extensions

Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac), type:

```
Extensions: Show Recommended Extensions
```

Click the cloud icon next to each one to install. The key ones are:

- **Python** (ms-python.python) — IntelliSense, linting
- **Pylance** — fast type checking
- **Black Formatter** — auto-format on save
- **DotENV** — syntax highlight your `.env` file
- **Error Lens** — inline error display

---

## Step 3 — Run the Full Setup task

Press `Ctrl+Shift+P` → **Tasks: Run Task** → **Full Setup (steps 1–3)**

This will, in sequence:
1. Create a `venv/` virtual environment inside the project folder
2. Install all dependencies from `requirements.txt`
3. Copy `.env.example` → `.env` (if `.env` doesn't exist yet)

You'll see output in the integrated terminal. When it finishes you'll see:
```
.env created - please fill in your credentials
```

---

## Step 4 — Configure your credentials

Open `.env` in VS Code (it's in the project root). Fill in every value:

```env
# ── Amazon SP-API ──────────────────────────────────────────
REFRESH_TOKEN=Atzr|...
LWA_APP_ID=amzn1.application-oa2-client....
LWA_CLIENT_SECRET=...
AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
ROLE_ARN=arn:aws:iam::123456789012:role/SellingPartnerRole

# ── Your Amazon UK Seller ID ────────────────────────────────
SELLER_ID=A1B2C3D4E5F6G7

# ── App settings ────────────────────────────────────────────
REPRICE_INTERVAL_MINUTES=15
DB_PATH=repricer.db
HOST=127.0.0.1
PORT=8000

# ── Auth (login page) ───────────────────────────────────────
AUTH_USERNAME=admin
AUTH_PASSWORD=your-strong-password-here
SECRET_KEY=<paste output from step 4a below>
```

### Step 4a — Generate a SECRET_KEY

Press `Ctrl+Shift+P` → **Tasks: Run Task** → **Generate SECRET_KEY**

Copy the printed value (looks like `SECRET_KEY=3f2a1b...`) and paste it into `.env`.

---

## Step 5 — How to get your SP-API credentials

### 5a. Register a developer app

1. Log into **Seller Central UK**: https://sellercentral.amazon.co.uk
2. Go to **Apps & Services → Develop Apps**
3. Click **Add new app client**
4. Fill in the app name (e.g. "My Repricer") and select **Self-authorised**
5. Under **IAM ARN**, paste your Role ARN (created in step 5c)
6. Note your **Client ID** → `LWA_APP_ID`
7. Click **View** next to Client Secret → `LWA_CLIENT_SECRET`

### 5b. Authorise the app to get a Refresh Token

1. In Seller Central go to **Apps & Services → Authorise Apps**
2. Find your app, click **Authorise**
3. Follow the OAuth flow — at the end you'll get a `Refresh Token`
4. Copy it → `REFRESH_TOKEN`

### 5c. Create an AWS IAM Role

1. Open the **AWS Console**: https://console.aws.amazon.com/iam
2. Go to **Roles → Create Role**
3. Trust entity type: **AWS Account** — enter your Account ID
4. Attach the managed policy: **AmazonSPAPIFullAccess**
   (or grant only `sts:AssumeRole` for tighter security)
5. Name it `SellingPartnerRole` (or anything you like)
6. Copy the **ARN** → `ROLE_ARN`

### 5d. Create an IAM User

1. In IAM go to **Users → Create User**
2. Enable **Programmatic access**
3. Attach the policy: **AmazonSPAPIFullAccess** (or attach the role)
4. Download the CSV — copy:
   - Access key ID → `AWS_ACCESS_KEY`
   - Secret access key → `AWS_SECRET_KEY`

### 5e. Find your Seller ID (Merchant Token)

1. In Seller Central go to **Account Info**
2. Look for **Merchant Token** — it's a ~14-character code
3. Copy it → `SELLER_ID`

---

## Step 6 — Run the app

### Option A — Press F5 (recommended for development)

Press **F5** (or go to **Run → Start Debugging**).

VS Code will use the **"Run RepricerUK"** launch config from `.vscode/launch.json`.
The server starts with `--reload` so it restarts automatically when you save a file.

You'll see in the Debug Console:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

### Option B — Run via task

Press `Ctrl+Shift+P` → **Tasks: Run Task** → **4. Start RepricerUK**

### Option C — Terminal

```bash
# Make sure you're in the project folder
source venv/bin/activate          # Windows: venv\Scripts\activate
uvicorn main:app --reload
```

---

## Step 7 — Open the dashboard

Open your browser and go to:

```
http://127.0.0.1:8000
```

You'll see the **login page**. Sign in with the `AUTH_USERNAME` and `AUTH_PASSWORD`
you set in `.env`.

---

## Step 8 — Add your first listing

1. Click **+ Add Listing** in the top right
2. Fill in:
   - **SKU** — your exact seller SKU (case-sensitive, must match Seller Central)
   - **ASIN** — the 10-character Amazon product ID
   - **Current Price** — what you're selling it for right now
   - **Floor Price** — the lowest you'll ever go
   - **Ceiling Price** — the highest you'll ever set
3. Click **Save Listing**

The repricer will pick it up on the next scheduled run, or click **▶ Run Now**
to trigger it immediately.

---

## How the repricing logic works

Every N minutes (set by `REPRICE_INTERVAL_MINUTES`):

```
For each enabled listing:
  1. Fetch the Buy Box price from SP-API
  2. If Buy Box is within [floor, ceiling]  → match it exactly
  3. If Buy Box < floor                     → hold at floor
  4. If Buy Box > ceiling                   → hold at ceiling
  5. If no Buy Box (no winner on listing)   → skip
  6. If already at target (< 0.5p diff)     → skip (no API call)
  7. Log every action to the Activity Log
```

---

## Deploying online (optional)

When you're ready to access this from anywhere:

### Railway (easiest, ~5 minutes)

1. Push your project to a **private** GitHub repo (`.env` is in `.gitignore` — safe)
2. Go to https://railway.app → **New Project → Deploy from GitHub Repo**
3. Select your repo
4. In Railway's dashboard go to **Variables** and add every key from your `.env`
5. Railway auto-detects Python and runs `uvicorn main:app`
6. You get a URL like `https://amazon-repricer.up.railway.app`

Change `HOST=0.0.0.0` in your Railway variables so it binds correctly.

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError` | Wrong Python interpreter | In VS Code: `Ctrl+Shift+P` → **Python: Select Interpreter** → choose `venv/bin/python` |
| `403 Forbidden` from SP-API | IAM role trust not set up correctly | Re-check Role ARN and that your IAM user can assume the role |
| `400 Bad Request` on price update | Product type mismatch or SKU not live | Verify SKU exists and is active in Seller Central |
| Buy Box always shows `—` | ASIN has no Buy Box winner | Normal for some products — the repricer will skip them |
| Login page loops | `.env` not loaded | Make sure `.env` exists and `AUTH_PASSWORD` is set |

---

## Security checklist before going online

- [ ] `.env` is never committed (check `.gitignore`)
- [ ] `AUTH_PASSWORD` is a strong, unique password
- [ ] `SECRET_KEY` is a random 32-byte hex string (not the default placeholder)
- [ ] AWS IAM role uses least-privilege permissions
- [ ] Railway (or your host) environment variables are marked as **secret**
