
import os
import json
import time
import requests
import jwt
from typing import Optional
from flask import Flask, request, jsonify

app = Flask(__name__)

# ─────────────────────────────────────────
# ENV VARIABLES (Railway)
# ─────────────────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")  # PEM for ES256
COINBASE_API_URL = "https://api.coinbase.com"
PRODUCT_ID = "BTC-USDC"
DEFAULT_USD_AMOUNT = 50  # used only if buy amount isn't provided

# ─────────────────────────────────────────
# UTIL: Validate env vars per request
# ─────────────────────────────────────────
def require_env():
    missing = []
    if not COINBASE_API_KEY:
        missing.append("COINBASE_API_KEY")
    if not COINBASE_PRIVATE_KEY:
        missing.append("COINBASE_PRIVATE_KEY")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

# ─────────────────────────────────────────
# CREATE JWT (Coinbase Advanced Trade)
# ─────────────────────────────────────────
def create_jwt() -> str:
    now = int(time.time())
    payload = {
        "sub": COINBASE_API_KEY,
        "iss": "coinbase-cloud",
        "nbf": now,
        "exp": now + 120,  # 2 minutes
        "aud": ["coinbase-cloud"],
    }
    headers = {
        "kid": COINBASE_API_KEY,
        "nonce": str(int(time.time() * 1000)),
    }
    return jwt.encode(payload, COINBASE_PRIVATE_KEY, algorithm="ES256", headers=headers)

def auth_headers() -> dict:
    token = create_jwt()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

# ─────────────────────────────────────────
# FETCH ACCOUNT BALANCES (sell-all helper)
# ─────────────────────────────────────────
def fetch_accounts():
    headers = auth_headers()
    resp = requests.get(
        f"{COINBASE_API_URL}/api/v3/brokerage/accounts",
        headers=headers,
        timeout=10,
    )
    return resp.status_code, resp.json()

def get_available_btc() -> Optional[str]:
    status, data = fetch_accounts()
    if status >= 300:
        raise RuntimeError(f"Failed to fetch accounts: {data}")
    # Find BTC account and return available balance as string
    for acct in data.get("accounts", []):
        if acct.get("currency") == "BTC":
            # some payloads expose "available_balance": {"value":"...", "currency":"BTC"}
            ab = acct.get("available_balance") or {}
            val = ab.get("value")
            if val and float(val) > 0:
                # Coinbase expects base_size as string
                return val
            return None
    return None

# ─────────────────────────────────────────
# PLACE MARKET ORDER
# ─────────────────────────────────────────
def place_market_order(side: str, usd_amount: Optional[float] = None, base_size: Optional[str] = None):
    headers = auth_headers()

    order = {
        "client_order_id": str(int(time.time() * 1000)),
        "product_id": PRODUCT_ID,
        "side": side.upper(),
        "order_configuration": {
            "market_market_ioc": {}
        },
    }

    if side.lower() == "buy":
        # Buy by quote (USDC)
        order["order_configuration"]["market_market_ioc"]["quote_size"] = str(usd_amount or DEFAULT_USD_AMOUNT)
    else:
        # Sell by base size (BTC)
        if not base_size:
            # optionally allow small epsilon to avoid dust; here we require a base_size
            raise ValueError("SELL requires base_size (BTC amount).")
        order["order_configuration"]["market_market_ioc"]["base_size"] = str(base_size)

    response = requests.post(
        f"{COINBASE_API_URL}/api/v3/brokerage/orders",
        headers=headers,
        json=order,
        timeout=10,
    )
    print("Coinbase response:", response.status_code, response.text)
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {"raw": response.text}

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

    # Validate env per request
    try:
        require_env()
    except RuntimeError as e:
        return jsonify(error=str(e)), 500

    action = (data.get("action") or "").lower()
    symbol = data.get("symbol")

    if action not in {"buy", "sell"}:
        return jsonify(error="Invalid or missing action"), 400

    # Symbol guard (must match our configured product)
    if symbol != PRODUCT_ID:
        return jsonify(error=f"Unsupported symbol '{symbol}', expected '{PRODUCT_ID}'"), 400

    try:
        if action == "buy":
            # Railway controls the amount; use DEFAULT if none provided
            usd_amount = data.get("usd_amount")
            usd_amount = float(usd_amount) if usd_amount is not None else DEFAULT_USD_AMOUNT
            status, resp = place_market_order("buy", usd_amount=usd_amount)
        else:
            # SELL: market sell ALL available BTC for PRODUCT_ID
            base_size = get_available_btc()
            if not base_size:
                return jsonify(error="No BTC available to sell"), 400
            status, resp = place_market_order("sell", base_size=base_size)

        if status >= 300:
            return jsonify(error="Coinbase order failed", details=resp), 400

        return jsonify(status="order placed", action=action, details=resp), 200

    except Exception as e:
        print("ERROR:", repr(e))
        return jsonify(error="Unhandled exception", details=str(e)), 500

# ─────────────────────────────────────────
# HEALTH CHECK (OPTIONAL)
# ─────────────────────────────────────────# ─────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# ─────────────────────────────────────────
# START SERVER
# ─────────────────────────────────────────
if __name__ == "__main__":
