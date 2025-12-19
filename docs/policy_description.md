# SMT Trading Policy Description

## Strategy Overview

Smart Money Tracker (SMT) is an AI-powered trading system that generates signals by analyzing whale wallet behavior on Ethereum blockchain. Instead of relying on traditional technical indicators, we track the smart money - large wallets whose movements often precede market shifts.

## Signal Generation Logic

### Whale Classification
Our CatBoost ML model classifies whale wallets into 5 behavioral categories:

| Category | Description | Signal Weight |
|----------|-------------|---------------|
| Miner | Block reward recipients | High |
| Staker | ETH staking participants | Medium |
| Large_Holder | CEX wallets + Institutional | High |
| DeFi_Trader | Active DeFi protocol users | Medium |
| Exploiter | Hack/exploit related | Avoid |

### Entry Rules

**LONG Signal Generated When:**
- Large_Holder withdraws >100 ETH from CEX (confidence >70%)
- Multiple miners accumulating (3+ wallets, same direction)
- Institutional wallet increases position

**SHORT Signal Generated When:**
- Miner sells >500 ETH to CEX (confidence >70%)
- Large_Holder deposits >100 ETH to CEX
- Staker unstakes significant amount (>1000 ETH)

**NO TRADE When:**
- Classification confidence <60%
- Exploiter wallet detected (avoid affected tokens)
- Conflicting signals from multiple whales
- Gemini validation returns SKIP or WAIT

### Exit Rules

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Take Profit | 5% | Lock in gains |
| Stop Loss | 2% | Limit downside (2.5:1 R:R) |
| Time Stop | 24 hours | Exit stale positions |
| Trailing Stop | 3% | Protect profits on runners |

### Position Sizing

```
Position Size = Base Size × Confidence × Signal Strength

Where:
- Base Size = 10% of portfolio
- Confidence = Model confidence (0.6 - 1.0)
- Signal Strength = 0.5 (single whale) to 1.0 (multiple whales)
```

### Risk Management

| Rule | Limit | Action |
|------|-------|--------|
| Max Leverage | 20x | Hardcoded cap |
| Max Position | 20% portfolio | Per trade |
| Daily Loss Limit | 5% | Stop trading for day |
| Max Open Positions | 5 | Diversification |
| Min Time Between Trades | 30 seconds | Independence rule |

## Trading Pairs

Primary (direct whale signal):
- ETH/USDT
- BTC/USDT

Secondary (correlated):
- SOL/USDT
- BNB/USDT

## Execution Flow

```
1. Detect whale transaction (Etherscan API)
2. Classify whale behavior (CatBoost model)
3. Generate trading signal
4. Validate with Gemini + Google Search grounding
5. Execute on WEEX if validated
6. Log AI decision for compliance
```

## Uniqueness Statement

Our strategy is fundamentally different from typical trading bots:

- **No technical indicators** (RSI, MACD, etc.)
- **No price prediction** models
- **Pure on-chain behavioral analysis**
- **Real-time whale wallet monitoring**

This ensures our signals are independent and cannot be replicated by other participants using standard approaches.
