# 📈 Robosh V6: Institutional Execution Node

A highly resilient, asynchronous Python execution engine designed to bridge TradingView strategy webhooks to Prop Firm futures accounts via the Ghost API. 

Engineered for extreme stability on Windows VPS environments and local machines, V6 is built explicitly to support **closed-source algorithms** utilizing fully dynamic TradingView placeholders, backed by a persistent SQLite state and automatic ngrok network tunneling.

---

## ✨ Core Architecture & Features

* **⚡ Zero-Latency Async Routing:** Built on FastAPI and `httpx`, the engine catches webhooks and executes Ghost API orders concurrently without bottlenecking the server.
* **🌐 Ngrok Auto-Tunneling:** Bypasses complex Windows Server firewalls and local router NATs. Python automatically spins up a secure `https` tunnel on boot and logs the public URL directly to your dashboard.
* **🔍 Interactive Webhook Audit Trail:** The UI features a clickable, expanding database of every single webhook. Instantly inspect what TradingView sent, what Python forwarded to Ghost, and Ghost's exact server response for flawless debugging.
* **✅ TV Truth Sync (Market Position):** The engine reads `{{strategy.market_position}}` directly. If TradingView's strategy goes flat, the engine overrides any mixed signals and enforces an absolute `exit` command, completely eliminating state desynchronization.
* **🔄 Intelligent Reversal Interpreter:** A secondary fallback for closed-source strategies. If a `long` position is actively open and a dynamic `short` signal arrives, the engine intercepts the reversal, overrides it to `"exit"`, and flattens the position to ensure prop-firm compliance.
* **🛡️ Global Directional Lock (Anti-Hedge):** Strictly prevents margin violations. If a Long position is open in one symbol (e.g., MNQ), the engine automatically rejects any incoming Short signals for other symbols until the portfolio is flat.
* **🗄️ SQLite State & Crash Recovery:** Replaces fragile `.txt` files with Write-Ahead Logging (WAL). If the system forcefully reboots, the engine instantly recovers its open positions and system state from the hard drive upon waking up.
* **📱 2-Way Telegram Integration (Optional):** Pushes live trade execution logs to your phone. Includes a `/status`, `/positions`, and `/closed` command menu, plus an automated End-of-Day (EOD) summary report at 5:00 PM EST.
* **🎛️ Decoupled Streamlit UI:** A localized web dashboard that reads the database in real-time. Features a live dashcam of engine logs, open position tracking, and a hardware-level 🛑 KILL SWITCH.

---

## 🛠️ Phase 1: Install Python

### Windows (VPS or Local PC)
1. Download the latest Python installer from [python.org](https://www.python.org/downloads/windows/).
2. Run the installer. **CRITICAL:** You must check the box at the bottom that says **"Add Python to PATH"** before clicking Install.
3. Open Command Prompt and type `python --version` to verify the installation.

### Linux (Ubuntu/Debian VPS)
\`\`\`bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
\`\`\`

---

## ⚙️ Phase 2: System Setup

**1. Clone or Copy the Files**
Ensure the following files are placed into a dedicated folder (e.g., `C:\TradingBot`):
* `main.py` (The Execution Engine)
* `dashboard.py` (The Streamlit UI)
* `config.json` (Your Credentials)
* `requirements.txt` (Dependencies)

**2. Open your Terminal/Command Prompt in that folder:**
\`\`\`cmd
cd C:\TradingBot
\`\`\`

**3. Create and Activate a Virtual Environment:**
\`\`\`cmd
python -m venv venv
venv\Scripts\activate
\`\`\`

**4. Install Dependencies:**
\`\`\`cmd
pip install -r requirements.txt
\`\`\`

---

## 🔐 Phase 3: Configuration (`config.json`)

Create or edit your `config.json` file to insert your API credentials. 
* *To get your free ngrok auth token, sign up at [dashboard.ngrok.com](https://dashboard.ngrok.com/).*
* *If you do not want to use Telegram, leave the token and chat ID as empty strings `""`.*

\`\`\`json
{
    "credentials": {
        "secret_passphrase": "YOUR_UNIQUE_PASSPHRASE",
        "telegram_bot_token": "YOUR_BOT_TOKEN_OR_LEAVE_BLANK",
        "telegram_chat_id": "YOUR_CHAT_ID_OR_LEAVE_BLANK",
        "ngrok_auth_token": "YOUR_NGROK_AUTH_TOKEN"
    },
    "ghost_urls": {
        "MNQ": "https://ghost.lucid.com/webhook/YOUR_MNQ_ENDPOINT",
        "MES": "https://ghost.lucid.com/webhook/YOUR_MES_ENDPOINT",
        "MYM": "https://ghost.lucid.com/webhook/YOUR_MYM_ENDPOINT"
    }
}
\`\`\`

---

## 🚀 Phase 4: Booting the Node

### Windows Environments (Auto-Recovering)
Create two text files in your trading folder, save them with a `.bat` extension, and double-click them to launch. These scripts automatically restart the servers if they crash.

**`run_engine.bat`**
\`\`\`cmd
@echo off
:loop
call venv\Scripts\activate
python main.py
echo Engine crashed. Rebooting in 5 seconds...
timeout /t 5
goto loop
\`\`\`

**`run_dashboard.bat`**
\`\`\`cmd
@echo off
:loop
call venv\Scripts\activate
streamlit run dashboard.py --server.port 8501
echo UI crashed. Rebooting in 5 seconds...
timeout /t 5
goto loop
\`\`\`

### Linux/Mac Environments
Run the services quietly in the background:
\`\`\`bash
nohup python3 main.py >/dev/null 2>&1 &
nohup streamlit run dashboard.py --server.port 8501 >/dev/null 2>&1 &
\`\`\`

---

## 📡 Phase 5: Dynamic TradingView Webhook Syntax

Because your strategy is closed-source, configure your TradingView Strategy Alert to use dynamic placeholders. The Python engine will catch these, evaluate the true state of the market, format the symbols (e.g., cleaning `MNQ1!` to `MNQ`), and route the order.

1. **Check your Streamlit Dashboard:** Look for the live log entry that says `🌐 NGROK TUNNEL ACTIVE`. 
2. **Webhook URL:** Paste that exact URL into TradingView, followed by `/tv-webhook` (e.g., `https://1a2b-3c.ngrok-free.app/tv-webhook`).
3. **Message Payload:**

\`\`\`json
{
  "passphrase": "YOUR_UNIQUE_PASSPHRASE",
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "market_position": "{{strategy.market_position}}",
  "price": {{close}}
}
\`\`\`
*(Note: Ensure you include the quotation marks `""` around the dynamic string variables).*

<img width="1889" height="857" alt="image" src="https://github.com/user-attachments/assets/4c81b111-a42c-41e1-bb79-f00c9b1a9a59" />


---

## 🛑 Troubleshooting

* **Database is Locked Error:** This occurs if multiple processes try to write to the SQLite database at the exact same millisecond. V6 utilizes WAL (Write-Ahead Logging) and sequential connection closing to mitigate this.
* **Ngrok 502 / ERR_NGROK_334:** If the engine crashes and fails to release the ngrok tunnel, the cloud server will block the new connection. Wait 60 seconds for the cloud to timeout, run `taskkill /f /im ngrok.exe`, and restart the engine.
* **UI Not Updating:** The Streamlit dashboard refreshes automatically every 5 seconds. If the Audit Trail is not appearing, ensure the browser tab is refreshed.
