import sqlite3
import httpx
import json
import os
import asyncio
import threading
import re
import time
import sys # <-- Added
from datetime import datetime, timedelta
import pytz
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import telebot
from pyngrok import ngrok

# --- FIX FOR WINDOWS ASYNCIO WINERROR 10054 SPAM ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SYMBOL_REGEX = re.compile(r"^([A-Za-z]+)")
http_client = None

def get_vps_time():
    return datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')

def clean_symbol(raw: str) -> str:
    match = SYMBOL_REGEX.match(raw)
    return match.group(1).upper() if match else raw.upper()

def load_config():
    try:
        with open("config.json", "r") as f: return json.load(f)
    except: return {}

config = load_config()
TELEGRAM_TOKEN = config.get("credentials", {}).get("telegram_bot_token", "")
TELEGRAM_CHAT_ID = config.get("credentials", {}).get("telegram_chat_id", "")
NGROK_AUTH_TOKEN = config.get("credentials", {}).get("ngrok_auth_token", "")

if TELEGRAM_TOKEN and "REPLACE_" not in TELEGRAM_TOKEN: bot = telebot.TeleBot(TELEGRAM_TOKEN)
else: bot = None

def send_telegram_bg(msg: str):
    if bot and TELEGRAM_CHAT_ID:
        try: bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="HTML")
        except: pass

def log_msg(msg: str, to_tg: bool = True):
    timestamp = get_vps_time()
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("INSERT INTO logs (timestamp, message) VALUES (?, ?)", (timestamp, msg))
    conn.commit()
    conn.close()
    print(f"[{timestamp}] {msg}")
    if to_tg: threading.Thread(target=send_telegram_bg, args=(f"ℹ️ {msg}",), daemon=True).start()

