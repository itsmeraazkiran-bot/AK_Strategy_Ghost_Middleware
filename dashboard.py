import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import pytz
import json
import os
import requests

st.set_page_config(page_title="Robosh V3 Monitor", page_icon="📈", layout="wide")
EXCHANGE_TZ = pytz.timezone('America/New_York')
CONFIG_FILE = "config.json"

def get_est_time(): return datetime.now(pytz.utc).astimezone(EXCHANGE_TZ)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
    return {}

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)

def get_db_connection(): 
    # Read-only connection to prevent Database Locked errors
    conn = sqlite3.connect("trades.db", timeout=20)
    return conn

def fetch_data():
    conn = get_db_connection()
    try: 
        pos_df = pd.read_sql_query("SELECT symbol, direction, qty, entry_price, current_price, floating_pnl FROM open_positions", conn)
        # --- FIX: Fill blanks with 0.0 before the ping arrives ---
        if not pos_df.empty:
            pos_df['floating_pnl'] = pos_df['floating_pnl'].fillna(0.0)
            pos_df['current_price'] = pos_df['current_price'].fillna(pos_df['entry_price'])
    except: 
        pos_df = pd.DataFrame()
    
    today = get_est_time().strftime('%Y-%m-%d')
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT trade_count, realized_pnl, highest_pnl FROM daily_risk WHERE date=?", (today,))
        metrics = cursor.fetchone() or (0, 0.0, 0.0)
        
        cursor.execute("SELECT value FROM system_status WHERE key='last_ping'")
        last_ping = cursor.fetchone()
        last_ping = last_ping[0] if last_ping else "Awaiting Signal..."
        
        logs_df = pd.read_sql_query("SELECT timestamp, symbol, action, price, status, tv_payload, ghost_payload FROM webhooks ORDER BY id DESC LIMIT 15", conn)
    except:
        metrics, last_ping, logs_df = (0, 0.0, 0.0), "Database Booting...", pd.DataFrame()
    
    conn.close()
    return pos_df, metrics, last_ping, logs_df

# --- UI LAYOUT ---
st.title("📈 Robosh V3 Command Center")
config = load_config()
pos_df, metrics, last_ping, logs_df = fetch_data()

total_floating = pos_df['floating_pnl'].sum() if not pos_df.empty and 'floating_pnl' in pos_df else 0.0

st.caption(f"📡 **Last TV Ping Received:** {last_ping} EST")
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Realized PnL (Net)", f"${metrics[1]:.2f}")
with col2: st.metric("Floating PnL", f"${total_floating:.2f}", delta_color="normal" if total_floating >= 0 else "inverse")
with col3: st.metric("High Water Mark", f"${metrics[2]:.2f}")
with col4: st.metric("Trades Today", str(metrics[0]))

st.markdown("---")
col_left, col_right = st.columns([1, 1.5])

