"""
Amazon SP-API integration for Amazon UK.

Handles:
  - Fetching competitive (Buy Box) pricing for an ASIN
  - Patching listing price for a SKU via the Listings Items API

Dependencies: python-amazon-sp-api  (pip install python-amazon-sp-api)
"""
from __future__ import annotations
import logging
from typing import Optional

from sp_api.api import Products, ListingsItems
from sp_api.base import Marketplaces, SellingApiException

from config import config

logger = logging.getLogger(__name__)

# ── Credentials dict consumed by python-amazon-sp-api ─────────────────────
_CREDENTIALS: dict = {
    "refresh_token":      config.REFRESH_TOKEN,
    "lwa_app_id":         config.LWA_APP_ID,
    "lwa_client_secret":  config.LWA_CLIENT_SECRET,
    "aws_access_key":     config.AWS_ACCESS_KEY,
    "aws_secret_key":     config.AWS_SECRET_KEY,
    "role_arn":           config.ROLE_ARN,
}

_MARKETPLACE = Marketplaces.UK


# ── Public helpers ─────────────────────────────────────────────────────────

def get_buy_box_price(asin: str) -> Optional[float]:
    """
    Return the current Buy Box (competitive price ID = '1') for *asin*
    on Amazon UK, or None if unavailable / request failed.
    """
    try:
        api = Products(credentials=_CREDENTIALS, marketplace=_MARKETPLACE)
        resp = api.get_competitive_pricing_for_asins(asins=[asin])

        for item in resp.payload:
            if item.get("ASIN") != asin:
                continue
            comp = item.get("Product", {}).get("CompetitivePricing", {})
            for cp in comp.get("CompetitivePrices", []):
                # CompetitivePriceId '1' = Buy Box price
                if str(cp.get("CompetitivePriceId")) == "1":
                    amount = cp["Price"]["LandedPrice"]["Amount"]
                    return float(amount)
        return None

    except SellingApiException as exc:
        logger.warning("SP-API [get_buy_box_price] ASIN=%s  error=%s", asin, exc)
        return None
    except Exception as exc:                          # network, parse, etc.
        logger.error("Unexpected error in get_buy_box_price: %s", exc)
        return None


def update_price(sku: str, new_price: float) -> bool:
    """
    Patch the listing price for *sku* using the Listings Items API v2021-08-01.
    Returns True if Amazon accepted the request.
    """
    try:
        api = ListingsItems(credentials=_CREDENTIALS, marketplace=_MARKETPLACE)

        body = {
            "productType": "PRODUCT",
            "patches": [
                {
                    "op": "replace",
                    "path": "/attributes/purchasable_offer",
                    "value": [
                        {
                            "marketplace_id": config.MARKETPLACE_ID,
                            "currency": "GBP",
                            "our_price": [
                                {
                                    "schedule": [
                                        {"value_with_tax": round(new_price, 2)}
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        resp = api.patch_listings_item(
            sellerId=config.SELLER_ID,
            sku=sku,
            marketplaceIds=[config.MARKETPLACE_ID],
            body=body,
        )

        status = resp.payload.get("status", "")
        if status == "ACCEPTED":
            logger.info("Price updated  SKU=%s  new=£%.2f", sku, new_price)
            return True

        logger.warning(
            "Unexpected status from patch_listings_item  SKU=%s  status=%s",
            sku,
            status,
        )
        return False

    except SellingApiException as exc:
        logger.warning("SP-API [update_price] SKU=%s  error=%s", sku, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error in update_price SKU=%s: %s", sku, exc)
        return False
