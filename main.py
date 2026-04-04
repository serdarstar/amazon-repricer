"""
Amazon UK Repricer — FastAPI backend (multi-seller)

Auth
────
All routes are protected by a signed session cookie.
  GET/POST /login   → login page
  GET      /logout  → clears session, redirects to /login

Seller endpoints (scoped to logged-in seller)
──────────────────────────────────────────────
GET  /                       → Dashboard
GET  /api/stats              → Summary counts
GET  /api/listings           → Seller's listings
POST /api/listings           → Add a listing
PUT  /api/listings/{sku}     → Update a listing
DEL  /api/listings/{sku}     → Remove a listing
POST /api/reprice/run        → Trigger manual reprice
GET  /api/reprice/logs       → Activity log
GET  /api/settings           → Settings
PUT  /api/settings/{key}     → Upsert a setting

Admin endpoints (admin only)
─────────────────────────────
GET    /admin/sellers                     → List all sellers
POST   /admin/sellers                     → Create a seller
DELETE /admin/sellers/{seller_id}         → Delete a seller
POST   /admin/sellers/{seller_id}/creds   → Set SP-API credentials
GET    /api/me                            → Current session info
"""
from __future__ import annotations
import logging
from typing import Optional

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt
from pydantic import BaseModel, validator
from starlette.middleware.base import BaseHTTPMiddleware

from config import config
from database import get_db, init_db, seed_admin
from repricer import run_repricer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Session helpers ───────────────────────────────────────────────────────────

_signer         = URLSafeTimedSerializer(config.SECRET_KEY, salt="session")
SESSION_COOKIE  = "rp_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7   # 7 days


def _make_session_token(seller_id: int, username: str, is_admin: bool) -> str:
    return _signer.dumps({"id": seller_id, "u": username, "admin": is_admin})


def _verify_session_token(token: str) -> dict | None:
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _get_current_seller(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE, "")
    data  = _verify_session_token(token)
    if not data:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return data


