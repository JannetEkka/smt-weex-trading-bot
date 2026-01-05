# SMT - Smart Money Tracker

AI-Powered Whale Behavior Trading Bot for WEEX Exchange

**Team:** SMT | **Lead Engineer:** Jannet Ekka | **Email:** jtech26smt@gmail.com

---

## What is SMT?

Smart Money Tracker (SMT) is an autonomous AI trading bot that generates trading signals by analyzing Ethereum whale wallet behavior. Unlike traditional bots that rely on technical indicators (RSI, MACD), SMT tracks the "smart money" - large wallets whose movements often precede market shifts.

**Our Edge:** While competitors show you what happened, SMT tells you what will happen next and can act on it automatically.

---

## Architecture

```
+------------------+     +----------------------+     +--------------------+
|   PERCEPTION     | --> |   CLASSIFICATION     | --> |    PREDICTION      |
|   Etherscan V2   |     |   Behavior Classifier|     |    What/When/How   |
|   ETH Mainnet    |     |   CatBoost Model     |     |    Direction       |
+------------------+     +----------------------+     +--------------------+
                                                              |
                                                              v
+------------------+     +----------------------+     +--------------------+
|    FEEDBACK      | <-- |      ACTION          | <-- |   AI VALIDATION    |
|    P&L Tracking  |     |   WEEX Execution     |     |   Gemini 2.5 Flash |
|    AI Logs       |     |   cmt_ethusdt        |     |   Google Search    |
+------------------+     +----------------------+     +--------------------+
```

---

## How It Works

### 1. Whale Monitoring (Perception Layer)
- Monitors 8+ high-value Ethereum wallets (Binance, MEXC, Lido, etc.)
- Fetches transactions via Etherscan V2 API every 6 hours
- Filters for significant movements (>100 ETH)

### 2. Signal Generation (Classification Layer)
Whales are classified into behavioral categories with specific signal logic:

| Category | Inflow Signal | Outflow Signal |
|----------|---------------|----------------|
| CEX_Wallet | Bearish (75%) | Bullish (85%) |
| Staker | Bullish (60%) | Bearish (70%) |
| Large_Holder | Bullish (65%) | Bearish (70%) |
| DeFi_Trader | Neutral (40%) | Neutral (40%) |
| Miner | Neutral (30%) | Bearish (80%) |

### 3. AI Validation (Gemini 2.5 Flash + Google Search Grounding)
Before executing any trade, the signal is validated against real-time market context:
- Searches current ETH news and whale movements
- Checks for conflicting signals
- Returns EXECUTE, WAIT, or SKIP decision

### 4. Trade Execution (WEEX API)
- Market orders on cmt_ethusdt
- 20x leverage (competition maximum)
- Automatic position management

---

## AI Decision Example

```json
{
  "run_id": "smt_nightly_20260105_135102",
  "pipeline_version": "SMT-ETH-v1.0",
  "steps": [
    {
      "step": "SIGNAL_GENERATED",
      "data": {
        "whale": "Binance 15",
        "signal": "LONG",
        "confidence": "85%",
        "reasoning": "CEX_Wallet (Binance 15) shows outflow of 770.00 ETH. Signal: BULLISH."
      }
    },
    {
      "step": "GEMINI_VALIDATION",
      "data": {
        "decision": "WAIT",
        "signal": "NEUTRAL",
        "reasoning": "Conflicting signals detected. A larger whale converted 22,000 ETH to WBTC (bearish). The 770 ETH outflow is overshadowed by this larger bearish movement."
      }
    }
  ],
  "final_decision": "NO_TRADE"
}
```

**The AI correctly identified conflicting signals and chose to WAIT rather than execute a potentially losing trade.**

---

## Technology Stack

| Component | Technology |
|-----------|------------|
| Data Source | Etherscan V2 API (chainid=1) |
| ML Model | CatBoost (5-class classifier) |
| AI Validation | Gemini 2.5 Flash + Google Search |
| Trade Execution | WEEX Contract API v2 |
| Hosting | Google Compute Engine |
| Storage | Google BigQuery |

---

## Risk Management

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max Leverage | 20x | Competition limit |
| Position Size | 10 USDT | Conservative sizing |
| Min Confidence | 60% | Skip weak signals |
| Stop Loss | 2% | Limit downside |
| Take Profit | 5% | 2.5:1 reward ratio |

---

## Setup Instructions

### Prerequisites
- Python 3.11+
- Google Cloud account with Vertex AI enabled
- WEEX API credentials

### Installation

```bash
# Clone repository
git clone https://github.com/JannetEkka/smt-weex-trading-bot.git
cd smt-weex-trading-bot

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_GENAI_USE_VERTEXAI=True
export WEEX_API_KEY=your-key
export WEEX_API_SECRET=your-secret
export WEEX_API_PASSPHRASE=your-passphrase
export ETHERSCAN_API_KEY=your-key
```

### Running the Bot

```bash
# Test pipeline (no real trades)
python test_all.py

# Run nightly trade
python smt_nightly_trade_v2.py
```

---

## File Structure

```
smt-weex-trading-bot/
├── smt_nightly_trade_v2.py    # Main trading pipeline
├── weex_trader.py             # WEEX API wrapper
├── classifier.py              # Whale behavior classifier
├── test_all.py                # Pre-flight tests
├── requirements.txt           # Dependencies
├── ai_logs/                   # AI decision logs
│   └── nightly_*.json
└── docs/
    ├── policy_description.md
    ├── ai_participation.md
    └── smt_ai_agent_architecture.md
```

---

## What Makes SMT Unique

| Other Bots | SMT |
|------------|-----|
| Technical indicators (RSI, MACD) | On-chain whale behavior |
| Price prediction models | Behavioral classification |
| React to price changes | Predict before price moves |
| Single decision source | Dual-AI validation (CatBoost + Gemini) |

**Our signals are fundamentally different from any other participant because we don't look at price - we look at what smart money is doing.**

---

## Links

- **GitHub:** https://github.com/JannetEkka/smt-weex-trading-bot
- **GCP Project:** smt-weex-2025
- **Hackathon:** WEEX AI Wars - Alpha Awakens

---

## License

MIT License - See LICENSE file

---

*Built for WEEX AI Wars: Alpha Awakens Hackathon*
*December 2024 - January 2025*
