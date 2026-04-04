import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ── App ───────────────────────────────────────────────────────────────
    DB_PATH                    = os.getenv("DB_PATH", "repricer.db")
    REPRICE_INTERVAL_MINUTES   = int(os.getenv("REPRICE_INTERVAL_MINUTES", "15"))
    HOST                       = os.getenv("HOST", "127.0.0.1")
    PORT                       = int(os.getenv("PORT", "8000"))

    # ── Amazon UK marketplace constant ────────────────────────────────────
    MARKETPLACE_ID             = "A1F83G8C2ARO7P"

    # ── Admin account (seeded on first startup) ───────────────────────────
    ADMIN_USERNAME             = os.getenv("AUTH_USERNAME", "admin")
    ADMIN_PASSWORD             = os.getenv("AUTH_PASSWORD", "changeme")
    SECRET_KEY                 = os.getenv("SECRET_KEY", "change-this-secret-key-before-deploying")

config = Config()
