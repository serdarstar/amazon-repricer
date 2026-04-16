"""Quick test to verify SP-API credentials are working."""
from dotenv import load_dotenv, dotenv_values
load_dotenv()

from amazon_api import get_buy_box_price, update_price

TEST_ASIN = "B084NZLX89"
TEST_SKU   = "9G-C6HT-FDRE"
NEW_PRICE  = 14.28

env = dotenv_values(".env")
credentials = {
    "refresh_token":     env["REFRESH_TOKEN"],
    "lwa_app_id":        env["LWA_APP_ID"],
    "lwa_client_secret": env["LWA_CLIENT_SECRET"],
    "aws_access_key":    env["AWS_ACCESS_KEY"],
    "aws_secret_key":    env["AWS_SECRET_KEY"],
    "role_arn":          env["ROLE_ARN"],
}
seller_id = env["SELLER_ID"]

print("Testing SP-API connection...")
try:
    price = get_buy_box_price(TEST_ASIN, credentials)
    if price is not None:
        print(f"SUCCESS — Buy Box price for {TEST_ASIN}: £{price:.2f}")
    else:
        print(f"Connected OK — but no Buy Box winner currently for {TEST_ASIN}")
except Exception as e:
    print(f"FAILED — {e}")

print(f"\nSetting price for SKU {TEST_SKU} to £{NEW_PRICE:.2f}...")
try:
    ok = update_price(TEST_SKU, NEW_PRICE, credentials, seller_id)
    if ok:
        print(f"SUCCESS — Price set to £{NEW_PRICE:.2f}")
    else:
        print("FAILED — Amazon did not accept the price update (check logs)")
except Exception as e:
    print(f"FAILED — {e}")
