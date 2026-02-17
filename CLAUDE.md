# CLAUDE.md - SMT WEEX Trading Bot

## What This Is

AI trading bot for the **WEEX AI Wars: Alpha Awakens** competition (Feb 8-23, 2026).
Trades 8 crypto pairs on WEEX futures using a 5-persona ensemble (Whale, Sentiment, Flow, Technical, Judge).
Starting balance $1,000 USDT. Prelims: +566% ROI, #2 overall.

**Current version: V3.1.92** — all production code is in `v3/`.

## Architecture

```
v3/
├── smt_daemon_v3_1.py          # 24/7 daemon loop (~3150 lines)
│   - check_trading_signals()    → Signal check cycle (every 10min)
│   - monitor_positions()        → Position monitor cycle (every 2min)
│   - regime_aware_exit_check()  → Regime-based exit logic
│   - gemini_portfolio_review()  → Gemini AI portfolio optimization
│   - sync_tracker_with_weex()   → Reconcile local state vs WEEX
│   - quick_cleanup_check()      → Orphan order cleanup (every 30s)
│   - log_health()               → Health check (every 60s)
│
├── smt_nightly_trade_v3_1.py   # Core trading logic (~4000 lines)
│   - MultiPersonaAnalyzer       → 5-persona ensemble (line ~3113)
│   - place_order()              → WEEX order placement
│   - close_position_manually()  → Close + cancel orphan triggers
│   - cancel_all_orders_for_symbol() → Kill all orders (regular + plan)
│   - upload_ai_log_to_weex()    → Competition logging (REQUIRED)
│   - get_open_positions()       → WEEX positions API
│   - get_balance()              → WEEX balance API
│   - TRADING_PAIRS              → 8 pairs with tier/symbol config
│   - TIER_CONFIG                → TP/SL/hold times per tier
│   - find_chart_based_tp_sl()   → Support/resistance TP/SL
│   - detect_sideways_market()   → Chop filter
│   - TradeTracker               → Local state management (line ~3669)
│
├── trade_state_v3_1_7.json     # Live state: active trades, cooldowns, blacklist
├── close_all_positions.py      # Emergency close script (3-pass)
├── logs/daemon_v3_1_7_*.log    # Daily daemon logs
└── *.bak*, *.patch             # Version history (ignore)
```

## Critical Rules

### WEEX AI Log Uploads Are MANDATORY
Every trade open/close MUST call `upload_ai_log_to_weex()` with the order_id.
Missing logs = disqualification. The stage format matters:
- Open: `"Analysis - {PAIR} (Tier {N})"`
- Close: `"Portfolio Manager: Close {SIDE} {PAIR}"`
- The `explanation` field is capped at 1000 chars.

### Orphan Order Problem
When positions close, TP/SL trigger orders can persist on WEEX. This causes:
1. Leverage set failures ("open orders" blocking)
2. Old SL triggers executing at wrong prices on new positions
3. The recurring DOGE SL bug (bot sets $0.0999, WEEX shows $0.0936)

**Always** cancel orders BEFORE closing positions. `close_position_manually()` does this.
The daemon's `quick_cleanup_check()` also sweeps orphans every 30s.

### Position Tracker Must Stay In Sync
`trade_state_v3_1_7.json` tracks active trades, cooldowns, and blacklist.
If it drifts from WEEX reality, `sync_tracker_with_weex()` reconciles.
Never manually edit this file while the daemon is running.

### Competition Timing
- Signal check: every 10 minutes (V3.1.84, was 15)
- Position monitor: every 2 minutes
- Global trade cooldown: 15 minutes between trades (prevents fee bleed)

## Key Constants & Thresholds

```python
MAX_LEVERAGE = 20
MIN_CONFIDENCE_TO_TRADE = 0.80      # 80% HARD FLOOR - NO exceptions (V3.1.85)
GLOBAL_TRADE_COOLDOWN = 900          # 15min between trades
SIGNAL_CHECK_INTERVAL = 600          # 10min
POSITION_MONITOR_INTERVAL = 120      # 2min

# Slot system (equity-tiered)
# >= $500: 5 slots | $200-500: 3 slots | < $200: 1 slot
# When all slots are full: only slot swaps can enter (needs 83%+ confidence)
# If no signals reach 80%, ALL pairs show WAIT — this is expected, not a bug.
# Existing position + same direction signal = WAIT (already have that side).

# Slot swap gates (V3.1.87)
# Min age: 45min | Min confidence: 83%
# PnL threshold: regime-aware
#   Normal (F&G >= 20): -0.5% | Capitulation (F&G < 20): -0.25%

# Regime exit thresholds
# Regime fight: 35% margin loss | Hard stop: 45% margin loss

# Position sizing (V3.1.92: equity-based)
# sizing_base = max(min(equity, balance * 2.5), balance)
# base_size = sizing_base * 0.25 (confidence tiers: 1.0x / 1.25x / 1.5x)
# per_slot_cap = (sizing_base * 0.85) / max_slots
# Margin guard: skip trades if available margin < 15% of balance

# TP caps (V3.1.92: ATR-aware)
# MAX_TP_PCT = min(3.0%, max(2.0%, ATR * 2))
# Volatile pairs (SOL/DOGE) get up to 3% TP, stable pairs (BTC) stay ~2-2.4%
```

