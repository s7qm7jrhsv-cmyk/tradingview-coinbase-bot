import os
import json
import time
import requests
import jwt
import traceback
import base64
import secrets
import threading
from typing import Optional
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

# ─────────────────────────────────────────
# ENV VARIABLES (Railway / GitHub Actions)
# ─────────────────────────────────────────
COINBASE_API_KEY_NAME = os.environ.get("COINBASE_API_KEY_NAME")  # organizations/{org_id}/apiKeys/{key_id}
COINBASE_PRIVATE_KEY = os.environ.get("COINBASE_PRIVATE_KEY")    # PEM as single line with \n
# Optional base64 alternative
COINBASE_PRIVATE_KEY_B64 = os.environ.get("COINBASE_PRIVATE_KEY_B64")

COINBASE_API_URL = "https://api.coinbase.com"
PRODUCT_ID = "BTC-USDC"
DEFAULT_USD_AMOUNT = 50

# Telegram bot config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_PARSE_MODE = os.environ.get("TELEGRAM_PARSE_MODE", "HTML")

# Daily PnL scheduler config
DAILY_PNL_ENABLED = os.environ.get("DAILY_PNL_ENABLED", "0") == "1"
DAILY_PNL_HOUR_UTC = int(os.environ.get("DAILY_PNL_HOUR_UTC", "13"))  # e.g., 13:00 UTC (~08:00 ET)

