import sqlite3
import httpx
import pytz
import json
import os
import threading
import telebot
from telebot.types import BotCommand
import re
import pandas as pd
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import Response
from pydantic import BaseModel
from contextlib import asynccontextmanager

EXCHANGE_TZ = pytz.timezone('America/New_York')
CONFIG_FILE = "config.json"
MULTIPLIERS = {"MNQ": 2.0, "MES": 5.0, "MYM": 0.5, "M2K": 5.0, "MGC": 10.0, "SIL": 1000.0}

def get_est_time(): return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ)
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {}

# --- OPTIONAL TELEGRAM CONFIGURATION ---
config = load_config()
SECRET_PASSPHRASE = config.get("credentials", {}).get("secret_passphrase", "")
TELEGRAM_BOT_TOKEN = config.get("credentials", {}).get("telegram_bot_token", "")
TELEGRAM_USER_ID = config.get("credentials", {}).get("telegram_chat_id", "")

# Only initialize if the token actually exists and isn't the template placeholder
if TELEGRAM_BOT_TOKEN and "REPLACE_" not in TELEGRAM_BOT_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
else:
    bot = None

def send_telegram(msg):
    """Sends to Telegram if configured, otherwise falls back to VPS console."""
    if bot:
        try: 
            bot.send_message(TELEGRAM_USER_ID, msg, parse_mode="HTML")
        except: 
            pass # Failsafe if Telegram servers drop
    else:
        # Strip HTML tags for a clean local console log
        clean_msg = re.sub(r'<[^<]+>', '', msg)
        print(f"[{get_est_time().strftime('%H:%M:%S EST')}] LOCAL LOG: {clean_msg}")

def clean_symbol(raw: str):
    match = re.match(r"^([A-Za-z]+)", raw)
    return match.group(1).upper() if match else raw.upper()

async def async_send_ghost_webhook(url: str, payload: dict):
    async with httpx.AsyncClient(http2=True) as client:
        for _ in range(3):
            try:
                if (await client.post(url, json=payload, timeout=5.0)).status_code == 200: return True
            except: pass
            await asyncio.sleep(0.5)
        return False

# --- MARKET REGIME & RECONCILIATION LOOP ---
async def market_schedule_loop():
    flattened_today = False
    reset_today = False
    reconciled_this_week = False
    
    while True:
        now = get_est_time()
        current_time = now.strftime('%H:%M')
        if current_time == "00:00": flattened_today, reset_today = False, False
        if now.weekday() == 0: reconciled_this_week = False # Reset weekly flag on Monday

        # 1. 4:44 PM AUTO-FLATTEN
        if current_time == "16:44" and not flattened_today:
            conn = sqlite3.connect("trades.db", timeout=10)
            c = conn.cursor()
            c.execute("SELECT symbol, qty FROM open_positions")
            for sym, qty in c.fetchall():
                if sym in config.get("ghost_urls", {}):
                    await async_send_ghost_webhook(config["ghost_urls"][sym], {"action": "exit", "symbol": sym, "qty": qty})
            c.execute("DELETE FROM open_positions")
            conn.commit(); conn.close()
            send_telegram("🚨 <b>LUCID COMPLIANCE</b>: 4:44 PM Auto-Flatten executed.")
            flattened_today = True

        # 2. 5:00 PM DAILY RESET
        if current_time == "17:00" and not reset_today:
            conn = sqlite3.connect("trades.db", timeout=10)
            c = conn.cursor()
            c.execute(f"DELETE FROM webhooks WHERE timestamp < '{(now - timedelta(days=7)).strftime('%Y-%m-%d')}'")
            next_day = (now + timedelta(days=1)).strftime('%Y-%m-%d')
            c.execute("INSERT OR IGNORE INTO daily_risk (date, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, 0.0, 0.0)", (next_day,))
            conn.commit(); conn.close()
            reset_today = True
            
        # 3. FRIDAY 5:05 PM WEEKLY RECONCILIATION REMINDER
        if now.weekday() == 4 and current_time == "17:05" and not reconciled_this_week:
            msg = "📋 <b>WEEKLY RECONCILIATION REQUIRED</b>\n\n1. Download your CSV from the Lucid Dashboard.\n2. Compare Net PnL with Robosh Shadow PnL.\n3. If Robosh is over-reporting, increase `mumbai_slippage_ticks` in your config for next week."
            send_telegram(msg)
            reconciled_this_week = True

        await asyncio.sleep(60)

