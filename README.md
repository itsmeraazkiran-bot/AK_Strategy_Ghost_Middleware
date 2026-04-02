# ⚡ Robosh V6.3 Command Center

Robosh V6.3 is a high-frequency, zero-latency Python trading engine engineered specifically to act as an asynchronous bridge between TradingView webhooks and prop-firm middleware (Ghost/Lucid). 

Designed for rigorous prop-firm environments, it features native anti-hedging correlation locks, deep slippage analytics, and an interactive command center that physically separates UI rendering from trade execution to guarantee maximum speed.

## 🚀 Core Architecture & Features

* **Zero-Latency Priority Execution:** The engine guarantees that incoming TradingView webhooks are processed, logic-checked, and fired to the broker *before* any local JSON parsing, SQLite database logging, or Telegram messaging occurs.
* **Thread-Isolated Health Heartbeat:** The system continually monitors its own uptime. To ensure 0ms of event-loop blocking, the database heartbeat is isolated on a background OS thread, meaning an incoming webhook will never wait for a local database write.
* **3-Way Tactical Execution Mode:**
    * **🟢 SAFE Mode:** Enforces strict directional discipline. Auto-flattens flips before reversing. Blocks illegal correlated margin-hedging (e.g., Long NQ + Short ES) to prevent account blowouts.
    * **⚡ BYPASS Mode:** Acts as a raw, unfiltered API bridge. Passes the order ID directly to Ghost. Disables all local safety checks and position tracking for manual override scenarios.
    * **🔴 STOPPED Mode:** Absolute kill switch. Webhooks are acknowledged (Status 200) so TradingView doesn't disable your alert, but the trade is ignored.
* **Ghost Post-Trade JSON Intelligence:** Asynchronously reads the Ghost broker response post-execution to extract:
    * Exact Realized PNL & Win/Loss Boolean.
    * TradingView Signal Price vs. Actual Broker Fill Price.
    * Exact execution slippage (in ticks/points).
    * True Quantity Fills (handling partials).
* **Hierarchical PNL Tracker:** The dashboard dynamically extracts all historical trading dates. Users can select any date to view a nested, interactive breakdown of their trades: **Grand Total ➡️ Global Session (Asian/London/NY) ➡️ Specific Symbol ➡️ Individual Trades.**
* **Slippage Analytics Wizard:** A live UI module that dynamically compares average slippage across assets, segmented by Safe vs. Bypass mode to identify latency costs and broker manipulation over time.

<img width="1365" height="945" alt="Main" src="https://github.com/user-attachments/assets/2a38702b-c0c0-4721-9f41-bf127a1f8945" />
<img width="1365" height="945" alt="S2" src="https://github.com/user-attachments/assets/0f07eb8f-c30c-41b0-ae8d-151f2830a774" />
<img width="1365" height="945" alt="S1" src="https://github.com/user-attachments/assets/687ad2a2-23b1-4184-ba32-31c9cb3d1864" />
<img width="1365" height="945" alt="S3" src="https://github.com/user-attachments/assets/7f385a9e-30d8-4651-88fd-a998e70bd2b5" />
  

## 🛠️ Tech Stack

* **Core Engine:** Python 3.12+, FastAPI, Uvicorn, Asyncio, OS Threading
* **Dashboard UI:** Streamlit, Pandas, Plotly
* **Database:** SQLite3 (WAL Mode, `synchronous=NORMAL` for high-concurrency)
* **Networking:** PyNgrok (Dynamic tunneling), HTTPX (Connection Pooling)

---

## ⚙️ Installation & Setup

Robosh V6.3 is fully cross-platform. Ensure you have **Python 3.12+** and **Git** installed on your system before beginning.

### 1. Clone & Prepare the Environment

**For Windows:**
Open Command Prompt and run the following lines one by one:
`git clone <your-repository-url> C:\TradingBot`
`cd C:\TradingBot`
`python -m venv venv`
`call venv\Scripts\activate`
`pip install fastapi uvicorn httpx pyngrok telebot streamlit pandas plotly pytz`

**For macOS / Linux:**
Open Terminal and run the following lines one by one:
`git clone <your-repository-url> ~/TradingBot`
`cd ~/TradingBot`
`python3 -m venv venv`
`source venv/bin/activate`
`pip install fastapi uvicorn httpx pyngrok telebot streamlit pandas plotly pytz`

### 2. Configure Credentials (config.json)
Create a `config.json` file in the root directory. This holds your routing endpoints and secure passphrase.

{
  "credentials": {
    "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "YOUR_CHAT_ID",
    "ngrok_auth_token": "YOUR_NGROK_TOKEN",
    "secret_passphrase": "YourSecretPassphrase"
  },
  "ghost_urls": {
    "MNQ": "https://api.ghost.com/webhook/mnq_endpoint",
    "MES": "https://api.ghost.com/webhook/mes_endpoint",
    "MGC": "https://api.ghost.com/webhook/mgc_endpoint"
  }
}

### 3. TradingView Webhook Configuration
Set your TradingView alert to send webhooks to your generated Ngrok URL (e.g., `https://<your-ngrok>.ngrok-free.app/tv-webhook`). 

The payload **must** match this exact structure to support the dual-action routing (Safe vs. Bypass):

{
  "passphrase": "YourSecretPassphrase",
  "strategy": "thirdparty_ak_strategy",
  "action": "{{strategy.order.id}}",
  "market_position": "{{strategy.market_position}}",
  "symbol": "{{ticker}}",
  "price": {{close}},
  "filter_action": "{{strategy.order.action}}"
}

> **Prop Firm Compliance Tip:** If you are trading with **The5ers**, remember that they strictly require all stop-loss orders to be visible in the trading platform. Ensure your TradingView strategy properties are configured to send hard Stop Loss commands along with the initial entry, rather than relying on mental or delayed script stops.

---

## 🖥️ Running the System

Robosh V6.3 uses a split-process architecture to ensure UI rendering never throttles trade execution.

### For Windows Users
We have provided a unified launcher. Simply double-click the **start_all.bat** file.
1. It launches the Execution Engine (`main.py`) in a background terminal.
2. It waits exactly 5 seconds for the database and OS Heartbeat to initialize.
3. It launches the Streamlit Dashboard (`dashboard.py`) in your default web browser.

### For macOS / Linux Users
Create a unified launcher script named `start_all.sh` in your root directory and run it.

---

## 🛡️ VPS Production Deployment Best Practices

If deploying to a 24/7 Virtual Private Server:
* **Windows Server VPS:** Create shortcut `.lnk` files for your `.bat` scripts and place them in the `shell:startup` folder. **Crucial:** Always disconnect from your VPS by clicking the "X" on the Remote Desktop window. Using "Sign Out" or "Log Off" will terminate the bot's background processes.
* **Linux VPS (Ubuntu/Debian):** It is highly recommended to run the `start_all.sh` script inside a `tmux` or `screen` session, or configure it as a `systemd` background service to ensure it survives SSH disconnections.
