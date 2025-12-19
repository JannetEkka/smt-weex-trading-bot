# SMT WEEX Trading Bot

AI-powered trading bot that tracks whale wallet behavior to generate trading signals for WEEX exchange.

## Architecture

1. **Data Collection** - Fetch whale transactions from Etherscan V2 API (ETH) and Helius API (SOL)
2. **Behavior Classification** - CatBoost model classifies whale behavior into 6 categories
3. **Signal Validation** - Gemini 2.5 Flash with Google Search grounding validates signals
4. **Trade Execution** - WEEX API executes trades based on validated signals

## Whale Categories & Signals

| Category | Signal Logic |
|----------|-------------|
| CEX_Wallet | Inflow = bearish, Outflow = bullish |
| Staker | Unstaking = bearish |
| Institutional | Follow their direction |
| DeFi_Trader | DEX volume spike = volatility |
| Miner | Selling = bearish |
| Exploiter | Avoid/short affected tokens |

## Setup

```bash
pip install -r requirements.txt
export ETHERSCAN_API_KEY="your_key"
export WEEX_API_KEY="your_key"
export WEEX_API_SECRET="your_secret"
```

## Pipeline

```bash
# 1. Fetch balances for all labeled addresses
python scripts/01_fetch_balances.py

# 2. Filter top 200 whales per category
python scripts/02_filter_top_whales.py

# 3. Fetch transactions for filtered whales
python scripts/03_fetch_transactions.py

# 4. Extract features for ML
python scripts/04_extract_features.py
```

## Tech Stack

- Python 3.11+
- CatBoost (ML model)
- Vertex AI (model hosting + Gemini)
- BigQuery (data storage)
- Cloud Run (API hosting)
- WEEX API (trade execution)

## Model

**CatBoost Classifier** - 5-class whale behavior classification

| Class | F1-Score | Signal |
|-------|----------|--------|
| Miner | 82% | Sells = Bearish |
| Exploiter | 82% | Avoid token |
| Staker | 73% | Unstake = Bearish |
| DeFi_Trader | 51% | Volume spike = Volatility |
| Large_Holder | 45% | Follow direction |

**Training:** 646 labeled whales, 32 features, 5-fold CV
**Model file:** `gs://smt-weex-2025-models/models/production/catboost_whale_classifier_production.cbm`

## Author

Jannet Ekka - Smart Money Tracker
