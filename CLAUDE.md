# CLAUDE.md - SMT WEEX Trading Bot

## What This Is

AI trading bot for the **WEEX AI Wars: Alpha Awakens** competition (Feb 8-23, 2026).
Trades 7 crypto pairs on WEEX futures using a 5-persona ensemble (Whale, Sentiment, Flow, Technical, Judge).
Starting balance $10,000 USDT (Finals). Prelims (was $1K): +566% ROI, #2 overall.

**Current version: V3.2.34** — all production code is in `v3/`.

## Architecture

```
v3/                              # PRIMARY production folder
├── smt_daemon_v3_1.py          # 24/7 daemon loop (~3503 lines)
│   - check_trading_signals()    → Signal check cycle (every 10min)   [line 532]
│   - monitor_positions()        → Position monitor cycle (every 2min) [line 1534]
│   - regime_aware_exit_check()  → Regime-based exit logic             [line 3147]
│   - gemini_portfolio_review()  → Gemini AI portfolio optimization    [line 2146] DISABLED V3.2.17
│   - sync_tracker_with_weex()   → Reconcile local state vs WEEX      [line 2777]
│   - quick_cleanup_check()      → Orphan order cleanup (every 30s)    [line 2645]
│   - log_health()               → Health check (every 60s)            [line 2745]
│   - _check_opposite_swap_gates() → V3.1.100 opposite swap TP gate   [line 1974]
│   - _execute_deferred_flips()  → Deferred flip queue execution       [line 2031]
│   - cleanup_dust_positions()   → Remove near-zero positions          [line 2112]
│   - resolve_opposite_sides()   → Close losing side when both exist   [line 2946]
│   - get_market_regime_for_exit() → Market regime detection           [line 3071] DISABLED V3.2.17
│   - fill_rl_outcomes_inline()  → RL training data fill               [line 61]
│
├── smt_nightly_trade_v3_1.py   # Core trading logic (~4819 lines)
│   - MultiPersonaAnalyzer       → 5-persona ensemble                  [line 3648]
│   - place_order()              → WEEX order placement                [line 3809]
│   - close_position_manually()  → Close + cancel orphan triggers      [line 4628]
│   - cancel_all_orders_for_symbol() → Kill all orders (regular + plan)[line 4549]
│   - upload_ai_log_to_weex()    → Competition logging (REQUIRED)      [line 3857]
│   - get_open_positions()       → WEEX positions API                  [line 1979]
│   - get_balance()              → WEEX balance API                    [line 1922]
│   - TRADING_PAIRS              → 7 pairs with tier/symbol config     [line 1816]
│   - TIER_CONFIG                → TP/SL/hold times per tier           [line 1808]
│   - find_chart_based_tp_sl()   → Support/resistance TP/SL            [line 1104]
│   - detect_sideways_market()   → Chop filter (logging only, V3.2.18) [line 671]
│   - get_chart_context()        → Multi-TF Gemini chart context        [line 1284]
│   - TradeTracker               → Local state management               [line 4272]
│
├── watchdog.sh                 # Process watchdog — start this, NOT the daemon directly
│                               # Restarts daemon on crash; hang detection (15min log staleness)
│                               # Enforces single-instance (kills duplicates)
├── telegram_alerts.py          # Telegram notification wrapper
├── cryptoracle_client.py       # Cryptoracle community sentiment API client
├── leverage_manager.py         # Fixed 20x leverage policy (flat, no dynamic scaling)
├── smt_live_dashboard.py       # Real-time HTML dashboard (dark-theme, optional)
├── trade_state_v3_1_7.json     # Live state: active trades, cooldowns, blacklist
├── close_all_positions.py      # Emergency close script (3-pass: cancel orders → close → verify)
├── rl_data_collector.py        # RL training data collection (passive)
├── rl_training_data/           # Daily RL experience files (exp_YYYYMMDD.jsonl)
├── logs/daemon_v3_1_7_*.log    # Daily daemon logs
└── *.bak*, *.patch, fix_*.py   # Version history artifacts (ignore)

v2/                              # BACKUP SNAPSHOT ONLY — do not run
│                               # V3.2.2 snapshot kept for emergency rollback
└── (mirror of v3/ — not in active use)

v4/                              # Future version — not in production
└── (experimental; ignore)
```

