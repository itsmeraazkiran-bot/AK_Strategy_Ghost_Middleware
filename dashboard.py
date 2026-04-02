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

def get_trading_session(timestamp_str):
    """Categorizes a local VPS timestamp into the major global trading session."""
    try:
        local_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        local_tz = datetime.now().astimezone().tzinfo
        utc_dt = local_dt.replace(tzinfo=local_tz).astimezone(pytz.utc)
        hour = utc_dt.hour
        if 8 <= hour < 13: return "🇬🇧 London Session"
        elif 13 <= hour < 22: return "🇺🇸 New York Session"
        else: return "🇯🇵/🇦🇺 Asian Session"
    except: return "Unknown Session"

def get_prop_firm_day(timestamp_str):
    """
    Translates VPS Time to Prop Firm CME Trading Days.
    Prop Firm days roll over at 5:00 PM EST (New York Time).
    Trades placed after 5:00 PM EST count towards the NEXT calendar day's PNL.
    """
    try:
        local_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        local_tz = datetime.now().astimezone().tzinfo
        utc_dt = local_dt.replace(tzinfo=local_tz).astimezone(pytz.utc)
        ny_tz = pytz.timezone('America/New_York')
        ny_dt = utc_dt.astimezone(ny_tz)
        
        # If the trade occurred at or after 17:00 (5 PM NY Time), it belongs to tomorrow's prop firm day.
        if ny_dt.hour >= 17:
            trading_day = (ny_dt + timedelta(days=1)).date()
        else:
            trading_day = ny_dt.date()
            
        return trading_day.strftime('%Y-%m-%d')
    except:
        return timestamp_str[:10]

st.title("⚡ Robosh V6 Command Center")

conn = get_db()

# --- 💓 ENGINE HEALTH CHECK ---
try:
    hb_data = conn.execute("SELECT value FROM system_state WHERE key='last_heartbeat'").fetchone()
    if hb_data and hb_data[0] != 'UNKNOWN':
        last_hb = datetime.strptime(hb_data[0], '%Y-%m-%d %H:%M:%S')
        if (datetime.now() - last_hb).total_seconds() < 15:
            st.success("### 🟢 SYSTEM HEALTH: ONLINE\nEngine is actively running and listening for webhooks.")
        else:
            st.error(f"### 🔴 SYSTEM HEALTH: OFFLINE\nEngine is not responding! (Last heartbeat: {hb_data[0]}). Restart `run_engine.bat`.")
    else:
        st.warning("### 🟡 SYSTEM HEALTH: UNKNOWN\nAwaiting engine heartbeat...")
except Exception as e:
    st.error("### 🔴 SYSTEM HEALTH: OFFLINE\nCould not read engine status from database.")

try: mode = conn.execute("SELECT value FROM system_state WHERE key='execution_mode'").fetchone()[0]
except: mode = "SAFE"

st.divider()

# --- ⚙️ EXECUTION MODE CONTROL PANEL ---
st.markdown("### ⚙️ Engine Execution Mode")
col1, col2, col3 = st.columns(3)

with col1:
    if mode == 'SAFE': st.info("### 🛡️ SAFE MODE ACTIVE\nAnti-Hedge & Reversal logic is **ON**.")
    else:
        if st.button("🛡️ Switch to SAFE MODE", use_container_width=True):
            conn.execute("UPDATE system_state SET value='SAFE' WHERE key='execution_mode'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🛡️ ENGINE SET TO SAFE MODE (Checks Enabled)')")
            conn.commit(); st.rerun()

with col2:
    if mode == 'BYPASS': st.warning("### ⚡ BYPASS ACTIVE\nRaw signals passing directly to broker.")
    else:
        if st.button("⚡ Switch to BYPASS MODE", use_container_width=True):
            conn.execute("UPDATE system_state SET value='BYPASS' WHERE key='execution_mode'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '⚡ ENGINE SET TO BYPASS MODE (Raw Passthrough)')")
            conn.commit(); st.rerun()

