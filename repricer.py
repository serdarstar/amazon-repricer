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
from typing import Any, Optional

from database import get_db
from amazon_api import get_buy_box_price, update_price, _build_credentials

logger = logging.getLogger(__name__)


def _reprice_one(listing: dict[str, Any], credentials: dict, seller_id_amz: str, force: bool = False) -> dict[str, Any]:
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
    buy_box = get_buy_box_price(asin, credentials)
    result["buy_box_price"] = buy_box

    if buy_box is None:
        result["action"] = "SKIPPED"
        result["reason"] = "No Buy Box price available"
        return result

    # ── 2. Clamp to guardrails ─────────────────────────────────────────────
    if buy_box < min_price:
        target = min_price
        result["reason"] = f"Buy Box £{buy_box:.2f} < min £{min_price:.2f} — holding at floor"
    elif buy_box > max_price:
        target = max_price
        result["reason"] = f"Buy Box £{buy_box:.2f} > max £{max_price:.2f} — holding at ceiling"
    else:
        target = buy_box
        result["reason"] = f"Matching Buy Box at £{buy_box:.2f}"

    # ── 3. Skip if already correct ────────────────────────────────────────
    if abs(target - current_price) < 0.005 and not force:
        result["action"] = "NO_CHANGE"
        result["reason"] = f"Already at target £{target:.2f}"
        return result

    # ── 4. Push update ────────────────────────────────────────────────────
    success = update_price(sku, target, credentials, seller_id_amz, asin=asin)
    if success:
        result["new_price"] = target
        result["action"]    = "REPRICED"
    else:
        result["action"] = "FAILED"
        result["reason"] = "SP-API price update was rejected or failed"

    return result


def force_push_sku(sku: str, seller_id: int) -> dict[str, Any]:
    """Force-push the current target price for one SKU to Amazon, bypassing NO_CHANGE check."""
    conn = get_db()
    cursor = conn.cursor()

    creds_row = cursor.execute(
        "SELECT * FROM seller_credentials WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    if not creds_row:
        conn.close()
        return {"error": "No credentials found"}

    listing_row = cursor.execute(
        "SELECT * FROM listings WHERE sku = ? AND seller_id = ?", (sku, seller_id)
    ).fetchone()
    if not listing_row:
        conn.close()
        return {"error": "Listing not found"}

    credentials   = _build_credentials(dict(creds_row))
    seller_id_amz = creds_row["seller_id_amz"]
    listing       = dict(listing_row)

    try:
        res = _reprice_one(listing, credentials, seller_id_amz, force=True)
    except Exception as exc:
        conn.close()
        return {"error": str(exc)}

    cursor.execute(
        """UPDATE listings SET buy_box_price = ?, current_price = ?, last_repriced = ?
            WHERE sku = ? AND seller_id = ?""",
        (res["buy_box_price"], res["new_price"], datetime.now().isoformat(), sku, seller_id),
    )
    cursor.execute(
        """INSERT INTO reprice_log
               (seller_id, sku, asin, old_price, new_price, buy_box_price, action, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (seller_id, res["sku"], res["asin"], res["old_price"], res["new_price"],
         res["buy_box_price"], res["action"], res["reason"]),
    )
    conn.commit()
    conn.close()
    return res


def run_repricer(seller_id: Optional[int] = None) -> list[dict[str, Any]]:
    """
    Iterate over all enabled listings for all sellers (or one seller if seller_id given).
    Returns the list of action-result dicts.
    """
    conn = get_db()
    cursor = conn.cursor()

    if seller_id is not None:
        sellers = cursor.execute(
            "SELECT id FROM sellers WHERE id = ?", (seller_id,)
        ).fetchall()
    else:
        sellers = cursor.execute("SELECT id FROM sellers").fetchall()

    all_results: list[dict[str, Any]] = []

    for seller_row in sellers:
        sid = seller_row["id"]

        creds_row = cursor.execute(
            "SELECT * FROM seller_credentials WHERE seller_id = ?", (sid,)
        ).fetchone()

        if not creds_row:
            logger.warning("Seller id=%d has no credentials — skipping", sid)
            continue

        credentials  = _build_credentials(dict(creds_row))
        seller_id_amz = creds_row["seller_id_amz"]

        listings = cursor.execute(
            "SELECT * FROM listings WHERE enabled = 1 AND seller_id = ?", (sid,)
        ).fetchall()

        for row in listings:
            listing = dict(row)
            try:
                res = _reprice_one(listing, credentials, seller_id_amz)
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

            cursor.execute(
                """
                UPDATE listings
                   SET buy_box_price = ?,
                       current_price = ?,
                       last_repriced = ?
                 WHERE sku = ? AND seller_id = ?
                """,
                (res["buy_box_price"], res["new_price"], datetime.now().isoformat(),
                 res["sku"], sid),
            )

            cursor.execute(
                """
                INSERT INTO reprice_log
                    (seller_id, sku, asin, old_price, new_price, buy_box_price, action, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, res["sku"], res["asin"], res["old_price"], res["new_price"],
                 res["buy_box_price"], res["action"], res["reason"]),
            )

            all_results.append(res)

    conn.commit()
    conn.close()

    repriced = sum(1 for r in all_results if r["action"] == "REPRICED")
    logger.info(
        "Repricer run complete — %d processed, %d repriced", len(all_results), repriced
    )
    return all_results