## Trading Philosophy — Dip Signals, Fast Banking

**This is a high-frequency, high-margin, small-move strategy. Do NOT evaluate it with classical swing-trade R:R logic.**

Key pattern: **FLOW/WHALE fire first → dip completes → bot enters at the bottom.** FLOW detects buying pressure building and WHALE detects accumulation as the dip is forming — signals are predictive/concurrent, not reactive to a completed move. The bot rides the signal into the low and targets a quick bounce recovery — NOT a full trend reversal.

- **Preferred TP is ~0.5%.** Grab the dip bounce and exit fast. If the move continues, the next 10-min signal cycle catches re-entry. Do NOT hold waiting for a bigger move — the 10-min daemon loop IS the strategy.
- **High win rate > high R:R.** With correct dip entries the win rate is high enough that small TPs are profitable at scale. Classical swing-trade R:R math does not apply here.
- **The chop filter exists for logging only (V3.2.18).** Chop penalties have been removed — the 80% confidence floor + 0.5% minimum TP handle signal quality. Chop data is still computed and logged for diagnostics.
- **FLOW EXTREME overrides chop (V3.2.3):** If FLOW confidence >=85% and matches signal direction, MEDIUM chop penalty is skipped. HIGH chop is reduced to medium (15% penalty). Now historical reference only since V3.2.18 removed all chop penalties.
- **Compound fast.** Many small wins × leverage × reinvestment beats waiting for 3% moves. Capital rotation speed is the edge.

**Do NOT flag small TPs or "poor R:R" — that framing is wrong for this strategy.** A 0.5% TP on a dip-bottom entry with a 10-min re-entry loop is the design, not a flaw.

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
3. The recurring DOGE SL bug (bot sets $0.0999, WEEX shows $0.0936) — DOGE removed in V3.2.11 for this reason.

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
MAX_LEVERAGE = 20                    # Flat 20x on all positions, all tiers (leverage_manager.py)
MIN_CONFIDENCE_TO_TRADE = 0.80      # 80% HARD FLOOR - NO exceptions (V3.1.85)
GLOBAL_TRADE_COOLDOWN = 900          # 15min between trades
SIGNAL_CHECK_INTERVAL = 600          # 10min
POSITION_MONITOR_INTERVAL = 120      # 2min
MAX_TOTAL_POSITIONS = 4              # Default 4 flat slots (V3.2.25: no hard cap — margin guard is limiter)

# Slot system (V3.2.25: no hard slot cap)
# Pairs: BTC, ETH, BNB, LTC, XRP, SOL, ADA (7 pairs, BTC/ETH/BNB re-added V3.2.16)
# Shorts: ALL pairs as of V3.2.18 (was LTC-only)
# If no signals reach 80%, ALL pairs show WAIT — this is expected, not a bug.
# Existing position + same direction signal = WAIT (already have that side).

# Slot overflow (V3.2.25: no slot cap — all 80%+ signals execute; margin guard is limiter)
# confidence >= 85% with full slots: opens additional slot directly
# confidence < 85% with full slots: SLOTS FULL, skip
# No slot swap (removed V3.2.22); resolve_opposite_sides() runs at cycle end

# Regime exit thresholds (V3.2.17: get_market_regime_for_exit() DISABLED)
# Regime fight: 35% margin loss | Hard stop: 45% margin loss
# NOTE: regime-based auto-exits are disabled in V3.2.17 — SL handles exits

# Position sizing (V3.2.25: available-based; V3.2.26: $1000 floor)
# sizing_base = available (free margin from API)
# sizing_base = min(sizing_base, balance * 2.5)   # cap at 2.5× balance
# sizing_base = max(sizing_base, 1000.0)           # floor at $1000 always
# base_size = sizing_base * 0.25 (confidence tiers: 1.0x / 1.25x / 1.5x)
# Per-slot cap REMOVED (V3.2.25) — sizing_base is already free available margin
# Margin guard: skip trades if available margin < $1000 (V3.2.26, was balance×0.15 ≈ $150)
# Sizing cache: 60s TTL; invalidated immediately after each trade (V3.2.28)
# Cycle housekeeping (V3.2.25): dust + orphan sweep at START of every signal cycle

