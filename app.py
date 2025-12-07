import os
import time
import json
import threading
import sqlite3
import datetime
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import pytz

load_dotenv()

# ------------------------ CONFIG ------------------------
API_BASE = "https://api.dhan.co/v2"
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_CLIENT_SECRET = os.getenv("DHAN_CLIENT_SECRET")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "bot_state.db")

MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", 10))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", 0.20))
TRADING_START = os.getenv("TRADING_START", "09:25")
TRADING_END = os.getenv("TRADING_END", "15:00")
TIMEZONE = os.getenv("TRADING_TIMEZONE")  # optional

HEADERS = {
    "accept": "application/json",
    "X-Dhan-Client-Id": DHAN_CLIENT_ID,
    "X-Dhan-Client-Secret": DHAN_CLIENT_SECRET,
}

# ------------------------ UTIL: DB State ------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, date TEXT UNIQUE, trades INTEGER, initial_cap REAL)"
    )
    conn.commit()
    return conn

DB = init_db()

def get_state_for_today():
    today = datetime.date.today().isoformat()
    cur = DB.cursor()
    cur.execute("SELECT trades, initial_cap FROM state WHERE date=?", (today,))
    row = cur.fetchone()
    if row:
        return {"date": today, "trades": row[0], "initial_cap": row[1]}
    # create new state using live equity
    initial = fetch_live_equity()
    cur.execute("INSERT OR IGNORE INTO state(date,trades,initial_cap) VALUES(?,?,?)", (today, 0, initial))
    DB.commit()
    return {"date": today, "trades": 0, "initial_cap": initial}

def update_trades_for_today(n):
    today = datetime.date.today().isoformat()
    cur = DB.cursor()
    cur.execute("UPDATE state SET trades = trades + ? WHERE date=?", (n, today))
    DB.commit()

def set_initial_cap_for_today(value):
    today = datetime.date.today().isoformat()
    cur = DB.cursor()
    cur.execute("UPDATE state SET initial_cap = ? WHERE date=?", (value, today))
    DB.commit()

# ------------------------ MARKET / ACCOUNT ------------------------
def fetch_live_equity():
    try:
        r = requests.get(f"{API_BASE}/user/margins", headers=HEADERS, timeout=6)
        r.raise_for_status()
        data = r.json()
        # The exact field name may vary. Use cashMargin or netAvailableMargin as per response.
        equity = float(data.get("cashMargin") or data.get("netAvailableMargin") or 0)
        return equity
    except Exception as e:
        print("[WARN] fetch_live_equity failed:", e)
        return 0.0

def list_positions():
    try:
        r = requests.get(f"{API_BASE}/positions", headers=HEADERS, timeout=6)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print("[WARN] list_positions failed:", e)
        return []

def place_order(payload):
    try:
        r = requests.post(f"{API_BASE}/orders", headers=HEADERS, json=payload, timeout=6)
        return r.json()
    except Exception as e:
        print("[ERROR] place_order failed:", e)
        return {"status": "error", "error": str(e)}

# ------------------------ RISK RULES ------------------------
def time_in_range(start_hm, end_hm, now=None):
    if now is None:
        now = datetime.datetime.now()
    tz = None
    if TIMEZONE:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.datetime.now(tz)
    start = datetime.datetime.combine(now.date(), datetime.time(int(start_hm.split(":")[0]), int(start_hm.split(":")[1])))
    end = datetime.datetime.combine(now.date(), datetime.time(int(end_hm.split(":")[0]), int(end_hm.split(":")[1])))
    return start.time() <= now.time() < end.time()

