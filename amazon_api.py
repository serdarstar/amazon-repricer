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

_MARKETPLACE = Marketplaces.UK


def _build_credentials(seller: dict) -> dict:
    """Build the credentials dict expected by python-amazon-sp-api from a seller row."""
    return {
        "refresh_token":     seller["refresh_token"],
        "lwa_app_id":        seller["lwa_app_id"],
        "lwa_client_secret": seller["lwa_client_secret"],
        "aws_access_key":    seller["aws_access_key"],
        "aws_secret_key":    seller["aws_secret_key"],
        "role_arn":          seller["role_arn"],
    }


def get_buy_box_price(asin: str, credentials: dict) -> Optional[float]:
    """
    Return the current Buy Box price for *asin* on Amazon UK, or None if unavailable.
    """
    try:
        api = Products(credentials=credentials, marketplace=_MARKETPLACE)
        resp = api.get_competitive_pricing_for_asins(asin_list=[asin])

        for item in resp.payload:
            if item.get("ASIN") != asin:
                continue
            comp = item.get("Product", {}).get("CompetitivePricing", {})
            for cp in comp.get("CompetitivePrices", []):
                if str(cp.get("CompetitivePriceId")) == "1":
                    amount = cp["Price"]["LandedPrice"]["Amount"]
                    return float(amount)
        return None

    except SellingApiException as exc:
        logger.warning("SP-API [get_buy_box_price] ASIN=%s  error=%s", asin, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error in get_buy_box_price: %s", exc)
        return None


def update_price(sku: str, new_price: float, credentials: dict, seller_id_amz: str) -> bool:
    """
    Patch the listing price for *sku* using the Listings Items API v2021-08-01.
    Returns True if Amazon accepted the request.
    """
    try:
        api = ListingsItems(credentials=credentials, marketplace=_MARKETPLACE)

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
            sellerId=seller_id_amz,
            sku=sku,
            marketplaceIds=[config.MARKETPLACE_ID],
            body=body,
        )

        status = resp.payload.get("status", "")
        if status == "ACCEPTED":
            logger.info("Price updated  SKU=%s  new=£%.2f", sku, new_price)
            return True

        logger.warning(
            "Unexpected status from patch_listings_item  SKU=%s  status=%s", sku, status
        )
        return False

    except SellingApiException as exc:
        logger.warning("SP-API [update_price] SKU=%s  error=%s", sku, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error in update_price SKU=%s: %s", sku, exc)
        return False
