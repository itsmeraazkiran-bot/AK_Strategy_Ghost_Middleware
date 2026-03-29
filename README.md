# 📈 Robosh V3: Institutional Prop Firm Execution Node

An ultra-low latency, asynchronous Python execution engine designed to bridge TradingView strategies with Prop Firm MT5 accounts via Ghost Webhooks. 

Engineered specifically for Windows VPS environments (8GB RAM optimized), this system features a decoupled Streamlit Command Center, an embedded Telegram Bot, and strict institutional risk management protocols to protect funded accounts.

---

## 🚀 Core Architecture & Features

### 1. Zero-Latency Asynchronous Execution
Built on **FastAPI** and **HTTPX (HTTP/2)**, the engine processes incoming TradingView signals in under 5 milliseconds. Heavy network execution (Ghost routing, Telegram alerts, DB writes) is offloaded to background threads, ensuring the engine never blocks or queues signals during high-volatility events like the Nasdaq open.

### 2. The "Trust but Verify" Protocol
* **Ghost 3-Strike Retry:** Automatically catches dropped packets (502/504 errors) from the MT5 Ghost bridge and retries execution 3 times, 500ms apart.
* **Desync Dead-Letter:** If Ghost completely fails to respond, the engine refuses to write the trade to the local database, instantly fires a Telegram Desync Alert, and physically locks the symbol's sandbox to prevent ghost positions.

### 3. Advanced Risk Management
* **Intelligent Anti-Hedge Matrix:** Dynamically prevents correlated margin violations based on configurable `hedge_groups` (e.g., blocking an MES Short if MNQ is Long).
* **Dual-Switch Shutdown:** * **🛑 Hard Kill:** Panic button that instantly flattens all open positions and locks the system.
    * **🌙 Soft Fade:** Rejects new entries but allows existing runners to hit their natural exit signals.
* **Live Configuration:** Adjust Daily Max Loss, Profit Targets, and Trailing Stops via the UI without rebooting the engine.

### 4. Zero-Data-Feed Floating PnL
Instead of paying for expensive live data feeds, the engine processes silent **1-minute heartbeat pings** directly from TradingView. This updates the local SQLite database with current market prices, allowing the Streamlit UI to calculate live Floating PnL and trigger trailing stops independently of the execution engine.

### 5. Automated EOD Operations
To comply with standard prop firm rules (e.g., 4:45 PM EST closures):
* The engine runs a background loop that generates a daily CSV trade report at **4:50 PM EST**.
* The report is sent via Telegram.
* The local SQLite database is automatically pruned (logs older than 7 days are deleted) to maintain a footprint of <150MB.

---

## 🛠️ Installation & Setup

### 1. Prerequisites
* Python 3.12+ installed on Windows Server.
* `git` installed.

### 2. Clone & Install
\`\`\`cmd
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
python -m pip install fastapi uvicorn requests httpx[http2] streamlit pandas pyTelegramBotAPI pytz
\`\`\`

### 3. Configure Secrets
1. Rename `config.sample.json` to `config.json`.
2. Insert your TradingView `secret_passphrase`, Telegram tokens, and Ghost Webhook URLs.
3. *Note: `config.json` is protected by `.gitignore` and will never be uploaded to the cloud.*

### 4. Boot the Node
Run the provided batch scripts (ideally mapped to Windows Task Scheduler for auto-boot on system restart):
\`\`\`cmd
start run_engine.bat
start run_dashboard.bat
\`\`\`
* **FastAPI Engine:** Runs silently on Port `8001`.
* **Streamlit UI:** Accessible on Port `8501`.

---

## 📡 TradingView Alert Formatting

### Standard Entry/Exit Signal
Trigger: *Once Per Bar Close*
\`\`\`json
{
  "passphrase": "YOUR_SECRET_PASSPHRASE",
  "action": "long", 
  "symbol": "MNQ",
  "price": {{close}}
}
\`\`\`
*(Valid actions: `long`, `short`, `buy`, `sell`, `exit`, `close`, `flat`)*

### The 1-Minute Heartbeat Ping
Trigger: *Once Per Bar (on a 1-Minute Chart)*
\`\`\`json
{
  "passphrase": "YOUR_SECRET_PASSPHRASE",
  "action": "ping",
  "symbol": "MNQ",
  "price": {{close}}
}
\`\`\`
*(Ensure "Notify on App" and "Send Email" are unchecked in TradingView to prevent mobile spam).*

---
*Disclaimer: This software is designed for execution routing and risk management. It is the user's responsibility to ensure compliance with their specific Proprietary Trading Firm's terms of service regarding automated trading and API usage.*
