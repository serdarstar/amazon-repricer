import sqlite3
from config import config


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_db()

    # ── New tables ────────────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sellers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT    UNIQUE NOT NULL,
            password_hash   TEXT    NOT NULL,
            is_admin        INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS seller_credentials (
            seller_id           INTEGER PRIMARY KEY REFERENCES sellers(id) ON DELETE CASCADE,
            refresh_token       TEXT NOT NULL,
            lwa_app_id          TEXT NOT NULL,
            lwa_client_secret   TEXT NOT NULL,
            aws_access_key      TEXT NOT NULL,
            aws_secret_key      TEXT NOT NULL,
            role_arn            TEXT NOT NULL,
            seller_id_amz       TEXT NOT NULL,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Migrate listings table ────────────────────────────────────────────
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(listings)").fetchall()
    }
    if "seller_id" not in existing_cols:
        # Recreate with (seller_id, sku) unique constraint
        conn.executescript("""
            CREATE TABLE listings_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id       INTEGER REFERENCES sellers(id),
                sku             TEXT    NOT NULL,
                asin            TEXT    NOT NULL,
                title           TEXT    DEFAULT '',
                current_price   REAL,
                min_price       REAL    NOT NULL,
                max_price       REAL    NOT NULL,
                buy_box_price   REAL,
                enabled         INTEGER DEFAULT 1,
                last_repriced   TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(seller_id, sku)
            );
            INSERT INTO listings_new
                (id, sku, asin, title, current_price, min_price, max_price,
                 buy_box_price, enabled, last_repriced, created_at)
            SELECT id, sku, asin, title, current_price, min_price, max_price,
                   buy_box_price, enabled, last_repriced, created_at
            FROM listings;
            DROP TABLE listings;
            ALTER TABLE listings_new RENAME TO listings;
        """)

    # ── Migrate reprice_log table ─────────────────────────────────────────
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(reprice_log)").fetchall()
    }
    if "seller_id" not in existing_cols:
        conn.execute("ALTER TABLE reprice_log ADD COLUMN seller_id INTEGER REFERENCES sellers(id)")

    # ── Migrate settings table ────────────────────────────────────────────
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(settings)").fetchall()
    }
    if "seller_id" not in existing_cols:
        conn.executescript("""
            CREATE TABLE settings_new (
                seller_id   INTEGER REFERENCES sellers(id),
                key         TEXT    NOT NULL,
                value       TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (seller_id, key)
            );
            INSERT INTO settings_new (key, value, updated_at)
            SELECT key, value, updated_at FROM settings;
            DROP TABLE settings;
            ALTER TABLE settings_new RENAME TO settings;
        """)

    conn.commit()
    conn.close()


def seed_admin(username: str, password_hash: str) -> int:
    """Insert admin account if none exists. Returns the admin's seller id."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM sellers WHERE is_admin = 1"
    ).fetchone()
    if row:
        conn.close()
        return row["id"]

    cur = conn.execute(
        "INSERT INTO sellers (username, password_hash, is_admin) VALUES (?, ?, 1)",
        (username, password_hash),
    )
    admin_id = cur.lastrowid

    # Reassign any existing data to admin
    conn.execute("UPDATE listings    SET seller_id = ? WHERE seller_id IS NULL", (admin_id,))
    conn.execute("UPDATE reprice_log SET seller_id = ? WHERE seller_id IS NULL", (admin_id,))
    conn.execute("UPDATE settings    SET seller_id = ? WHERE seller_id IS NULL", (admin_id,))

    conn.commit()
    conn.close()
    return admin_id