with col3:
    if mode == 'STOPPED': st.error("### 🛑 ENGINE STOPPED\nIgnoring all incoming webhooks.")
    else:
        if st.button("🛑 STOP SENDING", use_container_width=True):
            conn.execute("UPDATE system_state SET value='STOPPED' WHERE key='execution_mode'")
            conn.execute("INSERT INTO logs (timestamp, message) VALUES (datetime('now', 'localtime'), '🛑 ENGINE STOPPED (Ignoring all signals)')")
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
            utc_volume_curve = {0: 25, 1: 30, 2: 30, 3: 25, 4: 20, 5: 20, 6: 25, 7: 40, 8: 60, 9: 65, 10: 60, 11: 55, 12: 60, 13: 85, 14: 100, 15: 95, 16: 80, 17: 60, 18: 50, 19: 45, 20: 35, 21: 25, 22: 15, 23: 20}
            x_times, y_sydney, y_tokyo, y_london, y_ny, y_vol = [], [], [], [], [], []
            midnight_local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            nan = float('nan') 
            
            for i in range(25):
                hr_local = midnight_local + timedelta(hours=i)
                hr_utc = hr_local.astimezone(pytz.utc).hour
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

            fig.add_trace(go.Scatter(x=[vps_now, vps_now], y=[0.5, 4.5], mode='lines', line=dict(color='red', width=2, dash='dash'), hoverinfo='none', showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[vps_now, vps_now], y=[0, 100], mode='lines', line=dict(color='red', width=2, dash='dash'), hoverinfo='none', showlegend=False), row=2, col=1)

            fig.update_layout(height=350, margin=dict(l=0, r=0, t=20, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False, hovermode="x unified",
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
                    countdown = f"In {hours}h {mins}m" if delta.days == 0 and hours < 24 else "Tomorrow"
                    impact_icon = "🔥" if e['impact'] == "High" else "⚠️"
                    upcoming_events.append({"Time": event_time.strftime('%I:%M %p'), "T-Minus": countdown, "Impact": impact_icon, "Asset": e['currency'], "Event": e['title'], "FCST": e.get('forecast', '-'), "PREV": e.get('previous', '-')})
            if upcoming_events: st.dataframe(pd.DataFrame(upcoming_events), hide_index=True, use_container_width=True)
            else: st.success("✅ No high-impact events remaining for the next 48 hours.")
        else: st.caption("Awaiting data...")
    except Exception as e: st.caption(f"Error loading events: {e}")

st.divider()

# --- 🧠 AI MARKET BIAS & FUNDAMENTALS ---
st.markdown("### 🧠 Daily Market Bias & Drivers")
try:
    bias_data = conn.execute("SELECT value FROM system_state WHERE key='market_bias'").fetchone()
    if bias_data:
        bias = json.loads(bias_data[0])
        b_col1, b_col2 = st.columns(2)
        with b_col1:
            st.info(f"**Nasdaq (MNQ/NQ)** | Current Price: {bias.get('Nasdaq (MNQ)', {}).get('price', '')}")
            st.markdown(f"**Trend:** {bias.get('Nasdaq (MNQ)', {}).get('trend', '')} ({bias.get('Nasdaq (MNQ)', {}).get('change', '')})")
            for news in bias.get('Nasdaq (MNQ)', {}).get('news', []): st.caption(f"📰 {news}")
        with b_col2:
            st.warning(f"**Gold (MGC/GC)** | Current Price: {bias.get('Gold (MGC)', {}).get('price', '')}")
            st.markdown(f"**Trend:** {bias.get('Gold (MGC)', {}).get('trend', '')} ({bias.get('Gold (MGC)', {}).get('change', '')})")
            for news in bias.get('Gold (MGC)', {}).get('news', []): st.caption(f"📰 {news}")
    else: st.caption("Awaiting bias calculation...")
except Exception as e: st.caption(f"Error loading bias: {e}")

st.divider()

# --- TRADING ACTIVITY ---
col_log, col_pos = st.columns([1.2, 1])

with col_log:
    st.subheader("📡 Live Engine Logs")
    if st.button("🗑️ Clear Logs", key="clear_logs"):
        conn.execute("DELETE FROM logs")
        conn.commit(); st.rerun()
    try:
        logs_df = pd.read_sql_query("SELECT timestamp, message FROM logs ORDER BY timestamp DESC LIMIT 50", conn)
        if not logs_df.empty: st.dataframe(logs_df, use_container_width=True, hide_index=True, height=500)
        else: st.info("No logs yet. Awaiting signals...")
    except: st.warning("Database initializing...")

with col_pos:
    st.subheader("🎯 Open Positions")
    try:
        try: open_df = pd.read_sql_query("SELECT symbol, direction, tv_price, broker_price, mode FROM positions", conn)
        except: open_df = pd.read_sql_query("SELECT symbol, direction, entry_price FROM positions", conn)
        
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
        else: st.info("No active positions tracked.")
    except Exception as e: pass

    # --- PNL SESSION TRACKER (PROP FIRM HIERARCHICAL) ---
    st.subheader("🏁 Prop Firm PNL Tracker & Consistency")
    try:
        try: closed_df = pd.read_sql_query("SELECT timestamp, symbol, direction, tv_price, broker_price, pnl, is_win, slippage, exit_reason, mode FROM closed_trades ORDER BY timestamp DESC", conn)
        except: closed_df = pd.read_sql_query("SELECT timestamp, symbol, direction, close_price, pnl, is_win, mode FROM closed_trades ORDER BY timestamp DESC", conn)
        
        if not closed_df.empty:
            closed_df['pnl'] = pd.to_numeric(closed_df['pnl'], errors='coerce').fillna(0.0)
            
            # --- VITAL: Map trades to official Prop Firm Calendar Day ---
            closed_df['Prop_Firm_Day'] = closed_df['timestamp'].apply(get_prop_firm_day)
            
            available_dates = closed_df['Prop_Firm_Day'].unique().tolist()
            current_pf_day = get_prop_firm_day(vps_now.strftime('%Y-%m-%d %H:%M:%S'))
            
            if current_pf_day not in available_dates: available_dates.insert(0, current_pf_day)
            
            col_date, _ = st.columns([1.5, 1])
            with col_date:
                selected_date = st.selectbox("📅 Prop Firm Trading Day (CME Boundaries)", available_dates, index=0)
            
            day_df = closed_df[closed_df['Prop_Firm_Day'] == selected_date].copy()
            
            if not day_df.empty:
                day_df['Session'] = day_df['timestamp'].apply(get_trading_session)
                
                def style_pnl(val):
                    try: return 'color: #20BF6B' if float(val) > 0 else 'color: #FC427B' if float(val) < 0 else ''
                    except: return ''

                # Hierarchical UI: Group by Session -> Symbol
                for session_name, session_df in day_df.groupby('Session', sort=False):
                    session_pnl = session_df['pnl'].sum()
                    session_icon = "🟢" if session_pnl >= 0 else "🔴"
                    
                    with st.expander(f"{session_icon} {session_name} | Session Subtotal: ${session_pnl:.2f}", expanded=True):
                        for symbol_name, symbol_df in session_df.groupby('symbol', sort=False):
                            symbol_pnl = symbol_df['pnl'].sum()
                            sym_color = "green" if symbol_pnl >= 0 else "red"
                            st.markdown(f"**🔹 {symbol_name}** (Symbol PNL: :{sym_color}[${symbol_pnl:.2f}])")
                            
                            display_df = symbol_df.drop(columns=['Prop_Firm_Day', 'Session'])
                            st.dataframe(display_df.style.map(style_pnl, subset=['pnl']), use_container_width=True, hide_index=True)
                
                # Bottom Grand Total
                st.divider()
                grand_total = day_df['pnl'].sum()
                if grand_total >= 0: st.success(f"## 💵 PROP FIRM DAILY TOTAL: +${grand_total:.2f}")
                else: st.error(f"## 🔻 PROP FIRM DAILY TOTAL: -${abs(grand_total):.2f}")
            else:
                st.info(f"No trades exist for Prop Firm Day {selected_date}.")
        else: st.info("No closed trades yet.")
    except Exception as e: st.caption(f"Awaiting initial trades for PNL history... ({e})")

st.divider()

# --- 📉 SLIPPAGE & EXECUTION ANALYTICS WIZARD ---
st.subheader("📉 Slippage & Execution Analytics Wizard")
try:
    slip_df = pd.read_sql_query("SELECT symbol, mode, slippage FROM closed_trades WHERE slippage IS NOT NULL AND mode IS NOT NULL", conn)
    if not slip_df.empty:
        slip_df['slippage'] = pd.to_numeric(slip_df['slippage'], errors='coerce').fillna(0.0)
        col_safe, col_bypass = st.columns(2)
        safe_data = slip_df[slip_df['mode'] == 'SAFE']
        bypass_data = slip_df[slip_df['mode'] == 'BYPASS']
        
        with col_safe:
            st.markdown("#### 🟢 SAFE MODE")
            if not safe_data.empty:
                avg_slip = safe_data['slippage'].mean()
                max_slip = safe_data['slippage'].max()
                st.metric("Average Slippage", f"{avg_slip:.2f} pts", delta=f"Max: {max_slip:.2f}", delta_color="inverse")
            else: st.caption("No SAFE trades recorded with slippage yet.")
            
        with col_bypass:
            st.markdown("#### ⚡ BYPASS MODE")
            if not bypass_data.empty:
                avg_slip = bypass_data['slippage'].mean()
                max_slip = bypass_data['slippage'].max()
                st.metric("Average Slippage", f"{avg_slip:.2f} pts", delta=f"Max: {max_slip:.2f}", delta_color="inverse")
            else: st.caption("No BYPASS trades recorded with slippage yet.")
            
        st.markdown("**Asset Breakdown (Avg Slippage by Mode)**")
        sym_summary = slip_df.groupby(['symbol', 'mode'])['slippage'].mean().unstack().fillna(0.0)
        st.dataframe(sym_summary.style.format("{:.2f}"), use_container_width=True)
        
    else: st.info("No slippage data recorded in the database yet. Execute a trade to begin tracking.")
except Exception as e: st.caption("Awaiting initial trades to generate Slippage Wizard...")

st.divider()

# --- AUDIT TRAIL ---
st.subheader("🔍 Webhook Execution Audit Trail")
if st.button("🗑️ Clear Audits", key="clear_audits"):
    conn.execute("DELETE FROM webhook_audits")
    conn.commit(); st.rerun()

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