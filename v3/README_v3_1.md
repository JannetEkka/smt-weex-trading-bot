# SMT Trading Bot V3.1 - Multi-Persona Strategy

## WEEX AI Wars: Alpha Awakens
**Team:** SMT (Smart Money Tracker)  
**Lead:** Jannet Ekka  
**Competition:** Jan 12 - Feb 2, 2026

---

## What Makes V3.1 Different

### Multi-Persona Voting System

Unlike simple bots that use one signal source, SMT V3.1 uses **5 AI personas** that each analyze the market independently, then a **Judge** weighs their votes to make the final decision.

```
┌─────────────────────────────────────────────────────────┐
│                    MARKET DATA                          │
└─────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │  WHALE   │    │SENTIMENT │    │   FLOW   │
    │ (2.0x)   │    │ (1.5x)   │    │ (1.5x)   │
    └──────────┘    └──────────┘    └──────────┘
           │               │               │
           │       ┌──────────┐            │
           │       │TECHNICAL │            │
           │       │ (1.0x)   │            │
           │       └──────────┘            │
           │               │               │
           └───────────────┼───────────────┘
                           ▼
                    ┌──────────┐
                    │  JUDGE   │
                    │ (Final)  │
                    └──────────┘
                           │
                           ▼
                   LONG / SHORT / WAIT
```

### The 5 Personas

| Persona | Weight | What It Analyzes | Our Edge |
|---------|--------|------------------|----------|
| **WHALE** | 2.0x | On-chain whale movements (ETH) | Unique - competitors don't have this |
| **SENTIMENT** | 1.5x | News & market sentiment via Gemini | Google Search grounding |
| **FLOW** | 1.5x | Taker buy/sell ratio, orderbook depth | Real-time order flow |
| **TECHNICAL** | 1.0x | RSI, SMA, momentum, funding rate | Standard TA |
| **JUDGE** | - | Weighs all votes, makes final call | Consensus-based |

### Why Multi-Persona Works

- **No single point of failure** - One bad signal doesn't ruin the trade
- **Weighted consensus** - Whale data (our edge) counts more
- **Diverse analysis** - On-chain + sentiment + flow + technical
- **Judge validates** - Only trades when multiple personas agree

---

## Position Sizing (Conservative)

| Confidence | Margin % | On $1000 | Notional (20x) |
|------------|----------|----------|----------------|
| > 80% | 10.5% | $105 | $2,100 |
| > 70% | 9% | $90 | $1,800 |
| > 60% | 7% | $70 | $1,400 |
| Min | 5% | $50 | $1,000 |
| Max | 12% | $120 | $2,400 |

**Risk Philosophy:** Conservative margins (5-12%) but meaningful enough to generate real profits. A 5% winning trade yields $50-120 instead of $4.

---

## Smart Exit Logic

V3.1 doesn't just wait for TP/SL. It actively monitors positions:

| Condition | Action |
|-----------|--------|
| TP hit | Close with profit |
| SL hit | Close with loss |
| After 3h + losing >2.5% | **Early exit** - cut losers |
| Losing >4% any time | **Force exit** - limit damage |
| Max 24h hold | Exit regardless |

**Goal:** Keep winners, cut losers early. Don't let small losses become big losses.

---

## Timing

| Check | Interval | Purpose |
|-------|----------|---------|
| Signal check | 2 hours | Find new trades |
| Position monitor | 3 minutes | Smart exit checks |
| Quick cleanup | 30 seconds | Detect TP/SL |
| On position close | Immediate | Look for new opportunity |

---

## Files

```
v3/
├── smt_nightly_trade_v3_1.py   # Core trading logic + personas
├── smt_daemon_v3_1.py          # Always-running daemon
├── trade_state_v3_1.json       # Position tracking (auto-created)
└── logs/
    └── daemon_v3_1_YYYYMMDD.log
```

---

## Running

### Test Mode
```bash
python3 smt_nightly_trade_v3_1.py --test
```

### Production
```bash
# Run directly (foreground)
python3 smt_daemon_v3_1.py

# Or as systemd service
sudo systemctl start smt-trading-v31
```

### Monitor
```bash
# View logs
tail -f logs/daemon_v3_1_*.log

# Check positions
./smt positions
```

---

## Trading Pairs

All 8 WEEX competition pairs:

| Pair | Tier | Whale Data |
|------|------|------------|
| ETH | 1 | Yes |
| BTC | 1 | Yes (correlated) |
| SOL | 2 | No |
| DOGE | 2 | No |
| XRP | 2 | No |
| ADA | 2 | No |
| BNB | 2 | No |
| LTC | 2 | No |

---

## AI Log Display

V3.1 uploads AI logs with `order_id` so trades show **"AI order"** on the WEEX leaderboard, proving automated (not manual) trading.

Example log:
```
V3.1 Trade: LONG SOLUSDT
Personas: [WHALE, SENTIMENT, FLOW, TECHNICAL]
Confidence: 72%
Votes: WHALE=LONG, SENTIMENT=LONG, FLOW=NEUTRAL, TECHNICAL=LONG
```

---

## Configuration

Key settings in `smt_nightly_trade_v3_1.py`:

```python
MAX_LEVERAGE = 20                    # Competition max
MAX_OPEN_POSITIONS = 5               # Diversification
MAX_SINGLE_POSITION_PCT = 0.12       # 12% max per trade
MIN_SINGLE_POSITION_PCT = 0.05       # 5% min per trade
MIN_CONFIDENCE_TO_TRADE = 0.60       # Need 60% Judge confidence
EARLY_EXIT_LOSS_PCT = -2.5           # Cut losers after 3h
MAX_HOLD_HOURS_DEFAULT = 24          # Don't hold forever
```

---

## Competition Strategy

### Early Phase (Now - Jan 20)
- Build consistent profits
- 5 positions max, conservative sizing
- Let the multi-persona system find opportunities

### Mid Phase (Jan 20 - Jan 28)
- Assess standings
- Adjust aggression if needed
- Faster exits (12h max hold)

### Final Phase (Jan 28 - Feb 2)
- Protect gains if leading
- Or push harder if behind
- 6h max hold, quick trades

---

## Why SMT Will Win

1. **Unique Edge:** Whale intelligence - no one else has on-chain analysis
2. **Multi-Persona:** Consensus beats single-signal bots
3. **Smart Exits:** Cut losers, let winners run
4. **Conservative Risk:** Won't blow up like aggressive bots
5. **Always On:** 24/7 daemon, never misses opportunities

---

## Contact

- **Email:** jtech26smt@gmail.com
- **GitHub:** https://github.com/JannetEkka/smt-weex-trading-bot

---

*SMT V3.1 - Smart Money Tracker*  
*"Follow the whales, trade with confidence"*
