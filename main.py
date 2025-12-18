import os
import json
import time
import requests
import jwt
import traceback
import base64
from typing import Optional
from flask import Flask, request, jsonify

app = Flask(__name__)

# ─────────────────────────────────────────
# ENV VARIABLES (Railway)
# ─────────────────────────────────────────
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY")
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")  # ES256 PEM or JSON-escaped PEM
COINBASE_PRIVATE_KEY_B64 = os.environ.get("COINBASE_PRIVATE_KEY_B64")  # optional base64 of PEM
COINBASE_API_URL = "https://api.coinbase.com"
PRODUCT_ID = "BTC-USDC"
DEFAULT_USD_AMOUNT = 50  # used only if buy amount isn't provided

# ─────────────────────────────────────────
# UTIL: Normalize PEM from env (handles quotes and escaped newlines)
# ─────────────────────────────────────────
def normalize_pem(pem: Optional[str]) -> Optional[str]:
    if pem is None:
        return None
    # env var may contain bytes; coerce to str
    if isinstance(pem, bytes):
        try:
            pem = pem.decode('utf-8')
        except Exception:
            return None
    v = pem.strip()
    # remove surrounding quotes if present
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    # convert escaped newlines to real newlines
    v = v.replace('\r', '').replace('\\n', '\n')
    return v

# Try to normalize raw PEM first
COINBASE_PRIVATE_KEY = normalize_pem(COINBASE_PRIVATE_KEY)
# Fallback: decode base64 if provided
if not COINBASE_PRIVATE_KEY and COINBASE_PRIVATE_KEY_B64:
    try:
        decoded = base64.b64decode(COINBASE_PRIVATE_KEY_B64)
        COINBASE_PRIVATE_KEY = normalize_pem(decoded)
        print("INFO: Loaded COINBASE_PRIVATE_KEY from base64 env")
    except Exception as e:
        print("ERROR: Failed to decode COINBASE_PRIVATE_KEY_B64:", repr(e))

# Log first line of PEM to help diagnose formatting
if COINBASE_PRIVATE_KEY:
    first_line = COINBASE_PRIVATE_KEY.splitlines()[0] if COINBASE_PRIVATE_KEY.splitlines() else ''
    print("INFO: PEM first line:", first_line)

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
    try:
        return jwt.encode(payload, COINBASE_PRIVATE_KEY, algorithm="ES256", headers=headers)
    except Exception as e:
        print("ERROR: JWT encode failed:", repr(e))
        print("TRACEBACK:", traceback.format_exc())
        raise


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
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


def get_available_btc() -> Optional[str]:
    status, data = fetch_accounts()
    if status >= 300:
        raise RuntimeError(f"Failed to fetch accounts: {data}")

    accounts = data.get("accounts") or []
    for acct in accounts:
        if acct.get("currency") == "BTC":
            ab = acct.get("available_balance") or {}
            val = ab.get("value")
            if val is not None:
                try:
                    if float(val) > 0:
                        return str(val)
                except Exception:
                    pass
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
        order["order_configuration"]["market_market_ioc"]["quote_size"] = str(usd_amount or DEFAULT_USD_AMOUNT)
    else:
        if not base_size:
            raise ValueError("SELL requires base_size (BTC amount).")
        order["order_configuration"]["market_market_ioc"]["base_size"] = str(base_size)

    resp = requests.post(
        f"{COINBASE_API_URL}/api/v3/brokerage/orders",
        headers=headers,
        json=order,
        timeout=10,
    )
    print("Coinbase response:", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

# ─────────────────────────────────────────
# WEBHOOK ENDPOINT (TradingView)
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.data.decode("utf-8", errors="ignore")
    print("RAW WEBHOOK BODY:", raw_body)

    # Try JSON first
    data = None
    try:
        data = json.loads(raw_body)
    except Exception:
        text = raw_body.strip()
        upper = text.upper()
        if upper.startswith("BUY"):
            data = {"action": "buy"}
        elif upper.startswith("SELL"):
            data = {"action": "sell"}
        else:
            return jsonify(error="Body is not valid JSON and no BUY/SELL keyword found"), 400
        tokens = upper.split()
        sym = None
        for tok in tokens:
            if tok in {"BTCUSDC", "BTC-USDC"}:
                sym = tok
                break
        data["symbol"] = sym or PRODUCT_ID

    try:
        require_env()
    except RuntimeError as e:
        print("ERROR: Env validation failed:", str(e))
        return jsonify(error=str(e)), 500

    action = (data.get("action") or "").strip().lower()
    symbol = (data.get("symbol") or "").strip().upper()

    if symbol == "BTCUSDC":
        symbol = "BTC-USDC"

    if action not in {"buy", "sell"}:
        return jsonify(error="Invalid or missing action",
                       hint="Use {'action':'buy'|'sell','symbol':'BTC-USDC'}"), 400

    if symbol != PRODUCT_ID:
        return jsonify(error=f"Unsupported symbol '{symbol}'", expected=PRODUCT_ID), 400

    try:
        if action == "buy":
            usd_amount = data.get("usd_amount")
            try:
                usd_amount = float(usd_amount) if usd_amount is not None else DEFAULT_USD_AMOUNT
            except Exception:
                usd_amount = DEFAULT_USD_AMOUNT
            status, resp = place_market_order("buy", usd_amount=usd_amount)
        else:
            base_size = get_available_btc()
            if not base_size:
                return jsonify(error="No BTC available to sell"), 400
            status, resp = place_market_order("sell", base_size=base_size)

        if status >= 300:
            return jsonify(error="Coinbase order failed", details=resp), 400

        return jsonify(status="order placed", action=action, details=resp), 200

    except Exception as e:
        print("ERROR:", repr(e))
        print("TRACEBACK:", traceback.format_exc())
        return jsonify(error="Unhandled exception", details=str(e)), 500

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
