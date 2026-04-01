import streamlit as st
import sqlite3
import pandas as pd
import json
import time
from datetime import datetime, timedelta
import pytz
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Robosh V6 Command Center", page_icon="⚡", layout="wide")

def get_db(): return sqlite3.connect("trades.db", timeout=10)

st.title("⚡ Robosh V6 Command Center")

conn = get_db()
try: status = conn.execute("SELECT value FROM system_state WHERE key='status'").fetchone()[0]
except: status = "LOADING..."

# --- CONTROL PANEL ---
col1, col2, col3 = st.columns(3)
with col1:
    if status == 'RUNNING':
        if st.button("🛑 KILL ENGINE", type="primary", use_container_width=True):
            conn.execute("UPDATE system_state SET value='KILLED' WHERE key='status'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🛑 SYSTEM MANUALLY KILLED')")
            conn.commit(); st.rerun()
    else: st.error("🚨 ENGINE IS KILLED")

with col2:
    if status == 'KILLED':
        if st.button("▶️ RESUME ENGINE", type="secondary", use_container_width=True):
            conn.execute("UPDATE system_state SET value='RUNNING' WHERE key='status'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '▶️ SYSTEM RESUMED')")
            conn.commit(); st.rerun()
    else: st.success("🟢 ENGINE IS RUNNING")

with col3:
    if st.button("🗑️ CLEAR TRADE DATA", use_container_width=True):
        conn.execute("DELETE FROM logs")
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM closed_trades")
        conn.execute("DELETE FROM webhook_audits") 
        conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🧹 TRADE HISTORY WIPED BY USER')")
        conn.commit(); st.rerun()

st.divider()

# --- MARKET DASHBOARD ---
vps_now = datetime.now()
st.subheader(f"🌍 Live Market & Events (VPS Time: {vps_now.strftime('%I:%M:%S %p')})")

dash_col1, dash_col2 = st.columns([1.5, 1])

with dash_col1:
    st.markdown("##### 🕰️ Global Session Timeline & Volume Predictor")
    try:
        session_data = conn.execute("SELECT value FROM system_state WHERE key='market_sessions'").fetchone()
        if session_data:
            utc_volume_curve = {
                0: 25, 1: 30, 2: 30, 3: 25, 4: 20, 5: 20, 6: 25, 7: 40, 
                8: 60, 9: 65, 10: 60, 11: 55, 12: 60, 13: 85, 14: 100, 15: 95, 
                16: 80, 17: 60, 18: 50, 19: 45, 20: 35, 21: 25, 22: 15, 23: 20
            }

            x_times = []
            y_sydney, y_tokyo, y_london, y_ny, y_vol = [], [], [], [], []
            midnight_local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            nan = float('nan') 
            
            for i in range(25):
                hr_local = midnight_local + timedelta(hours=i)
                hr_utc = hr_local.astimezone(pytz.utc).hour
                
                # By passing pure datetime objects, Plotly intrinsically knows exactly what timezone we are targeting
                x_times.append(hr_local)
                y_vol.append(utc_volume_curve.get(hr_utc, 0))
                
                y_sydney.append(4 if (hr_utc >= 22 or hr_utc < 7) else nan)
                y_tokyo.append(3 if (hr_utc >= 23 or hr_utc < 8) else nan)
                y_london.append(2 if (8 <= hr_utc < 16) else nan)
                y_ny.append(1 if (13 <= hr_utc < 22) else nan)

            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)

            fig.add_trace(go.Scatter(x=x_times, y=y_sydney, mode='lines', line=dict(color='#4B7BEC', width=20), name='Sydney'), row=1, col=1)
            fig.add_trace(go.Scatter(x=x_times, y=y_tokyo, mode='lines', line=dict(color='#A55EEA', width=20), name='Tokyo'), row=1, col=1)
            fig.add_trace(go.Scatter(x=x_times, y=y_london, mode='lines', line=dict(color='#2BCBBA', width=20), name='London'), row=1, col=1)
            fig.add_trace(go.Scatter(x=x_times, y=y_ny, mode='lines', line=dict(color='#20BF6B', width=20), name='New York'), row=1, col=1)
            fig.add_trace(go.Scatter(x=x_times, y=y_vol, fill='tozeroy', mode='lines', line=dict(color='#F7B731', width=2), name='Volume'), row=2, col=1)

            # Fix: We render the "Now" line as a standalone trace series so Plotly mathematical engines never collide
            fig.add_trace(go.Scatter(x=[vps_now, vps_now], y=[0, 4.5], mode='lines', line=dict(color='red', width=2, dash='dash'), name='Now', hoverinfo='none'), row=1, col=1)
            fig.add_trace(go.Scatter(x=[vps_now, vps_now], y=[0, 100], mode='lines', line=dict(color='red', width=2, dash='dash'), hoverinfo='none', showlegend=False), row=2, col=1)

            fig.update_layout(
                height=350, margin=dict(l=0, r=0, t=20, b=0),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                showlegend=False, hovermode="x unified",
                xaxis=dict(type='date', tickformat="%I:%M %p", showgrid=True, gridcolor='rgba(255,255,255,0.1)'),
                yaxis=dict(showgrid=False, zeroline=False, tickmode='array', tickvals=[1,2,3,4], ticktext=['New York', 'London', 'Tokyo', 'Sydney'], range=[0.5, 4.5]),
                yaxis2=dict(showgrid=False, zeroline=False, showticklabels=False)
            )
            st.plotly_chart(fig, use_container_width=True)
        else: st.caption("Awaiting initial daily sync...")
    except Exception as e: st.caption(f"Error drawing timeline: {e}")

