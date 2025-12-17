from flask import Flask, request, jsonify
import os
import requests
import time
import hmac
import hashlib
from datetime import datetime, date

app = Flask(__name__)

# ───────────────────────────────
# ENV VARIABLES
# ───────────────────────────────
COINBASE_API_KEY = os.environ["COINBASE_API_KEY"]
COINBASE_API_SECRET = os.environ["COINBASE_API_SECRET"]
COINBASE_API_PASSPHRASE = os.environ["COINBASE_API_PASSPHRASE"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://api.exchange.coinbase.com"

daily_pnl = 0.0
last_pnl_date = date.today()

# ───────────────────────────────
# TELEGRAM HELPER
# ───────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    requests.post(url, json=payload)

# ───────────────────────────────
# COINBASE AUTH
# ───────────────────────────────
import base64

def coinbase_headers(method, request_path, body=""):
    timestamp = str(time.time())
    message = timestamp + method + request_path + body

    secret_decoded = base64.b64decode(COINBASE_API_SECRET)

    signature = hmac.new(
        secret_decoded,
        message.encode(),
        hashlib.sha256
    ).digest()

    return {
        "CB-ACCESS-KEY": COINBASE_API_KEY,
        "CB-ACCESS-SIGN": base64.b64encode(signature).decode(),
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": COINBASE_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

# ───────────────────────────────
# WEBHOOK ENDPOINT
# ───────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    global daily_pnl, last_pnl_date

    data = request.json
    print("Webhook received:", data)

    # Daily PnL reset & report
    if date.today() != last_pnl_date:
        send_telegram(f"📊 Daily PnL Summary: ${daily_pnl:.2f}")
        daily_pnl = 0.0
        last_pnl_date = date.today()

    action = data.get("action")

    try:
        if action == "buy":
            usd_amount = data.get("amount_usd", 50)

            order = {
                "type": "market",
                "side": "buy",
                "product_id": "BTC-USDC",
                "funds": str(usd_amount)
            }

            body = str(order).replace("'", '"')
            headers = coinbase_headers("POST", "/orders", body)
            response = requests.post(BASE_URL + "/orders", headers=headers, data=body)

            result = response.json()

            send_telegram(
                f"🟢 BUY EXECUTED\n"
                f"Asset: BTC-USDC\n"
                f"Amount: ${usd_amount}"
            )

            return jsonify(result)

        elif action == "sell":
            size = data.get("size")

            order = {
                "type": "market",
                "side": "sell",
                "product_id": "BTC-USDC",
                "size": str(size)
            }

            body = str(order).replace("'", '"')
            headers = coinbase_headers("POST", "/orders", body)
            response = requests.post(BASE_URL + "/orders", headers=headers, data=body)

            send_telegram(
                f"🔴 SELL EXECUTED\n"
                f"Asset: BTC-USDC\n"
                f"Size: {size} BTC"
            )

            return jsonify(response.json())

        return jsonify({"status": "ignored"})

    except Exception as e:
        send_telegram(f"🚨 ERROR ALERT\n{str(e)}")
        return jsonify({"error": str(e)}), 500

# ───────────────────────────────
# HEALTH CHECK
# ───────────────────────────────
@app.route("/")
def health():
    return "Bot is running", 200

if __name__ == "__main__":
    app.run()