# TP/SL bounds (V3.2.24: no TP floor; V3.2.27: 12H haircut validity; V3.2.28: bad-TP discard; V3.2.29: walk list)
# MIN_TP_PCT removed (V3.2.24) — chart SR is the TP, whatever distance that is
# COMPETITION_FALLBACK_TP = 0.5% — MAX TP CEILING on ALL trades (applied after chart SR + Gemini targeting)
#   Only caps down (ceiling); never raises a low TP. If TP is 0.3%, it stays 0.3%.
#   NOT a fallback for missing SR — if chart SR finds no TP, the trade is DISCARDED (V3.2.29)
# TP method: max high of last 2 complete 1H candles (LONG); min low (SHORT)
#   V3.2.20 fallback: if 2H anchor is at/below entry, scan 12H resistance list
#   V3.2.29: walk full resistance list (ascending LONG / descending SHORT) until one clears entry
#     → if ALL candidates fail haircut check: tp_not_found → COMPETITION_FALLBACK_TP (no SR data)
#     → never use COMPETITION_FALLBACK_TP as a workaround for resistance-too-close
# Final guard (V3.2.28): if TP still wrong-side of entry → discard trade entirely
# TP caps are ceiling-only; only apply when tp_pct > cap threshold (never raise a low TP)
# SL method: lowest wick in last 12H (1H grid) + last 3 4H candles
# MIN_SL_PCT = 1.0%  (floor only — no ceiling, SL sits at real structure)

# Chop filter (V3.2.18: NO PENALTIES — logging only)
# detect_sideways_market(): ADX(14) + Bollinger Bands(40) on 5m candles
# Chop data still computed and logged for diagnostics; no score penalties applied

# Opposite swap gates (V3.1.100)
# OPPOSITE_MIN_AGE_MIN = 20       # Don't flip positions younger than 20 minutes
# OPPOSITE_TP_PROGRESS_BLOCK = 30 # Block flip if position is >= 30% toward TP
# DEFERRED_FLIP_MAX_AGE_MIN = 30  # Deferred signal expires after 30 minutes
# When blocked, signal is queued. After old position closes, deferred flip auto-executes.