with dash_col2:
    st.markdown("##### 🚨 Institutional News Terminal")
    try:
        event_data = conn.execute("SELECT value FROM system_state WHERE key='calendar_events'").fetchone()
        if event_data:
            events = json.loads(event_data[0])
            upcoming_events = []
            
            for e in events:
                if 'timestamp_iso' not in e: continue
                event_time = datetime.fromisoformat(e['timestamp_iso']).replace(tzinfo=None) 
                
                if vps_now < event_time < (vps_now + timedelta(hours=48)):
                    delta = event_time - vps_now
                    hours, rem = divmod(delta.seconds, 3600)
                    mins = rem // 60
                    
                    if delta.days == 0 and hours < 24: countdown = f"In {hours}h {mins}m"
                    else: countdown = "Tomorrow"
                    impact_icon = "🔥" if e['impact'] == "High" else "⚠️"
                    
                    upcoming_events.append({
                        "Time": event_time.strftime('%I:%M %p'),
                        "T-Minus": countdown,
                        "Impact": impact_icon,
                        "Asset": e['currency'],
                        "Event": e['title'],
                        "FCST": e.get('forecast', '-'),
                        "PREV": e.get('previous', '-')
                    })
            
            if upcoming_events:
                df_events = pd.DataFrame(upcoming_events)
                st.dataframe(df_events, hide_index=True, use_container_width=True)
            else:
                st.success("✅ No high-impact events remaining for the next 48 hours.")
        else: st.caption("Awaiting data...")
    except Exception as e: st.caption(f"Error loading events: {e}")

st.divider()

# --- TRADING ACTIVITY ---
col_log, col_pos = st.columns([1.2, 1])

with col_log:
    st.subheader("📡 Live Engine Logs")
    try:
        logs_df = pd.read_sql_query("SELECT timestamp, message FROM logs ORDER BY timestamp DESC LIMIT 30", conn)
        if not logs_df.empty: st.dataframe(logs_df, use_container_width=True, hide_index=True, height=500)
        else: st.info("No logs yet. Awaiting signals...")
    except: st.warning("Database initializing...")

with col_pos:
    st.subheader("🎯 Open Positions")
    try:
        open_df = pd.read_sql_query("SELECT symbol, direction, entry_price FROM positions", conn)
        if not open_df.empty: 
            st.dataframe(open_df, use_container_width=True, hide_index=True)
            st.markdown("### 🔧 Manual State Sync")
            colA, colB = st.columns([2, 1])
            with colA: symbol_to_remove = st.selectbox("Select stuck symbol:", open_df['symbol'].tolist(), label_visibility="collapsed")
            with colB:
                if st.button("🗑️ Force Clear", use_container_width=True):
                    conn.execute("DELETE FROM positions WHERE symbol=?", (symbol_to_remove,))
                    conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), ?)", (f"🔧 MANUAL SYNC: Force cleared {symbol_to_remove} from database.",))
                    conn.commit()
                    st.rerun()
        else: st.info("No active positions.")
    except Exception as e: pass

    st.subheader("🏁 Closed Trades History")
    try:
        closed_df = pd.read_sql_query("SELECT timestamp, symbol, direction, close_price FROM closed_trades ORDER BY timestamp DESC LIMIT 15", conn)
        if not closed_df.empty: st.dataframe(closed_df, use_container_width=True, hide_index=True)
        else: st.info("No closed trades yet.")
    except: pass

st.divider()

# --- AUDIT TRAIL ---
st.subheader("🔍 Webhook Execution Audit Trail")
try:
    audits_df = pd.read_sql_query("SELECT * FROM webhook_audits ORDER BY timestamp DESC LIMIT 30", conn)
    if audits_df.empty: st.info("No webhooks processed yet.")
    else:
        for index, row in audits_df.iterrows():
            with st.expander(f"⏱️ {row['timestamp']} | 🎯 {row['symbol']} | ⚡ {row['action']}"):
                colA, colB, colC = st.columns(3)
                with colA: st.markdown("**📥 TradingView Payload**"); st.json(json.loads(row['tv_inbound']))
                with colB: st.markdown("**📤 Sent to Ghost**"); st.json(json.loads(row['ghost_outbound']))
                with colC:
                    st.markdown("**✅ Ghost Response**")
                    if "Status: 200" in row['ghost_response'] or "Success" in row['ghost_response']: st.success(row['ghost_response'])
                    else: st.error(row['ghost_response'])
except Exception as e: st.warning("Audit trail initializing...")

conn.close()
time.sleep(5)
st.rerun()