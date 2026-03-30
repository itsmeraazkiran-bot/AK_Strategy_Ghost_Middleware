# 📈 Robosh V5: Institutional Prop Firm Execution Node

An ultra-low latency, asynchronous Python execution engine designed to bridge TradingView strategies with Prop Firm futures accounts via Ghost Webhooks. 

Engineered specifically for Windows VPS environments and strict Prop Firm compliance (e.g., Lucid Trading), this system features a decoupled Streamlit Command Center, an embedded Telegram Bot, and advanced institutional risk management protocols.

## 🚀 The V5 Architecture Edge
* **Zero-Latency Async Execution:** Utilizes `httpx` and FastAPI `BackgroundTasks` to process webhooks concurrently, eliminating execution bottlenecks and RAM locking.
* **Fort Knox Security:** Built-in middleware instantly drops HTTP requests from any IP address not officially registered to TradingView's cloud servers.
* **RAM-Optimized Database:** Uses SQLite with Write-Ahead Logging (WAL) and automated End-of-Day (EOD) pruning to keep memory footprint under 150MB.

## 🛡️ Quantitative Risk & Alpha Filters
* **Dynamic Volatility Sizing (ATR):** Automatically calculates contract sizing based on a fixed dollar risk and the real-time Average True Range (ATR) passed from TradingView.
* **Choppy Market Filter (ADX):** Rejects entries when the Average Directional Index (ADX) falls below 20, protecting capital during trendless, mean-reverting regimes.
* **Intelligent Anti-Hedge Matrix:** Dynamically prevents correlated margin violations across predefined asset classes (e.g., blocking an MES Short if MNQ is Long).
* **Prop Firm Auto-Flatten:** A scheduled background loop that forcefully flattens all open positions at exactly 4:44 PM EST to guarantee compliance with intraday margin rules.
* **Ghost 3-Strike Retry Protocol:** Automatically catches dropped packets and 502 Bad Gateway errors, retrying execution to prevent UI/Broker desyncs.

## 📊 Supported Instruments
* **Equities:** MNQ, MES, MYM, M2K
* **Metals:** MGC, SIL

## 🛠️ Installation & Setup

**1. Clone the Repository**
Open your Windows Server Administrator Command Prompt:
\`\`\`cmd
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
\`\`\`

**2. Install Dependencies**
\`\`\`cmd
python -m pip install fastapi uvicorn requests httpx streamlit pandas pyTelegramBotAPI pytz
\`\`\`

**3. Configure Your Secrets**
* Rename the template file: `ren config.sample.json config.json`
* Input your Telegram tokens, Ghost webhook URLs, and TV Passphrase into `config.json`.
* Adjust your `mumbai_slippage_ticks` and `commission_round_trip` based on your physical latency to the CME.

**4. Boot the Node**
Run the background execution engine and the Streamlit UI:
\`\`\`cmd
start run_engine.bat
start run_dashboard.bat
\`\`\`
*(Engine runs silently on Port `8001`; UI is accessible on Port `8501`).*

## 📡 TradingView Webhook Configuration

To fully utilize the V5 Quantitative Filters, your TradingView alerts must pass the ADX and ATR values in the JSON payload. If they are not included, the engine will safely default to 1 contract and bypass the chop filter.

**Standard Entry Payload:**
\`\`\`json
{
  "passphrase": "YOUR_SECRET_PASSPHRASE",
  "action": "long",
  "symbol": "MNQ",
  "price": {{close}},
  "adx": {{plot("ADX")}},
  "atr": {{plot("ATR")}}
}
\`\`\`
*(Note: Ensure `"ADX"` and `"ATR"` match the exact plot names in your Pine Script).*

**The 1-Minute Live PnL Ping:**
Set this alert to fire "Once Per Bar" on a 1-minute chart (Disable App/Email notifications in TradingView to prevent spam).
\`\`\`json
{
  "passphrase": "YOUR_SECRET_PASSPHRASE",
  "action": "ping",
  "symbol": "MNQ",
  "price": {{close}}
}
\`\`\`

## 📋 Standard Operating Procedures (SOPs)

**Weekly Reconciliation (Friday @ 5:05 PM EST)**
To prevent "PnL Drift" caused by latency variations, perform this audit weekly:
1. Export the weekly trade log from your Prop Firm dashboard.
2. Compare the Prop Firm Net PnL to the Robosh Shadow PnL (delivered via Telegram EOD reports).
3. If Robosh is over-reporting profits, increase the `mumbai_slippage_ticks` value in `config.json` before Sunday open.
