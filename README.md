# ⚡ Robosh V6 Command Center

Robosh V6 is a hyper-optimized, asynchronous Python trading engine and monitoring dashboard designed for zero-latency execution of TradingView webhooks to prop-firm middleware (Ghost/Lucid). 

Engineered specifically for Windows Server VPS environments, it features intelligent state management, native prop-firm correlation hedging protection, and an interactive command center.

## 🚀 Core Features

* **Light-Speed Execution Pipeline:** Utilizes global HTTP connection pooling (`httpx`) and deferred SQLite disk-writes to achieve sub-millisecond internal processing. Trades are routed to the broker *before* logs are written to the disk.
* **Prop-Firm Correlation Engine:** Built-in protection against illegal cross-asset hedging. Automatically groups equities (MNQ, MES, MYM, M2K) and metals (MGC, SIL) to prevent margin-violation account blowouts.
* **Intelligent Reversal Handling:** Automatically intercepts 'flip' signals (e.g., Long to Short) and converts them to precise `EXIT` orders if a position is already open, maintaining strict directional discipline.
* **Zero-Blocking Telegram Integration:** Telegram notifications and command polling (`/status`, `/positions`, `/closed`) run on dedicated background threads, ensuring network drops or API delays never slow down trade execution.
* **Autonomous Recovery:** Uses a WAL-mode SQLite database to remember open positions. If the VPS force-reboots, the engine instantly recovers its state and automatically re-establishes the Ngrok webhook tunnel.
* **Institutional Dashboard:** A decoupled Streamlit UI featuring:
  * A live Plotly-rendered 24-hour global session timeline mapped to local VPS time.
  * A real-time, self-cleaning Forex Factory High-Impact News terminal.
  * A 1-click Manual Database Sync tool to clear broker-side manual interventions.

## 🛠️ Tech Stack

* **Core Engine:** Python 3.12+, FastAPI, Uvicorn, Asyncio
* **Dashboard:** Streamlit, Pandas, Plotly
* **Database:** SQLite3 (WAL Mode, `synchronous=NORMAL`)
* **Networking:** PyNgrok, HTTPX (Connection Pooling)
* **Notifications:** Telebot (PyTelegramBotAPI)

## ⚙️ Prerequisites & Installation

1. Clone or download the repository to your VPS (e.g., `C:\TradingBot`).
2. Install Python 3.12+ and create a virtual environment:
   ```cmd
   python -m venv venv
   call venv\Scripts\activate
   pip install fastapi uvicorn httpx pyngrok telebot streamlit pandas plotly pytz
   ```
3. Create a `config.json` file in the root directory with the following structure:
   ```json
   {
     "credentials": {
       "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
       "telegram_chat_id": "YOUR_CHAT_ID",
       "ngrok_auth_token": "YOUR_NGROK_TOKEN",
       "secret_passphrase": "YourWebhookPassword123"
     },
     "ghost_urls": {
       "MNQ": "[https://api.ghost.com/webhook/mnq_endpoint](https://api.ghost.com/webhook/mnq_endpoint)",
       "MYM": "[https://api.ghost.com/webhook/mym_endpoint](https://api.ghost.com/webhook/mym_endpoint)",
       "MGC": "[https://api.ghost.com/webhook/mgc_endpoint](https://api.ghost.com/webhook/mgc_endpoint)"
     }
   }
   ```

## 🖥️ Running the System

The system is split into two completely decoupled processes to guarantee UI rendering never impacts trade execution.

### 1. The Execution Engine (`main.py`)
Runs the FastAPI webhook listener on Port 8001 and handles all trade logic.
```cmd
call venv\Scripts\activate
python main.py
```
*Note: Uvicorn logging is suppressed to `error` level to hide harmless public internet port-scanning noise.*

### 2. The Command Center (`dashboard.py`)
Runs the Streamlit interactive monitoring dashboard on Port 8501.
```cmd
call venv\Scripts\activate
streamlit run dashboard.py --server.port 8501 --logger.level=error
```

## 🛡️ 24/7 VPS Deployment (Windows Server)

To ensure the bot survives Windows Server updates and automatic reboots:
1. Create shortcut `.lnk` files for your `run_engine.bat` and `run_dashboard.bat` scripts.
2. Press `Win + R`, type `shell:startup`, and hit Enter.
3. Drag and drop the shortcuts into the Startup folder.
4. **Important:** When leaving the VPS, click the **"X"** on the Remote Desktop window to disconnect. **Do not** click "Sign Out" or "Log Off" inside Windows, as this will terminate the background processes.

## 📊 TradingView Webhook Format

Configure your TradingView alerts to send the following JSON payload to your generated Ngrok URL (e.g., `https://<your-ngrok-url>.ngrok-free.app/tv-webhook`):

```json
{
  "passphrase": "YourWebhookPassword123",
  "action": "{{strategy.order.action}}",
  "market_position": "{{strategy.market_position}}",
  "symbol": "{{ticker}}",
  "price": {{close}},
  "qty": 1
}
```
<img width="1889" height="857" alt="image" src="https://github.com/user-attachments/assets/4c81b111-a42c-41e1-bb79-f00c9b1a9a59" />

## ⚠️ Important Notes on Latency
Robosh V6 processes internal logic in **< 5 milliseconds**. Any execution delays observed in the logs (typically 1.5s to 3.0s) represent the unavoidable physical round-trip time of the internet: `VPS -> Ghost Middleware -> Prop Firm Risk API -> CME Match Engine -> VPS`. Ensure your VPS is geographically located as close to the Chicago CME servers (Aurora, IL) as possible for optimal routing


---

## 🛑 Troubleshooting

* **Database is Locked Error:** This occurs if multiple processes try to write to the SQLite database at the exact same millisecond. V6 utilizes WAL (Write-Ahead Logging) and sequential connection closing to mitigate this.
* **Ngrok 502 / ERR_NGROK_334:** If the engine crashes and fails to release the ngrok tunnel, the cloud server will block the new connection. Wait 60 seconds for the cloud to timeout, run `taskkill /f /im ngrok.exe`, and restart the engine.
* **UI Not Updating:** The Streamlit dashboard refreshes automatically every 5 seconds. If the Audit Trail is not appearing, ensure the browser tab is refreshed.
