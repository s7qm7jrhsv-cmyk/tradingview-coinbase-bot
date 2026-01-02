import os
import json
import time
import requests
import jwt
import traceback
import base64
import secrets
from typing import Optional
from flask import Flask, request, jsonify

app = Flask(__name__)

# This one works with Telegram notifications!

# ─────────────────────────────────────────
# ENV VARIABLES (Railway / GitHub Actions)
# ─────────────────────────────────────────
# Coinbase CDP key metadata
COINBASE_API_KEY_ID = os.environ.get("COINBASE_API_KEY_ID")
COINBASE_API_KEY_NAME = os.environ.get("COINBASE_API_KEY_NAME")

# Private key (EC/ES256) in PEM or Base64
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")
COINBASE_PRIVATE_KEY_B64 = os.environ.get("COINBASE_PRIVATE_KEY_B64")

# ═════════════════════════════════════════
# TELEGRAM CONFIGURATION
# ═════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # Your bot token from BotFather
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")      # Your chat ID

COINBASE_API_URL = "https://api.coinbase.com"
PRODUCT_ID = "BTC-USDC"
DEFAULT_USD_AMOUNT = 250

# ─────────────────────────────────────────
# TELEGRAM NOTIFICATION HELPER
# ─────────────────────────────────────────
def send_telegram_message(message: str):
    """
    Send a message to Telegram.
    This is where ALL notifications are sent from.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: Telegram not configured. Skipping notification:", message)
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"  # Allows bold, italic formatting
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"✅ Telegram notification sent: {message[:50]}...")
        else:
            print(f"❌ Telegram API error: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {repr(e)}")

# ─────────────────────────────────────────
# PEM normalization helpers
# ─────────────────────────────────────────
def normalize_pem(pem: Optional[str]) -> Optional[str]:
    if pem is None:
        return None
    if isinstance(pem, bytes):
        try:
            pem = pem.decode('utf-8')
        except Exception:
            return None
    v = pem.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    v = v.replace("\\r", "").replace("\r", "").replace("\\n", "\n")
    return v

COINBASE_PRIVATE_KEY = normalize_pem(COINBASE_PRIVATE_KEY)

if not COINBASE_PRIVATE_KEY and COINBASE_PRIVATE_KEY_B64:
    try:
        decoded = base64.b64decode(COINBASE_PRIVATE_KEY_B64)
        COINBASE_PRIVATE_KEY = normalize_pem(decoded)
        print("INFO: Loaded COINBASE_PRIVATE_KEY from base64 env")
    except Exception as e:
        print("ERROR: Failed to decode COINBASE_PRIVATE_KEY_B64:", repr(e))

if COINBASE_PRIVATE_KEY:
    lines = COINBASE_PRIVATE_KEY.splitlines()
    first_line = lines[0] if lines else ''
    last_line = lines[-1] if lines else ''
    print("INFO: PEM first line:", first_line)
    print("INFO: PEM last line:", last_line)

# ─────────────────────────────────────────
# Require env vars per request
# ─────────────────────────────────────────
def require_env():
    missing = []
    if not COINBASE_API_KEY_NAME:
        missing.append("COINBASE_API_KEY_NAME")
    if not COINBASE_PRIVATE_KEY:
        missing.append("COINBASE_PRIVATE_KEY")
    if missing:
        # ═════════════════════════════════════════
        # NOTIFICATION #4: Connection/Configuration Error
        # ═════════════════════════════════════════
        error_msg = f"⚠️ <b>Railway Configuration Error</b>\n\nMissing: {', '.join(missing)}"
        send_telegram_message(error_msg)
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

# ─────────────────────────────────────────
# JWT creation for Coinbase Advanced Trade REST (ES256)
# ─────────────────────────────────────────
DEF_HOST = "api.coinbase.com"

def build_uri(method: str, path: str, host: str = DEF_HOST) -> str:
    return f"{method.upper()} {host}{path}"

def create_jwt(method: str, path: str) -> str:
    now = int(time.time())
    payload = {
        "iss": "cdp",
        "sub": COINBASE_API_KEY_NAME,
        "nbf": now,
        "exp": now + 120,
        "uri": build_uri(method, path, DEF_HOST),
    }
    headers = {
        "kid": COINBASE_API_KEY_NAME,
        "nonce": secrets.token_hex(),
    }
    try:
        return jwt.encode(payload, COINBASE_PRIVATE_KEY, algorithm="ES256", headers=headers)
    except Exception as e:
        print("ERROR: JWT encode failed:", repr(e))
        print("TRACEBACK:", traceback.format_exc())
        raise

def auth_headers(method: str, path: str) -> dict:
    token = create_jwt(method, path)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

# ─────────────────────────────────────────
# REST helpers
# ─────────────────────────────────────────
ACCOUNTS_PATH = "/api/v3/brokerage/accounts"
ORDERS_PATH = "/api/v3/brokerage/orders"

def fetch_accounts():
    headers = auth_headers("GET", ACCOUNTS_PATH)
    resp = requests.get(f"{COINBASE_API_URL}{ACCOUNTS_PATH}", headers=headers, timeout=10)
    print("Coinbase response (accounts):", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

def place_market_order(side: str, usd_amount: Optional[float] = None, base_size: Optional[str] = None):
    headers = auth_headers("POST", ORDERS_PATH)
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

    resp = requests.post(f"{COINBASE_API_URL}{ORDERS_PATH}", headers=headers, json=order, timeout=10)
    print("Coinbase response (order):", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

# ─────────────────────────────────────────
# Webhook endpoint
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.data.decode("utf-8", errors="ignore")
    print("RAW WEBHOOK BODY:", raw_body)

    # Parse JSON or fallback plain text
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
            
            # Execute BUY order
            status, resp = place_market_order("buy", usd_amount=usd_amount)
            
            if status >= 300:
                # ═════════════════════════════════════════
                # NOTIFICATION #3: Order Failed
                # ═════════════════════════════════════════
                error_details = resp.get("error_response", {}).get("message", "Unknown error")
                message = (
                    f"❌ <b>BUY Order FAILED</b>\n\n"
                    f"Symbol: {symbol}\n"
                    f"Amount: ${usd_amount}\n"
                    f"Error: {error_details}"
                )
                send_telegram_message(message)
                return jsonify(error="Coinbase order failed", details=resp), 400
            
            # ═════════════════════════════════════════
            # NOTIFICATION #1: Order Successfully Opened
            # ═════════════════════════════════════════
            order_id = resp.get("success_response", {}).get("order_id", "N/A")
            message = (
                f"✅ <b>BUY Order Opened</b>\n\n"
                f"Symbol: {symbol}\n"
                f"Amount: ${usd_amount}\n"
                f"Order ID: {order_id}\n"
                f"Status: Success"
            )
            send_telegram_message(message)
            
            return jsonify(status="order placed", action=action, details=resp), 200
            
        else:  # SELL
            # Fetch account balance
            status_accounts, data_accounts = fetch_accounts()
            if status_accounts >= 300:
                # ═════════════════════════════════════════
                # NOTIFICATION #3: Order Failed (account fetch)
                # ═════════════════════════════════════════
                message = (
                    f"❌ <b>SELL Order FAILED</b>\n\n"
                    f"Symbol: {symbol}\n"
                    f"Error: Failed to fetch account balance"
                )
                send_telegram_message(message)
                return jsonify(error="Failed to fetch accounts", details=data_accounts), 400

            # Derive base_size from accounts JSON
            base_size = None
            for acct in data_accounts.get("accounts", []):
                if acct.get("currency") == "BTC":
                    ab = acct.get("available_balance") or {}
                    val = ab.get("value")
                    if val and float(val) > 0:
                        base_size = str(val)
                        break
            
            if not base_size:
                # ═════════════════════════════════════════
                # NOTIFICATION #3: Order Failed (no balance)
                # ═════════════════════════════════════════
                message = (
                    f"❌ <b>SELL Order FAILED</b>\n\n"
                    f"Symbol: {symbol}\n"
                    f"Error: No BTC available to sell"
                )
                send_telegram_message(message)
                return jsonify(error="No BTC available to sell"), 400
            
            # Execute SELL order
            status, resp = place_market_order("sell", base_size=base_size)
            
            if status >= 300:
                # ═════════════════════════════════════════
                # NOTIFICATION #3: Order Failed
                # ═════════════════════════════════════════
                error_details = resp.get("error_response", {}).get("message", "Unknown error")
                message = (
                    f"❌ <b>SELL Order FAILED</b>\n\n"
                    f"Symbol: {symbol}\n"
                    f"Size: {base_size} BTC\n"
                    f"Error: {error_details}"
                )
                send_telegram_message(message)
                return jsonify(error="Coinbase order failed", details=resp), 400
            
            # ═════════════════════════════════════════
            # NOTIFICATION #2: Order Successfully Closed
            # ═════════════════════════════════════════
            order_id = resp.get("success_response", {}).get("order_id", "N/A")
            message = (
                f"✅ <b>SELL Order Closed</b>\n\n"
                f"Symbol: {symbol}\n"
                f"Size: {base_size} BTC\n"
                f"Order ID: {order_id}\n"
                f"Status: Success"
            )
            send_telegram_message(message)
            
            return jsonify(status="order placed", action=action, details=resp), 200

    except Exception as e:
        # ═════════════════════════════════════════
        # NOTIFICATION #4: Unhandled Exception
        # ═════════════════════════════════════════
        print("ERROR:", repr(e))
        print("TRACEBACK:", traceback.format_exc())
        
        message = (
            f"⚠️ <b>Railway Error</b>\n\n"
            f"Action: {action.upper()}\n"
            f"Symbol: {symbol}\n"
            f"Error: {str(e)[:200]}"
        )
        send_telegram_message(message)
        
        return jsonify(error="Unhandled exception", details=str(e)), 500

# ─────────────────────────────────────────
# Health check endpoint
# ─────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# Send startup notification
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    send_telegram_message("🚀 <b>Railway Trading Bot Started</b>\n\nBot is online and ready to receive signals.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
