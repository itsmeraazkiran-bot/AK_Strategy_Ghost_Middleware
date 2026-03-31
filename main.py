import sqlite3
import httpx
import json
import os
import asyncio
import threading
import re
import time
from datetime import datetime
import pytz
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import telebot
from pyngrok import ngrok

EXCHANGE_TZ = pytz.timezone('America/New_York')

# --- GLOBAL CONNECTION POOL ---
# This keeps a permanent, pre-warmed connection open to Ghost to eliminate TLS handshake latency.
http_client = None

def get_est_time():
    return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ).strftime('%Y-%m-%d %H:%M:%S')

def clean_symbol(raw: str) -> str:
    match = re.match(r"^([A-Za-z]+)", raw)
    return match.group(1).upper() if match else raw.upper()

def load_config():
    try:
        with open("config.json", "r") as f: return json.load(f)
    except: return {}

config = load_config()
TELEGRAM_TOKEN = config.get("credentials", {}).get("telegram_bot_token", "")
TELEGRAM_CHAT_ID = config.get("credentials", {}).get("telegram_chat_id", "")
NGROK_AUTH_TOKEN = config.get("credentials", {}).get("ngrok_auth_token", "")

if TELEGRAM_TOKEN and "REPLACE_" not in TELEGRAM_TOKEN: 
    bot = telebot.TeleBot(TELEGRAM_TOKEN)
else: 
    bot = None

# --- BACKGROUND TELEGRAM LOGGING ---
def send_telegram_bg(msg: str):
    """Fires in the background so trading execution never waits for Telegram."""
    if bot and TELEGRAM_CHAT_ID:
        try: bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="HTML")
        except: pass

def log_msg(msg: str, to_tg: bool = True):
    timestamp = get_est_time()
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("INSERT INTO logs (timestamp, message) VALUES (?, ?)", (timestamp, msg))
    conn.commit()
    conn.close()
    
    print(f"[{timestamp}] {msg}")
    
    if to_tg: 
        threading.Thread(target=send_telegram_bg, args=(f"ℹ️ {msg}",), daemon=True).start()

