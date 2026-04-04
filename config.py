import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ── Amazon SP-API Credentials ──────────────────────────────────────────
    REFRESH_TOKEN      = os.getenv("REFRESH_TOKEN", "")
    LWA_APP_ID         = os.getenv("LWA_APP_ID", "")          # Client ID
    LWA_CLIENT_SECRET  = os.getenv("LWA_CLIENT_SECRET", "")   # Client Secret
    AWS_ACCESS_KEY     = os.getenv("AWS_ACCESS_KEY", "")
    AWS_SECRET_KEY     = os.getenv("AWS_SECRET_KEY", "")
    ROLE_ARN           = os.getenv("ROLE_ARN", "")
    SELLER_ID          = os.getenv("SELLER_ID", "")

    # ── Amazon UK ──────────────────────────────────────────────────────────
    MARKETPLACE_ID     = "A1F83G8C2ARO7P"

    # ── App ───────────────────────────────────────────────────────────────
    DB_PATH                    = os.getenv("DB_PATH", "repricer.db")
    REPRICE_INTERVAL_MINUTES   = int(os.getenv("REPRICE_INTERVAL_MINUTES", "15"))
    HOST                       = os.getenv("HOST", "127.0.0.1")
    PORT                       = int(os.getenv("PORT", "8000"))

    # ── Auth ──────────────────────────────────────────────────────────────
    AUTH_USERNAME              = os.getenv("AUTH_USERNAME", "admin")
    AUTH_PASSWORD              = os.getenv("AUTH_PASSWORD", "changeme")
    SECRET_KEY                 = os.getenv("SECRET_KEY", "change-this-secret-key-before-deploying")

config = Config()