def init_db():
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, message TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, direction TEXT, entry_price REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS closed_trades (timestamp TEXT, symbol TEXT, direction TEXT, close_price REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS webhook_audits (timestamp TEXT, symbol TEXT, action TEXT, tv_inbound TEXT, ghost_outbound TEXT, ghost_response TEXT)")
    conn.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('status', 'RUNNING')")
    conn.commit()
    conn.close()

async def fetch_daily_data():
    try:
        sessions = {
            "Sydney": {"open": 22, "close": 7},
            "Tokyo": {"open": 23, "close": 8},
            "London": {"open": 8, "close": 16},
            "New York": {"open": 13, "close": 22}
        }
        
        events = []
        async with httpx.AsyncClient() as client:
            res = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                vps_tz = datetime.now().astimezone().tzinfo
                
                for item in data:
                    impact = str(item.get("impact", "")).title()
                    if impact in ["High", "Medium"]:
                        try:
                            event_utc = datetime.fromisoformat(item["date"])
                            if event_utc.tzinfo is None:
                                event_utc = event_utc.replace(tzinfo=pytz.utc)
                            
                            event_local = event_utc.astimezone(vps_tz)
                            events.append({
                                "title": item.get("title", ""),
                                "currency": item.get("country", ""),
                                "impact": impact,
                                "timestamp_iso": event_local.isoformat(),
                                "forecast": item.get("forecast", ""),
                                "previous": item.get("previous", "")
                            })
                        except: pass

        conn = sqlite3.connect("trades.db", timeout=10)
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('market_sessions', ?)", (json.dumps(sessions),))
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('calendar_events', ?)", (json.dumps(events),))
        conn.commit()
        conn.close()
        log_msg("📅 Daily Calendar & Session Times Updated.", to_tg=False)
    except Exception as e:
        log_msg(f"⚠️ Failed to fetch daily calendar: {e}", to_tg=False)

async def daily_maintenance_loop():
    await fetch_daily_data()
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        await asyncio.sleep((next_midnight - now).total_seconds())
        await fetch_daily_data()

if bot:
    @bot.message_handler(commands=['status', 'positions', 'closed'])
    def handle_commands(message):
        if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
        cmd = message.text.replace('/', '')
        conn = sqlite3.connect("trades.db", timeout=10)
        if cmd == 'status':
            status = conn.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0]
            bot.reply_to(message, f"🖥️ Engine Status: <b>{status}</b>\n⏱️ VPS Time: {get_vps_time()}", parse_mode="HTML")
        elif cmd == 'positions':
            rows = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
            bot.reply_to(message, "🎯 <b>Open Positions:</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else "No active positions.", parse_mode="HTML")
        elif cmd == 'closed':
            today = get_vps_time().split(" ")[0]
            rows = conn.execute("SELECT symbol, direction, close_price FROM closed_trades WHERE timestamp LIKE ?", (f"{today}%",)).fetchall()
            bot.reply_to(message, f"🏁 <b>Closed Trades ({today}):</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else "No closed trades today.", parse_mode="HTML")
        conn.close()

    def start_telegram_polling():
        try: bot.set_my_commands([telebot.types.BotCommand("status", "Check status"), telebot.types.BotCommand("positions", "List open trades"), telebot.types.BotCommand("closed", "Today's trades")])
        except Exception as e: pass
        while True:
            try: bot.infinity_polling(timeout=20, long_polling_timeout=15)
            except: time.sleep(10)

async def market_close_report_loop():
    reported_today = False
    while True:
        now_str = get_vps_time()
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
        res = await http_client.post(url, json=payload, timeout=8.0)
        return payload, f"Status: {res.status_code} | Response: {res.text}"
    except Exception as e: return payload, f"❌ ERROR: {str(e)}"

async def process_signal(tv_payload: dict):
    try:
        pending_logs = []
        raw_action = str(tv_payload.get("action") or "").lower()
        raw_symbol = str(tv_payload.get("symbol") or "")
        market_pos = str(tv_payload.get("market_position") or "").lower()
        
        try: price = float(tv_payload.get("price") or 0.0)
        except: price = 0.0
        try: qty = float(tv_payload.get("qty") or 1.0)
        except: qty = 1.0

        symbol = clean_symbol(raw_symbol)
        action = 'exit' if raw_action in ['close', 'flat'] else raw_action
        
        # 1. Primary Flat Check
        if market_pos == 'flat' and action != 'exit':
            pending_logs.append(f"🔄 TV SYNC: Strategy flat. Overriding {raw_action.upper()} to EXIT on {symbol}.")
            action = 'exit'

        conn = sqlite3.connect("trades.db", timeout=10)
        c = conn.cursor()

        if c.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0] == 'KILLED':
            pending_logs.append(f"🛑 SYSTEM KILLED: Ignored {raw_action} on {symbol}")
            conn.close()
            for log in pending_logs: log_msg(log)
            return

        # 2. Symbol-Specific Reversal Check
        pos = c.execute("SELECT direction FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if pos and action != 'exit':
            if (pos[0] in ['long', 'buy'] and action in ['short', 'sell']) or (pos[0] in ['short', 'sell'] and action in ['long', 'buy']):
                pending_logs.append(f"🔄 INTELLIGENT REVERSAL: Converted {raw_action.upper()} to EXIT for open {pos[0].upper()} on {symbol}.")
                action = 'exit'

        is_entry = action in ['long', 'short', 'buy', 'sell']
        
        # 3. 🛡️ PROP FIRM CORRELATION ANTI-HEDGE LOCK
        # Prevents illegal hedging across highly correlated assets (e.g., Long MNQ vs Short MES)
        if is_entry:
            target_dir = 'long' if action in ['long', 'buy'] else 'short'
            
            EQUITIES = {'MNQ', 'MES', 'MYM', 'M2K', 'NQ', 'ES', 'YM', 'RTY'}
            METALS = {'MGC', 'GC', 'SIL', 'SI'}
            
            my_group = None
            if symbol in EQUITIES: my_group = EQUITIES
            elif symbol in METALS: my_group = METALS
            
            if my_group:
                open_positions = c.execute("SELECT symbol, direction FROM positions").fetchall()
                for open_sym, open_dir in open_positions:
                    if open_sym != symbol and open_sym in my_group:
                        norm_open_dir = 'long' if open_dir in ['long', 'buy'] else 'short'
                        if target_dir != norm_open_dir:
                            pending_logs.append(f"🛡️ CORRELATION LOCK: Ignored {action.upper()} {symbol}. Correlated asset ({open_sym}) is currently {norm_open_dir.upper()}.")
                            conn.close()
                            for log in pending_logs: log_msg(log)
                            return

        # 4. Light-Speed Execution
        ghost_payload, ghost_response = await send_to_ghost(symbol, action, price, qty)
        
        safe_tv_payload = {k: v for k, v in tv_payload.items() if k != 'passphrase'}
        c.execute("INSERT INTO webhook_audits (timestamp, symbol, action, tv_inbound, ghost_outbound, ghost_response) VALUES (?, ?, ?, ?, ?, ?)",
                  (get_vps_time(), symbol, action.upper(), json.dumps(safe_tv_payload), json.dumps(ghost_payload), ghost_response))

        if is_entry:
            c.execute("INSERT OR REPLACE INTO positions (symbol, direction, entry_price) VALUES (?, ?, ?)", (symbol, action, price))
            pending_logs.append(f"🟢 OPENED: {action.upper()} {qty}x {symbol} @ {price}")
        elif action == 'exit' and pos:
            c.execute("INSERT INTO closed_trades (timestamp, symbol, direction, close_price) VALUES (?, ?, ?, ?)", (get_vps_time(), symbol, pos[0], price))
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            pending_logs.append(f"🏁 CLOSED: {symbol} @ {price}")

        conn.commit()
        conn.close()
        for log in pending_logs: log_msg(log)
    except Exception as e: log_msg(f"❌ ENGINE CRASH: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=5, max_connections=10))
    init_db()
    
    if NGROK_AUTH_TOKEN:
        try:
            ngrok.set_auth_token(NGROK_AUTH_TOKEN)
            public_url = ngrok.connect(8001).public_url
            log_msg(f"🌐 NGROK TUNNEL ACTIVE: Set your TradingView Webhook to: {public_url}/tv-webhook")
        except Exception as e: log_msg(f"⚠️ NGROK FAILED to start: {str(e)}")
    else: log_msg("🚀 Engine Booted. Localhost only.")

    conn = sqlite3.connect("trades.db", timeout=10)
    open_positions = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
    conn.close()
    
    if open_positions: log_msg(f"🔄 <b>ENGINE RESTARTED:</b> Recovered {len(open_positions)} positions.")
    if bot: threading.Thread(target=start_telegram_polling, daemon=True).start()
    
    eod_task = asyncio.create_task(market_close_report_loop())
    daily_task = asyncio.create_task(daily_maintenance_loop())
    yield
    eod_task.cancel()
    daily_task.cancel()
    await http_client.aclose()
    ngrok.kill()

app = FastAPI(lifespan=lifespan)

@app.post("/tv-webhook")
async def tv_webhook(request: Request, background_tasks: BackgroundTasks):
    try: payload = json.loads((await request.body()).decode("utf-8"))
    except: raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("passphrase") != config.get("credentials", {}).get("secret_passphrase", ""):
        log_msg("🔑 Auth Failed: Bad Passphrase")
        raise HTTPException(status_code=401)

    print(f"[{get_vps_time()}] 📥 Received Signal: {payload.get('action', '')} {payload.get('symbol', '')}")
    if bot and TELEGRAM_CHAT_ID: threading.Thread(target=send_telegram_bg, args=(f"ℹ️ 📥 Received Signal: {payload.get('action', '')} {payload.get('symbol', '')}",), daemon=True).start()
    
    background_tasks.add_task(process_signal, payload)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, access_log=False, log_level="error")