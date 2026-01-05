# SMT AI Participation Description

## AI/ML Components Overview

Smart Money Tracker uses a dual-AI architecture combining traditional machine learning with large language model validation:

1. **CatBoost Classifier** - Whale behavior classification
2. **Gemini 2.5 Flash** - Signal validation with real-time Google Search grounding

---

## Component 1: CatBoost Whale Behavior Classifier

### Purpose
Classify Ethereum whale wallets into behavioral categories based on their on-chain transaction patterns.

### Model Specifications

| Attribute | Value |
|-----------|-------|
| Algorithm | CatBoost (Gradient Boosting) |
| Task | Multi-class classification |
| Classes | 5 (Miner, Staker, Large_Holder, DeFi_Trader, CEX_Wallet) |
| Features | 32 engineered features |
| Training Samples | 646 labeled whales |
| Validation | 5-fold stratified cross-validation |
| Performance | 67% macro F1-score |

### Feature Engineering

**Transaction Volume Features:**
- total_txs, incoming_count, outgoing_count
- outgoing_volume_eth, incoming_volume_eth, net_flow_eth

**Transaction Type Features:**
- erc20_ratio, nft_ratio, internal_ratio
- normal_tx_count, erc20_tx_count

**Temporal Features:**
- avg_time_between_tx_hours, activity_span_days
- tx_per_day, business_hour_ratio

**Network Features:**
- unique_counterparties, defi_interactions
- unique_tokens, stablecoin_ratio

**Value Features:**
- avg_tx_value_eth, max_tx_value_eth
- large_tx_ratio, balance_eth_log

### Per-Class Performance

| Class | Precision | Recall | F1-Score |
|-------|-----------|--------|----------|
| Miner | 76% | 90% | 82% |
| CEX_Wallet | 78% | 88% | 82% |
| Staker | 78% | 69% | 73% |
| DeFi_Trader | 53% | 49% | 51% |
| Large_Holder | 46% | 44% | 45% |

### Training Data Sources

- **Etherscan Labels:** Official labeled addresses from etherscan.io/labelcloud
- **Categories:** CEX wallets, Stakers, Institutional, DeFi protocols, Miners
- **Transactions:** Fetched via Etherscan V2 API (chainid=1)
- **Timeframe:** Last 90 days of transaction history per wallet

---

## Component 2: Gemini 2.5 Flash Validator

### Purpose
Validate trading signals against real-time market context using Google Search grounding before execution.

### Why Gemini Validation?

Even high-confidence classification signals can be wrong if market context has changed. Gemini acts as a "sanity check":

- Searches current news about ETH
- Finds conflicting whale movements
- Identifies market events that override signals
- Prevents trades during high-uncertainty periods

### Implementation

```python
from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

# Step 1: Search real-time market context
grounding_config = GenerateContentConfig(
    tools=[Tool(google_search=GoogleSearch())],
    temperature=0.2
)
news_response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Latest Ethereum ETH news today. Any major whale movements?",
    config=grounding_config
)

# Step 2: Validate signal against context
json_config = GenerateContentConfig(
    temperature=0.1,
    response_mime_type="application/json"
)
validation = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=f"""
    WHALE SIGNAL: {signal}
    CONFIDENCE: {confidence}
    NEWS: {news_response.text}
    
    Should we EXECUTE, WAIT, or SKIP?
    """,
    config=json_config
)
```

### Validation Decision Matrix

| Signal Confidence | Market Alignment | Gemini Decision |
|-------------------|------------------|-----------------|
| >70% | Supportive news | EXECUTE |
| >70% | Conflicting news | WAIT |
| >70% | No relevant news | EXECUTE (cautious) |
| 50-70% | Supportive news | EXECUTE (small size) |
| 50-70% | Conflicting news | SKIP |
| <50% | Any | SKIP |

### Example Validation Output

```json
{
  "decision": "WAIT",
  "signal": "NEUTRAL",
  "confidence": 0.65,
  "reasoning": "Conflicting signals detected. While Binance 15 shows 770 ETH outflow (bullish), a larger whale converted 22,000 ETH to WBTC (bearish). The larger movement outweighs the smaller signal."
}
```

---

## AI Decision Logging

Every pipeline run creates a detailed AI decision log:

```json
{
  "run_id": "smt_nightly_20260105_135102",
  "timestamp": "2026-01-05T13:51:02.776981+00:00",
  "pipeline_version": "SMT-ETH-v1.0",
  "trading_pair": "cmt_ethusdt",
  "steps": [
    {
      "step": "MARKET_CHECK",
      "data": {
        "eth_price": 3146.79,
        "balance_usdt": 0.0
      }
    },
    {
      "step": "SIGNAL_GENERATED",
      "data": {
        "whale": "Binance 15",
        "signal": "LONG",
        "confidence": "85%",
        "reasoning": "CEX_Wallet shows outflow of 770.00 ETH. Signal: BULLISH."
      }
    },
    {
      "step": "GEMINI_VALIDATION",
      "data": {
        "decision": "WAIT",
        "signal": "NEUTRAL",
        "reasoning": "Conflicting signals - larger bearish whale activity detected"
      }
    }
  ],
  "final_decision": "NO_TRADE"
}
```

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| ML Framework | CatBoost 1.2.2 | Whale classification |
| LLM | Gemini 2.5 Flash | Signal validation |
| Grounding | Google Search | Real-time context |
| Data Pipeline | Etherscan V2 API | Transaction data |
| Runtime | Python 3.11 | Execution environment |
| Hosting | Google Compute Engine | Bot hosting |

---

## Model Artifacts

Stored in: `gs://smt-weex-2025-models/models/production/`

- `catboost_whale_classifier_production.cbm` - Trained model
- `label_encoder_production.pkl` - Label encoder
- `features.json` - Feature list
- `model_config.json` - Hyperparameters

---

## How AI Makes Trade Decisions

```
1. DETECT WHALE ACTIVITY
   └── Etherscan V2 API fetches recent transactions
   
2. CLASSIFY WHALE BEHAVIOR
   └── CatBoost model identifies: CEX_Wallet, Staker, etc.
   
3. GENERATE SIGNAL
   └── Signal matrix converts behavior to LONG/SHORT/NEUTRAL
   
4. VALIDATE WITH GEMINI
   └── Google Search finds current market context
   └── Gemini decides: EXECUTE / WAIT / SKIP
   
5. EXECUTE OR SKIP
   └── If EXECUTE: Place order on WEEX
   └── If WAIT/SKIP: Log reason, no trade
   
6. LOG EVERYTHING
   └── Full AI reasoning saved for review
```

---

## Continuous Improvement Plan

| Trigger | Action |
|---------|--------|
| Weekly | Update whale list with new high-value addresses |
| Monthly | Retrain classifier with expanded labeled data |
| After 100 trades | Analyze performance, adjust thresholds |
| After major miss | Root cause analysis, model review |

---

*Document Version: 1.0*
*Last Updated: January 2026*
*For: WEEX AI Wars - Alpha Awakens Hackathon*