# Signal persistence (V3.2.6)
# signal_history tracked per-pair (persists across daemon restarts via trade_state JSON)
# 2-cycle persistence: confidence minimum drops to 80% if same signal fires 2+ consecutive cycles
# 20-minute cutoff when loading history at startup
```

## Trading Pairs & Tiers

**Active pairs (V3.2.16+): BTC, ETH, BNB, LTC, XRP, SOL, ADA — 4 flat slots**

| Pair | Symbol | Tier | TP (fallback) | SL (fallback) | Max Hold | Shorts? |
|------|--------|------|---------------|---------------|----------|---------|
| BTC  | cmt_btcusdt  | 1 | 3.0% | 1.5% | 24h | Yes (V3.2.18) |
| ETH  | cmt_ethusdt  | 1 | 3.0% | 1.5% | 24h | Yes (V3.2.18) |
| BNB  | cmt_bnbusdt  | 2 | 3.5% | 1.5% | 12h | Yes (V3.2.18) |
| LTC  | cmt_ltcusdt  | 2 | 3.5% | 1.5% | 12h | Yes |
| XRP  | cmt_xrpusdt  | 2 | 3.5% | 1.5% | 12h | Yes (V3.2.18) |
| SOL  | cmt_solusdt  | 3 | 3.0% | 1.8% | 8h  | Yes (V3.2.18) |
| ADA  | cmt_adausdt  | 3 | 3.0% | 1.8% | 8h  | Yes (V3.2.18) |

DOGE removed V3.2.11 (erratic SL/orphan behavior). BTC/ETH/BNB re-added V3.2.16.
TP/SL above are **fallback values only** — chart-based SR (find_chart_based_tp_sl) is primary.
Chart TP/SL uses real support/resistance levels with no ceiling.

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

1. **WHALE** — On-chain wallet tracking (Etherscan + Cryptoracle). CEX inflow/outflow signals. Cryptoracle provides community sentiment, prediction market, sentiment momentum Z-score. V3.2.20: BTC and ETH always run Etherscan on-chain flow regardless of `has_whale_data` flag (dual source: Etherscan + Cryptoracle combined).
2. **SENTIMENT** — Gemini 2.5 Flash with Search Grounding. Real-time news analysis.
3. **FLOW** — WEEX order book + trades. Taker ratios, bid/ask depth, funding rates. V3.2.20: order book wall detection (depth limit=200) — nearest significant ask/bid wall passed to Judge as context.
4. **TECHNICAL** — RSI(14), SMA 20/50, 5-candle momentum on 1h candles.
5. **JUDGE** — Aggregates all votes with regime-aware weights. Final LONG/SHORT/WAIT. V3.2.16: receives Gemini chart context (1D + 4H structural levels). V3.2.17: receives signal cycle memory + live chop microstructure. V3.2.20: receives FLOW order book wall prices as additional TP context.

Post-judge filters (V3.2.18): Freshness filter, Regime veto, Consecutive loss block.
Chop filter kept for **logging only** — no score penalties applied as of V3.2.18.

## Supporting Components

### telegram_alerts.py
Simple wrapper sending HTML-formatted trade alerts and errors to Telegram.
Graceful failure — logs error but never crashes the bot. Chat ID `6655570461`.

### cryptoracle_client.py
Cryptoracle prediction market + community sentiment API:
- `CO-A-02-03` — net sentiment direction (>0.5 = bullish)
- `CO-S-01-01` — sentiment momentum Z-score (>1 = overheated, <-1 = panic opportunity)
- `CO-S-01-05` — sentiment-vs-price gap (>2 = mean-reversion signal)
- `CO-P-01-01` — BTC prediction market implied sentiment (1-min granularity)
- 10min TTL cache, 1s rate limit. Returns None on failure (bot falls back to Etherscan-only).

### leverage_manager.py
Fixed flat 20x leverage policy. `calculate_safe_leverage()` always returns 20 regardless of tier, volatility, or regime. No dynamic scaling. MAX_POSITION_PCT = 0.50 cap.

### smt_live_dashboard.py
Generates `smt_dashboard_live.html` — dark-theme web dashboard with live position cards, PnL stats, and trade history. Run with `--watch` for auto-refresh every 60s.

### rl_data_collector.py / rl_training_data/
Passive RL training data collection. Experience files written daily (exp_YYYYMMDD.jsonl). Read-only during competition — do not modify.

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
# Kill existing daemon AND watchdog (watchdog will restart daemon if you don't)
pkill -f watchdog.sh; pkill -f smt_daemon
# Restart via watchdog — ALWAYS start the watchdog, not the daemon directly
cd v3 && nohup bash watchdog.sh >> logs/watchdog.log 2>&1 &
```

### Emergency close all positions
```bash
cd ~/smt-weex-trading-bot && python3 v3/close_all_positions.py
```
3-pass: (1) cancel all TP/SL trigger orders → (2) close all positions → (3) verify + sweep orphans.

### Test mode (no real orders)
```bash
python3 v3/smt_nightly_trade_v3_1.py --test
```

### View live dashboard
```bash
python3 v3/smt_live_dashboard.py --watch  # Auto-refresh every 60s → smt_dashboard_live.html
```

### Validate Cryptoracle connection
```bash
python3 v3/cryptoracle_client.py
```

## Common Bugs to Watch For

