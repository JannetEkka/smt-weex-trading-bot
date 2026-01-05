# SMT AI Agent Architecture

## Smart Money Tracker - WEEX Trading Bot

---

## Overview

SMT is an AI-powered whale intelligence platform that transforms on-chain whale behavior into actionable trading signals. The system operates as a fully autonomous AI agent - perceiving whale movements, reasoning about their intent, predicting future actions, and executing trades without human intervention.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           SMT TRADING PIPELINE                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 1: PERCEPTION                                                    │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ Etherscan V2    │───▶│ Transaction     │───▶│ Whale Activity  │     │
│  │ API (chainid=1) │    │ Parser          │    │ Detector        │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                        │                │
│  Monitors: Binance, MEXC, Lido, Institutional Wallets  │                │
│  Filters: >100 ETH transactions in last 6 hours        │                │
└────────────────────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 2: CLASSIFICATION                                                │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ Feature         │───▶│ CatBoost        │───▶│ Category        │     │
│  │ Extraction      │    │ Classifier      │    │ Assignment      │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                        │                │
│  Categories: CEX_Wallet, Staker, Large_Holder,        │                │
│              DeFi_Trader, Miner                        │                │
└────────────────────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 3: SIGNAL GENERATION                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ Flow Analysis   │───▶│ Signal Matrix   │───▶│ Confidence      │     │
│  │ (In/Out/Net)    │    │ Lookup          │    │ Calculation     │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                        │                │
│  Outputs: LONG (85%) | SHORT (75%) | NEUTRAL (skip)   │                │
└────────────────────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 4: AI VALIDATION (Gemini 2.5 Flash)                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ Google Search   │───▶│ Context         │───▶│ Decision        │     │
│  │ Grounding       │    │ Analysis        │    │ Engine          │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                        │                │
│  Decisions: EXECUTE | WAIT | SKIP                      │                │
│  Searches: ETH news, whale movements, market events    │                │
└────────────────────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 5: EXECUTION                                                     │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ WEEX API        │───▶│ Order           │───▶│ Position        │     │
│  │ Authentication  │    │ Management      │    │ Monitoring      │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                        │                │
│  Pair: cmt_ethusdt | Leverage: 20x | Size: 10 USDT    │                │
└────────────────────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 6: LOGGING & FEEDBACK                                            │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ AI Decision     │───▶│ P&L Tracking    │───▶│ Model           │     │
│  │ Logs            │    │                 │    │ Improvement     │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│                                                                         │
│  Logs saved to: ai_logs/nightly_YYYYMMDD_HHMMSS.json                   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
Etherscan API (V2)
      │
      │ Raw transactions (normal, internal, erc20)
      ▼
Transaction Parser
      │
      │ Filtered transactions (>100 ETH, last 6 hours)
      ▼
Flow Analyzer
      │
      │ Net flow direction (inflow/outflow/mixed)
      ▼
Signal Generator
      │
      │ Trading signal + confidence
      ▼
Gemini Validator
      │
      │ Google Search grounding + decision
      ▼
WEEX Executor (if EXECUTE)
      │
      │ Order placement + management
      ▼
AI Logger
      │
      │ Full decision trail saved
      ▼
