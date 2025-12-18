import os
import json
import time
import hmac
import hashlib
import requests
from flask import Flask, request

app = Flask(__name__)

# ─────────────────────────────────────────
# ENVIRONMENT VARIABLES (Railway)
# ─────────────────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_API_SECRET = os.environ.get("COINBASE_API_SECRET")
COINBASE_API_PASSPHRASE = os.environ.get("COINBASE_API_PASSPHRASE")

COINBASE_API_URL = "https://api.exchange.coinbase.com"

PRODUCT_ID = "BTC-USDC"   # ✅ correct for BTC-USDC
USD_AMOUNT = 50           # fallback amount if not provided

# ─────────────────────────────────────────
# COINBASE SIGNATURE
# ─────────────────────────────────────────
def sign_request(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    hmac_key = base64.b64decode(COINBASE_API_SECRET)
    signature = hmac.new(hmac_key, message.encode(), hashlib.sha256)
    return base64.b64encode(signature.digest()).decode()

# ─────────────────────────────────────────
# PLACE ORDER
# ─────────────────────────────────────────
def place_market_order(side, usd_amount=None):
    timestamp = str(time.time())
    request_path = "/orders"

    order = {
        "type": "market",
        "side": side,
        "product_id": PRODUCT_ID
    }

    if side == "buy":
        order["funds"] = str(usd_amount or USD_AMOUNT)
    else:
        order["size"] = "all"

    body = json.dumps(order)
    signature = sign_request(timestamp, "POST", request_path, body)

    headers = {
        "CB-ACCESS-KEY": COINBASE_API_KEY,
        "CB-ACCESS-SIGN": signature,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": COINBASE_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

    response = requests.post(
        COINBASE_API_URL + request_path,
        headers=headers,
        data=body
    )

    print("Coinbase response:", response.status_code, response.text)
    return response.text

# ─────────────────────────────────────────
# WEBHOOK ROUTE (FIXED)
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_data = request.data.decode("utf-8")
        print("Raw webhook received:", raw_data)

        data = json.loads(raw_data)

        action = data.get("action")
        symbol = data.get("symbol")
        usd_amount = data.get("usd_amount")

        if symbol != PRODUCT_ID:
            print("Ignoring symbol:", symbol)
            return "Ignored", 200

        if action == "buy":
            place_market_order("buy", usd_amount)
        elif action == "sell":
            place_market_order("sell")

        return "OK", 200

    except Exception as e:
        print("Webhook error:", str(e))
        return "Error", 200   # IMPORTANT: always return 200 to TradingView

# ─────────────────────────────────────────
# START SERVER (Railway compatible)
# ─────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