1. **Orphan triggers** — Always cancel orders before closing positions
2. **Bad-TP discard (V3.2.28)** — If TP lands at/below entry after haircut, trade is discarded. This means entry was at resistance — correct behavior, not a bug. V3.2.29 tries all SR candidates before discarding.
3. **Gemini timeouts** — 90s timeout + 8s rate limit between calls. Use bulletproof wrapper.
4. **Regime exits too aggressive** — Trust the 2% SL. Only regime-exit at 35%+ margin loss. (Note: regime exits disabled in V3.2.17)
5. **Late entries** — Freshness filter blocks entering after a move already happened
6. **Consecutive losses** — Block re-entry after 2 losses (any type) same direction in 24h (V3.1.91: counts ALL losses, not just force-stops)
7. **AI log missing** — Every trade MUST upload logs or competition results won't count
8. **Premature opposite flips** — V3.1.100 gates: don't flip if position >= 30% toward TP or < 20min old. Blocked signals queue for deferred execution.
9. **FLOW noise** — V3.2.1: if FLOW flips direction 180° from last cycle, confidence is halved. One-cycle flip = noise (single large print); sustained direction = real signal.
10. **TECHNICAL in fear markets** — V3.2.1: TECHNICAL weight halved (0.8→0.4) when F&G < 30. SMA signals lag in fear/capitulation; FLOW + WHALE are more reliable.
11. **Watchdog hang detection** — If daemon logs go stale for 15min, watchdog force-kills and restarts. This is intentional; don't disable it.
12. **Gemini portfolio review disabled** — `gemini_portfolio_review()` is disabled in V3.2.17. Do not re-enable without testing.

## Version Naming

Format: `V3.{MAJOR}.{N}` where N increments with each fix/feature.
Major bumps for strategy pivots (V3.1.x → V3.2.x for dip-signal strategy).
Bump the version number in the daemon startup banner and any new scripts.
Current: V3.2.34. Next change should be V3.2.35.

**Recent version history:**
- V3.2.34: (**CURRENT**) Judge receives WHALE dual-source data for BTC/ETH as a dedicated prompt section — Etherscan on-chain whale flow (net_flow, inflow, outflow, wallet count) and Cryptoracle (net_sentiment, momentum_zscore, sentiment_price_gap, trend) shown separately so Judge can weigh them independently. BTC also shows prediction market (CO-P-01-01). SIGNAL RELIABILITY guideline updated to explain dual-source nature. Non-BTC/ETH pairs unchanged.
- V3.2.33: Revert V3.2.32 — SHORT TP back to `min(lows_1h[1:3])` (deepest wick). The deepest wick IS the real support; when it's within 0.5% of entry the cap doesn't apply and TP lands at the actual chart level. max (nearest wick) was picking meaningless noise wicks closer to entry.
- V3.2.32: SHORT TP anchor changed from `min(lows_1h[1:3])` → `max(lows_1h[1:3])`. Reverted in V3.2.33.
- V3.2.31: Extended 1H candle lookback from 12H (13 candles) to 48H (49 candles) for resistance/support walk. When entry is near recent highs, the 12H pool had only 1-2 candidates above entry and both failed the haircut, discarding high-confidence signals. Competition TP cap (0.5%) applies on top regardless of how far out the level is.
- V3.2.30: WEEX TP/SL confirmation logging — after each trade, query WEEX plan orders and compare actual stored trigger prices vs what was sent. Logs `[WEEX-CONFIRM]` with delta % and *** MISMATCH *** flag for any discrepancy > 0.1%.
- V3.2.29: Walk full resistance list (ascending LONG / descending SHORT) before discarding — if nearest SR is too close after haircut, try next candidate. COMPETITION_FALLBACK_TP promoted from fallback to universal 0.5% MAX TP ceiling on ALL trades (replaces extreme-fear-only and XRP caps). If chart SR finds no valid TP → trade discarded (no fallback %). TP caps are ceiling-only: never raise a low TP below 0.5%.
- V3.2.28: Bad-TP trades discarded — if TP lands at/below entry (haircut + slippage means entry is at resistance), skip the trade entirely. Sizing cache invalidated after each trade so next order sees updated available margin.
- V3.2.27: 12H SR haircut validity check — haircut must still clear entry; final TP direction guard before place_order() (prevents WEEX 40015 rejection).
- V3.2.26: Margin guard threshold fixed at $1000 (was balance×0.15 ≈ $150 — too low, produced tiny rejected orders); sizing base floor fixed at $1000 (same reason).
- V3.2.25: No hard slot cap — margin guard is the natural limiter; cycle housekeeping (dust + orphan sweep) runs at START of every signal cycle; sizing base changed from equity to available free margin, floored at $1000; per-slot cap removed; resolve_opposite_sides() moved to end of cycle.
- V3.2.24: MIN_TP_PCT=0.3% floor removed — chart SR is the TP, no artificial minimum. Flooring to 0.3% was placing TP beyond real resistance so price rejected and TP never filled.
- V3.2.23: Banner cleanup (removed stale slot swap lines); FLOW persona calls regime before data fetches so [REGIME] prints before [FLOW] Depth (not mid-block)
- V3.2.22: Slot swap removed — confidence>=85% opens 5th slot directly (no closing existing positions); opposite sides close immediately (no 15-min wait gate)
- V3.2.21: resolve_opposite_sides() closes OLDER position, not losing side — newer position represents current signal; ctime fallback to PnL-based close if timestamps unavailable
- V3.2.20: 12H SR fallback TP scan when 2H anchor is at/below entry; WHALE always uses Etherscan for BTC/ETH (dual source); FLOW order book wall detection (depth limit=200, top-15 levels, 1.5× avg threshold) fed to Judge as context (not hard TP override)
- V3.2.19: Fee bleed tracking — [FEE] Open per trade, Gross/Fees(R-T)/Net at close
- V3.2.18: CHOP filter penalties removed (logging only); shorts allowed for ALL pairs; 80% floor + 0.5% TP protection; trust the signals
- V3.2.17: Stale position auto-close removed; extreme fear TP cap bug fixed (TypeError silently skipped cap); Gemini portfolio review disabled
- V3.2.16: BTC/ETH/BNB re-added; 7 pairs; 4 flat slots; Gemini chart context (1D+4H) for TP targeting; XRP TP cap only applies without structural target
- V3.2.15: (previous stable)
- V3.2.14: Flat slot cap — no equity scaling; LTC/XRP/SOL/ADA only; 3 slots
- V3.2.12: TP anchor 6H→2H; extreme fear TP cap extended to LONG signals; XRP/ADA 2-3% TP reduced to 0.5% cap
- V3.2.11: DOGE removed (erratic SL/orphan behavior); capitulation swap threshold -0.25%→-0.10%

