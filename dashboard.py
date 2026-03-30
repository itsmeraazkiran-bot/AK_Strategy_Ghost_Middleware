import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import pytz
import json
import os
import requests

st.set_page_config(page_title="Robosh V5 Monitor", page_icon="📈", layout="wide")
EXCHANGE_TZ = pytz.timezone('America/New_York')
CONFIG_FILE = "config.json"

def get_est_time(): return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ)
def to_local_time_str(est_time_str):
    try:
        dt = datetime.strptime(est_time_str.replace(" EST", ""), "%Y-%m-%d %H:%M:%S")
        return EXCHANGE_TZ.localize(dt).astimezone().strftime('%Y-%m-%d %H:%M:%S (Local)')
    except: return est_time_str

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {}

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)

def get_db_connection(): 
    conn = sqlite3.connect("trades.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def fetch_data():
    conn = get_db_connection()
    try: pos_df = pd.read_sql_query("SELECT symbol, direction, qty, entry_price, current_price, floating_pnl FROM open_positions", conn)
    except: pos_df = pd.DataFrame()
    today = get_est_time().strftime('%Y-%m-%d')
    try:
        c = conn.cursor()
        c.execute("SELECT trade_count, realized_pnl, highest_pnl FROM daily_risk WHERE date=?", (today,))
        metrics = c.fetchone() or (0, 0.0, 0.0)
        c.execute("SELECT value FROM system_status WHERE key='last_ping'")
        lp = c.fetchone()
        last_ping = to_local_time_str(lp[0]) if lp else "Awaiting Signal..."
    except: metrics, last_ping = (0, 0.0, 0.0), "Database Locked"
    conn.close(); return pos_df, metrics, last_ping

st.title("📈 Robosh V5 Command Center")
config = load_config()
pos_df, metrics, last_ping = fetch_data()

total_floating = pos_df['floating_pnl'].sum() if not pos_df.empty and 'floating_pnl' in pos_df else 0.0

st.caption(f"📡 **Last TV Heartbeat:** {last_ping}")
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Realized PnL (Net)", f"${metrics[1]:.2f}")
with col2: st.metric("Floating PnL", f"${total_floating:.2f}", delta_color="normal" if total_floating >= 0 else "inverse")
with col3: st.metric("High Water Mark", f"${metrics[2]:.2f}")
with col4: st.metric("Trades Today", str(metrics[0]))

st.markdown("---")
col_left, col_right = st.columns([1, 1.5])

with col_left:
    st.subheader("🛑 Execution Controls")
    risk_cfg, feat_cfg = config.get("risk", {}), config.get("features", {})
    
    if risk_cfg.get("hard_kill", False):
        st.error("🚨 SYSTEM HARD LOCKED")
        if st.button("🔓 UNLOCK SYSTEM", type="primary", use_container_width=True):
            config["risk"]["hard_kill"] = False; save_config(config); st.rerun()
    else:
        if st.button("🛑 HARD KILL (Flatten & Lock)", type="primary", use_container_width=True):
            config["risk"]["hard_kill"] = True; save_config(config)
            try: requests.post("http://127.0.0.1:8001/tv-webhook", json={"passphrase": config["credentials"]["secret_passphrase"], "action": "panic_flatten", "symbol": "ALL"}, timeout=2)
            except: pass
            st.rerun()
            
    st.divider()
    soft_fade = st.toggle("🌙 Soft Fade (Allow exits only)", value=risk_cfg.get("soft_fade", False))
    anti_hedge = st.toggle("🛡️ Anti-Hedge Protection", value=feat_cfg.get("anti_hedge_protection", True))
    news_blk = st.toggle("📰 News Blackout (High-Impact USD)", value=feat_cfg.get("news_blackout", False))
    chop_flt = st.toggle("🌊 Choppy Market Filter (ADX < 20)", value=feat_cfg.get("choppy_market_filter", False))
    dyn_size = st.toggle("⚖️ Dynamic Volatility Sizing (ATR)", value=feat_cfg.get("dynamic_sizing", False))
    
    if (soft_fade != risk_cfg.get("soft_fade") or anti_hedge != feat_cfg.get("anti_hedge_protection") or 
        news_blk != feat_cfg.get("news_blackout") or chop_flt != feat_cfg.get("choppy_market_filter") or 
        dyn_size != feat_cfg.get("dynamic_sizing")):
        config["risk"]["soft_fade"], config["features"]["anti_hedge_protection"] = soft_fade, anti_hedge
        config["features"]["news_blackout"], config["features"]["choppy_market_filter"] = news_blk, chop_flt
        config["features"]["dynamic_sizing"] = dyn_size
        save_config(config); st.rerun()

    st.divider()
    st.subheader("⚙️ Live Risk Parameters")
    with st.form("risk_form"):
        max_loss = st.number_input("Daily Max Loss ($)", value=float(risk_cfg.get("max_daily_loss", -150.0)), step=50.0)
        tgt_prof = st.number_input("Daily Profit Target ($)", value=float(risk_cfg.get("daily_profit_target", 500.0)), step=50.0)
        risk_per = st.number_input("Risk Per Trade ($) - For Sizing", value=float(risk_cfg.get("risk_per_trade_usd", 50.0)), step=10.0)
        if st.form_submit_button("Save Risk Settings"):
            config["risk"]["max_daily_loss"], config["risk"]["daily_profit_target"], config["risk"]["risk_per_trade_usd"] = max_loss, tgt_prof, risk_per
            save_config(config); st.success("Risk params live!")

with col_right:
    st.subheader("🎯 Active Positions")
    if not pos_df.empty:
        def color_pnl(val): return 'color: green' if val > 0 else 'color: red'
        st.dataframe(pos_df.style.map(color_pnl, subset=['floating_pnl']), use_container_width=True, hide_index=True)
    else: st.info("No active positions.")

    st.subheader("✅ Symbol Sandbox")
    sandbox = config.get("sandbox", {})
    s_cols = st.columns(6)
    updates = {}
    with s_cols[0]: updates["MNQ"] = st.checkbox("MNQ", value=sandbox.get("MNQ", True))
    with s_cols[1]: updates["MES"] = st.checkbox("MES", value=sandbox.get("MES", True))
    with s_cols[2]: updates["MYM"] = st.checkbox("MYM", value=sandbox.get("MYM", True))
    with s_cols[3]: updates["M2K"] = st.checkbox("M2K", value=sandbox.get("M2K", True))
    with s_cols[4]: updates["MGC"] = st.checkbox("MGC", value=sandbox.get("MGC", True))
    with s_cols[5]: updates["SIL"] = st.checkbox("SIL", value=sandbox.get("SIL", True))
    
    if updates != sandbox:
        config["sandbox"] = updates; save_config(config); st.rerun()
        
    if st.button("🔄 REBOOT ENGINE", type="secondary", use_container_width=True):
        os.system("taskkill /f /im python.exe")

if st.button("🔄 Refresh UI"): st.rerun()