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
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import Response
from pydantic import BaseModel
from contextlib import asynccontextmanager

# --- INSTITUTIONAL ROLLING LOGGER ---
log_formatter = logging.Formatter('[%(asctime)s EST] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('robosh_engine')
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# Using system_log.txt to avoid the Windows File Lock
file_handler = RotatingFileHandler('system_log.txt', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

EXCHANGE_TZ = pytz.timezone('America/New_York')
CONFIG_FILE = "config.json"
MULTIPLIERS = {"MNQ": 2.0, "MES": 5.0, "MYM": 0.5, "M2K": 5.0, "MGC": 10.0, "SIL": 1000.0}

def get_est_time(): return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ)
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {}

config = load_config()
SECRET_PASSPHRASE = config.get("credentials", {}).get("secret_passphrase", "")
TELEGRAM_BOT_TOKEN = config.get("credentials", {}).get("telegram_bot_token", "")
TELEGRAM_USER_ID = config.get("credentials", {}).get("telegram_chat_id", "")

telegram_db_status = "🔴 OFFLINE"
if TELEGRAM_BOT_TOKEN and "REPLACE_" not in TELEGRAM_BOT_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    telegram_db_status = "🟢 ONLINE"
else:
    bot = None

def send_telegram(msg):
    clean_msg = re.sub(r'<[^<]+>', '', msg)
    logger.info(f"TELEGRAM: {clean_msg}")
    if bot:
        try: bot.send_message(TELEGRAM_USER_ID, msg, parse_mode="HTML")
        except: pass

def clean_symbol(raw: str):
    match = re.match(r"^([A-Za-z]+)", raw)
    return match.group(1).upper() if match else raw.upper()

async def async_send_ghost_webhook(url: str, payload: dict):
    logger.info(f"📤 OUTBOUND TO GHOST | URL: {url} | Payload: {payload}")
    async with httpx.AsyncClient(http2=True) as client:
        for attempt in range(3):
            try:
                response = await client.post(url, json=payload, timeout=5.0)
                logger.info(f"✅ GHOST RESPONSE (Attempt {attempt+1}) | Status: {response.status_code} | Body: {response.text}")
                if response.status_code == 200: return True
            except Exception as e: 
                logger.error(f"❌ GHOST ERROR (Attempt {attempt+1}) | {str(e)}")
            await asyncio.sleep(0.5)
        return False

# --- NEWS BLACKOUT CACHE ---
cached_news_blackouts = []
last_news_fetch = None

async def fetch_news_loop():
    global cached_news_blackouts, last_news_fetch
    async with httpx.AsyncClient() as client:
        while True:
            today_str = get_est_time().strftime('%Y-%m-%d')
            if last_news_fetch != today_str:
                try:
                    # UPDATED MIRROR URL
                    res = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
                    events = res.json()
                    blackouts = []
                    for e in events:
                        if e.get('country') == 'USD' and e.get('impact') == 'High':
                            dt = datetime.fromisoformat(e['date']).astimezone(EXCHANGE_TZ)
                            blackouts.append(dt)
                    cached_news_blackouts = blackouts
                    last_news_fetch = today_str
                    logger.info(f"📰 News Cached: {len(blackouts)} Red Folder events today.")
                except Exception as e: 
                    logger.error(f"News Fetch Error: {str(e)}")
            await asyncio.sleep(3600)

def is_news_blackout_active(current_est):
    if not load_config().get("features", {}).get("news_blackout", False): return False
    for event_time in cached_news_blackouts:
        if (event_time - timedelta(minutes=15)) <= current_est <= (event_time + timedelta(minutes=15)):
            return True
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
        if now.weekday() == 0: reconciled_this_week = False

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

        if current_time == "17:00" and not reset_today:
            conn = sqlite3.connect("trades.db", timeout=10)
            c = conn.cursor()
            c.execute(f"DELETE FROM webhooks WHERE timestamp < '{(now - timedelta(days=7)).strftime('%Y-%m-%d')}'")
            next_day = (now + timedelta(days=1)).strftime('%Y-%m-%d')
            c.execute("INSERT OR IGNORE INTO daily_risk (date, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, 0.0, 0.0)", (next_day,))
            conn.commit(); conn.close()
            reset_today = True
            logger.info("🧹 EOD Database Maintenance Completed.")
            
        if now.weekday() == 4 and current_time == "17:05" and not reconciled_this_week:
            msg = "📋 <b>WEEKLY RECONCILIATION REQUIRED</b>\n\n1. Download CSV from Lucid.\n2. Compare Net PnL with Robosh.\n3. Adjust `mumbai_slippage_ticks` if necessary."
            send_telegram(msg)
            reconciled_this_week = True

        await asyncio.sleep(60)

# --- EXECUTION ENGINE ---
async def execute_trade_logic(signal_dict: dict):
    symbol, action, price = signal_dict['symbol'], signal_dict['action'].lower(), signal_dict.get('price')
    adx, atr = signal_dict.get('adx', 25.0), signal_dict.get('atr', 1.0) 
    cfg = load_config()
    est_now = get_est_time()
    today, timestamp = est_now.strftime('%Y-%m-%d'), est_now.strftime('%Y-%m-%d %H:%M:%S EST')

    if action == "panic_flatten": return 

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

    risk, feat = cfg.get("risk", {}), cfg.get("features", {})
    if risk.get("hard_kill", False): conn.close(); return 
    
    is_entry = action in ['long', 'short', 'buy', 'sell']
    if is_entry:
        if risk.get("soft_fade", False) or not cfg.get("sandbox", {}).get(symbol, True): conn.close(); return 
        
        if is_news_blackout_active(est_now):
            send_telegram(f"🚫 <b>NEWS BLACKOUT:</b> Blocked {action.upper()} {symbol}")
            conn.close(); return
            
        if feat.get("choppy_market_filter", False) and adx < 20.0:
            send_telegram(f"🌊 <b>CHOP FILTER BLOCKED:</b> {action.upper()} {symbol} rejected (ADX: {adx:.1f})")
            conn.close(); return

    qty = 1
    if is_entry and feat.get("dynamic_sizing", False) and atr > 0:
        risk_usd = risk.get("risk_per_trade_usd", 50.0)
        calc_qty = int(risk_usd / (atr * MULTIPLIERS.get(symbol, 1.0)))
        qty = max(1, calc_qty) 

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

# --- TELEGRAM LISTENER THREAD ---
def setup_telegram_menu():
    commands = [
        BotCommand("status", "📊 Check daily PnL & status"),
        BotCommand("positions", "🎯 List open trades"),
        BotCommand("lock", "🛑 EMERGENCY KILL SWITCH"),
        BotCommand("unlock", "✅ UNLOCK SYSTEM"),
        BotCommand("reboot", "🔄 Reboot engines")
    ]
    try: bot.set_my_commands(commands)
    except: pass

if bot:
    @bot.message_handler(commands=['status', 'positions', 'lock', 'unlock', 'reboot'])
    def handle_telegram_commands(message):
        if str(message.chat.id) != str(TELEGRAM_USER_ID): return
        cmd = message.text.replace('/', '')
        conn = sqlite3.connect("trades.db", timeout=10)
        c = conn.cursor()
        
        if cmd == 'status':
            today = get_est_time().strftime('%Y-%m-%d')
            c.execute("SELECT trade_count, realized_pnl FROM daily_risk WHERE date=?", (today,))
            row = c.fetchone()
            bot.reply_to(message, f"📊 Trades Today: {row[0] if row else 0}\nRealized PnL: ${row[1] if row else 0.0:.2f}")
        elif cmd == 'positions':
            c.execute("SELECT symbol, direction, qty, entry_price FROM open_positions")
            rows = c.fetchall()
            bot.reply_to(message, "🎯 <b>Active Positions:</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} {r[2]}x @ {r[3]}" for r in rows]) if rows else "No active positions.", parse_mode="HTML")
        elif cmd in ['lock', 'unlock']:
            cfg = load_config(); cfg["risk"]["hard_kill"] = (cmd == 'lock'); save_config(cfg)
            bot.reply_to(message, "🔒 System LOCKED." if cmd == 'lock' else "✅ System UNLOCKED.")
        elif cmd == 'reboot':
            bot.reply_to(message, "🔄 Rebooting Python Engines...")
            os.system("taskkill /f /im python.exe")
        
        conn.commit(); conn.close()

    def start_telegram_polling():
        setup_telegram_menu()
        logger.info("📡 Telegram Polling Thread Started.")
        bot.infinity_polling(timeout=10, long_polling_timeout=5)