def can_trade_now():
    st = get_state_for_today()
    # time window
    now = datetime.datetime.now(pytz.timezone(TIMEZONE)) if TIMEZONE else datetime.datetime.now()
    start = datetime.time(int(TRADING_START.split(":")[0]), int(TRADING_START.split(":")[1]))
    end = datetime.time(int(TRADING_END.split(":")[0]), int(TRADING_END.split(":")[1]))
    if now.time() < start:
        return False, "before_start_time"
    if now.time() >= end:
        return False, "after_end_time"
    # trades per day
    if st["trades"] >= MAX_TRADES_PER_DAY:
        return False, "trades_limit_reached"
    # drawdown
    current = fetch_live_equity()
    if st["initial_cap"] is None:
        set_initial_cap_for_today(current)
        st["initial_cap"] = current
    if current <= st["initial_cap"] * (1 - MAX_DRAWDOWN_PCT):
        return False, "max_drawdown_reached"
    return True, "ok"

# ------------------------ SQUARE-OFF ------------------------
def square_off_all_positions():
    positions = list_positions()
    if not positions:
        return {"status": "no_positions"}
    results = []
    for p in positions:
        # Dhan returns position fields; adjust as per your response
        qty = abs(p.get("quantity", 0))
        if qty == 0:
            continue
        side = "SELL" if p.get("netQty", 0) > 0 else "BUY"
        payload = {
            "exchangeSegment": p.get("exchangeSegment"),
            "securityId": p.get("securityId"),
            "transactionType": side,
            "quantity": qty,
            "orderType": "MARKET",
            "productType": "INTRADAY"
        }
        res = place_order(payload)
        results.append({"pos": p, "order_resp": res})
    return results

# ------------------------ BACKGROUND SCHEDULER ------------------------
app = Flask(__name__)

def daily_tasks_runner():
    """Runs continuously in background. At or after END time squares off positions and locks trading until next day."""
    while True:
        try:
            now = datetime.datetime.now(pytz.timezone(TIMEZONE)) if TIMEZONE else datetime.datetime.now()
            end = datetime.time(int(TRADING_END.split(":")[0]), int(TRADING_END.split(":")[1]))
            # If current time >= end_time and within a 10-minute window, run square_off
            if now.time() >= end and now.time() < (datetime.datetime.combine(now.date(), end) + datetime.timedelta(minutes=10)).time():
                print("[INFO] Running scheduled square-off at", now)
                square_off_all_positions()
                # ensure no trades remaining: we can set trades to limit to block further trades
                # update trades so that further signals decline
                st = get_state_for_today()
                if st["trades"] < MAX_TRADES_PER_DAY:
                    update_trades_for_today(MAX_TRADES_PER_DAY - st["trades"])  # make it reach limit
                time.sleep(60)
            time.sleep(10)
        except Exception as e:
            print("[ERROR] daily_tasks_runner:", e)
            time.sleep(10)

# start background thread
bg_thread = threading.Thread(target=daily_tasks_runner, daemon=True)
bg_thread.start()

# ------------------------ API Endpoints ------------------------
@app.route("/", methods=["GET"])
def home():
    return "Dhan Risk Bot running"

@app.route("/state", methods=["GET"])
def state_api():
    return jsonify(get_state_for_today())

@app.route("/signal", methods=["POST"])
def signal():
    """Receive an order payload (JSON). The endpoint enforces rules, places order if allowed, and increments trade count when order is placed.

    Expected JSON shape: {"order": { ...dhan order payload... }}
    """
    payload = request.get_json() or {}
    allowed, reason = can_trade_now()
    if not allowed:
        # If it's after end time, also run square-off
        if reason == "after_end_time" or reason == "max_drawdown_reached":
            # attempt square-off as safety
            square_off_all_positions()
        return jsonify({"status": "declined", "reason": reason}), 403

    order = payload.get("order")
    if not order:
        return jsonify({"status": "error", "error": "missing order payload"}), 400

    resp = place_order(order)
    # NOTE: In production it's better to listen to order-update websocket to increment only on fill.
    # Here we increment when the API returns success/accepted.
    if resp.get("status") in ("success", "accepted", "ok") or resp.get("statusCode") == 200:
        update_trades_for_today(1)
    else:
        print("[WARN] Order response not success:", resp)

    return jsonify(resp)

# simple health checker
@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    # ensure initial state exists
    get_state_for_today()
    app.run(host='0.0.0.0', port=PORT)