def init_db():
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, message TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, direction TEXT, entry_price REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS closed_trades (timestamp TEXT, symbol TEXT, direction TEXT, close_price REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS webhook_audits (timestamp TEXT, symbol TEXT, action TEXT, tv_inbound TEXT, ghost_outbound TEXT, ghost_response TEXT)")
    conn.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('status', 'RUNNING')")
    conn.commit()
    conn.close()

# --- TELEGRAM LISTENER ---
if bot:
    @bot.message_handler(commands=['status', 'positions', 'closed'])
    def handle_commands(message):
        if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
        cmd = message.text.replace('/', '')
        conn = sqlite3.connect("trades.db", timeout=10)
        
        if cmd == 'status':
            status = conn.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0]
            bot.reply_to(message, f"🖥️ Engine Status: <b>{status}</b>", parse_mode="HTML")
        elif cmd == 'positions':
            rows = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
            bot.reply_to(message, "🎯 <b>Open Positions:</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else "No active positions.", parse_mode="HTML")
        elif cmd == 'closed':
            today = get_est_time().split(" ")[0]
            rows = conn.execute("SELECT symbol, direction, close_price FROM closed_trades WHERE timestamp LIKE ?", (f"{today}%",)).fetchall()
            bot.reply_to(message, f"🏁 <b>Closed Trades ({today}):</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else "No closed trades today.", parse_mode="HTML")
        conn.close()

    def start_telegram_polling():
        try:
            bot.set_my_commands([
                telebot.types.BotCommand("status", "Check status"), 
                telebot.types.BotCommand("positions", "List open trades"), 
                telebot.types.BotCommand("closed", "Today's trades")
            ])
        except Exception as e:
            print(f"⚠️ Telegram setup warning: {e}")

        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=15)
            except Exception as e:
                print(f"⚠️ Telegram Connection Dropped: {e}. Reconnecting in 10s...")
                time.sleep(10)

async def market_close_report_loop():
    reported_today = False
    while True:
        now_str = get_est_time()
        today, current_time = now_str.split(" ")[0], now_str.split(" ")[1][:5]
        if current_time == "00:00": reported_today = False
        if current_time == "17:00" and not reported_today:
            conn = sqlite3.connect("trades.db", timeout=10)
            rows = conn.execute("SELECT symbol, direction, close_price FROM closed_trades WHERE timestamp LIKE ?", (f"{today}%",)).fetchall()
            conn.close()
            send_telegram_bg(f"📊 <b>EOD Market Report ({today}):</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else f"📊 <b>EOD Market Report ({today}):</b>\nNo trades executed today.")
            reported_today = True
        await asyncio.sleep(60)

async def send_to_ghost(symbol: str, action: str, price: float, qty: float):
    global http_client
    url = config.get("ghost_urls", {}).get(symbol)
    payload = {"action": action, "symbol": symbol, "price": price, "qty": qty}
    
    if not url: return payload, "⚠️ NO GHOST URL CONFIGURED"
    
    try:
        # Uses the pre-warmed connection pool instead of opening a new one
        res = await http_client.post(url, json=payload, timeout=8.0)
        return payload, f"Status: {res.status_code} | Response: {res.text}"
    except Exception as e: 
        return payload, f"❌ ERROR: {str(e)}"

async def process_signal(tv_payload: dict):
    try:
        raw_action = str(tv_payload.get("action") or "").lower()
        raw_symbol = str(tv_payload.get("symbol") or "")
        market_pos = str(tv_payload.get("market_position") or "").lower()
        
        try: price = float(tv_payload.get("price") or 0.0)
        except (ValueError, TypeError): price = 0.0

        try: qty = float(tv_payload.get("qty") or 1.0)
        except (ValueError, TypeError): qty = 1.0

        symbol = clean_symbol(raw_symbol)
        action = 'exit' if raw_action in ['close', 'flat'] else raw_action
        
        if market_pos == 'flat' and action != 'exit':
            log_msg(f"🔄 TV SYNC: Strategy flat. Overriding {raw_action.upper()} to EXIT on {symbol}.")
            action = 'exit'

        conn = sqlite3.connect("trades.db", timeout=10)
        c = conn.cursor()

        if c.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0] == 'KILLED':
            log_msg(f"🛑 SYSTEM KILLED: Ignored {raw_action} on {symbol}")
            conn.close(); return

        pos = c.execute("SELECT direction FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if pos and action != 'exit':
            if (pos[0] in ['long', 'buy'] and action in ['short', 'sell']) or (pos[0] in ['short', 'sell'] and action in ['long', 'buy']):
                log_msg(f"🔄 INTELLIGENT REVERSAL: Converted {raw_action.upper()} to EXIT for open {pos[0].upper()} on {symbol}.")
                action = 'exit'

        is_entry = action in ['long', 'short', 'buy', 'sell']
        if is_entry:
            open_dirs = [row[0] for row in c.execute("SELECT DISTINCT direction FROM positions").fetchall()]
            if open_dirs and ('long' if action in ['long', 'buy'] else 'short') != ('long' if open_dirs[0] in ['long', 'buy'] else 'short'):
                log_msg(f"🛡️ ANTI-HEDGE LOCK: Ignored {action.upper()} {symbol}. Direction locked to {open_dirs[0].upper()}.")
                conn.close(); return

        ghost_payload, ghost_response = await send_to_ghost(symbol, action, price, qty)
        
        safe_tv_payload = {k: v for k, v in tv_payload.items() if k != 'passphrase'}
        c.execute("INSERT INTO webhook_audits (timestamp, symbol, action, tv_inbound, ghost_outbound, ghost_response) VALUES (?, ?, ?, ?, ?, ?)",
                  (get_est_time(), symbol, action.upper(), json.dumps(safe_tv_payload), json.dumps(ghost_payload), ghost_response))

        msg_to_log = None
        if is_entry:
            c.execute("INSERT OR REPLACE INTO positions (symbol, direction, entry_price) VALUES (?, ?, ?)", (symbol, action, price))
            msg_to_log = f"🟢 OPENED: {action.upper()} {qty}x {symbol} @ {price}"
        elif action == 'exit' and pos:
            c.execute("INSERT INTO closed_trades (timestamp, symbol, direction, close_price) VALUES (?, ?, ?, ?)", (get_est_time(), symbol, pos[0], price))
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            msg_to_log = f"🏁 CLOSED: {symbol} @ {price}"

        conn.commit()
        conn.close()

        if msg_to_log:
            log_msg(msg_to_log)

    except Exception as e:
        log_msg(f"❌ ENGINE CRASH: {str(e)}")

# --- FASTAPI LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    # Boot the persistent HTTP client pool
    http_client = httpx.AsyncClient()
    
    init_db()
    
    if NGROK_AUTH_TOKEN:
        try:
            ngrok.set_auth_token(NGROK_AUTH_TOKEN)
            public_url = ngrok.connect(8001).public_url
            log_msg(f"🌐 NGROK TUNNEL ACTIVE: Set your TradingView Webhook to: {public_url}/tv-webhook")
        except Exception as e:
            log_msg(f"⚠️ NGROK FAILED to start: {str(e)}")
    else:
        log_msg("🚀 Engine Booted. Listening on Localhost/Direct IP (No ngrok configured).")

    conn = sqlite3.connect("trades.db", timeout=10)
    open_positions = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
    conn.close()
    
    if open_positions: 
        log_msg(f"🔄 <b>ENGINE RESTARTED:</b> Recovered {len(open_positions)} positions.")
        
    if bot: 
        threading.Thread(target=start_telegram_polling, daemon=True).start()
    
    eod_task = asyncio.create_task(market_close_report_loop())
    
    yield
    
    # Clean shutdown
    eod_task.cancel()
    await http_client.aclose()
    ngrok.kill()

app = FastAPI(lifespan=lifespan)

@app.post("/tv-webhook")
async def tv_webhook(request: Request, background_tasks: BackgroundTasks):
    try: payload = json.loads((await request.body()).decode("utf-8"))
    except Exception as e:
        log_msg(f"❌ Invalid JSON received: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("passphrase") != config.get("credentials", {}).get("secret_passphrase", ""):
        log_msg("🔑 Auth Failed: Bad Passphrase")
        raise HTTPException(status_code=401)

    log_msg(f"📥 Received Signal: {payload.get('action', '')} {payload.get('symbol', '')}")
    background_tasks.add_task(process_signal, payload)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, access_log=False)