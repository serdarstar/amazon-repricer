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

from sp_api.api import Products, ListingsItems, CatalogItems, ProductFees
from sp_api.base import Marketplaces, SellingApiException

from config import config

logger = logging.getLogger(__name__)

_MARKETPLACE = Marketplaces.UK

# Cache product types so we don't look them up on every reprice cycle
_product_type_cache: dict[str, str] = {}


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


def _get_product_type(asin: str, credentials: dict) -> str:
    """
    Look up the correct product type for an ASIN from the Amazon catalog.
    Falls back to 'PRODUCT' if unavailable.
    """
    if asin in _product_type_cache:
        return _product_type_cache[asin]
    try:
        api = CatalogItems(credentials=credentials, marketplace=_MARKETPLACE)
        resp = api.get_catalog_item(
            asin=asin,
            marketplaceIds=[config.MARKETPLACE_ID],
            includedData=["productTypes"],
        )
        for pt in resp.payload.get("productTypes", []):
            if pt.get("marketplaceId") == config.MARKETPLACE_ID:
                product_type = pt["productType"]
                _product_type_cache[asin] = product_type
                logger.info("Product type for ASIN=%s: %s", asin, product_type)
                return product_type
    except Exception as exc:
        logger.warning("Could not fetch product type for ASIN=%s: %s", asin, exc)
    return "PRODUCT"


