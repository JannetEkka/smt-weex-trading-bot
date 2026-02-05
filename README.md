# SMT - Smart Money Tracker

**AI-Powered Whale Intelligence Trading Bot**

Built for WEEX AI Wars: Alpha Awakens Hackathon

---

## Results

| Metric | Value |
|--------|-------|
| Prelims ROI | +566% |
| Starting Balance | $1,000 USDT |
| Final Equity | $6,662 USDT |
| Prelims Rank | #1 in Group, #2 Overall |
| Survival | Survived Jan 18 flash crash |

---

## What is SMT?

Smart Money Tracker is an autonomous AI trading bot that tracks Ethereum whale wallets and executes trades based on their behavior patterns.

**The Edge:** While other bots react to price movements, SMT watches what smart money is doing *before* the price moves.

Most trading bots use technical indicators (RSI, MACD, moving averages). These are lagging indicators - they tell you what already happened.

SMT uses on-chain whale intelligence as a *leading* indicator. When large wallets move funds to exchanges, they're often preparing to sell. When they withdraw, they're accumulating. SMT detects these patterns and acts on them.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        SMT V3.1 ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│   │   WHALE     │  │  SENTIMENT  │  │    FLOW     │            │
│   │  Etherscan  │  │   Gemini    │  │  Order Book │            │
│   │  On-chain   │  │  + Search   │  │  Taker Ratio│            │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘            │
│          │                │                │                    │
│          │    ┌───────────┴───────────┐    │                    │
│          │    │                       │    │                    │
│          ▼    ▼                       ▼    ▼                    │
│   ┌─────────────┐              ┌─────────────┐                  │
│   │  TECHNICAL  │              │    JUDGE    │                  │
│   │  RSI, SMA   │─────────────▶│   Gemini    │                  │
│   │  Momentum   │              │  Weighs All │                  │
│   └─────────────┘              └──────┬──────┘                  │
│                                       │                         │
│                                       ▼                         │
│                              ┌─────────────────┐                │
│                              │  REGIME FILTER  │                │
│                              │  BTC Trend Gate │                │
│                              └────────┬────────┘                │
│                                       │                         │
│                                       ▼                         │
│                              ┌─────────────────┐                │
│                              │  WEEX EXECUTION │                │
│                              │  20x Leverage   │                │
│                              └─────────────────┘                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## The 5 Persona System

SMT uses a "multiple personality" approach. Five AI personas analyze the market independently, then a Judge persona weighs their opinions and makes the final call.

### Persona 1: WHALE (Weight: 2.5x in bullish regime)

**Purpose:** On-chain whale intelligence - our unique edge

**Data Source:** Etherscan V2 API

**What it does:**
- Monitors 8+ high-value Ethereum wallets
- Tracks CEX deposit/withdrawal patterns
- Detects accumulation vs distribution behavior

**Signal Logic:**
- Net inflow to CEX > 500 ETH = BEARISH (preparing to sell)
- Net outflow from CEX > 500 ETH = BULLISH (accumulating)

**Why it matters:** Whales move before retail. If Binance hot wallets are receiving large deposits, selling pressure is coming.

---

### Persona 2: SENTIMENT (Weight: 2.0x)

**Purpose:** Real-time market sentiment analysis

**Data Source:** Gemini 2.5 Flash with Google Search grounding

**What it does:**
- Searches current news for the trading pair
- Analyzes social sentiment
- Identifies structural breaks (support/resistance)

**Signal Logic:**
- Breaking DOWN through support = BEARISH
- Breaking UP through resistance = BULLISH
- Choppy/sideways = NEUTRAL

---

### Persona 3: FLOW (Weight: 1.0-2.5x based on regime)

**Purpose:** Order flow and market microstructure

**Data Source:** WEEX API (order book, trades)

**What it does:**
- Analyzes bid/ask depth ratio
- Tracks taker buy/sell ratio
- Monitors funding rates

**Signal Logic:**
- Taker ratio > 1.5 + strong bid depth = BULLISH
- Taker ratio < 0.7 + strong ask depth = BEARISH

**Special Logic:** In bearish regimes, extreme taker buying is often short covering, not reversal. The persona adjusts for this.

---

### Persona 4: TECHNICAL (Weight: 1.5-2.0x)

**Purpose:** Traditional technical indicators

**Data Source:** WEEX candle data

**Indicators:**
- RSI (14 period)
- SMA 20 & SMA 50
- 5-candle momentum

**Signal Logic:**
- RSI < 30 = Oversold = LONG candidate
- RSI > 70 = Overbought = SHORT candidate
- Price above both SMAs = BULLISH trend
- Price below both SMAs = BEARISH trend

---

### Persona 5: JUDGE (Final Decision)

**Purpose:** Weighs all persona votes and makes the final call

**Data Source:** All persona outputs + market regime

**What it does:**
- Calculates weighted scores for LONG/SHORT/NEUTRAL
- Applies regime-specific weight adjustments
- Requires minimum 70-80% confidence to trade
- Can veto trades that conflict with whale signals

**Regime-Aware Weighting:**

