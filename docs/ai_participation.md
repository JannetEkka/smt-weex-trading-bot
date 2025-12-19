# SMT AI Participation Description

## AI/ML Components Overview

Smart Money Tracker uses a dual-AI architecture:

1. **CatBoost Classifier** - Whale behavior classification
2. **Gemini 2.5 Flash** - Signal validation with real-time grounding

## Component 1: CatBoost Whale Classifier

### Purpose
Classify Ethereum whale wallets into behavioral categories based on their on-chain transaction patterns.

### Model Details

| Attribute | Value |
|-----------|-------|
| Algorithm | CatBoost (Gradient Boosting) |
| Task | Multi-class classification |
| Classes | 5 (Miner, Staker, Large_Holder, DeFi_Trader, Exploiter) |
| Features | 32 engineered features |
| Training Samples | 646 labeled whales |
| Validation | 5-fold stratified cross-validation |
| Performance | 67% macro F1-score |

### Feature Categories

1. **Transaction Volume Features**
   - total_txs, incoming_count, outgoing_count
   - outgoing_volume_eth, incoming_volume_eth, net_flow_eth

2. **Transaction Type Features**
   - erc20_ratio, nft_ratio, internal_ratio
   - normal_tx_count, erc20_tx_count

3. **Temporal Features**
   - avg_time_between_tx_hours, activity_span_days
   - tx_per_day, business_hour_ratio, peak_hour_pct

4. **Network Features**
   - unique_counterparties, defi_interactions
   - unique_tokens, stablecoin_ratio

5. **Value Features**
   - avg_tx_value_eth, max_tx_value_eth
   - large_tx_ratio, balance_eth_log

### Per-Class Performance

| Class | Precision | Recall | F1-Score |
|-------|-----------|--------|----------|
| Miner | 76% | 90% | 82% |
| Exploiter | 78% | 88% | 82% |
| Staker | 78% | 69% | 73% |
| DeFi_Trader | 53% | 49% | 51% |
| Large_Holder | 46% | 44% | 45% |

### Training Data Sources

- **Etherscan Labels**: Official labeled addresses from etherscan.io/labelcloud
- **Categories**: CEX wallets, Stakers, Institutional, DeFi protocols, Miners, Exploiters
- **Transactions**: Fetched via Etherscan V2 API (chainid=1)
- **Timeframe**: Last 90 days of transaction history

## Component 2: Gemini 2.5 Flash Validator

### Purpose
Validate trading signals against real-time market context using Google Search grounding.

### Implementation

```python
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

config = GenerateContentConfig(
    tools=[Tool(google_search=GoogleSearch())],
    temperature=0.1,  # Deterministic output
    response_mime_type="application/json"
)
```

### Validation Process

1. **Input**: Whale classification + proposed signal
2. **Grounding**: Search current token price, news, market sentiment
3. **Analysis**: Check for conflicting information
4. **Output**: EXECUTE / WAIT / SKIP decision with confidence

### Gemini Decision Factors

- Current token price and 24h change
- Recent news affecting the token
- Market sentiment alignment
- Classification confidence threshold
- Conflicting whale signals

### Output Format

```json
{
  "decision": "EXECUTE",
  "signal": "SHORT",
  "confidence": 0.75,
  "market_sentiment": "BEARISH",
  "reasoning": "Miner selling 500 ETH to Binance, market shows weakness",
  "risk_level": "MEDIUM",
  "suggested_position_size_pct": 15
}
```

## AI Decision Logging

Every trade includes an AI decision log:

```json
{
  "timestamp": "2025-12-20T10:30:00Z",
  "whale_address": "0x1234...5678",
  "classification": "Miner",
  "classification_confidence": 0.82,
  "signal": "SHORT",
  "gemini_validation": {
    "decision": "EXECUTE",
    "confidence": 0.75,
    "reasoning": "..."
  },
  "trade_executed": {
    "pair": "ETHUSDT",
    "side": "sell",
    "size": 0.5,
    "price": 3450.00
  }
}
```

## Technology Stack

| Component | Technology |
|-----------|------------|
| ML Framework | CatBoost 1.2.2 |
| LLM | Gemini 2.5 Flash (Vertex AI) |
| Grounding | Google Search |
| Data Pipeline | BigQuery |
| Model Storage | Google Cloud Storage |
| Runtime | Python 3.11 |

## Model Artifacts

Stored in: `gs://smt-weex-2025-models/models/production/`

- `catboost_whale_classifier_production.cbm` - Trained model
- `label_encoder_production.pkl` - Label encoder
- `features.json` - Feature list
- `model_config.json` - Hyperparameters and metrics

## Continuous Improvement

During competition:
1. Log all predictions and outcomes
2. Track per-class accuracy in production
3. Retrain if significant drift detected
4. Adjust confidence thresholds based on performance