with col_left:
    st.subheader("🛑 Execution Controls")
    risk_cfg = config.get("risk", {})
    
    if risk_cfg.get("hard_kill", False):
        st.error("🚨 SYSTEM HARD LOCKED: All trades blocked.")
        if st.button("🔓 UNLOCK SYSTEM", type="primary", use_container_width=True):
            config["risk"]["hard_kill"] = False; save_config(config); st.rerun()
    else:
        if st.button("🛑 HARD KILL (Flatten & Lock)", type="primary", use_container_width=True):
            config["risk"]["hard_kill"] = True; save_config(config)
            try: requests.post("http://127.0.0.1:8001/tv-webhook", json={"passphrase": config.get("credentials", {}).get("secret_passphrase", ""), "action": "panic_flatten", "symbol": "ALL"}, timeout=2)
            except: pass
            st.rerun()
            
    soft_fade = st.toggle("🌙 Soft Fade (Reject new entries, allow exits)", value=risk_cfg.get("soft_fade", False))
    anti_hedge = st.toggle("🛡️ Anti-Hedge Protection", value=config.get("features", {}).get("anti_hedge_protection", True))
    
    if soft_fade != risk_cfg.get("soft_fade") or anti_hedge != config.get("features", {}).get("anti_hedge_protection"):
        config["risk"]["soft_fade"] = soft_fade
        config["features"]["anti_hedge_protection"] = anti_hedge
        save_config(config); st.rerun()

    st.divider()
    st.subheader("⚙️ Live Risk Parameters")
    with st.form("risk_form"):
        max_loss = st.number_input("Daily Max Loss ($)", value=float(risk_cfg.get("max_daily_loss", -150.0)), step=50.0)
        tgt_prof = st.number_input("Daily Profit Target ($)", value=float(risk_cfg.get("daily_profit_target", 500.0)), step=50.0)
        trl_act = st.number_input("Trailing Activation ($)", value=float(risk_cfg.get("trailing_activation", 250.0)), step=50.0)
        trl_buf = st.number_input("Trailing Buffer ($)", value=float(risk_cfg.get("trailing_buffer", 100.0)), step=50.0)
        if st.form_submit_button("Save Risk Settings"):
            config["risk"]["max_daily_loss"], config["risk"]["daily_profit_target"] = max_loss, tgt_prof
            config["risk"]["trailing_activation"], config["risk"]["trailing_buffer"] = trl_act, trl_buf
            save_config(config); st.success("Risk params live!")

    st.divider()
    with st.expander("⚠️ DANGER ZONE: Factory Reset"):
        st.warning("Permanently delete all PnL, open positions, and webhook history. Use this when starting a new Prop Firm account.")
        if st.button("🚨 WIPE ALL DATA", type="primary", use_container_width=True):
            try:
                res = requests.post("http://127.0.0.1:8001/factory-reset", json={"passphrase": config.get("credentials", {}).get("secret_passphrase", "")}, timeout=5)
                if res.status_code == 200:
                    st.toast("Database Wiped Clean & Telegram Alert Sent!", icon="✅")
                    st.rerun()
                else: st.error("Authentication failed. Check config.json.")
            except: st.error("Engine offline. Cannot execute reset.")

with col_right:
    st.subheader("🎯 Active Positions")
    if not pos_df.empty:
        # --- FIX: Bulletproof color logic for blank/zero PnL ---
        def color_pnl(val):
            if pd.isna(val) or val == 0: return 'color: gray'
            return 'color: green' if val > 0 else 'color: red'
            
        st.dataframe(pos_df.style.map(color_pnl, subset=['floating_pnl']), use_container_width=True, hide_index=True)
    else:
        st.info("No active positions.")

    st.subheader("✅ Symbol Sandbox")
    sandbox = config.get("sandbox", {})
    s_cols = st.columns(6)
    updates = {}
    with s_cols[0]: updates["MNQ"] = st.checkbox("MNQ", value=sandbox.get("MNQ", True))
    with s_cols[1]: updates["MES"] = st.checkbox("MES", value=sandbox.get("MES", True))
    with s_cols[2]: updates["M2K"] = st.checkbox("M2K", value=sandbox.get("M2K", True))
    with s_cols[3]: updates["MYM"] = st.checkbox("MYM", value=sandbox.get("MYM", True))
    with s_cols[4]: updates["MGC"] = st.checkbox("MGC", value=sandbox.get("MGC", True))
    with s_cols[5]: updates["SIL"] = st.checkbox("SIL", value=sandbox.get("SIL", True))
    if updates != sandbox:
        config["sandbox"] = updates; save_config(config); st.rerun()

    st.divider()
    st.subheader("🛠️ Deep Diagnostic Logs")
    if not logs_df.empty:
        for _, row in logs_df.iterrows():
            icon = "✅" if "Executed" in str(row['status']) else "❌"
            expander_title = f"{icon} {row['timestamp']} | {row['symbol']} | {row['action'].upper()} @ {row['price']}"
            with st.expander(expander_title):
                diag_col1, diag_col2 = st.columns(2)
                with diag_col1:
                    st.markdown("**📥 Received from TV:**")
                    try: st.json(json.loads(row['tv_payload']))
                    except: st.write(str(row['tv_payload']))
                with diag_col2:
                    st.markdown("**📤 Sent to Ghost:**")
                    try: st.json(json.loads(row['ghost_payload']))
                    except: st.write(str(row['ghost_payload']))
                st.markdown(f"**Final Status:** `{row['status']}`")
    else:
        st.info("No recent webhooks found.")

    if st.button("🔄 REBOOT ENGINE", type="secondary", use_container_width=True):
        st.toast("Rebooting... wait 5 seconds.", icon="🔄")
        os.system("taskkill /f /im python.exe")

if st.button("🔄 Refresh UI"): st.rerun()