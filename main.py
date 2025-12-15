from flask import Flask, request, jsonify
import os
import requests
import time
import hmac
import hashlib

app = Flask(__name__)

COINBASE_API_KEY = os.environ["COINBASE_API_KEY"]
COINBASE_API_SECRET = os.environ["COINBASE_API_SECRET"]
COINBASE_API_PASSPHRASE = os.environ["COINBASE_API_PASSPHRASE"]

BASE_URL = "https://api.exchange.coinbase.com"

def coinbase_headers(method, request_path, body=""):
    timestamp = str(time.time())
    message = timestamp + method + request_path + body
    signature = hmac.new(
        COINBASE_API_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()

    return {
        "CB-ACCESS-KEY": COINBASE_API_KEY,
        "CB-ACCESS-SIGN": signature.hex(),
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": COINBASE_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if data.get("action") != "buy":
        return jsonify({"status": "ignored"})

    order = {
        "type": "market",
        "side": "buy",
        "product_id": "BTC-USD",
        "funds": "50"
    }

    body = str(order).replace("'", '"')
    headers = coinbase_headers("POST", "/orders", body)

    response = requests.post(
        BASE_URL + "/orders",
        headers=headers,
        data=body
    )

    return jsonify(response.json())

if __name__ == "__main__":
    app.run()
