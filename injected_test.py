import sqlite3
from datetime import datetime, timedelta

def inject_dummy_data():
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    # 1. Insert Active Positions
    c.execute("INSERT INTO open_positions (symbol, direction, entry_price, qty) VALUES ('MNQ', 'long', 20150.25, 2)")
    c.execute("INSERT INTO open_positions (symbol, direction, entry_price, qty) VALUES ('XAUUSD', 'short', 2340.50, 1)")

    # 2. Insert Webhook Logs
    time_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')
    c.execute("INSERT INTO webhooks (timestamp, strategy, symbol, action, price, status) VALUES (?, 'AK_Test', 'MNQ', 'long', 20150.25, '✅ Executed')", (time_now,))
    c.execute("INSERT INTO webhooks (timestamp, strategy, symbol, action, price, status) VALUES (?, 'AK_Test', 'XAUUSD', 'short', 2340.50, '✅ Executed')", (time_now,))

    # 3. Build a Fake Equity Curve (Past 5 Days)
    base_date = datetime.now()
    pnl_sequence = [50.0, -20.0, 150.0, -40.0, 200.0] # Simulating daily wins/losses
    
    for i in range(5):
        past_date = (base_date - timedelta(days=4-i)).strftime('%Y-%m-%d')
        # Use REPLACE to overwrite today's default row if it exists
        c.execute("REPLACE INTO daily_risk (date, is_locked, trade_count, realized_pnl, highest_pnl) VALUES (?, 0, ?, ?, ?)", 
                  (past_date, i+2, pnl_sequence[i], pnl_sequence[i] + 25.0))

    conn.commit()
    conn.close()
    print("✅ Test data injected successfully!")

if __name__ == "__main__":
    inject_dummy_data()