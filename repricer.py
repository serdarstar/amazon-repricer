"""
Repricing engine — Match Buy Box strategy.

Logic:
  1. Fetch Buy Box price from SP-API.
  2. Clamp target to [min_price, max_price].
  3. Only call update_price() if the price actually needs to change.
  4. Persist result to DB and write a log entry.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from database import get_db
from amazon_api import get_buy_box_price, update_price

logger = logging.getLogger(__name__)


def _reprice_one(listing: dict[str, Any]) -> dict[str, Any]:
    """Process a single listing and return an action result dict."""
    sku           = listing["sku"]
    asin          = listing["asin"]
    current_price = listing["current_price"] or 0.0
    min_price     = listing["min_price"]
    max_price     = listing["max_price"]

    result = {
        "sku":           sku,
        "asin":          asin,
        "old_price":     current_price,
        "new_price":     current_price,
        "buy_box_price": None,
        "action":        "NO_CHANGE",
        "reason":        "",
    }

    # ── 1. Get Buy Box price ───────────────────────────────────────────────
    buy_box = get_buy_box_price(asin)
    result["buy_box_price"] = buy_box

    if buy_box is None:
        result["action"] = "SKIPPED"
        result["reason"] = "No Buy Box price available"
        return result

    # ── 2. Clamp to guardrails ─────────────────────────────────────────────
    if buy_box < min_price:
        target = min_price
        result["reason"] = (
            f"Buy Box £{buy_box:.2f} < min £{min_price:.2f} — holding at floor"
        )
    elif buy_box > max_price:
        target = max_price
        result["reason"] = (
            f"Buy Box £{buy_box:.2f} > max £{max_price:.2f} — holding at ceiling"
        )
    else:
        target = buy_box
        result["reason"] = f"Matching Buy Box at £{buy_box:.2f}"

    # ── 3. Skip if already correct ────────────────────────────────────────
    if abs(target - current_price) < 0.005:
        result["action"] = "NO_CHANGE"
        result["reason"] = f"Already at target £{target:.2f}"
        return result

    # ── 4. Push update ────────────────────────────────────────────────────
    success = update_price(sku, target)
    if success:
        result["new_price"] = target
        result["action"]    = "REPRICED"
    else:
        result["action"] = "FAILED"
        result["reason"] = "SP-API price update was rejected or failed"

    return result


def run_repricer() -> list[dict[str, Any]]:
    """
    Iterate over all enabled listings, apply repricing, persist results.
    Returns the list of action-result dicts (one per listing).
    """
    conn = get_db()
    cursor = conn.cursor()

    listings = cursor.execute(
        "SELECT * FROM listings WHERE enabled = 1"
    ).fetchall()

    results: list[dict[str, Any]] = []

    for row in listings:
        listing = dict(row)
        try:
            res = _reprice_one(listing)
        except Exception as exc:
            logger.exception("Unhandled error repricing SKU=%s: %s", listing["sku"], exc)
            res = {
                "sku":           listing["sku"],
                "asin":          listing["asin"],
                "old_price":     listing["current_price"],
                "new_price":     listing["current_price"],
                "buy_box_price": None,
                "action":        "ERROR",
                "reason":        str(exc),
            }

        # ── Persist to DB ──────────────────────────────────────────────
        cursor.execute(
            """
            UPDATE listings
               SET buy_box_price = ?,
                   current_price = ?,
                   last_repriced = ?
             WHERE sku = ?
            """,
            (res["buy_box_price"], res["new_price"], datetime.now().isoformat(), res["sku"]),
        )

        cursor.execute(
            """
            INSERT INTO reprice_log
                (sku, asin, old_price, new_price, buy_box_price, action, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                res["sku"], res["asin"],
                res["old_price"], res["new_price"],
                res["buy_box_price"],
                res["action"], res["reason"],
            ),
        )

        results.append(res)

    conn.commit()
    conn.close()

    repriced = sum(1 for r in results if r["action"] == "REPRICED")
    logger.info(
        "Repricer run complete — %d processed, %d repriced", len(results), repriced
    )
    return results