def _require_admin(request: Request) -> dict:
    data = _get_current_seller(request)
    if not data.get("admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return data


# ── Auth middleware ───────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE, "")
        data  = _verify_session_token(token)

        if not data:
            if request.url.path.startswith("/api/") or request.url.path.startswith("/admin/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)

        return await call_next(request)


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Amazon UK Repricer", version="2.0.0")
app.add_middleware(AuthMiddleware)

scheduler = BackgroundScheduler(timezone="Europe/London")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup() -> None:
    init_db()
    pw_hash = bcrypt.hash(config.ADMIN_PASSWORD)
    seed_admin(config.ADMIN_USERNAME, pw_hash)
    interval = config.REPRICE_INTERVAL_MINUTES
    scheduler.add_job(run_repricer, "interval", minutes=interval, id="auto_repricer")
    scheduler.start()
    logger.info("Auto-repricer scheduled every %d minutes", interval)


@app.on_event("shutdown")
def on_shutdown() -> None:
    scheduler.shutdown(wait=False)


# ── Pydantic models ───────────────────────────────────────────────────────────

class ListingCreate(BaseModel):
    sku:           str
    asin:          str
    title:         Optional[str] = ""
    current_price: float
    min_price:     float
    max_price:     float
    enabled:       bool = True

    @validator("max_price")
    def max_must_exceed_min(cls, v, values):
        if "min_price" in values and v < values["min_price"]:
            raise ValueError("max_price must be >= min_price")
        return v


class ListingUpdate(BaseModel):
    title:         Optional[str]   = None
    current_price: Optional[float] = None
    min_price:     Optional[float] = None
    max_price:     Optional[float] = None
    enabled:       Optional[bool]  = None


class SettingUpdate(BaseModel):
    value: str


class SellerCreate(BaseModel):
    username: str
    password: str


class SellerCredentials(BaseModel):
    refresh_token:     str
    lwa_app_id:        str
    lwa_client_secret: str
    aws_access_key:    str
    aws_secret_key:    str
    role_arn:          str
    seller_id_amz:     str


# ══════════════════════════════════════════════════════════════════════════════
# Auth routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = "") -> HTMLResponse:
    with open("templates/login.html", encoding="utf-8") as fh:
        html = fh.read()
    if error:
        html = html.replace(
            "<!--ERROR-->",
            '<p class="error-msg">Incorrect username or password.</p>',
        )
    return HTMLResponse(html)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    conn = get_db()
    row  = conn.execute(
        "SELECT id, password_hash, is_admin FROM sellers WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if row and bcrypt.verify(password, row["password_hash"]):
        token    = _make_session_token(row["id"], username, bool(row["is_admin"]))
        response = RedirectResponse("/", status_code=302)
        is_https = request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=is_https,
        )
        logger.info("Successful login: %s", username)
        return response

    logger.warning("Failed login attempt: username=%s", username)
    return RedirectResponse("/login?error=1", status_code=302)


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ══════════════════════════════════════════════════════════════════════════════
# Frontend
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    with open("templates/index.html", encoding="utf-8") as fh:
        return HTMLResponse(content=fh.read())


# ══════════════════════════════════════════════════════════════════════════════
# Session info
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/me")
def get_me(request: Request) -> dict:
    data = _get_current_seller(request)
    return {"seller_id": data["id"], "username": data["u"], "is_admin": data["admin"]}


# ══════════════════════════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
def get_stats(request: Request) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE seller_id = ?", (seller_id,)
    ).fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE enabled = 1 AND seller_id = ?", (seller_id,)
    ).fetchone()[0]
    repriced_today = conn.execute(
        """SELECT COUNT(DISTINCT sku) FROM reprice_log
            WHERE action = 'REPRICED' AND date(timestamp) = date('now') AND seller_id = ?""",
        (seller_id,)
    ).fetchone()[0]
    matching_bb = conn.execute(
        """SELECT COUNT(*) FROM listings
            WHERE buy_box_price IS NOT NULL
              AND current_price  IS NOT NULL
              AND ABS(current_price - buy_box_price) < 0.01
              AND seller_id = ?""",
        (seller_id,)
    ).fetchone()[0]
    last_run_row = conn.execute(
        "SELECT MAX(timestamp) FROM reprice_log WHERE seller_id = ?", (seller_id,)
    ).fetchone()[0]
    conn.close()
    return {
        "total_listings":   total,
        "active_listings":  active,
        "repriced_today":   repriced_today,
        "matching_buy_box": matching_bb,
        "last_run":         last_run_row or "Never",
        "interval_minutes": config.REPRICE_INTERVAL_MINUTES,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Listings CRUD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/listings")
def get_listings(request: Request) -> list[dict]:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM listings WHERE seller_id = ? ORDER BY created_at DESC", (seller_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/listings", status_code=201)
def create_listing(request: Request, listing: ListingCreate) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO listings
               (seller_id, sku, asin, title, current_price, min_price, max_price, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (seller_id, listing.sku, listing.asin, listing.title,
             listing.current_price, listing.min_price, listing.max_price, int(listing.enabled)),
        )
        conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
    return {"message": "Listing added", "sku": listing.sku}


@app.put("/api/listings/{sku}")
def update_listing(sku: str, update: ListingUpdate, request: Request) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM listings WHERE sku = ? AND seller_id = ?", (sku, seller_id)
    ).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Listing not found")

    fields = {k: v for k, v in update.dict().items() if v is not None}
    if not fields:
        conn.close()
        return {"message": "Nothing to update"}

    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values     = list(fields.values()) + [sku, seller_id]
    conn.execute(f"UPDATE listings SET {set_clause} WHERE sku = ? AND seller_id = ?", values)
    conn.commit()
    conn.close()
    return {"message": "Listing updated", "sku": sku}