JSON Log File
```

---

## Layer Details

### Layer 1: Perception

**Purpose:** Continuously monitor Ethereum blockchain for whale wallet activity.

**Implementation:**
```python
# Etherscan V2 API call
params = {
    "chainid": 1,
    "module": "account",
    "action": "txlist",
    "address": whale_address,
    "sort": "desc",
    "apikey": ETHERSCAN_API_KEY
}
```

**Tracked Wallets:**
| Wallet | Category | Balance |
|--------|----------|---------|
| Binance 8 | CEX_Wallet | 538,622 ETH |
| Binance 14 | CEX_Wallet | 400,000 ETH |
| Binance 15 | CEX_Wallet | 350,000 ETH |
| Lido stETH | Staker | 9,000,000 ETH |
| Binance Cold | Large_Holder | 1,000,000 ETH |
| 1inch Router | DeFi_Trader | 50,000 ETH |

---

### Layer 2: Classification

**Purpose:** Determine behavioral profile of each whale.

**Signal Matrix:**
```python
SIGNAL_MAP = {
    "CEX_Wallet": {
        "inflow": {"signal": "BEARISH", "weight": 0.75},
        "outflow": {"signal": "BULLISH", "weight": 0.85}
    },
    "Staker": {
        "inflow": {"signal": "BULLISH", "weight": 0.6},
        "outflow": {"signal": "BEARISH", "weight": 0.7}
    },
    "Large_Holder": {
        "inflow": {"signal": "BULLISH", "weight": 0.65},
        "outflow": {"signal": "BEARISH", "weight": 0.7}
    },
    "Miner": {
        "inflow": {"signal": "NEUTRAL", "weight": 0.3},
        "outflow": {"signal": "BEARISH", "weight": 0.8}
    }
}
```

---

### Layer 3: Signal Generation

**Purpose:** Convert whale behavior into actionable trading signals.

**Confidence Calculation:**
```
Base Confidence = Signal Weight (0.3 - 0.85)
+ Transaction Count Boost (+0.05 to +0.10)
+ CEX Interaction Boost (+0.10)
= Final Confidence (capped at 0.95)
```

---

### Layer 4: AI Validation

**Purpose:** Validate signals against real-time market context.

**Gemini Configuration:**
```python
# Grounding search
grounding_config = GenerateContentConfig(
    tools=[Tool(google_search=GoogleSearch())],
    temperature=0.2
)

# JSON response
json_config = GenerateContentConfig(
    temperature=0.1,
    response_mime_type="application/json"
)
```

---

### Layer 5: Execution

**Purpose:** Execute validated trades on WEEX exchange.

**WEEX API Authentication:**
```python
def weex_sign(timestamp, method, path, body=""):
    message = timestamp + method.upper() + path + body
    sig = hmac.new(
        WEEX_API_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()
    return base64.b64encode(sig).decode()
```

**Order Types:**
- 1 = Open Long
- 2 = Open Short
- 3 = Close Long
- 4 = Close Short

---

### Layer 6: Logging

**Purpose:** Track all AI decisions for compliance and improvement.

**Log Structure:**
```json
{
  "run_id": "smt_nightly_20260105_135102",
  "timestamp": "2026-01-05T13:51:02Z",
  "pipeline_version": "SMT-ETH-v1.0",
  "trading_pair": "cmt_ethusdt",
  "steps": [...],
  "final_decision": "NO_TRADE|EXECUTED"
}
```

---

## Competitive Advantage

| Competitor | What They Do | SMT Difference |
|------------|--------------|----------------|
| Nansen | Whale activity dashboards | SMT predicts AND executes |
| Arkham | Labels and tracks wallets | SMT has autonomous trading |
| Whale Alert | Tweets large transactions | SMT classifies behavior |
| DeBank | Portfolio tracking | SMT predicts future moves |

**SMT's Edge:** While competitors show you what happened, SMT tells you what will happen next and can act on it automatically.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Data Ingestion | Python, requests, Etherscan V2 API |
| ML Model | CatBoost |
| AI Validation | Gemini 2.5 Flash + Google Search |
| Trade Execution | WEEX Contract API v2 |
| Hosting | Google Compute Engine (asia-southeast1-b) |
| Storage | Google BigQuery |

---

## Contact

- **Founder:** Jannet Ekka
- **Email:** jtech26smt@gmail.com
- **Project:** Smart Money Tracker (SMT)
- **GitHub:** https://github.com/JannetEkka/smt-weex-trading-bot

---

*Document Version: 1.0*
*Last Updated: January 2026*
*For: WEEX AI Wars - Alpha Awakens Hackathon*
