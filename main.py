import sqlite3
import httpx
import json
import os
import asyncio
import threading
import re
import time
import sys
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

# --- GLOBAL RAM CACHE FOR 0ms ZERO-LATENCY GUARDS ---
PROP_GUARDS = {
    "max_loss_on": False, "max_loss": -500.0,
    "ratchet_on": False, "ratchet_act": 500.0, "ratchet_trail": 250.0,
    "target_on": False, "target": 2000.0,
    "consist_on": False, "consist": 1500.0,
    "pnl": 0.0,
    "hwm": 0.0,
    "tripped": False,
    "reason": ""
}

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
    
    for col in ['pnl', 'slippage', 'qty', 'tv_price', 'broker_price']:
        try: conn.execute(f"ALTER TABLE closed_trades ADD COLUMN {col} REAL")
        except: pass
    for col in ['is_win', 'mode', 'exit_reason']:
        try: conn.execute(f"ALTER TABLE closed_trades ADD COLUMN {col} TEXT")
        except: pass
    for col in ['tv_price', 'broker_price']:
        try: conn.execute(f"ALTER TABLE positions ADD COLUMN {col} REAL")
        except: pass
    try: conn.execute("ALTER TABLE positions ADD COLUMN mode TEXT")
    except: pass

    conn.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('execution_mode', 'SAFE')")
    conn.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('last_heartbeat', 'UNKNOWN')")
    conn.commit()
    conn.close()

# --- 💓 BACKGROUND WORKER: HEARTBEAT & GUARD SYNC ---
def background_worker():
    while True:
        try:
            conn = sqlite3.connect("trades.db", timeout=10)
            conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('last_heartbeat', ?)", (get_vps_time(),))
            
            st_row = conn.execute("SELECT value FROM system_state WHERE key='guard_settings'").fetchone()
            if st_row:
                settings = json.loads(st_row[0])
                PROP_GUARDS.update(settings)
            
            rst = conn.execute("SELECT value FROM system_state WHERE key='guard_reset'").fetchone()
            if rst and rst[0] == '1':
                PROP_GUARDS["pnl"], PROP_GUARDS["hwm"] = 0.0, 0.0
                PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = False, ""
                conn.execute("UPDATE system_state SET value='0' WHERE key='guard_reset'")
                log_msg("⚠️ PROP GUARDS MANUALLY RESET. ENGINE UNLOCKED.")

            state = {"pnl": PROP_GUARDS["pnl"], "hwm": PROP_GUARDS["hwm"], "tripped": PROP_GUARDS["tripped"], "reason": PROP_GUARDS["reason"]}
            conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('guard_state', ?)", (json.dumps(state),))
            
            conn.commit(); conn.close()
        except: pass
        time.sleep(2)

def evaluate_prop_guards():
    if PROP_GUARDS["tripped"]: return
    p, h = PROP_GUARDS["pnl"], PROP_GUARDS["hwm"]
    if PROP_GUARDS.get("max_loss_on") and p <= PROP_GUARDS.get("max_loss", -500):
        PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = True, f"Max Daily Loss Hit (${p:.2f})"
    elif PROP_GUARDS.get("target_on") and p >= PROP_GUARDS.get("target", 2000):
        PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = True, f"Daily Target Reached (${p:.2f})"
    elif PROP_GUARDS.get("consist_on") and p >= PROP_GUARDS.get("consist", 1500):
        PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = True, f"Consistency Limit Hit (${p:.2f})"
    elif PROP_GUARDS.get("ratchet_on") and h >= PROP_GUARDS.get("ratchet_act", 500):
        guard_level = h - PROP_GUARDS.get("ratchet_trail", 250)
        if p <= guard_level:
            PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = True, f"Ratchet Trail Hit (Shield: ${guard_level:.2f})"
            
    if PROP_GUARDS["tripped"]:
        log_msg(f"🚨 PROP GUARD TRIPPED: {PROP_GUARDS['reason']}. Engine restricted to FLAT-ONLY Mode.")