@app.delete("/api/listings/{sku}")
def delete_listing(sku: str, request: Request) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    conn.execute("DELETE FROM listings WHERE sku = ? AND seller_id = ?", (sku, seller_id))
    conn.commit()
    conn.close()
    return {"message": "Deleted", "sku": sku}


# ══════════════════════════════════════════════════════════════════════════════
# Repricing
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/reprice/run")
def manual_reprice(request: Request, background_tasks: BackgroundTasks) -> dict:
    seller_id = _get_current_seller(request)["id"]
    background_tasks.add_task(run_repricer, seller_id)
    return {"message": "Repricing started"}


@app.get("/api/reprice/logs")
def get_logs(request: Request, limit: int = 200) -> list[dict]:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reprice_log WHERE seller_id = ? ORDER BY timestamp DESC LIMIT ?",
        (seller_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/settings")
def get_settings(request: Request) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE seller_id = ?", (seller_id,)
    ).fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings/{key}")
def upsert_setting(key: str, setting: SettingUpdate, request: Request) -> dict:
    seller_id = _get_current_seller(request)["id"]
    conn = get_db()
    conn.execute(
        """INSERT INTO settings (seller_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT(seller_id, key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
        (seller_id, key, setting.value, setting.value),
    )
    conn.commit()
    conn.close()
    return {"message": "Setting saved", "key": key}


# ══════════════════════════════════════════════════════════════════════════════
# Admin routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/sellers")
def list_sellers(request: Request) -> list[dict]:
    _require_admin(request)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, is_admin, created_at FROM sellers ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/admin/sellers", status_code=201)
def create_seller(seller: SellerCreate, request: Request) -> dict:
    _require_admin(request)
    pw_hash = bcrypt.hash(seller.password)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO sellers (username, password_hash) VALUES (?, ?)",
            (seller.username, pw_hash),
        )
        conn.commit()
        new_id = cur.lastrowid
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
    return {"message": "Seller created", "seller_id": new_id, "username": seller.username}


@app.delete("/admin/sellers/{sid}")
def delete_seller(sid: int, request: Request) -> dict:
    _require_admin(request)
    conn = get_db()
    conn.execute("DELETE FROM sellers WHERE id = ? AND is_admin = 0", (sid,))
    conn.commit()
    conn.close()
    return {"message": "Seller deleted", "seller_id": sid}


@app.post("/admin/sellers/{sid}/creds", status_code=201)
def set_seller_creds(sid: int, creds: SellerCredentials, request: Request) -> dict:
    _require_admin(request)
    conn = get_db()
    conn.execute(
        """INSERT INTO seller_credentials
               (seller_id, refresh_token, lwa_app_id, lwa_client_secret,
                aws_access_key, aws_secret_key, role_arn, seller_id_amz)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(seller_id) DO UPDATE SET
               refresh_token     = excluded.refresh_token,
               lwa_app_id        = excluded.lwa_app_id,
               lwa_client_secret = excluded.lwa_client_secret,
               aws_access_key    = excluded.aws_access_key,
               aws_secret_key    = excluded.aws_secret_key,
               role_arn          = excluded.role_arn,
               seller_id_amz     = excluded.seller_id_amz,
               updated_at        = CURRENT_TIMESTAMP""",
        (sid, creds.refresh_token, creds.lwa_app_id, creds.lwa_client_secret,
         creds.aws_access_key, creds.aws_secret_key, creds.role_arn, creds.seller_id_amz),
    )
    conn.commit()
    conn.close()
    return {"message": "Credentials saved", "seller_id": sid}


@app.get("/admin/sellers/{sid}/creds")
def get_seller_creds(sid: int, request: Request) -> dict:
    _require_admin(request)
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM seller_credentials WHERE seller_id = ?", (sid,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No credentials found")
    d = dict(row)
    # Mask secrets
    for field in ("lwa_client_secret", "aws_secret_key", "refresh_token"):
        if d.get(field):
            d[field] = "****" + d[field][-4:]
    return d


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )
