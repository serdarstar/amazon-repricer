import sqlite3
from config import config


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sku             TEXT    UNIQUE NOT NULL,
            asin            TEXT    NOT NULL,
            title           TEXT    DEFAULT '',
            current_price   REAL,
            min_price       REAL    NOT NULL,
            max_price       REAL    NOT NULL,
            buy_box_price   REAL,
            enabled         INTEGER DEFAULT 1,
            last_repriced   TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reprice_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sku             TEXT    NOT NULL,
            asin            TEXT,
            old_price       REAL,
            new_price       REAL,
            buy_box_price   REAL,
            action          TEXT,
            reason          TEXT,
            timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