# ─────────────────────────────────────────
# Telegram helper
# ─────────────────────────────────────────
def send_telegram(text: str):
    """Send a Telegram message via Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        print("TELEGRAM status:", resp.status_code, resp.text)
    except Exception as e:
        print("WARN: Telegram send failed:", repr(e))

# ─────────────────────────────────────────
# PEM normalization helpers
# ─────────────────────────────────────────
def normalize_pem(pem: Optional[str]) -> Optional[str]:
    """Normalize PEM coming from env: strip quotes, convert \n to newlines, remove \r."""
    if pem is None:
        return None
    if isinstance(pem, bytes):
        try:
            pem = pem.decode("utf-8")
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
    print("INFO: PEM first line:", lines[0] if lines else "")
    print("INFO: PEM last line:", lines[-1] if lines else "")

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
        "kid": COINBASE_API_KEY_NAME,  # important: kid = key name (same as sub)
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
BEST_BID_ASK_PATH = "/api/v3/brokerage/best_bid_ask"
ORDER_DETAILS_PATH_TMPL = "/api/v3/brokerage/orders/historical/{order_id}"
FILLS_PATH = "/api/v3/brokerage/orders/historical/fills"
TRANSACTION_SUMMARY_PATH = "/api/v3/brokerage/transaction_summary"

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

# Best bid/ask for price calculation (mid price from first bid/ask)
# Docs: GET /api/v3/brokerage/best_bid_ask
def fetch_best_bid_ask(product_id: str):
    headers = auth_headers("GET", BEST_BID_ASK_PATH)
    params = {"product_ids": product_id}
    resp = requests.get(f"{COINBASE_API_URL}{BEST_BID_ASK_PATH}", headers=headers, params=params, timeout=10)
    print("Best bid/ask:", resp.status_code, resp.text)
    try:
        data = resp.json()
        books = data.get("pricebooks", [])
        for b in books:
            if b.get("product_id") == product_id:
                bids = b.get("bids", [])
                asks = b.get("asks", [])
                bid_price = float(bids[0]["price"]) if bids else None
                ask_price = float(asks[0]["price"]) if asks else None
                if bid_price and ask_price:
                    return (bid_price + ask_price) / 2.0
                return bid_price or ask_price
        return None
    except Exception:
        return None

def fetch_order_details(order_id: str):
    path = ORDER_DETAILS_PATH_TMPL.format(order_id=order_id)
    headers = auth_headers("GET", path)
    resp = requests.get(f"{COINBASE_API_URL}{path}", headers=headers, timeout=10)
    print("Order details:", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

def fetch_fills(product_id: str = "BTC-USDC", limit: int = 50):
    headers = auth_headers("GET", FILLS_PATH)
    params = {"product_id": product_id, "limit": str(limit)}
    resp = requests.get(f"{COINBASE_API_URL}{FILLS_PATH}", headers=headers, params=params, timeout=10)
    print("Order fills:", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

def fetch_transaction_summary():
    headers = auth_headers("GET", TRANSACTION_SUMMARY_PATH)
    resp = requests.get(f"{COINBASE_API_URL}{TRANSACTION_SUMMARY_PATH}", headers=headers, timeout=10)
    print("Transaction summary:", resp.status_code, resp.text)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}

# ─────────────────────────────────────────
# Utility formatting
# ─────────────────────────────────────────
def fmt_usd(x: float) -> str:
    return f"${x:,.2f}"

def fmt_btc(x: float) -> str:
    return f"{x:.8f} BTC"

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
            send_telegram("<b>Error:</b> Body not valid JSON and no BUY/SELL keyword found.")
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
        send_telegram(f"<b>Error:</b> Env validation failed: {str(e)}")
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

            if status < 300:
                # Telegram: Buy executed
                send_telegram(f"✅ <b>Buy executed</b> — {PRODUCT_ID} • Amount: {fmt_usd(usd_amount)}")
                # Optional: confirm exact BTC filled
                try:
                    order_id = resp.get("success_response", {}).get("order_id") if isinstance(resp, dict) else None
                    if order_id:
                        d_status, d_json = fetch_order_details(order_id)
                        print("DEBUG: order_id =", order_id, "details status =", d_status)
                        f_status, f_json = fetch_fills(product_id=PRODUCT_ID, limit=10)
                        print("DEBUG: fills status =", f_status)
                except Exception as _e:
                    print("WARN: Unable to fetch order details/fills:", repr(_e))
            else:
                send_telegram(f"❌ <b>Error alert</b> — Buy failed: {resp}")
                return jsonify(error="Coinbase order failed", details=resp), 400

        else:  # SELL
            status_accounts, data_accounts = fetch_accounts()
            if status_accounts >= 300:
                send_telegram(f"❌ <b>Error alert</b> — Fetch accounts failed: {data_accounts}")
                return jsonify(error="Failed to fetch accounts", details=data_accounts), 400

            # derive base_size from accounts JSON
            base_size = None
            for acct in data_accounts.get("accounts", []):
                if acct.get("currency") == "BTC":
                    ab = acct.get("available_balance") or {}
                    val = ab.get("value")
                    if val and float(val) > 0:
                        base_size = str(val)
                        break
            if not base_size:
                send_telegram("❌ <b>Error alert</b> — No BTC available to sell")
                return jsonify(error="No BTC available to sell"), 400

            status, resp = place_market_order("sell", base_size=base_size)

            if status < 300:
                # Estimate USD amount using best bid/ask
                price = fetch_best_bid_ask(PRODUCT_ID) or 0.0
                usd_estimate = float(base_size) * float(price)
                send_telegram(
                    f"✅ <b>Sell executed</b> — {PRODUCT_ID} • Qty: {fmt_btc(float(base_size))} • Est: {fmt_usd(usd_estimate)}"
                )
            else:
                send_telegram(f"❌ <b>Error alert</b> — Sell failed: {resp}")
                return jsonify(error="Coinbase order failed", details=resp), 400

        return jsonify(status="order placed", action=action, details=resp), 200

    except Exception as e:
        print("ERROR:", repr(e))
        print("TRACEBACK:", traceback.format_exc())
        send_telegram(f"❌ <b>Error alert</b> — Unhandled: {str(e)}")
        return jsonify(error="Unhandled exception", details=str(e)), 500

# ─────────────────────────────────────────
# Health
# ─────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# Optional: order details/fills HTTP endpoints
@app.route("/order/<order_id>", methods=["GET"])
def order_details_http(order_id):
    try:
        require_env()
        status, data = fetch_order_details(order_id)
        return jsonify({"status": status, "data": data}), 200
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/fills", methods=["GET"])
def fills_http():
    try:
        require_env()
        status, data = fetch_fills(product_id=PRODUCT_ID, limit=10)
        return jsonify({"status": status, "data": data}), 200
    except Exception as e:
        return jsonify(error=str(e)), 500

# ─────────────────────────────────────────
# Daily PnL scheduler (once per day at DAILY_PNL_HOUR_UTC)
# Computes naive PnL from recent fills (last 24h): sum(SELL) - sum(BUY) - fees
# Also includes fee tier / total fees from transaction_summary when available.
# ─────────────────────────────────────────

def compute_daily_pnl_and_notify():
    try:
        require_env()
        status, fills = fetch_fills(product_id=PRODUCT_ID, limit=100)
        buys_usd, sells_usd, fees_usd = 0.0, 0.0, 0.0
        count = 0
        now = time.time()
        cutoff = now - 24 * 3600

        if isinstance(fills, dict):
            items = fills.get("fills", fills.get("orders", []))
            for it in items or []:
                t = it.get("trade_time") or it.get("time") or it.get("created_time")
                ts_ok = True
                if isinstance(t, str):
                    try:
                        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                        ts_ok = (dt.timestamp() >= cutoff)
                    except Exception:
                        ts_ok = True
                side = (it.get("side") or it.get("order_side") or "").upper()
                price = float(it.get("price") or 0.0)
                size = float(it.get("size") or it.get("base_size") or 0.0)
                fee = float(it.get("fee") or 0.0)
                if ts_ok and size > 0:
                    usd = price * size if price > 0 else 0.0
                    if side == "BUY":
                        buys_usd += usd
                    elif side == "SELL":
                        sells_usd += usd
                    fees_usd += fee
                    count += 1

        ts_status, ts_json = fetch_transaction_summary()
        tier = None
        total_fees_all = None
        if ts_status < 300 and isinstance(ts_json, dict):
            tier = (ts_json.get("fee_tier") or {}).get("pricing_tier")
            total_fees_all = ts_json.get("total_fees")

        pnl = sells_usd - buys_usd - fees_usd
        msg = (
            f"📊 <b>Daily PnL</b> — {PRODUCT_ID}\n"
            f"Buys: {fmt_usd(buys_usd)} | Sells: {fmt_usd(sells_usd)} | Fees: {fmt_usd(fees_usd)}\n"
            f"Net PnL 24h: <b>{fmt_usd(pnl)}</b>\n"
            f"Trades counted: {count}"
        )
        if tier:
            msg += f"\nFee tier: {tier}"
        if total_fees_all is not None:
            try:
                msg += f"\nTotal fees (all-time): {fmt_usd(float(total_fees_all))}"
            except Exception:
                msg += f"\nTotal fees (all-time): {total_fees_all}"
        send_telegram(msg)
    except Exception as e:
        print("WARN: Daily PnL failed:", repr(e))
        send_telegram(f"❌ <b>Error alert</b> — Daily PnL failed: {str(e)}")

def start_daily_pnl_thread():
    if not DAILY_PNL_ENABLED:
        return
    def worker():
        while True:
            try:
                now = datetime.utcnow()
                next_run = now.replace(hour=DAILY_PNL_HOUR_UTC, minute=0, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                sleep_seconds = (next_run - now).total_seconds()
                print(f"Daily PnL scheduled in {int(sleep_seconds)}s for {next_run.isoformat()} UTC")
                time.sleep(max(5, sleep_seconds))
                compute_daily_pnl_and_notify()
            except Exception as e:
                print("Daily PnL thread error:", repr(e))
                time.sleep(60)
    threading.Thread(target=worker, daemon=True).start()

# Start scheduler
start_daily_pnl_thread()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
