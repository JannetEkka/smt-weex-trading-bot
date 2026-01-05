# SMT Trading Policy Description

## Strategy Overview

Smart Money Tracker (SMT) is an AI-powered trading system that generates signals by analyzing whale wallet behavior on Ethereum blockchain. Instead of relying on traditional technical indicators, we track the smart money - large wallets whose movements often precede market shifts.

---

## Core Philosophy

**"Follow the smart money, not the crowd."**

Large institutional wallets, exchange wallets, and professional traders often have information or conviction that precedes market movements. By monitoring their on-chain behavior, we can infer their intent and position accordingly.

---

## Signal Generation Logic

### Whale Classification

Our system monitors wallets classified into behavioral categories:

| Category | Description | Signal Weight |
|----------|-------------|---------------|
| CEX_Wallet | Exchange hot/cold wallets | High (75-85%) |
| Staker | ETH staking participants | Medium (60-70%) |
| Large_Holder | Institutional wallets | High (65-70%) |
| DeFi_Trader | Active DeFi users | Low (40%) |
| Miner | Block reward recipients | High (80%) |

### Signal Matrix

| Category | Inflow (receiving ETH) | Outflow (sending ETH) |
|----------|------------------------|----------------------|
| CEX_Wallet | BEARISH - Users depositing to sell | BULLISH - Withdrawals indicate accumulation |
| Staker | BULLISH - Staking indicates confidence | BEARISH - Unstaking signals selling intent |
| Large_Holder | BULLISH - Accumulation | BEARISH - Distribution |
| DeFi_Trader | NEUTRAL - Could be either | NEUTRAL - Could be either |
| Miner | NEUTRAL - Normal operations | BEARISH - Selling block rewards |

---

## Entry Rules

### LONG Position Triggered When:
1. CEX_Wallet shows outflow > 100 ETH (confidence > 70%)
2. Large_Holder accumulating (net inflow > 100 ETH)
3. Multiple whales showing same direction (2+ confirmations)
4. Gemini validation returns EXECUTE

### SHORT Position Triggered When:
1. CEX_Wallet shows inflow > 100 ETH (confidence > 70%)
2. Miner selling > 100 ETH to exchange
3. Staker unstaking significant amount
4. Gemini validation returns EXECUTE

### NO TRADE When:
- Classification confidence < 60%
- Conflicting signals from multiple whales
- Gemini validation returns WAIT or SKIP
- Insufficient balance for position size

---

## Exit Rules

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Take Profit | 5% | Lock in gains at target |
| Stop Loss | 2% | Limit downside (2.5:1 R:R) |
| Time Stop | 30 min (nightly) / 8 hr (hackathon) | Exit stale positions |
| Force Close | Always close before pipeline ends | No overnight risk |

---

## Position Sizing

```
Position Size = Base Size x Confidence Factor

Where:
- Base Size = 10 USDT (fixed for hackathon)
- Confidence Factor = Model confidence (0.6 - 0.95)
- Result: 6-9.5 USDT per trade

Example:
- 85% confidence signal = 10 x 0.85 = 8.5 USDT position
```

---

## Risk Management Rules

| Rule | Limit | Implementation |
|------|-------|----------------|
| Max Leverage | 20x | Hardcoded in config |
| Max Position | 10 USDT | Per trade limit |
| Min Confidence | 60% | Skip weak signals |
| Daily Loss Limit | 5% of portfolio | Stop trading if hit |
| Min Time Between Trades | 30 seconds | Independence rule |

---

## Trading Pairs

Primary (direct whale signal):
- **ETH/USDT** (cmt_ethusdt)

Supported by WEEX for hackathon:
- BTC, ETH, SOL, DOGE, XRP, ADA, BNB, LTC

---

## Execution Flow

```
1. PERCEIVE   - Fetch whale transactions (Etherscan V2 API)
2. CLASSIFY   - Determine whale category and behavior
3. SIGNAL     - Generate trading signal with confidence
4. VALIDATE   - Gemini + Google Search grounding check
5. EXECUTE    - Place order on WEEX (if validated)
6. MANAGE     - Monitor position, apply TP/SL
7. LOG        - Save AI decision log
```

---

## Uniqueness Statement

Our strategy is fundamentally different from typical trading bots:

**What We DON'T Use:**
- Technical indicators (RSI, MACD, Bollinger Bands)
- Price prediction models
- Order book analysis
- Social sentiment scores

**What We DO Use:**
- On-chain transaction analysis
- Whale wallet behavior classification
- Real-time Google Search grounding
- Dual-AI validation (CatBoost + Gemini)

This ensures our signals are independent and cannot be replicated by other participants using standard approaches. Our trades are based on blockchain data, not price charts.

---

## Compliance with WEEX Rules

| Rule | Our Implementation |
|------|-------------------|
| 100% AI-driven trading | No manual intervention, all decisions logged |
| Max 20x leverage | Hardcoded limit, cannot be overridden |
| Min 30-sec independence | Unique whale-based signals |
| AI logs required | Every decision saved with full reasoning |
| Whitelisted pairs only | Only trade ETH/USDT |

---

*Document Version: 1.0*
*Last Updated: January 2026*
*For: WEEX AI Wars - Alpha Awakens Hackathon*