**CRITICAL RULE (V3.1.85+): The 80% confidence floor is ABSOLUTE.**
Never add session discounts, contrarian boosts, or any other override that
lowers the trading threshold below 80%. Quality over quantity wins competitions.

## Claude Code Rules (MANDATORY)

1. **Plan mode = plan ONLY.** Never write code, create commits, or push during plan mode. Plan mode is for research and writing the plan file — nothing else.
2. **Always confirm before commit/push.** Never commit or push code without explicit user approval. Present the changes, wait for "go ahead" or equivalent.
3. **Do what you said.** If you propose approach X, implement approach X. Never silently switch to approach Y. If you realize a different approach is better mid-implementation, stop and confirm the change with the user first.
4. **No surprise deployments.** This is a live trading bot handling real money in a competition. Every change must be reviewed and approved before it touches the branch.

## Files to Ignore

- `v3/*.bak*`, `v3/*.backup*`, `v3/*.patch`, `v3/*.orig`, `v3/*.rej`, `v3/*.save*` — backups/patches, don't modify
- `v3/fix_*.py`, `v3/patch_*.py`, `v3/apply_*.py`, `v3/apply_*.sh` — one-shot fix scripts, already applied
- `v3/close_btc*.py`, `v3/close_eth*.py`, `v3/close_bnb*.py`, etc. — ad-hoc manual close scripts
- `v3/pm_close_eth_btc.py`, `v3/pm_close_ltc_bnb.py` — ad-hoc portfolio manager close scripts (Feb 2026)
- `v2/` — backup snapshot, not in active use. Do not run.
- `v4/` — future version, not in production
- `v3/all_rl_data.jsonl`, `v3/rl_training_data/` — RL training data, read-only during competition
- `data/`, `models/`, `notebooks/` — analysis artifacts
- `src/` — legacy signal pipeline (pre-V3), not in active use
- `smt_nightly_trade.py`, `smt_nightly_trade_v2.py` — legacy root-level scripts, superseded by v3/
