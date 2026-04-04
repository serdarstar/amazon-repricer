"""
Amazon UK Repricer — FastAPI backend

Auth
────
All routes are protected by a signed session cookie.
  GET/POST /login   → login page
  GET      /logout  → clears session, redirects to /login

Endpoints
─────────
GET  /                       → Dashboard (requires auth)
GET  /api/stats              → Summary counts
GET  /api/listings           → All listings
POST /api/listings           → Add a listing
PUT  /api/listings/{sku}     → Update a listing
DEL  /api/listings/{sku}     → Remove a listing
POST /api/reprice/run        → Trigger manual reprice (async)
GET  /api/reprice/logs       → Activity log
GET  /api/settings           → All settings key/value
PUT  /api/settings/{key}     → Upsert a setting
"""
from __future__ import annotations
import logging
from typing import Optional

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, validator
from starlette.middleware.base import BaseHTTPMiddleware

from config import config
from database import get_db, init_db
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


def _make_session_token(username: str) -> str:
    return _signer.dumps(username)


def _verify_session_token(token: str) -> str | None:
    """Return the username if valid, else None."""
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ── Auth middleware ───────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        token    = request.cookies.get(SESSION_COOKIE, "")
        username = _verify_session_token(token)

        if not username:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)

        return await call_next(request)


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Amazon UK Repricer", version="1.0.0")
app.add_middleware(AuthMiddleware)

scheduler = BackgroundScheduler(timezone="Europe/London")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup() -> None:
    init_db()
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
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    if username == config.AUTH_USERNAME and password == config.AUTH_PASSWORD:
        token    = _make_session_token(username)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
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
# Stats
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
def get_stats() -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE enabled = 1"
    ).fetchone()[0]
    repriced_today = conn.execute(
        """SELECT COUNT(DISTINCT sku) FROM reprice_log
            WHERE action = 'REPRICED' AND date(timestamp) = date('now')"""
    ).fetchone()[0]
    matching_bb = conn.execute(
        """SELECT COUNT(*) FROM listings
            WHERE buy_box_price IS NOT NULL
              AND current_price  IS NOT NULL
              AND ABS(current_price - buy_box_price) < 0.01"""
    ).fetchone()[0]
    last_run_row = conn.execute(
        "SELECT MAX(timestamp) FROM reprice_log"
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
def get_listings() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM listings ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/listings", status_code=201)
def create_listing(listing: ListingCreate) -> dict:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO listings
               (sku, asin, title, current_price, min_price, max_price, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                listing.sku, listing.asin, listing.title,
                listing.current_price, listing.min_price,
                listing.max_price, int(listing.enabled),
            ),
        )
        conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
    return {"message": "Listing added", "sku": listing.sku}


@app.put("/api/listings/{sku}")
def update_listing(sku: str, update: ListingUpdate) -> dict:
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM listings WHERE sku = ?", (sku,)
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
    values     = list(fields.values()) + [sku]
    conn.execute(f"UPDATE listings SET {set_clause} WHERE sku = ?", values)
    conn.commit()
    conn.close()
    return {"message": "Listing updated", "sku": sku}


@app.delete("/api/listings/{sku}")
def delete_listing(sku: str) -> dict:
    conn = get_db()
    conn.execute("DELETE FROM listings WHERE sku = ?", (sku,))
    conn.commit()
    conn.close()
    return {"message": "Deleted", "sku": sku}


# ══════════════════════════════════════════════════════════════════════════════
# Repricing
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/reprice/run")
def manual_reprice(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(run_repricer)
    return {"message": "Repricing started"}


@app.get("/api/reprice/logs")
def get_logs(limit: int = 200) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reprice_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/settings")
def get_settings() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings/{key}")
def upsert_setting(key: str, setting: SettingUpdate) -> dict:
    conn = get_db()
    conn.execute(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
        (key, setting.value, setting.value),
    )
    conn.commit()
    conn.close()
    return {"message": "Setting saved", "key": key}


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