# --- FASTAPI SETUP & MIDDLEWARE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS system_status (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS daily_risk (date TEXT PRIMARY KEY, trade_count INTEGER, realized_pnl REAL, highest_pnl REAL)")
    conn.execute("INSERT OR IGNORE INTO daily_risk (date, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, 0.0, 0.0)", (get_est_time().strftime('%Y-%m-%d'),))
    conn.execute("INSERT OR REPLACE INTO system_status (key, value) VALUES ('telegram_status', ?)", (telegram_db_status,))
    conn.commit(); conn.close()
    
    asyncio.create_task(market_schedule_loop())
    asyncio.create_task(fetch_news_loop())
    
    if bot:
        threading.Thread(target=start_telegram_polling, daemon=True).start()
        
    logger.info(f"🟢 SYSTEM BOOT: Engine Online. Telegram Status: {telegram_db_status}")
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def enforce_ip_whitelist(request: Request, call_next):
    if request.url.path == "/tv-webhook":
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        allowed_ips = load_config().get("security", {}).get("allowed_tv_ips", [])
        if allowed_ips and client_ip not in allowed_ips: 
            logger.warning(f"🚫 BLOCKED IP: Unauthorized webhook attempt from {client_ip}")
            return Response(status_code=403, content="Forbidden")
    return await call_next(request)

class TradingSignal(BaseModel):
    passphrase: str = None
    action: str
    symbol: str
    price: float = None
    market_position: str = None 
    adx: float = 25.0
    atr: float = 1.0

# --- THE BULLETPROOF RAW WEBHOOK CATCHER ---
@app.post("/tv-webhook")
async def tv_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_data = await request.body()
    raw_text = raw_data.decode("utf-8")
    
    logger.info(f"📥 RAW TV PAYLOAD CAUGHT: {raw_text}")
        
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON CONVERSION FAILED! TradingView sent bad formatting. Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
        
    try:
        signal = TradingSignal(**payload)
    except Exception as e:
        logger.error(f"❌ DATA FORMAT ERROR! Missing required fields. Error: {e}")
        raise HTTPException(status_code=422, detail="Data mismatch")

    if signal.passphrase != SECRET_PASSPHRASE: 
        logger.warning("🔑 AUTH FAILED: Invalid passphrase.")
        raise HTTPException(status_code=401)
    
    signal.symbol = clean_symbol(signal.symbol)
    if signal.market_position and signal.market_position.lower() == "flat": 
        signal.action = "exit"
        
    background_tasks.add_task(execute_trade_logic, signal.dict())
    return {"status": "success", "message": "Payload converted and routed successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, access_log=False)