# --- 🕔 5:00 PM EST AUTONOMOUS CME RESET LOOP ---
async def cme_reset_loop():
    ny_tz = pytz.timezone('America/New_York')
    last_reset = None
    while True:
        now_ny = datetime.now(ny_tz)
        if now_ny.hour == 17 and now_ny.minute == 0 and last_reset != now_ny.date():
            PROP_GUARDS["pnl"], PROP_GUARDS["hwm"] = 0.0, 0.0
            PROP_GUARDS["tripped"], PROP_GUARDS["reason"] = False, ""
            last_reset = now_ny.date()
            log_msg("🔄 CME CLOSE (5:00 PM EST): Daily PNL wiped. Prop Guards Reset.")
        await asyncio.sleep(30)

def extract_ghost_data(response_text):
    metrics = {"pnl": None, "is_win": None, "slippage": 0.0, "qty_filled": 0.0, "broker_entry": None, "broker_exit": None, "status": "unknown", "exit_reason": "UNKNOWN"}
    try:
        parts = response_text.split("Response: ")
        if len(parts) > 1:
            data = json.loads(parts[1])
            pos = data.get("position", {})
            metrics["pnl"] = data.get("pnl") if data.get("pnl") is not None else pos.get("pnl")
            metrics["is_win"] = data.get("isWin")
            if isinstance(pos, dict):
                metrics["slippage"] = float(pos.get("entry_slippage") or 0.0) + float(pos.get("exit_slippage") or 0.0)
                metrics["qty_filled"] = float(pos.get("quantity_filled") or 0.0)
                metrics["broker_entry"] = pos.get("broker_entry_price") or pos.get("entry_price")
                metrics["broker_exit"] = pos.get("broker_exit_price") or pos.get("exit_price")
                metrics["status"] = pos.get("status", "unknown")
                metrics["exit_reason"] = pos.get("exit_reason", "MANUAL/WEBHOOK")
    except: pass
    return metrics

async def market_close_report_loop():
    reported_today = False
    while True:
        now_str = get_vps_time()
        today, current_time = now_str.split(" ")[0], now_str.split(" ")[1][:5]
        if current_time == "00:00": reported_today = False
        if current_time == "17:00" and not reported_today:
            conn = sqlite3.connect("trades.db", timeout=10)
            rows = conn.execute("SELECT symbol, direction, broker_price, pnl, is_win FROM closed_trades WHERE timestamp LIKE ?", (f"{today}%",)).fetchall()
            conn.close()
            report = f"📊 <b>EOD Market Report ({today}):</b>\n"
            if rows:
                total_pnl = sum([r[3] for r in rows if r[3] is not None])
                report += f"💵 <b>Total PNL: ${total_pnl:.2f}</b>\n\n"
                for r in rows:
                    pnl_str = f" | PNL: ${r[3]:.2f} {r[4]}" if r[3] is not None else ""
                    report += f"• {r[0]}: {r[1].upper()} @ {r[2]}{pnl_str}\n"
            else: report += "No trades executed today."
            send_telegram_bg(report)
            reported_today = True
        await asyncio.sleep(60)

async def fetch_market_bias():
    bias_data = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    async def get_asset_data(ticker, name):
        try:
            async with httpx.AsyncClient() as client:
                chart_res = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d", headers=headers, timeout=10.0)
                chart = chart_res.json()
                closes = chart['chart']['result'][0]['indicators']['quote'][0]['close']
                prev_close, current = closes[0], closes[-1]
                trend = "🐂 Bullish" if current > prev_close else "🐻 Bearish"
                change = ((current - prev_close) / prev_close) * 100
                news_res = await client.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={name}&newsCount=3", headers=headers, timeout=10.0)
                news = [n['title'] for n in news_res.json().get('news', [])][:2]
                return {"trend": trend, "change": f"{change:+.2f}%", "price": f"{current:,.2f}", "news": news}
        except: return {"trend": "Neutral", "change": "0.00%", "price": "N/A", "news": ["Market data unavailable"]}
    bias_data["Nasdaq (MNQ)"] = await get_asset_data("NQ=F", "Nasdaq")
    bias_data["Gold (MGC)"] = await get_asset_data("GC=F", "Gold")
    return bias_data