| Regime | WHALE | SENTIMENT | FLOW | TECHNICAL |
|--------|-------|-----------|------|-----------|
| BULLISH | 2.5x | 2.0x (LONG) | 1.2x | 1.5x |
| BEARISH | 0.5x | 2.0x (SHORT) | 2.5x | 2.0x |
| NEUTRAL | 1.5x | 1.2x | 1.0x | 1.5x |

---

## Regime Filter

Before any trade executes, SMT checks the overall market regime based on BTC's behavior.

**Regime Detection:**
- BTC 24h change < -1% = BEARISH
- BTC 24h change > +1.5% = BULLISH
- Otherwise = NEUTRAL

**Trading Rules:**
- BEARISH regime: Block all LONG entries
- BULLISH regime: Block all SHORT entries (unless very high confidence)
- Regime shifts trigger position review

**Why this matters:** During the Jan 18 flash crash, the regime filter prevented new LONG entries while BTC was dumping. Many competitors got liquidated. SMT survived.

---

## Tier-Based Risk Management

Not all coins behave the same. SMT uses a tiered system:

| Tier | Coins | TP | SL | Max Hold | Rationale |
|------|-------|----|----|----------|-----------|
| 1 (Stable) | BTC, ETH, BNB, LTC | 5% | 2% | 72h | Slow movers, hold longer |
| 2 (Mid) | SOL | 4% | 1.75% | 48h | Medium volatility |
| 3 (Fast) | DOGE, XRP, ADA | 4% | 2% | 24h | High volatility, quick exits |

---

## Risk Controls

### Position Sizing
- Maximum 20% of balance per trade
- Minimum 10% of balance per trade
- Maximum 3 open positions at once

### Hard Stops
- Regime-aligned positions: Trust the SL
- Positions fighting regime + losing > $50: Force exit
- Flash crash detected (3% drop): Pause trading for 4 hours

### Trailing Protection
- Track peak PnL for each position
- If position was +2% but now negative: Exit immediately

---

## Flash Crash Protection

On January 18, 2026, Greenland tariff news caused BTC to flash crash. Many competitors were liquidated.

**SMT's response:**
1. Regime filter detected BEARISH (BTC 24h < -1%)
2. Blocked all new LONG entries
3. Flash crash protection paused trading for 4 hours
4. Existing positions hit SL but within acceptable loss

**Result:** SMT survived while others got wiped out.

---

## Technology Stack

| Component | Technology |
|-----------|------------|
| Whale Data | Etherscan V2 API (chain_id=1) |
| AI Validation | Gemini 2.5 Flash + Google Search |
| Trade Execution | WEEX Contract API v2 |
| Hosting | Google Compute Engine (e2-micro) |
| Language | Python 3.11 |

---

## File Structure

```
smt-weex-trading-bot/
├── v3/
│   ├── smt_nightly_trade_v3_1.py   # Core trading logic + all personas
│   ├── smt_daemon_v3_1.py          # 24/7 daemon
│   ├── trade_state_v3_1_*.json     # Position tracking
│   ├── rl_training_data/           # RL shadow mode data
│   │   └── exp_*.jsonl
│   └── logs/
│       └── daemon_*.log
├── v4/                              # Post-hackathon RL development
│   ├── config.py
│   ├── secrets_manager.py
│   └── rl_data_collector.py
└── docs/
    ├── policy_description.md
    └── ai_participation.md
```

---

## Running the Bot

### Prerequisites
- Python 3.11+
- Google Cloud account with Vertex AI enabled
- WEEX API credentials (IP whitelisted)
- Etherscan API key

### Environment Variables
```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_GENAI_USE_VERTEXAI=True
export WEEX_API_KEY=your-key
export WEEX_API_SECRET=your-secret
export WEEX_API_PASSPHRASE=your-passphrase
export ETHERSCAN_API_KEY=your-key
```

### Start Daemon
```bash
cd ~/smt-weex-trading-bot/v3
nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
```

### Monitor
```bash
tail -f daemon.log
ps aux | grep smt_daemon
```

---

## What's Next: V4 Roadmap

SMT V3.1 uses rule-based weighting for the personas. V4 will use Reinforcement Learning to automatically learn optimal weights.

**Current (V3.1):** Manual weights - I decide WHALE gets 2.5x in bullish regime

**Future (V4):** RL agent learns - Model discovers optimal weights from trading results

**Data Collection:** V3.1 is already collecting training data in shadow mode (200+ entries). After the hackathon, this data will train the RL agent.

---

## Lessons Learned

1. **Trust your unique data.** Whale signals were right more often than I gave them credit for. When whales disagreed with sentiment, I should have listened to whales.

2. **Survival beats profit.** The competitors who got liquidated are out. If you're still playing, you have infinite chances.

3. **Regime awareness is critical.** Don't LONG in a bear market. Don't SHORT in a bull market. Sounds obvious, but most bots ignore this.

4. **Cut losers fast, let winners run.** Still working on this one.

---

## Contact

**Project:** Smart Money Tracker (SMT)

**Founder:** Jannet Ekka

**Email:** jtech26smt@gmail.com

**Twitter:** @JTechSMT

---

## License

MIT License - See LICENSE file

---

*Built for WEEX AI Wars: Alpha Awakens Hackathon*

*January - February 2026*