def get_buy_box_price(asin: str, credentials: dict) -> Optional[float]:
    """
    Return the current Buy Box price for *asin* on Amazon UK, or None if unavailable.
    Checks CompetitivePrices (third-party buy box) first, then BuyBoxPrices
    (which includes Amazon retail as the buy box holder).
    """
    try:
        api = Products(credentials=credentials, marketplace=_MARKETPLACE)
        resp = api.get_competitive_pricing_for_asins(asin_list=[asin])

        for item in resp.payload:
            if item.get("ASIN") != asin:
                continue
            comp = item.get("Product", {}).get("CompetitivePricing", {})

            # CompetitivePriceId=1 → third-party buy box winner
            for cp in comp.get("CompetitivePrices", []):
                if str(cp.get("CompetitivePriceId")) == "1":
                    amount = cp["Price"]["LandedPrice"]["Amount"]
                    return float(amount)

            # Fall back to BuyBoxPrices — includes Amazon retail as winner
            for bp in comp.get("BuyBoxPrices", []):
                if bp.get("condition", "").lower() in ("new", "used"):
                    amount = bp["LandedPrice"]["Amount"]
                    logger.info("Buy box held by Amazon retail  ASIN=%s  price=£%.2f", asin, amount)
                    return float(amount)

        return None

    except SellingApiException as exc:
        logger.warning("SP-API [get_buy_box_price] ASIN=%s  error=%s", asin, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error in get_buy_box_price: %s", exc)
        return None


def analyze_asin(asin: str, credentials: dict) -> dict:
    """
    Fetch a full product snapshot for the Analysis page.
    Returns: title, image_url, category, bsr, buy_box_price,
             offer_count_new, fba_fee, referral_fee
    """
    result: dict = {
        "asin":            asin,
        "title":           None,
        "image_url":       None,
        "category":        None,
        "bsr":             None,
        "buy_box_price":   None,
        "offer_count_new": None,
        "fba_fee":         None,
        "referral_fee":    None,
    }

    # ── 1. Catalog: title, image, BSR, category ────────────────────────────
    try:
        cat_api = CatalogItems(credentials=credentials, marketplace=_MARKETPLACE)
        cat_resp = cat_api.get_catalog_item(
            asin=asin,
            marketplaceIds=[config.MARKETPLACE_ID],
            includedData=["summaries", "salesRanks", "images"],
        )
        payload = cat_resp.payload

        for summary in payload.get("summaries", []):
            if summary.get("marketplaceId") == config.MARKETPLACE_ID:
                result["title"] = summary.get("itemName")
                img = summary.get("mainImage", {})
                if img.get("link"):
                    result["image_url"] = img["link"]
                break

        # Try images data if mainImage wasn't in summaries
        if not result["image_url"]:
            for img_block in payload.get("images", []):
                if img_block.get("marketplaceId") == config.MARKETPLACE_ID:
                    for img in img_block.get("images", []):
                        if img.get("variant") in ("MAIN", "PT01") and img.get("link"):
                            result["image_url"] = img["link"]
                            break
                    break

        for sr in payload.get("salesRanks", []):
            if sr.get("marketplaceId") == config.MARKETPLACE_ID:
                ranks = sr.get("ranks", [])
                if ranks:
                    result["bsr"]      = ranks[0].get("rank")
                    result["category"] = ranks[0].get("title") or ranks[0].get("displayGroupName")
                break
    except Exception as exc:
        logger.warning("CatalogItems failed ASIN=%s: %s", asin, exc)

    # ── 2. Products: buy box price + offer count ───────────────────────────
    try:
        prod_api = Products(credentials=credentials, marketplace=_MARKETPLACE)
        prod_resp = prod_api.get_competitive_pricing_for_asins(asin_list=[asin])

        for item in prod_resp.payload:
            if item.get("ASIN") != asin:
                continue
            comp = item.get("Product", {}).get("CompetitivePricing", {})

            for cp in comp.get("CompetitivePrices", []):
                if str(cp.get("CompetitivePriceId")) == "1":
                    result["buy_box_price"] = float(cp["Price"]["LandedPrice"]["Amount"])
                    break

            if result["buy_box_price"] is None:
                for bp in comp.get("BuyBoxPrices", []):
                    if bp.get("condition", "").lower() in ("new", "used"):
                        result["buy_box_price"] = float(bp["LandedPrice"]["Amount"])
                        break

            for offer in comp.get("NumberOfOfferListings", []):
                if offer.get("condition", "").lower() == "new":
                    result["offer_count_new"] = offer.get("Count")
                    break
    except Exception as exc:
        logger.warning("Products API failed ASIN=%s: %s", asin, exc)

    # ── 3. FBA fee estimate ────────────────────────────────────────────────
    price_for_fees = result["buy_box_price"] or 10.0
    try:
        fees_api = ProductFees(credentials=credentials, marketplace=_MARKETPLACE)
        fees_resp = fees_api.get_product_fees_estimate_for_asin(
            asin,
            price=price_for_fees,
            currency="GBP",
            is_fba=True,
        )
        fees_result = fees_resp.payload.get("FeesEstimateResult", {})
        fees_est    = fees_result.get("FeesEstimate", {})
        total       = fees_est.get("TotalFeesEstimate", {})
        if total:
            result["fba_fee"] = float(total.get("Amount", 0))
        for component in fees_est.get("FeeDetailList", []):
            if component.get("FeeType") == "ReferralFee":
                amt = component.get("FeeAmount", {})
                result["referral_fee"] = float(amt.get("Amount", 0))
                break
    except Exception as exc:
        logger.warning("ProductFees API failed ASIN=%s: %s", asin, exc)

    return result


def update_price(sku: str, new_price: float, credentials: dict, seller_id_amz: str, asin: str = "") -> bool:
    """
    Patch the listing price for *sku* using the Listings Items API v2021-08-01.
    Uses the correct product type from the Amazon catalog.
    Returns True if Amazon accepted the request.
    """
    try:
        product_type = _get_product_type(asin, credentials) if asin else "PRODUCT"

        api = ListingsItems(credentials=credentials, marketplace=_MARKETPLACE)

        body = {
            "productType": product_type,
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

        payload = resp.payload
        status = payload.get("status", "")
        issues = payload.get("submissionIssues", [])
        if issues:
            logger.warning("SP-API submission issues  SKU=%s  issues=%s", sku, issues)
        if status == "ACCEPTED":
            logger.info(
                "Price updated  SKU=%s  ASIN=%s  productType=%s  new=£%.2f  issues=%s",
                sku, asin, product_type, new_price, issues,
            )
            return True

        logger.warning(
            "Unexpected status  SKU=%s  status=%s  payload=%s", sku, status, payload,
        )
        return False

    except SellingApiException as exc:
        logger.warning("SP-API [update_price] SKU=%s  error=%s", sku, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error in update_price SKU=%s: %s", sku, exc)
        return False