# --- EXECUTION ENGINE ---
async def execute_trade_logic(signal_dict: dict):
    symbol, action, price = signal_dict['symbol'], signal_dict['action'].lower(), signal_dict['price']
    adx, atr = signal_dict.get('adx', 25.0), signal_dict.get('atr', 1.0) # Default to pass if TV doesn't send
    cfg = load_config()
    est_now = get_est_time()
    today, timestamp = est_now.strftime('%Y-%m-%d'), est_now.strftime('%Y-%m-%d %H:%M:%S EST')

    if action == "panic_flatten": return # Handled elsewhere

    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    if action == "ping":
        c.execute("INSERT OR REPLACE INTO system_status (key, value) VALUES ('last_ping', ?)", (timestamp,))
        if price:
            c.execute("SELECT direction, entry_price, qty FROM open_positions WHERE symbol=?", (symbol,))
            pos = c.fetchone()
            if pos:
                floating_pnl = ((price - pos[1]) if pos[0] == 'long' else (pos[1] - price)) * MULTIPLIERS.get(symbol, 1.0) * pos[2]
                c.execute("UPDATE open_positions SET current_price=?, floating_pnl=? WHERE symbol=?", (price, floating_pnl, symbol))
        conn.commit(); conn.close(); return

    # Safety Checks
    risk, feat = cfg.get("risk", {}), cfg.get("features", {})
    if risk.get("hard_kill", False): conn.close(); return 
    
    is_entry = action in ['long', 'short', 'buy', 'sell']
    if is_entry:
        if risk.get("soft_fade", False) or not cfg.get("sandbox", {}).get(symbol, True): conn.close(); return 
        
        # Institutional Filters
        if feat.get("choppy_market_filter", False) and adx < 20.0:
            send_telegram(f"🌊 <b>CHOP FILTER BLOCKED:</b> {action.upper()} {symbol} rejected (ADX: {adx:.1f})")
            conn.close(); return

    # Sizing Logic (Defaults to 1 if Dynamic is OFF)
    qty = 1
    if is_entry and feat.get("dynamic_sizing", False) and atr > 0:
        risk_usd = risk.get("risk_per_trade_usd", 50.0)
        # Formula: Quantity = Risk / (ATR * Point Multiplier)
        calc_qty = int(risk_usd / (atr * MULTIPLIERS.get(symbol, 1.0)))
        qty = max(1, calc_qty) # Always trade at least 1 contract

    ghost_url = cfg.get("ghost_urls", {}).get(symbol)
    if ghost_url:
        if await async_send_ghost_webhook(ghost_url, {"action": "exit" if action in ['exit', 'close', 'flat'] else action, "symbol": symbol, "price": price, "qty": qty}):
            if is_entry:
                c.execute("DELETE FROM open_positions WHERE symbol=?", (symbol,))
                c.execute("INSERT INTO open_positions (symbol, direction, entry_price, qty) VALUES (?, ?, ?, ?)", (symbol, action, price, qty))
                send_telegram(f"🟢 <b>ENTRY:</b> {action.upper()} {qty}x {symbol} @ {price}\nSizing Math: ATR={atr:.2f}")
            elif action in ['exit', 'close', 'flat']:
                c.execute("SELECT direction, entry_price, qty FROM open_positions WHERE symbol=?", (symbol,))
                pos = c.fetchone()
                if pos:
                    rt_comm, slip_ticks = cfg.get("costs", {}).get("commission_round_trip", 1.0), cfg.get("costs", {}).get("mumbai_slippage_ticks", 2)
                    gross_pnl = ((price - pos[1]) if pos[0] == 'long' else (pos[1] - price)) * MULTIPLIERS.get(symbol, 1.0) * pos[2]
                    net_pnl = gross_pnl - (rt_comm * pos[2]) - (slip_ticks * (MULTIPLIERS.get(symbol, 1.0) * 0.25) * pos[2])
                    c.execute("UPDATE daily_risk SET realized_pnl = realized_pnl + ?, trade_count = trade_count + 1 WHERE date=?", (net_pnl, today))
                    c.execute("DELETE FROM open_positions WHERE symbol=?", (symbol,))
                    send_telegram(f"🏁 <b>CLOSED {symbol}:</b> Net PnL: ${net_pnl:.2f}")

            c.execute("INSERT INTO webhooks (timestamp, symbol, action, price, status) VALUES (?, ?, ?, ?, ?)", (timestamp, symbol, action, price, "✅ Executed"))
        else:
            send_telegram(f"🚨 <b>DESYNC ALERT:</b> Ghost failed to respond for {symbol} after retries!")
            cfg["sandbox"][symbol] = False
            save_config(cfg)

    conn.commit(); conn.close()

# --- FASTAPI SETUP & MIDDLEWARE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure DB integrity on boot
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS daily_risk (date TEXT PRIMARY KEY, trade_count INTEGER, realized_pnl REAL, highest_pnl REAL)")
    conn.execute("INSERT OR IGNORE INTO daily_risk (date, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, 0.0, 0.0)", (get_est_time().strftime('%Y-%m-%d'),))
    conn.commit(); conn.close()
    
    # 2. Start background market loops
    asyncio.create_task(market_schedule_loop())
    
    # 3. Optional Telegram Polling
    if bot:
        # Assuming you have your Telegram command handlers defined above
        threading.Thread(target=start_telegram_polling, daemon=True).start()
        send_telegram("🟢 <b>SYSTEM BOOT:</b> Engine, DB, and Telegram Monitor Online.")
    else:
        send_telegram("🟢 SYSTEM BOOT: Engine Online. (Telegram Disabled - Logging to Console)")
        
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def enforce_ip_whitelist(request: Request, call_next):
    if request.url.path == "/tv-webhook":
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        allowed_ips = load_config().get("security", {}).get("allowed_tv_ips", [])
        if allowed_ips and client_ip not in allowed_ips: return Response(status_code=403, content="Forbidden")
    return await call_next(request)

# Updated Pydantic Model to accept ADX and ATR
class TradingSignal(BaseModel):
    passphrase: str = None
    action: str
    symbol: str
    price: float = None
    market_position: str = None 
    adx: float = 25.0
    atr: float = 1.0

@app.post("/tv-webhook")
async def tv_webhook(signal: TradingSignal, background_tasks: BackgroundTasks):
    if signal.passphrase != SECRET_PASSPHRASE: raise HTTPException(status_code=401)
    signal.symbol = clean_symbol(signal.symbol)
    if signal.market_position and signal.market_position.lower() == "flat": signal.action = "exit"
    background_tasks.add_task(execute_trade_logic, signal.dict())
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)