async def fetch_daily_data():
    try:
        sessions = {"Sydney": {"open": 22, "close": 7}, "Tokyo": {"open": 23, "close": 8}, "London": {"open": 8, "close": 16}, "New York": {"open": 13, "close": 22}}
        events = []
        async with httpx.AsyncClient() as client:
            res = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10.0)
            if res.status_code == 200:
                vps_tz = datetime.now().astimezone().tzinfo
                for item in res.json():
                    impact = str(item.get("impact", "")).title()
                    if impact in ["High", "Medium"]:
                        try:
                            event_utc = datetime.fromisoformat(item["date"])
                            if event_utc.tzinfo is None: event_utc = event_utc.replace(tzinfo=pytz.utc)
                            events.append({"title": item.get("title", ""), "currency": item.get("country", ""), "impact": impact, "timestamp_iso": event_utc.astimezone(vps_tz).isoformat(), "forecast": item.get("forecast", ""), "previous": item.get("previous", "")})
                        except: pass
        market_bias = await fetch_market_bias()
        conn = sqlite3.connect("trades.db", timeout=10)
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('market_sessions', ?)", (json.dumps(sessions),))
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('calendar_events', ?)", (json.dumps(events),))
        conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('market_bias', ?)", (json.dumps(market_bias),))
        conn.commit(); conn.close()
        log_msg("📅 Market Bias, Calendar & Sessions Updated.", to_tg=False)
    except Exception as e: log_msg(f"⚠️ Failed to fetch daily data: {e}", to_tg=False)

async def daily_maintenance_loop():
    await fetch_daily_data()
    while True:
        await asyncio.sleep(14400) 
        await fetch_daily_data()

async def send_to_ghost(symbol: str, action: str, price: float, qty: float):
    global http_client
    url = config.get("ghost_urls", {}).get(symbol)
    payload = {"action": action, "symbol": symbol, "price": price, "qty": qty}
    if not url: return payload, "⚠️ NO GHOST URL CONFIGURED"
    try:
        res = await http_client.post(url, json=payload, timeout=8.0)
        return payload, f"Status: {res.status_code} | Response: {res.text}"
    except Exception as e: return payload, f"❌ ERROR: {str(e)}"

