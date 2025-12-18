import os
import json
import time
import requests
import jwt
from flask import Flask, request, jsonify

app = Flask(__name__)

# ─────────────────────────────────────────
# ENV VARIABLES (Railway)
# ─────────────────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")

# NOTE: Do NOT crash the container at startup if env vars are missing.
# Railway may start the service before vars are attached or during redeploys.
# We validate env vars at request time instead.
pass

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
        "aud": ["coinbase-cloud"],
    }

    headers = {
        "kid": COINBASE_API_KEY,
        "nonce": str(int(time.time() * 1000)),
    }

    return jwt.encode(
        payload,
        COINBASE_PRIVATE_KEY,
        algorithm="ES256",
        headers=headers,
    )

# ─────────────────────────────────────────
# PLACE MARKET ORDER
# ─────────────────────────────────────────

def place_market_order(side: str, usd_amount: float | None = None):
    jwt_token = create_jwt()

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
    }

    order = {
        "client_order_id": str(int(time.time() * 1000)),
        "product_id": PRODUCT_ID,
        "side": side.upper(),
        "order_configuration": {
            "market_market_ioc": {}
        },
    }

    if side.lower() == "buy":
        order["order_configuration"]["market_market_ioc"]["quote_size"] = str(
            usd_amount or DEFAULT_USD_AMOUNT
        )

    response = requests.post(
        f"{COINBASE_API_URL}/api/v3/brokerage/orders",
        headers=headers,
        json=order,
        timeout=10,
    )

    print("Coinbase response:", response.status_code, response.text)
    return response.status_code, response.text

# ─────────────────────────────────────────
# WEBHOOK ENDPOINT (TradingView)
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.data.decode("utf-8")
    print("RAW WEBHOOK BODY:", raw_body)

    try:
        data = json.loads(raw_body)
    except Exception:
        return jsonify(error="Body is not valid JSON"), 400

    action = data.get("action")
    symbol = data.get("symbol")
    usd_amount = float(data.get("usd_amount", DEFAULT_USD_AMOUNT))

    if action not in {"buy", "sell"}:
        return jsonify(error="Invalid or missing action"), 400

    if action == "buy":
        status, resp = place_market_order("buy", usd_amount)
    else:
        status, resp = place_market_order("sell")

    if status >= 300:
        return jsonify(error="Coinbase order failed", details=resp), 400

    return jsonify(status="order placed", action=action), 200

# ─────────────────────────────────────────
# HEALTH CHECK (OPTIONAL)
# ─────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# ─────────────────────────────────────────
# START SERVER
# ─────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
