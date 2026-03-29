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
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from contextlib import asynccontextmanager

EXCHANGE_TZ = pytz.timezone('America/New_York')
CONFIG_FILE = "config.json"
MULTIPLIERS = {"MNQ": 2.0, "MES": 5.0, "M2K": 5.0, "MYM": 0.5, "MGC": 10.0, "SIL": 1000.0}

def get_est_time(): return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {}

config = load_config()
SECRET_PASSPHRASE = config.get("credentials", {}).get("secret_passphrase", "")
TELEGRAM_BOT_TOKEN = config.get("credentials", {}).get("telegram_bot_token", "")
TELEGRAM_USER_ID = config.get("credentials", {}).get("telegram_chat_id", "")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

def send_telegram(msg):
    if bot:
        try: bot.send_message(TELEGRAM_USER_ID, msg, parse_mode="HTML")
        except: pass

def clean_symbol(raw: str):
    match = re.match(r"^([A-Za-z]+)", raw)
    return match.group(1).upper() if match else raw.upper()

# --- DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect("trades.db", timeout=20)
    conn.execute("PRAGMA journal_mode=WAL") 
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS open_positions (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, entry_price REAL, current_price REAL, qty INTEGER, floating_pnl REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS daily_risk (date TEXT PRIMARY KEY, is_locked INTEGER, trade_count INTEGER, realized_pnl REAL, highest_pnl REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS webhooks (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, symbol TEXT, action TEXT, price REAL, status TEXT, tv_payload TEXT, ghost_payload TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS system_status (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

# --- ASYNC GHOST EXECUTOR ---
async def async_send_ghost_webhook(url: str, payload: dict):
    async with httpx.AsyncClient(http2=True) as client:
        for attempt in range(3):
            try:
                response = await client.post(url, json=payload, timeout=5.0)
                if response.status_code == 200: return True
            except: pass
            await asyncio.sleep(0.5)
        return False

# --- EOD DAILY REPORT & DB PRUNING ---
async def eod_daily_report_loop():
    while True:
        now = get_est_time()
        if now.hour == 16 and now.minute == 50:
            today = now.strftime('%Y-%m-%d')
            conn = sqlite3.connect("trades.db", timeout=20)
            c = conn.cursor()
            c.execute("SELECT trade_count, realized_pnl, highest_pnl FROM daily_risk WHERE date=?", (today,))
            metrics = c.fetchone() or (0, 0.0, 0.0)
            
            try:
                df = pd.read_sql_query(f"SELECT timestamp, symbol, action, price FROM webhooks WHERE timestamp LIKE '{today}%'", conn)
                csv_filename = f"Trade_Report_{today}.csv"
                df.to_csv(csv_filename, index=False)
                
                report_msg = f"📊 <b>EOD REPORT ({today})</b>\n\nTrades: {metrics[0]}\nNet PnL: ${metrics[1]:.2f}\nHigh Water: ${metrics[2]:.2f}"
                if bot:
                    with open(csv_filename, 'rb') as doc: bot.send_document(TELEGRAM_USER_ID, doc, caption=report_msg, parse_mode="HTML")
                os.remove(csv_filename)
            except Exception as e: print(f"EOD Report Error: {e}")

            seven_days_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
            c.execute(f"DELETE FROM webhooks WHERE timestamp < '{seven_days_ago}'")
            conn.commit(); conn.close()
            await asyncio.sleep(61)
        await asyncio.sleep(30)

def check_hedge_violation(symbol: str, action: str, cursor, config: dict) -> bool:
    if not config.get("features", {}).get("anti_hedge_protection", True): return False
    target_group = None
    for group_name, symbols in config.get("hedge_groups", {}).items():
        if symbol in symbols:
            target_group = symbols
            break
    if not target_group: return False
    
    cursor.execute("SELECT symbol, direction FROM open_positions")
    intended_dir = "long" if action in ["long", "buy"] else "short"
    for pos_sym, pos_dir in cursor.fetchall():
        if pos_sym in target_group and pos_sym != symbol and pos_dir != intended_dir: return True
    return False

# --- ASYNC EXECUTION LOGIC ---
async def execute_trade_logic(signal_dict: dict):
    symbol = signal_dict['symbol']
    action = signal_dict['action'].lower()
    price = signal_dict['price']
    config = load_config()
    est_now = get_est_time()
    today = est_now.strftime('%Y-%m-%d')
    timestamp = est_now.strftime('%Y-%m-%d %H:%M:%S EST')

    tv_payload_str = json.dumps(signal_dict)

    conn = sqlite3.connect("trades.db", timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    if action == "panic_flatten":
        c.execute("SELECT symbol, qty FROM open_positions")
        for sym, qty in c.fetchall():
            if sym in config.get("ghost_urls", {}):
                await async_send_ghost_webhook(config["ghost_urls"][sym], {"action": "exit", "symbol": sym, "qty": qty})
        c.execute("DELETE FROM open_positions")
        conn.commit(); conn.close()
        send_telegram("🚨 <b>HARD KILL INITIATED</b>: All positions flattened.")
        return

    if action == "ping":
        c.execute("INSERT OR REPLACE INTO system_status (key, value) VALUES ('last_ping', ?)", (timestamp,))
        if price:
            c.execute("SELECT direction, entry_price, qty FROM open_positions WHERE symbol=?", (symbol,))
            pos = c.fetchone()
            if pos:
                floating_pnl = ((price - pos[1]) if pos[0] == 'long' else (pos[1] - price)) * MULTIPLIERS.get(symbol, 1.0) * pos[2]
                c.execute("UPDATE open_positions SET current_price=?, floating_pnl=? WHERE symbol=?", (price, floating_pnl, symbol))
        conn.commit(); conn.close()
        return

    risk = config.get("risk", {})
    if risk.get("hard_kill", False): conn.close(); return 
    
    is_entry = action in ['long', 'short', 'buy', 'sell']
    if is_entry:
        if risk.get("soft_fade", False) or not config.get("sandbox", {}).get(symbol, True): conn.close(); return 
        if check_hedge_violation(symbol, action, c, config):
            send_telegram(f"🛡️ <b>ANTI-HEDGE BLOCKED:</b> Prevented {action.upper()} {symbol}")
            conn.close(); return

    qty = 1 
    ghost_url = config.get("ghost_urls", {}).get(symbol)
    
    if ghost_url:
        ghost_payload_dict = {"action": "exit" if action in ['exit', 'close', 'flat'] else action, "symbol": symbol, "price": price, "qty": qty}
        ghost_payload_str = json.dumps(ghost_payload_dict)
        
        success = await async_send_ghost_webhook(ghost_url, ghost_payload_dict)
        
        if success:
            if is_entry:
                c.execute("DELETE FROM open_positions WHERE symbol=?", (symbol,))
                c.execute("INSERT INTO open_positions (symbol, direction, entry_price, qty) VALUES (?, ?, ?, ?)", (symbol, action, price, qty))
                send_telegram(f"🟢 <b>ENTRY:</b> {action.upper()} {qty}x {symbol} @ {price}")
            
            elif action in ['exit', 'close', 'flat']:
                c.execute("SELECT direction, entry_price FROM open_positions WHERE symbol=?", (symbol,))
                pos = c.fetchone()
                if pos:
                    rt_comm = config.get("costs", {}).get("commission_round_trip", 1.0)
                    slip_ticks = config.get("costs", {}).get("mumbai_slippage_ticks", 2)
                    
                    gross_pnl = ((price - pos[1]) if pos[0] == 'long' else (pos[1] - price)) * MULTIPLIERS.get(symbol, 1.0) * qty
                    net_pnl = gross_pnl - (rt_comm * qty) - (slip_ticks * (MULTIPLIERS.get(symbol, 1.0) * 0.25) * qty)
                    
                    c.execute("INSERT OR IGNORE INTO daily_risk (date, is_locked, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, 0, 0.0, 0.0)", (today,))
                    c.execute("UPDATE daily_risk SET realized_pnl = realized_pnl + ?, trade_count = trade_count + 1 WHERE date=?", (net_pnl, today))
                    c.execute("DELETE FROM open_positions WHERE symbol=?", (symbol,))
                    send_telegram(f"🏁 <b>CLOSED {symbol}:</b> Net PnL: ${net_pnl:.2f}")

            c.execute("INSERT INTO webhooks (timestamp, symbol, action, price, status, tv_payload, ghost_payload) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                      (timestamp, symbol, action, price, "✅ Executed", tv_payload_str, ghost_payload_str))
        else:
            send_telegram(f"🚨 <b>DESYNC ALERT:</b> Ghost failed to respond for {symbol} after retries!")
            config["sandbox"][symbol] = False
            save_config(config)
            c.execute("INSERT INTO webhooks (timestamp, symbol, action, price, status, tv_payload, ghost_payload) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                      (timestamp, symbol, action, price, "❌ Ghost Timeout", tv_payload_str, ghost_payload_str))

    conn.commit()
    conn.close()

# --- TELEGRAM COMMAND LISTENER ---
def setup_telegram_menu():
    commands = [
        BotCommand("status", "📊 Check daily PnL & status"),
        BotCommand("positions", "🎯 List open trades"),
        BotCommand("lock", "🛑 EMERGENCY KILL SWITCH"),
        BotCommand("unlock", "✅ UNLOCK SYSTEM"),
        BotCommand("reboot", "🔄 Reboot engines"),
        BotCommand("reset", "🚨 Factory wipe database")
    ]
    try: bot.set_my_commands(commands)
    except: pass

@bot.message_handler(commands=['status', 'positions', 'lock', 'unlock', 'reboot', 'reset'])
def handle_telegram_commands(message):
    if str(message.chat.id) != str(TELEGRAM_USER_ID): return
    cmd = message.text.replace('/', '')
    conn = sqlite3.connect("trades.db", timeout=20)
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
    elif cmd == 'reset':
        c.execute("DELETE FROM open_positions"); c.execute("DELETE FROM webhooks"); c.execute("DELETE FROM daily_risk"); c.execute("DELETE FROM system_status")
        bot.reply_to(message, "🚨 <b>FACTORY RESET COMPLETE</b>", parse_mode="HTML")
    
    conn.commit(); conn.close()

def start_telegram_polling():
    setup_telegram_menu()
    bot.infinity_polling(timeout=10, long_polling_timeout=5)

# --- FASTAPI SETUP ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if bot: threading.Thread(target=start_telegram_polling, daemon=True).start()
    asyncio.create_task(eod_daily_report_loop())
    send_telegram("🟢 <b>SYSTEM BOOT:</b> Engine Online.")
    yield

app = FastAPI(lifespan=lifespan)

class TradingSignal(BaseModel):
    passphrase: str = None
    action: str
    symbol: str
    price: float = None
    market_position: str = None 

class ResetRequest(BaseModel):
    passphrase: str

@app.post("/tv-webhook")
async def tv_webhook(signal: TradingSignal, background_tasks: BackgroundTasks):
    if signal.passphrase != SECRET_PASSPHRASE: raise HTTPException(status_code=401)
    signal.symbol = clean_symbol(signal.symbol)
    if signal.market_position and signal.market_position.lower() == "flat": signal.action = "exit"
    background_tasks.add_task(execute_trade_logic, signal.dict())
    return {"status": "success"}

@app.post("/factory-reset")
async def api_factory_reset(req: ResetRequest):
    if req.passphrase != SECRET_PASSPHRASE: raise HTTPException(status_code=401)
    conn = sqlite3.connect("trades.db", timeout=20)
    c = conn.cursor()
    c.execute("DELETE FROM open_positions"); c.execute("DELETE FROM webhooks"); c.execute("DELETE FROM daily_risk"); c.execute("DELETE FROM system_status")
    conn.commit(); conn.close()
    send_telegram("🚨 <b>FACTORY RESET EXECUTED VIA UI.</b>")
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)