# --- UNIFIED CROSS-MODE EXECUTION ENGINE ---
async def process_signal(tv_payload: dict):
    try:
        pending_logs = []
        tv_action_id = str(tv_payload.get("action") or "") 
        raw_filter_action = str(tv_payload.get("filter_action") or tv_action_id).lower() 
        raw_symbol = str(tv_payload.get("symbol") or "")
        market_pos = str(tv_payload.get("market_position") or "").lower()
        tv_price = float(tv_payload.get("price") or 0.0)
        qty = float(tv_payload.get("qty") or 1.0)
        symbol = clean_symbol(raw_symbol)
        
        conn = sqlite3.connect("trades.db", timeout=10)
        c = conn.cursor()
        try: mode = c.execute("SELECT value FROM system_state WHERE key='execution_mode'").fetchone()[0]
        except: mode = 'SAFE'

        if mode == 'STOPPED':
            pending_logs.append(f"🛑 ENGINE STOPPED: Ignored incoming signal on {symbol}")
            conn.close(); [log_msg(log) for log in pending_logs]
            return

        action = 'exit' if raw_filter_action in ['close', 'flat'] else raw_filter_action
        if market_pos == 'flat' and action != 'exit':
            pending_logs.append(f"🔄 TV SYNC: Strategy flat. Overriding {raw_filter_action.upper()} to EXIT on {symbol}.")
            action = 'exit'

        is_entry = action in ['long', 'short', 'buy', 'sell']

        # 0ms RAM GUARD CHECK: Whitelist Exits, Block Entries if Tripped
        if is_entry and PROP_GUARDS["tripped"]:
            pending_logs.append(f"🛡️ GUARD ACTIVE ({PROP_GUARDS['reason']}): Blocked {action.upper()} on {symbol}. Waiting for exits.")
            conn.close(); [log_msg(log) for log in pending_logs]
            return

        # Fetch local open position to enable Cross-Mode exits and logging
        pos = c.execute("SELECT direction, mode FROM positions WHERE symbol=?", (symbol,)).fetchone()
        
        # SAFE MODE Logic (Reversal flattening & Correlation Locks)
        if mode == 'SAFE':
            if pos and action != 'exit':
                if (pos[0] in ['long', 'buy'] and action in ['short', 'sell']) or (pos[0] in ['short', 'sell'] and action in ['long', 'buy']):
                    pending_logs.append(f"🔄 REVERSAL DETECTED: Flattening open {pos[0].upper()} on {symbol} before entering {action.upper()}.")
                    ghost_payload_exit, ghost_response_exit = await send_to_ghost(symbol, 'exit', tv_price, qty)
                    safe_tv_payload = {k: v for k, v in tv_payload.items() if k != 'passphrase'}
                    c.execute("INSERT INTO webhook_audits (timestamp, symbol, action, tv_inbound, ghost_outbound, ghost_response) VALUES (?, ?, ?, ?, ?, ?)",
                              (get_vps_time(), symbol, 'REVERSAL-EXIT', json.dumps(safe_tv_payload), json.dumps(ghost_payload_exit), ghost_response_exit))
                    
                    m_exit = extract_ghost_data(ghost_response_exit)
                    pnl_val = float(m_exit["pnl"]) if m_exit["pnl"] is not None else 0.0
                    win_str = ("WIN" if m_exit["is_win"] else "LOSS") if m_exit["pnl"] is not None else ""
                    broker_price = m_exit["broker_exit"] if m_exit["broker_exit"] else tv_price
                    old_mode = pos[1]

                    c.execute("INSERT INTO closed_trades (timestamp, symbol, direction, close_price, pnl, is_win, mode, slippage, qty, tv_price, broker_price, exit_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                              (get_vps_time(), symbol, pos[0], broker_price, pnl_val, win_str, mode, m_exit["slippage"], m_exit["qty_filled"], tv_price, broker_price, m_exit["exit_reason"]))
                    c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                    pending_logs.append(f"🏁 [SAFE] CLOSED (Reversal): {symbol} [Opened in {old_mode}] | Broker Fill: {broker_price} | PNL: ${pnl_val:.2f} {win_str} | Slip: {m_exit['slippage']}")
                    pos = None 
                    
                    PROP_GUARDS["pnl"] += pnl_val
                    PROP_GUARDS["hwm"] = max(PROP_GUARDS["hwm"], PROP_GUARDS["pnl"])
                    evaluate_prop_guards()
                    
                    if PROP_GUARDS["tripped"]:
                        pending_logs.append(f"🛡️ GUARD TRIPPED BY REVERSAL EXIT ({PROP_GUARDS['reason']}). Halting the new entry leg.")
                        conn.commit(); conn.close(); [log_msg(log) for log in pending_logs]
                        return

            if is_entry:
                target_dir = 'long' if action in ['long', 'buy'] else 'short'
                EQUITIES, METALS = {'MNQ', 'MES', 'MYM', 'M2K', 'NQ', 'ES', 'YM', 'RTY'}, {'MGC', 'GC', 'SIL', 'SI'}
                my_group = EQUITIES if symbol in EQUITIES else (METALS if symbol in METALS else None)
                if my_group:
                    open_positions = c.execute("SELECT symbol, direction FROM positions").fetchall()
                    for open_sym, open_dir in open_positions:
                        if open_sym != symbol and open_sym in my_group:
                            norm_open_dir = 'long' if open_dir in ['long', 'buy'] else 'short'
                            if target_dir != norm_open_dir:
                                pending_logs.append(f"🛡️ CORRELATION LOCK: Ignored {action.upper()} {symbol}. Correlated asset ({open_sym}) is {norm_open_dir.upper()}.")
                                conn.commit(); conn.close(); [log_msg(log) for log in pending_logs]
                                return

        # FAST EXECUTION (UNIFIED FOR SAFE & BYPASS)
        exec_action = tv_action_id if mode == 'BYPASS' else action
        ghost_payload, ghost_response = await send_to_ghost(symbol, exec_action, tv_price, qty)
        
        safe_tv_payload = {k: v for k, v in tv_payload.items() if k != 'passphrase'}
        c.execute("INSERT INTO webhook_audits (timestamp, symbol, action, tv_inbound, ghost_outbound, ghost_response) VALUES (?, ?, ?, ?, ?, ?)",
                  (get_vps_time(), symbol, exec_action.upper(), json.dumps(safe_tv_payload), json.dumps(ghost_payload), ghost_response))

        m = extract_ghost_data(ghost_response)
        is_closing_event = (m["pnl"] is not None) or (action == 'exit')

        # POST-EXECUTION CROSS-MODE TRACKING
        if is_closing_event and pos:
            # Ghost successfully closed a known position (or TV sent explicit exit)
            pnl_val = float(m["pnl"]) if m["pnl"] is not None else 0.0
            win_str = ("WIN" if m["is_win"] else "LOSS") if m["pnl"] is not None else ""
            broker_price = m["broker_exit"] if m["broker_exit"] else tv_price
            old_mode = pos[1]
            
            c.execute("INSERT INTO closed_trades (timestamp, symbol, direction, close_price, pnl, is_win, mode, slippage, qty, tv_price, broker_price, exit_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                      (get_vps_time(), symbol, pos[0], broker_price, pnl_val, win_str, mode, m["slippage"], m["qty_filled"], tv_price, broker_price, m["exit_reason"]))
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            pending_logs.append(f"🏁 [{mode}] CLOSED: {symbol} [Opened in {old_mode}] | Broker Fill: {broker_price} | PNL: ${pnl_val:.2f} {win_str} | Slip: {m['slippage']}")
            
            if m["pnl"] is not None:
                PROP_GUARDS["pnl"] += pnl_val
                PROP_GUARDS["hwm"] = max(PROP_GUARDS["hwm"], PROP_GUARDS["pnl"])
                evaluate_prop_guards()

        elif is_closing_event and not pos:
            # Ghost closed a trade, but it wasn't tracked locally
            pnl_val = float(m["pnl"]) if m["pnl"] is not None else 0.0
            win_str = ("WIN" if m["is_win"] else "LOSS") if m["pnl"] is not None else ""
            broker_price = m["broker_exit"] if m["broker_exit"] else tv_price
            
            c.execute("INSERT INTO closed_trades (timestamp, symbol, direction, close_price, pnl, is_win, mode, slippage, qty, tv_price, broker_price, exit_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                      (get_vps_time(), symbol, 'UNKNOWN', broker_price, pnl_val, win_str, mode, m["slippage"], m["qty_filled"], tv_price, broker_price, m["exit_reason"]))
            pending_logs.append(f"🏁 [{mode}] CLOSED UNTRACKED: {symbol} | Broker Fill: {broker_price} | PNL: ${pnl_val:.2f} {win_str} | Slip: {m['slippage']}")

            if m["pnl"] is not None:
                PROP_GUARDS["pnl"] += pnl_val
                PROP_GUARDS["hwm"] = max(PROP_GUARDS["hwm"], PROP_GUARDS["pnl"])
                evaluate_prop_guards()
                
        else:
            # It's an entry (Long or Short)
            broker_price = m["broker_entry"] if m["broker_entry"] else tv_price
            c.execute("INSERT OR REPLACE INTO positions (symbol, direction, entry_price, mode, tv_price, broker_price) VALUES (?, ?, ?, ?, ?, ?)", 
                      (symbol, action, broker_price, mode, tv_price, broker_price))
            fill_str = f" (Filled: {m['qty_filled']})" if m['qty_filled'] > 0 else ""
            slip_str = f" | Slip: {m['slippage']}" if m['slippage'] > 0 else ""
            pending_logs.append(f"🟢 [{mode}] OPENED: {exec_action.upper()} {qty}x{fill_str} {symbol} | TV: {tv_price} -> Broker: {broker_price}{slip_str}")

        conn.commit(); conn.close()
        for log in pending_logs: log_msg(log)
    except Exception as e: log_msg(f"❌ ENGINE CRASH: {str(e)}")

if bot:
    @bot.message_handler(commands=['status', 'positions', 'closed'])
    def handle_commands(message):
        if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
        cmd = message.text.replace('/', '')
        conn = sqlite3.connect("trades.db", timeout=10)
        if cmd == 'status':
            try: mode = conn.execute("SELECT value FROM system_state WHERE key='execution_mode'").fetchone()[0]
            except: mode = 'SAFE'
            guard_status = f"🔴 TRIPPED ({PROP_GUARDS['reason']})" if PROP_GUARDS['tripped'] else "🟢 CLEAR"
            bot.reply_to(message, f"🖥️ Engine Mode: <b>{mode}</b>\n🛡️ Guard: {guard_status}\n⏱️ VPS Time: {get_vps_time()}", parse_mode="HTML")
        elif cmd == 'positions':
            try: rows = conn.execute("SELECT symbol, direction, broker_price FROM positions").fetchall()
            except: rows = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
            bot.reply_to(message, "🎯 <b>Open Positions:</b>\n" + "\n".join([f"• {r[0]}: {r[1].upper()} @ {r[2]}" for r in rows]) if rows else "No active positions.", parse_mode="HTML")
        elif cmd == 'closed':
            today = get_vps_time().split(" ")[0]
            rows = conn.execute("SELECT symbol, direction, broker_price, pnl, is_win FROM closed_trades WHERE timestamp LIKE ?", (f"{today}%",)).fetchall()
            if rows:
                reply = f"🏁 <b>Closed Trades ({today}):</b>\n"
                for r in rows:
                    pnl_str = f" | PNL: ${r[3]:.2f} {r[4]}" if r[3] is not None else ""
                    reply += f"• {r[0]}: {r[1].upper()} @ {r[2]}{pnl_str}\n"
                bot.reply_to(message, reply, parse_mode="HTML")
            else: bot.reply_to(message, "No closed trades today.", parse_mode="HTML")
        conn.close()

    def start_telegram_polling():
        try: bot.set_my_commands([telebot.types.BotCommand("status", "Check status"), telebot.types.BotCommand("positions", "List open trades"), telebot.types.BotCommand("closed", "Today's trades")])
        except Exception as e: pass
        
        # --- NEW: Silence the noisy Telegram network timeout logs ---
        import logging
        telebot_logger = logging.getLogger('TeleBot')
        telebot_logger.setLevel(logging.CRITICAL)
        
        while True:
            try: 
                # Extended timeouts to prevent disconnects, and set internal logger to CRITICAL
                bot.infinity_polling(timeout=60, long_polling_timeout=60, logger_level=logging.CRITICAL)
            except: 
                time.sleep(10)

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

    conn = sqlite3.connect("trades.db", timeout=10)
    open_positions = conn.execute("SELECT symbol, direction, entry_price FROM positions").fetchall()
    conn.close()
    if open_positions: log_msg(f"🔄 <b>ENGINE RESTARTED:</b> Recovered {len(open_positions)} positions.")
    if bot: threading.Thread(target=start_telegram_polling, daemon=True).start()
    
    eod_task = asyncio.create_task(market_close_report_loop())
    daily_task = asyncio.create_task(daily_maintenance_loop())
    cme_task = asyncio.create_task(cme_reset_loop())
    
    # 0ms SYNC: Run background worker in isolated OS Thread
    threading.Thread(target=background_worker, daemon=True).start()
    
    yield
    eod_task.cancel()
    daily_task.cancel()
    cme_task.cancel()
    await http_client.aclose()
    ngrok.kill()

app = FastAPI(lifespan=lifespan)

@app.post("/tv-webhook")
async def tv_webhook(request: Request, background_tasks: BackgroundTasks):
    try: payload = json.loads((await request.body()).decode("utf-8"))
    except: raise HTTPException(status_code=400, detail="Invalid JSON")
    if payload.get("passphrase") != config.get("credentials", {}).get("secret_passphrase", ""):
        raise HTTPException(status_code=401)

    print(f"[{get_vps_time()}] 📥 Received Signal: {payload.get('action', '')} {payload.get('symbol', '')}")
    if bot and TELEGRAM_CHAT_ID: threading.Thread(target=send_telegram_bg, args=(f"ℹ️ 📥 Received Signal: {payload.get('action', '')} {payload.get('symbol', '')}",), daemon=True).start()
    
    background_tasks.add_task(process_signal, payload)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, access_log=False, log_level="error")