import os
import json
import time
import requests
import jwt
from flask import Flask, request

app = Flask(__name__)

# ─────────────────────────────────────────
# ENV VARIABLES (Railway)
# ─────────────────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")

COINBASE_API_URL = "https://api.coinbase.com"
PRODUCT_ID = "BTC-USDC"
DEFAULT_USD_AMOUNT = 50

# ─────────────────────────────────────────
# CREATE JWT (Coinbase Advanced Trade)
# ─────────────────────────────────────────
def create_jwt():
    payload = {
        "sub": COINBASE_API_KEY,
        "iss": "coinbase-cloud",
        "nbf": int(time.time()),
        "exp": int(time.time()) + 120,
        "aud": ["coinbase-cloud"]
    }

    headers = {
        "kid": COINBASE_API_KEY,
        "nonce": str(int(time.time() * 1000))
    }

    token = jwt.encode(
        payload,
        COINBASE_PRIVATE_KEY,
        algorithm="ES256",
        headers=headers
    )

    return token

# ─────────────────────────────────────────
# PLACE MARKET ORDER
# ─────────────────────────────────────────
def place_market_order(side, usd_amount=None):
    jwt_token = create_jwt()

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }

    order = {
        "client_order_id": str(int(time.time() * 1000)),
        "product_id": PRODUCT_ID,
        "side": side.upper(),
        "order_configuration": {
            "market_market_ioc": {}
        }
    }

    if side == "buy":
        order["order_configuration"]["market_market_ioc"]["quote_size"] = str(
            usd_amount or DEFAULT_USD_AMOUNT
        )

    response = requests.post(
        f"{COINBASE_API_URL}/api/v3/brokerage/orders",
        headers=headers,
        json=order
    )

    print("Coinbase response:", response.status_code, response.text)
    return response.text

# ─────────────────────────────────────────
# WEBHOOK ENDPOINT
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return {"error": "Invalid JSON"}, 400

    action = data.get("action")
    symbol = data.get("symbol")

    if not action or not symbol:
        return {"error": "Missing fields"}, 400

    # proceed to Coinbase order logic
    return {"status": "ok"}, 200

# ─────────────────────────────────────────
# START SERVER
# ─────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