## Trading Pairs & Tiers

| Pair | Symbol | Tier | TP | SL | Max Hold |
|------|--------|------|----|----|----------|
| BTC  | cmt_btcusdt  | 2 | 3.5% | 1.5% | 12h |
| ETH  | cmt_ethusdt  | 1 | 3.0% | 1.5% | 24h |
| BNB  | cmt_bnbusdt  | 1 | 3.0% | 1.5% | 24h |
| LTC  | cmt_ltcusdt  | 2 | 3.5% | 1.5% | 12h |
| XRP  | cmt_xrpusdt  | 2 | 3.5% | 1.5% | 12h |
| SOL  | cmt_solusdt  | 3 | 3.0% | 1.8% | 8h  |
| DOGE | cmt_dogeusdt | 3 | 3.0% | 1.8% | 8h  |
| ADA  | cmt_adausdt  | 3 | 3.0% | 1.8% | 8h  |

Note: V3.1.84 uses chart-based TP/SL (support/resistance levels) with these as fallbacks.

## WEEX API

Base URL: `https://api-contract.weex.com`
Auth: HMAC-SHA256 signature via `weex_headers()`.

Key endpoints:
- `GET /capi/v2/account/balance` — balance
- `GET /capi/v2/account/allPosition` — open positions
- `POST /capi/v2/order/placeOrder` — place order (type: 1=open long, 2=open short, 3=close long, 4=close short)
- `POST /capi/v2/order/cancel` — cancel regular order
- `GET /capi/v2/order/plan_orders` — list trigger orders (TP/SL)
- `POST /capi/v2/order/cancel_plan` — cancel trigger order
- `POST /capi/v2/order/uploadAiLog` — upload AI decision log

## 5-Persona System

1. **WHALE** — On-chain wallet tracking (Etherscan + Cryptoracle). CEX inflow/outflow signals.
2. **SENTIMENT** — Gemini 2.5 Flash with Search Grounding. Real-time news analysis.
3. **FLOW** — WEEX order book + trades. Taker ratios, bid/ask depth, funding rates.
4. **TECHNICAL** — RSI(14), SMA 20/50, 5-candle momentum on 1h candles.
5. **JUDGE** — Aggregates all votes with regime-aware weights. Final LONG/SHORT/WAIT.

Post-judge filters: Chop filter, Freshness filter, Regime veto, Consecutive loss block.

## How to Make Changes

### Modifying trading logic
Edit `v3/smt_nightly_trade_v3_1.py`. The daemon imports from it at startup.
After changes: merge to main on VM, then restart daemon.

### Modifying daemon behavior
Edit `v3/smt_daemon_v3_1.py`. Same deploy process.

### Deploy to VM
```bash
# On VM:
cd ~/smt-weex-trading-bot
git pull origin main
# Kill existing daemon
ps aux | grep smt_daemon | grep -v grep | awk '{print $2}' | xargs kill
# Restart
cd v3 && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
```

### Emergency close all positions
```bash
cd ~/smt-weex-trading-bot && python3 v3/close_all_positions.py
```

### Test mode (no real orders)
```bash
python3 v3/smt_nightly_trade_v3_1.py --test
```

## Common Bugs to Watch For

1. **Orphan triggers** — Always cancel orders before closing positions
2. **Slot swap burning money** — Don't swap young positions (<45min) or barely-negative (<-0.5%)
3. **Gemini timeouts** — 90s timeout + 8s rate limit between calls. Use bulletproof wrapper.
4. **Regime exits too aggressive** — Trust the 2% SL. Only regime-exit at 35%+ margin loss.
5. **Late entries** — Freshness filter blocks entering after a move already happened
6. **Consecutive losses** — Block re-entry after 2 losses (any type) same direction in 24h (V3.1.91: counts ALL losses, not just force-stops)
7. **AI log missing** — Every trade MUST upload logs or competition results won't count

## Version Naming

Format: `V3.1.{N}` where N increments with each fix/feature.
Bump the version number in the daemon startup banner and any new scripts.
Current: V3.1.92. Next change should be V3.1.93.

**CRITICAL RULE (V3.1.85+): The 80% confidence floor is ABSOLUTE.**
Never add session discounts, contrarian boosts, or any other override that
lowers the trading threshold below 80%. Quality over quantity wins competitions.

## Claude Code Rules (MANDATORY)

1. **Plan mode = plan ONLY.** Never write code, create commits, or push during plan mode. Plan mode is for research and writing the plan file — nothing else.
2. **Always confirm before commit/push.** Never commit or push code without explicit user approval. Present the changes, wait for "go ahead" or equivalent.
3. **Do what you said.** If you propose approach X, implement approach X. Never silently switch to approach Y. If you realize a different approach is better mid-implementation, stop and confirm the change with the user first.
4. **No surprise deployments.** This is a live trading bot handling real money in a competition. Every change must be reviewed and approved before it touches the branch.

## Files to Ignore

- `v3/*.bak*`, `v3/*.patch` — old backups, don't modify
- `v2/`, `v4/` — legacy/future versions, not in production
- `v3/all_rl_data.jsonl` — RL training data, read-only during competition
- `data/`, `models/`, `notebooks/` — analysis artifacts
