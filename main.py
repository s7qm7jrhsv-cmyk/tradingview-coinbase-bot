from flask import Flask, request, jsonify
import os
import requests
import time
import hmac
import hashlib
import base64
import json

app = Flask(__name__)

# ───────────────────────────────
# ENVIRONMENT VARIABLES
# ───────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_API_SECRET = os.environ.get("COINBASE_API_SECRET")
COINBASE_API_PASSPHRASE = os.environ.get("COINBASE_API_PASSPHRASE")

BASE_URL = "https://api.exchange.coinbase.com"

# ───────────────────────────────
# COINBASE SIGNING FUNCTION
# ───────────────────────────────
def coinbase_headers(method, request_path, body=""):
    timestamp = str(time.time())
    message = timestamp + method + request_path + body

    signature = hmac.new(
        base64.b64decode(COINBASE_API_SECRET),
        message.encode("utf-8"),
        hashlib.sha256
    ).digest()

    signature_b64 = base64.b64encode(signature).decode()

    return {
        "CB-ACCESS-KEY": COINBASE_API_KEY,
        "CB-ACCESS-SIGN": signature_b64,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": COINBASE_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

# ───────────────────────────────
# WEBHOOK ENDPOINT
# ───────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    # ✅ LOG INCOMING WEBHOOK
    print("Webhook received:", data, flush=True)

    if not data:
        return "No JSON received", 400

    if data.get("action") != "buy":
        print("Ignored action:", data.get("action"), flush=True)
        return "Ignored", 200

    # ───────────────────────────────
    # ORDER DETAILS
    # ───────────────────────────────
    order = {
        "type": "market",
        "side": "buy",
        "product_id": "BTC-USDC",
        "funds": "50"
    }

    body = json.dumps(order)
    headers = coinbase_headers("POST", "/orders", body)

    response = requests.post(
        BASE_URL + "/orders",
        headers=headers,
        data=body
    )

    # ✅ LOG COINBASE RESPONSE
    print("Coinbase response:", response.text, flush=True)

    return jsonify(response.json()), response.status_code

# ───────────────────────────────
# LOCAL TESTING ONLY
# ───────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
