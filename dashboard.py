import streamlit as st
import sqlite3
import pandas as pd
import json
import time

st.set_page_config(page_title="Robosh V6 Monitor", page_icon="📈", layout="wide")

def get_db():
    return sqlite3.connect("trades.db", timeout=10)

st.title("⚡ Robosh V6 Command Center")

conn = get_db()
try: status = conn.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0]
except: status = "LOADING..."

col1, col2, col3 = st.columns(3)
with col1:
    if status == 'RUNNING':
        if st.button("🛑 KILL ENGINE", type="primary", width="stretch"):
            conn.execute("UPDATE system_state SET value='KILLED' WHERE key='status'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🛑 SYSTEM MANUALLY KILLED')")
            conn.commit(); st.rerun()
    else: st.error("🚨 ENGINE IS KILLED")

with col2:
    if status == 'KILLED':
        if st.button("▶️ RESUME ENGINE", type="secondary", width="stretch"):
            conn.execute("UPDATE system_state SET value='RUNNING' WHERE key='status'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '▶️ SYSTEM RESUMED')")
            conn.commit(); st.rerun()
    else: st.success("🟢 ENGINE IS RUNNING")

with col3:
    if st.button("🗑️ CLEAR DATABASE", width="stretch"):
        conn.execute("DELETE FROM logs")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM closed_trades")
        conn.execute("DELETE FROM webhook_audits") 
        conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🧹 DATABASE WIPED BY USER')")
        conn.commit(); st.rerun()

st.divider()

# --- TOP ROW: Positions & Logs ---
col_log, col_pos = st.columns([1.2, 1])

with col_log:
    st.subheader("📡 Live Engine Logs")
    if st.button("🔄 Refresh"): st.rerun()
    try:
        logs_df = pd.read_sql_query("SELECT timestamp, message FROM logs ORDER BY timestamp DESC LIMIT 30", conn)
        if not logs_df.empty: st.dataframe(logs_df, width="stretch", hide_index=True, height=350)
        else: st.info("No logs yet. Awaiting signals...")
    except: st.warning("Database initializing...")

with col_pos:
    st.subheader("🎯 Open Positions")
    try:
        open_df = pd.read_sql_query("SELECT symbol, direction, entry_price FROM positions", conn)
        if not open_df.empty: st.dataframe(open_df, width="stretch", hide_index=True)
        else: st.info("No active positions.")
    except: pass

    st.subheader("🏁 Closed Trades History")
    try:
        closed_df = pd.read_sql_query("SELECT timestamp, symbol, direction, close_price FROM closed_trades ORDER BY timestamp DESC LIMIT 15", conn)
        if not closed_df.empty: st.dataframe(closed_df, width="stretch", hide_index=True)
        else: st.info("No closed trades yet.")
    except: pass

st.divider()

# --- BOTTOM ROW: Expandable Webhook Audit Trail ---
st.subheader("🔍 Webhook Execution Audit Trail")
try:
    audits_df = pd.read_sql_query("SELECT * FROM webhook_audits ORDER BY timestamp DESC LIMIT 30", conn)
    if audits_df.empty:
        st.info("No webhooks processed yet.")
    else:
        for index, row in audits_df.iterrows():
            with st.expander(f"⏱️ {row['timestamp']} | 🎯 {row['symbol']} | ⚡ {row['action']}"):
                colA, colB, colC = st.columns(3)
                
                with colA:
                    st.markdown("**📥 TradingView Payload**")
                    st.json(json.loads(row['tv_inbound']))
                
                with colB:
                    st.markdown("**📤 Sent to Ghost**")
                    st.json(json.loads(row['ghost_outbound']))
                
                with colC:
                    st.markdown("**✅ Ghost Response**")
                    if "Status: 200" in row['ghost_response'] or "Success" in row['ghost_response']:
                        st.success(row['ghost_response'])
                    else:
                        st.error(row['ghost_response'])
except Exception as e:
    st.warning("Audit trail initializing...")

conn.close()
time.sleep(5)
st.rerun()