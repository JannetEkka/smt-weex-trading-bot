# CLAUDE.md - SMT WEEX Trading Bot

## What This Is

AI trading bot for the **WEEX AI Wars: Alpha Awakens** competition (Feb 8-23, 2026).
Trades 7 crypto pairs on WEEX futures using a 5-persona ensemble (Whale, Sentiment, Flow, Technical, Judge).
Starting balance $10,000 USDT (Finals). Prelims (was $1K): +566% ROI, #2 overall.

**Current version: V3.2.93** — all production code is in `v3/`.

## Architecture

```
v3/                              # PRIMARY production folder
├── smt_daemon_v3_1.py          # 24/7 daemon loop (~3660 lines)
│   - check_trading_signals()    → Signal check cycle (every 10min)   [line 591]
│   - monitor_positions()        → Position monitor cycle (every 2min) [line 1680]
│   - regime_aware_exit_check()  → Regime-based exit logic             [line 3384]
│   - gemini_portfolio_review()  → Gemini AI portfolio optimization    [line 2353] DISABLED V3.2.17
│   - sync_tracker_with_weex()   → Reconcile local state vs WEEX      [line 2997]
│   - quick_cleanup_check()      → Orphan order cleanup (every 30s)    [line 2854]
│   - log_health()               → Health check (every 60s)            [line 2965]
│   - _check_opposite_swap_gates() → V3.1.100 opposite swap TP gate   [line 2169]
│   - _execute_deferred_flips()  → Deferred flip queue execution       [line 2238]
│   - cleanup_dust_positions()   → Remove near-zero positions          [line 2319]
│   - resolve_opposite_sides()   → Close older side when both exist    [line 3170]
│   - get_market_regime_for_exit() → Market regime detection           [line 3308] DISABLED V3.2.17
│   - _is_macro_blackout()       → V3.2.48 macro event blackout gate  [line 435]
│   - _is_weekend_liquidity_mode() → V3.2.48 weekend/holiday filter   [line 464]
│   - _move_sl_to_breakeven()    → V3.2.46 breakeven SL placement     [line 1621]
│   - fill_rl_outcomes_inline()  → RL training data fill               [line 61]
│
├── smt_nightly_trade_v3_1.py   # Core trading logic (~5250 lines)
│   - MultiPersonaAnalyzer       → 5-persona ensemble                  [line 3951]
│   - place_order()              → WEEX order placement                [line 4120]
│   - close_position_manually()  → Close + cancel orphan triggers      [line 5059]
│   - get_recent_close_order_id() → Query WEEX for last filled close orderId [line 4893]
│   - _fetch_plan_order_ids()    → Fetch TP/SL plan IDs at open (V3.2.55) [line 4989]
│   - cancel_all_orders_for_symbol() → Kill all orders (regular + plan)[line 4848]
│   - upload_ai_log_to_weex()    → Competition logging (REQUIRED)      [line 4168]
│   - get_open_positions()       → WEEX positions API                  [line 2074]
│   - get_balance()              → WEEX balance API                    [line 2017]
│   - TRADING_PAIRS              → 7 pairs with tier/symbol config     [line 1911]
│   - TIER_CONFIG                → TP/SL/hold times per tier           [line 1903]
│   - find_chart_based_tp_sl()   → Support/resistance TP/SL            [line 1105]
│   - detect_sideways_market()   → Chop filter (logging only, V3.2.18) [line 672]
│   - get_chart_context()        → Multi-TF Gemini chart context        [line 1352]
│   - TradeTracker               → Local state management               [line 4569]
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

## Trading Philosophy — Swing Trade with Momentum Confirmation (V3.2.88+)

**This is a swing-trade strategy. Personas analyze on hours-to-days timeframes; execution matches.**

Key insight: **Personas give DIRECTION correctly (hours-ahead analysis) but TIMING requires momentum confirmation.** The bot trusts the signal direction but waits for price to start moving that way before entering. A late entry that catches the move beats an early entry that eats the SL.

- **Hold times: T1=8H, T2=6H, T3=4H.** These are swing windows, not scalp windows. Chart SR targets structural 4H/Daily levels.
- **TP targets structural levels.** Chart SR walks 2H→4H→48H resistance/support. Per-pair TP ceilings: BTC/ETH/SOL 2.0%, alts 1.5%. TP_HAIRCUT = 0.95 (target 95% of SR distance).
- **R:R must be ≥ 0.67:1.** MIN_RR_RATIO = 0.67 → TP must be ≥ 1.0% when SL is 1.5%. Trades with worse R:R are rejected.
- **Momentum confirmation gate.** Daemon checks 1h AND 15m momentum before entry. If 1h opposes AND 15m hasn't turned → BLOCK. If 1h opposes but 15m is turning → ALLOW (the turn is starting).
- **TECHNICAL momentum conflict = HARD BLOCK.** If 1h momentum opposes TECHNICAL's signal at > 0.20%, TECHNICAL returns NEUTRAL (not capped at 65%). Judge sees fewer agreeing personas.
- **The chop filter exists for logging only (V3.2.18).** Chop penalties have been removed — the 85% confidence floor (V3.2.57) + 0.50% minimum TP handle signal quality.
- **EMA snapback exit DISABLED (V3.2.88).** This was a scalp exit tool (8-EMA on 5m candles). Incompatible with swing holds. Breakeven SL + peak-fade handle exits.

**Do NOT re-add scalp-era TP ceilings (0.50-0.80%).** Those destroyed R:R when ATR widened SL to 1.5%, producing 0.5:1 R:R trades.

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
- Global trade cooldown: 10 minutes between trades (V3.2.57, was 15min — final stretch velocity)

## Key Constants & Thresholds

```python
MAX_LEVERAGE = 20                    # Flat 20x on all positions, all tiers (leverage_manager.py)
MIN_CONFIDENCE_TO_TRADE = 0.85      # V3.2.57: 85% floor (was 80%). 1-slot mode: 80-84% at 20% sizing blocks slot from better trades
GLOBAL_TRADE_COOLDOWN = 600          # V3.2.57: 10min between trades (was 900/15min)
SIGNAL_CHECK_INTERVAL = 600          # 10min
POSITION_MONITOR_INTERVAL = 120      # 2min
MAX_TOTAL_POSITIONS = 2              # V3.2.59: 2-slot cross-margin — diversification without cascading risk
# CONFIDENCE_EXTRA_SLOT = 0.90      # V3.2.46: DISABLED — extra slot bypass removed
TAKER_FEE_RATE = 0.0008              # V3.2.50: 0.08%/side taker fee (corrected from 0.0006); 3.2% margin round-trip at 20x

# Slot system (V3.2.59: 2-slot cross-margin strategy)
# Pairs: BTC, ETH, BNB, LTC, XRP, SOL, ADA (7 pairs, BTC/ETH/BNB re-added V3.2.16)
# Shorts: ALL pairs as of V3.2.18 (was LTC-only)
# If no signals reach 85%, ALL pairs show WAIT — this is expected, not a bug.
# Existing position + same direction signal = WAIT (already have that side).
# V3.2.59: Cross margin = shared collateral. Two 20x trades max.
#   Diversification benefit without excessive cascading risk.
#   Low equity (<$1500) falls back to 1 slot.
#   can_open_new = not low_equity_mode AND available_slots > 0

# Regime exit thresholds (V3.2.17: get_market_regime_for_exit() DISABLED)
# Regime fight: 35% margin loss | Hard stop: 45% margin loss
# NOTE: regime-based auto-exits are disabled in V3.2.17 — SL handles exits

# Position sizing (V3.2.46: confidence-scaled for 1-slot cross-margin)
# sizing_base = available (free margin from API)
# sizing_base = min(sizing_base, balance * 2.5)   # cap at 2.5× balance
# sizing_base = max(sizing_base, 500.0)            # V3.2.57: floor lowered $1000→$500 (meaningful size during drawdowns)
# V3.2.57: Confidence-scaled sizing (MIN_CONFIDENCE now 85%):
#   85-89% confidence: sizing_base * 0.35  (standard — all trades now in this band or higher)
#   90%+   confidence: sizing_base * 0.50  (maximum conviction)
# SOL capped at 30% of sizing_base via PAIR_MAX_POSITION_PCT (high beta risk control)
# Margin guard: skip trades if available margin < $1000 (V3.2.26)
# Sizing cache: 60s TTL; invalidated immediately after each trade (V3.2.28)
# Cycle housekeeping (V3.2.25): dust + orphan sweep at START of every signal cycle

# Circuit breaker (V3.2.46: post-loss cooldowns for cross-margin safety)
# COOLDOWN_MULTIPLIERS × base hours (T1=2h, T2=1.5h, T3=1h):
#   sl_hit: 1.0      → 60min+ cooldown after SL hit
#   force_stop: 1.0   → 60min+ cooldown after force stop
#   early_exit: 0.5   → 30-60min cooldown after early exit
#   tp_hit: 0.0       → immediate re-entry OK (trend confirmed)
#   max_hold/profit_lock/peak_fade/velocity_exit: 0.0  → no cooldown
# Cross margin = shared collateral — revenge trading is account-ending.

# TP/SL bounds (V3.2.41: per-pair ceiling + MAX_SL + 4H anchor; V3.2.46: SL cap instead of discard)
# MIN_TP_PCT removed (V3.2.24) — chart SR is the TP, whatever distance that is
# MIN_VIABLE_TP_PCT = 0.50% (V3.2.70, was 0.30% in V3.2.67) — SKIP SR levels < 0.50% from entry
#   Aligned with MIN_RR_RATIO(0.33) × MAX_SL_PCT(1.5%) = 0.50%. Ensures any selected TP can pass R:R.
#   Forces TP walk past 2H micro-bounce levels to 4H structural anchor where real resistance lives.
#   In chop, 2H levels are 0.30-0.45% (noise); 4H levels are 0.50-1.0% (structural).
#   Effective TP range after all guards: [0.40%, per-pair ceiling]
# PAIR_TP_CEILING (V3.2.89) — per-pair TP max for SWING TRADES:
#   BTC=2.0%, ETH=2.0%, SOL=2.0%, BNB=1.5%, LTC=1.5%, XRP=1.5%, ADA=1.5%
#   V3.2.89: raised from scalp-era 0.50-0.80%. Old ceilings destroyed R:R (ADA: 0.80% TP / 1.50% SL = 0.53:1).
#   Ceiling-only: never raises a low TP. Falls back to COMPETITION_FALLBACK_TP for unlisted pairs.
#   NOT a fallback for missing SR — if chart SR finds no TP, the trade is DISCARDED (V3.2.29)
# TP method: 2H anchor → 4H anchor (V3.2.41) → 48H walk
#   Step 1: max high of last 2 complete 1H candles (LONG); min low (SHORT) [V3.2.33]
#   Step 2 (V3.2.41): 4H anchor — max high of last 2 complete 4H candles (limit=9, candles[1:3])
#     If >= MIN_VIABLE_TP_PCT from entry → use as TP (method="chart_4h")
#   Step 3: if 2H+4H anchors fail, scan 48H resistance list [V3.2.31: 12H→48H]
#   V3.2.31: haircut removed — raw resistance IS the TP (ceiling handles sizing)
#   Walk full resistance list (ascending LONG / descending SHORT) until one clears MIN_VIABLE_TP_PCT
#     → if ALL candidates fail: tp_not_found → trade DISCARDED (no fallback %)
# Final guard (V3.2.28): if TP still wrong-side of entry → discard trade entirely
# TP caps are ceiling-only; only apply when tp_pct > cap threshold (never raise a low TP)
# SL method: lowest wick in last 12H (1H grid) + last 3 4H candles
# MIN_SL_PCT = 1.0%  (floor — SL must be at least 1% from entry)
# MAX_SL_PCT = 1.5%  (V3.2.41 ceiling; V3.2.46: CAP instead of discard)
#   4H anchors can produce wide SLs; 1.5% = 30% margin loss at 20x (survivable)
#   Hard liquidation at 20x requires ~4.5% adverse move; 1.5% is well clear

# Breakeven SL + peak-fade (V3.2.88/V3.2.46)
# BREAKEVEN_TRIGGER_PCT = 0.8   # V3.2.88: Move SL to entry when trade reaches +0.8% profit (was 0.4%)
#   Swing trades need room to breathe. 0.4% was too tight for 4-8H holds.
#   Protects principal once a trade is in meaningful profit. Stored per-trade: sl_moved_to_breakeven=True
# Peak-fade soft stop (fires ONLY when SL not yet at breakeven — mutually exclusive with BE-SL):
# PEAK_FADE_MIN_PEAK   = {1: 0.80, 2: 1.00, 3: 1.00}   # V3.2.88: swing-level (was 0.30/0.45/0.45)
# PEAK_FADE_TRIGGER_PCT = {1: 0.40, 2: 0.50, 3: 0.50}   # V3.2.88: swing-level (was 0.15/0.25/0.25)
#   T1 (BTC/ETH): tighter (majors have less wick noise). T2/T3 (altcoins): more room for swings.
#   exit_reason "peak_fade_T{n}" → zero cooldown (profit was taken).
# Velocity exit (V3.2.88): exits flat/stale trades that never moved — SWING-LEVEL TIMING
# VELOCITY_EXIT_MINUTES = {1: 180, 2: 120, 3: 90}  # V3.2.88: swing windows (was 75/60/50)
# VELOCITY_MIN_PEAK_PCT = 0.15   # V3.2.88: raised from 0.10% (swing trades should show some move in 2-3 hours)
#   Distinct from early_exit (needs actual loss) and peak_fade (needs peak then reversal).
#   Covers "trade opened but price never moved in our direction" — thesis is dead.
#   exit_reason "velocity_exit" → zero cooldown (slot freed immediately).
#
# FLOW contra exit (V3.2.74): exits underwater positions when FLOW shows extreme opposite
# Taker ratio < 0.15 for LONG (extreme selling) or > 7.0 for SHORT (extreme buying)
# Age gate: same as velocity exit per tier (T1=75m, T2=60m, T3=50m)
# Only fires when: pnl < 0, no BE-SL placed. Trade thesis invalidated by real-time orderbook.
# exit_reason "flow_contra_exit" → zero cooldown (thesis dead, free slot)
#
# Near-TP grace (V3.2.76): max_hold exit skipped when trade >= 60% toward TP
# NEAR_TP_GRACE_PCT = 0.60       # TP progress threshold
# NEAR_TP_GRACE_MINUTES = 15     # Extra time granted past max_hold
# Prevents killing trades that are actively approaching their target.
# After grace expires, max_hold fires normally. Applied in both monitor_positions + pre-cycle sweep.
#
# Judge thesis degradation exit (V3.2.76): closes stale positions when Judge says WAIT
# Runs in check_trading_signals() after Judge evaluates each pair.
# If Judge returns WAIT for a pair we're holding:
#   Gate 1: trade age > early_exit_hours (T1=1h, T2=45m, T3=30m)
#   Gate 2: PnL < BREAKEVEN_TRIGGER_PCT (0.4%) — if above, BE-SL handles it
#   Gate 3: BE-SL not placed (WEEX SL handles those)
# Uses structured decision enum ONLY — NO string parsing (avoids ANTI-WAIT V3.2.37 disaster).
# exit_reason "thesis_degraded" → zero cooldown (thesis dead, free slot immediately)

# 8-EMA snap-back exit (V3.2.68): mean-reversion exit for dip-bounce trades
# Computes 8-period EMA on 5m candles in monitor_positions()
# Fires when: age >= 10 min, peak >= 0.20%, pnl > 0, BE-SL not yet placed
# LONG: price crosses ABOVE 8-EMA = snap-back complete → exit
# SHORT: price crosses BELOW 8-EMA = snap-back complete → exit
# exit_reason "ema_snapback" → zero cooldown (profit taken at mean reversion)
# Mutually exclusive with BE-SL (once BE-SL placed, WEEX SL handles downside)

# Volume spike detection (V3.2.68): confirms institutional dip/peak moves
# In TECHNICAL persona: avg vol of last 3 5m candles vs avg of prior 12 candles
# vol_ratio >= 2.0 = volume spike confirmed → +0.25 confidence to dominant direction
# Prevents entering low-volume fakeout dips

# Entry velocity check (V3.2.68): ensures active dip, not slow grind
# In TECHNICAL persona: velocity_15m = price change over last 3 5m candles
# |velocity| > 0.20% = sharp move confirmed → +0.35 confidence to direction
# Drop = LONG signal (buying the dip), Rally = SHORT signal (fading the peak)

# Range gate (V3.2.68/V3.2.69): 12H gate 55/45 + 2H dip/peak override
# _RANGE_LONG_BLOCK = 55  # LONGs must be in lower half of 12H range (dip territory)
# _RANGE_SHORT_BLOCK = 45  # SHORTs must be in upper half (peak territory)
# V3.2.69: 2H OVERRIDE — if TECHNICAL's 2H range_pos < 30% (LONG) or > 70% (SHORT),
#   bypass the 12H gate. Short-term dips within uptrends are valid entries.
#   _technical_range_pos_cache stores 2H range from TECHNICAL persona.
#   Fixes: BNB 90% blocked at 12H=77%/2H=11%, SOL 85% blocked at 12H=57%/2H=7%.

# TP haircut (V3.2.68): TP_HAIRCUT = 0.90
# All SR-based TPs target 90% of distance from entry to S/R level
# Price typically reverses before reaching exact S/R → 90% captures the move

# FLOW flip boost (V3.2.68): replaces V3.2.1 flip discount
# SHORT→LONG flip at range < 45% = +15% boost (cap 0.95). Dip signal.
# LONG→SHORT flip at range > 55% = +15% boost (cap 0.95). Peak signal.
# Mid-range flips (45-55%) = neutral (no penalty, no boost).
# Replaces blanket 50% discount that killed confidence at dip bottoms.

# Emergency flip (V3.2.51)
# EMERGENCY_FLIP_CONFIDENCE = 0.90  # At 90%+ opposite confidence, bypass the 20-min age gate
#   TP proximity gate (>= 30% toward TP) is STILL enforced — never abandon a nearly-won trade.

# Plan order IDs stored at open (V3.2.50/V3.2.55)
# _fetch_plan_order_ids() fetches tp_plan_order_id/sl_plan_order_id immediately after placement.
#   Uses 2s initial sleep + 2 retries (2s apart) to handle WEEX registration lag.
#   Stored in trade state → used deterministically by daemon for AI log upload (no guesswork).

# Chop filter (V3.2.18: NO PENALTIES — logging only)
# detect_sideways_market(): ADX(14) + Bollinger Bands(40) on 5m candles
# Chop data still computed and logged for diagnostics; no score penalties applied

# Opposite swap gates (V3.1.100)
# OPPOSITE_MIN_AGE_MIN = 20       # Don't flip positions younger than 20 minutes
# OPPOSITE_TP_PROGRESS_BLOCK = 30 # Block flip if position is >= 30% toward TP
# DEFERRED_FLIP_MAX_AGE_MIN = 30  # Deferred signal expires after 30 minutes
# When blocked, signal is queued. After old position closes, deferred flip auto-executes.
# V3.2.51: Emergency flip bypasses age gate at 90%+ — TP proximity gate always preserved.

# Macro defense (V3.2.48/V3.2.56)
# MACRO_BLACKOUT_WINDOWS: list of (start_utc, end_utc, label) — skip ALL signal cycles in window
#   Example: ("2026-02-20 13:15", "2026-02-20 14:00", "US Core PCE Price Index")
# V3.2.56 BLACKOUT EXIT: monitor_positions() closes UNPROFITABLE positions when blackout activates.
#   Profitable positions ride with existing SL — no forced exit if in profit.
# Weekend liquidity mode: Sat/Sun → restrict to WEEKEND_ALLOWED_PAIRS = {BTC, ETH, SOL}
#   Altcoins (BNB, LTC, XRP, ADA) have 20-25% lower weekend volume → wider spreads, fakeouts
# Holiday thin liquidity: HOLIDAY_THIN_LIQUIDITY dict — bank holidays extend thin-book conditions
#   before 12 UTC only (US session restores liquidity)
# Funding hold-cost (V3.2.56 direction-aware): per-pair funding rate × 20x = margin drag
#   Judge told "paying" or "receiving" based on position direction vs funding rate sign.
#   Negative funding + LONG = bot RECEIVES; positive funding + LONG = bot PAYS. Passed as context.
# Macro event context: date-sensitive intelligence (PCE, token unlocks, holidays) fed to Judge

# Pre-cycle exit sweep (V3.2.52)
# max_hold/force_exit/early_exit checked at START of check_trading_signals() BEFORE 7-pair analysis.
# Positions closed with full AI log — fixes 6-min blind spot where expired trades weren't closed
# until AFTER the entire Gemini analysis loop completed.

# Signal persistence (V3.2.6)
# signal_history tracked per-pair (persists across daemon restarts via trade_state JSON)
# 2-cycle persistence: confidence minimum drops to 80% if same signal fires 2+ consecutive cycles
# 20-minute cutoff when loading history at startup
```

## Trading Pairs & Tiers

**Active pairs (V3.2.16+): BTC, ETH, BNB, LTC, XRP, SOL, ADA — 2 slots (V3.2.59 cross-margin)**

| Pair | Symbol | Tier | TP (ceiling) | SL (fallback) | Max Hold | Early Exit | Shorts? |
|------|--------|------|-------------|---------------|----------|------------|---------|
| BTC  | cmt_btcusdt  | 1 | 2.0% | 1.5% | 8h  | 2h   | Yes (V3.2.18) |
| ETH  | cmt_ethusdt  | 1 | 2.0% | 1.5% | 8h  | 2h   | Yes (V3.2.18) |
| BNB  | cmt_bnbusdt  | 2 | 1.5% | 1.5% | 6h  | 1.5h | Yes (V3.2.18) |
| LTC  | cmt_ltcusdt  | 2 | 1.5% | 1.5% | 6h  | 1.5h | Yes |
| XRP  | cmt_xrpusdt  | 2 | 1.5% | 1.5% | 6h  | 1.5h | Yes (V3.2.18) |
| SOL  | cmt_solusdt  | 3 | 2.0% | 1.8% | 4h  | 1h   | Yes (V3.2.18) |
| ADA  | cmt_adausdt  | 3 | 1.5% | 1.8% | 4h  | 1h   | Yes (V3.2.18) |

V3.2.89: **Swing trade guards** — TP ceilings raised, R:R guard, momentum gate, execution sort.
V3.2.88: **Long term pivot** — swing hold times (8/6/4H), momentum confirmation, EMA snapback disabled.
Previously: T1=24h/6h, T2=12h/4h, T3=8h/3h. Single-slot mode means stale positions block all capital.
DOGE removed V3.2.11 (erratic SL/orphan behavior). BTC/ETH/BNB re-added V3.2.16.
TP/SL above are **fallback values only** — chart-based SR (find_chart_based_tp_sl) is primary.
Chart TP/SL uses real support/resistance levels; PAIR_TP_CEILING caps per pair.

## WEEX API

Base URL: `https://api-contract.weex.com`
Auth: HMAC-SHA256 signature via `weex_headers()`.

Key endpoints:
- `GET /capi/v2/account/balance` — balance
- `GET /capi/v2/account/allPosition` — open positions
- `POST /capi/v2/order/placeOrder` — place order (type: 1=open long, 2=open short, 3=close long, 4=close short)
- `POST /capi/v2/order/cancel` — cancel regular order
- `GET /capi/v2/order/currentPlan` — list trigger orders (TP/SL); used by `_fetch_plan_order_ids()` (V3.2.62 fix: was `/plan_orders` which returned 404)
- `POST /capi/v2/order/cancel_plan` — cancel trigger order
- `POST /capi/v2/order/uploadAiLog` — upload AI decision log
- `GET /capi/v2/order/history` — filled order history; used by `get_recent_close_order_id()`

## 5-Persona System

1. **WHALE** — On-chain wallet tracking (Etherscan + Cryptoracle). CEX inflow/outflow signals. Cryptoracle provides community sentiment, prediction market, sentiment momentum Z-score. V3.2.20: BTC and ETH always run Etherscan on-chain flow regardless of `has_whale_data` flag (dual source: Etherscan + Cryptoracle combined).
2. **SENTIMENT** — Gemini 2.5 Flash with Search Grounding. Macro news analyst role (V3.2.41): catalysts, macro_bias, volatility_risk, volatility_event, pair_specific_news — qualitative only, no price targets. V3.2.45: dynamic date injection in search queries + regex JSON parser immune to grounding citation injection.
3. **FLOW** — WEEX order book + trades. Taker ratios, bid/ask depth, funding rates. V3.2.20: order book wall detection (depth limit=200) — nearest significant ask/bid wall passed to Judge as context. V3.2.68: FLOW flip (SHORT→LONG or LONG→SHORT) at dip/peak territory = +15% confidence boost (was 50% discount). Mid-range flips = neutral.
4. **TECHNICAL** — V3.2.68: 5m RSI(14) + VWAP + 30m momentum + 2H range position + volume spike (2x avg) + entry velocity (0.20%/15m). Was: RSI(14), SMA 20/50 on 1H candles (14-hour lookback, blind to 30-60 min dips).
5. **JUDGE** — Aggregates all votes with regime-aware weights. Final LONG/SHORT/WAIT. V3.2.16: receives Gemini chart context (1D + 4H structural levels). V3.2.17: receives signal cycle memory + live chop microstructure. V3.2.20: receives FLOW order book wall prices as additional TP context. V3.2.37: ANTI-WAIT removed — if Judge returns WAIT, it is WAIT with no overrides. V3.2.45: regex JSON extractor immune to grounding citation markers. V3.2.48: receives funding hold-cost and macro event context. V3.2.56: funding direction-aware — told "paying" or "receiving" based on position side vs rate sign (fixes bonus mislabeled as drag for LONGs on negative funding). V3.2.68: DIP/PEAK DETECTION PROTOCOL — FLOW flip is the dip signal, 2-persona dip rule (FLOW+TECHNICAL sufficient for 85% when WHALE/SENT neutral), explicit flip visibility in signal_history. V3.2.74: CATALYST DRIVE rule (SENTIMENT named catalyst + FLOW >=60% = 85%+, bypasses 3-persona requirement) + CONTINUATION HOLD thesis check (re-evaluate honestly for open positions, return WAIT/opposite if signals degraded).

Post-judge filters (V3.2.18): Freshness filter, Regime veto, Consecutive loss block.
Chop filter kept for **logging only** — no score penalties applied as of V3.2.18.

**ANTI-WAIT (V3.2.37 REMOVED):** Previously, if Judge returned WAIT, a post-Judge override could flip it to LONG/SHORT via persona consensus (2+ personas agree at >=70%) or keyword scanning (counting direction words in Judge's reasoning). Both were removed — Gemini's reasoning text often contains direction words in WAITs, causing spurious flips. WAIT = WAIT, no overrides.

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
8. **Premature opposite flips** — V3.1.100 gates: don't flip if position >= 30% toward TP or < 20min old. Blocked signals queue for deferred execution. V3.2.35: opposite signal now closes existing position first (via `close_position_manually()`), then opens the new direction — no more dual LONG+SHORT on same pair.
9. **FLOW flip = dip signal (V3.2.68)** — FLOW flip at dip/peak territory (range position < 45% for LONG, > 55% for SHORT) gets +15% confidence boost. Mid-range flips: neutral (no penalty, no boost). Replaces V3.2.1 behavior where ALL flips were penalized 50% — that halved confidence at exactly the moment dip-bounce entries need it most.
10. **TECHNICAL in fear markets** — V3.2.1: TECHNICAL weight halved (0.8→0.4) when F&G < 30. SMA signals lag in fear/capitulation; FLOW + WHALE are more reliable.
11. **Watchdog hang detection** — If daemon logs go stale for 15min, watchdog force-kills and restarts. This is intentional; don't disable it.
12. **Gemini portfolio review disabled** — `gemini_portfolio_review()` is disabled in V3.2.17. Do not re-enable without testing.
13. **ANTI-WAIT removed (V3.2.37)** — Do NOT re-add post-Judge direction overrides. Gemini's WAIT reasoning text often contains direction keywords (e.g. "WHALE (LONG 63%)" explaining why it's waiting), which caused the keyword-scanning fallback to flip WAIT→LONG on genuinely mixed signals. Trust the Judge.
14. **2-slot cross-margin (V3.2.59)** — MAX_TOTAL_POSITIONS = 2 (was 1 in V3.2.46). Two concurrent 20x trades max. Low equity (<$1500) falls back to 1 slot. Circuit breaker enforces 60min+ cooldown after losses.
15. **Gemini citation injection (V3.2.45)** — Gemini Search Grounding can inject `[1][2]` citation markers into JSON responses, breaking `json.loads()`. The regex JSON parser (`re.search(r'\{.*\}', re.DOTALL)`) handles this. Do not revert to naive JSON parsing.
16. **Macro blackout bypass (V3.2.48)** — `_is_macro_blackout()` skips the entire signal cycle during high-impact data releases. Do not add "check anyway" logic — macro volatility can spike 1-3% in seconds, invalidating any signal computed pre-release.
17. **Weekend thin liquidity (V3.2.48)** — Altcoins (BNB, LTC, XRP, ADA) restricted during Sat/Sun + bank holidays. Do not override — 20-25% lower volume means wider spreads and fakeout wicks.
18. **Breakeven SL + peak-fade mutual exclusion (V3.2.46/V3.2.54)** — `_move_sl_to_breakeven()` fires at +0.4% gain. Peak-fade fires ONLY when `sl_moved_to_breakeven=False` — the two are mutually exclusive. Once BE-SL is placed, WEEX's SL handles the downside; peak-fade is disabled (gating: `not trade.get("sl_moved_to_breakeven", False)`). Do not remove this gate.
19. **Emergency flip TP proximity gate preserved (V3.2.51)** — At 90%+ opposite confidence, the 20-min age gate is bypassed (EMERGENCY_FLIP_CONFIDENCE). The 30% TP proximity gate is ALWAYS enforced regardless of confidence — abandoning a 30%+ toward-TP trade costs real fees and foregone profit. Do not remove the TP proximity check for emergency flips.
20. **Plan order ID registration lag (V3.2.55)** — WEEX registers TP/SL plan orders with a brief lag after placement. `_fetch_plan_order_ids()` uses 2s initial sleep + 2 retries (2s apart) on 404/empty response. Do not remove retries — the fallback `stored-ID=None` was eliminated in V3.2.55 to prevent AI log uploads with missing order IDs.
21. **Pre-cycle exit sweep order (V3.2.52)** — Expired positions (max_hold/force_exit/early_exit) are closed at the START of `check_trading_signals()`, BEFORE the 7-pair Gemini analysis loop runs. Do not move this after the analysis — it fixes the 6-min blind spot where expired positions weren't closed until after a full analysis cycle.
22. **Funding rate direction (V3.2.56)** — Judge prompt now specifies whether the bot is "paying" or "receiving" funding for each position. Negative funding + LONG = bot receives funding (a bonus, not a drag). Positive funding + LONG = bot pays. Was previously always labeled as "drag" regardless of direction, causing incorrect Judge reasoning on favorable funding positions.
23. **Velocity exit vs peak-fade vs early exit (V3.2.57)** — Three distinct exit mechanisms: (1) early_exit = trade open > X min AND losing > -1%; (2) peak_fade = trade peaked then reversed (peak > threshold, current < peak - trigger); (3) velocity_exit = trade never moved at all (peak < 0.15% after 40 min). Velocity exit gate: `peak_pnl_pct < VELOCITY_MIN_PEAK_PCT` — fires ONLY when breakeven SL NOT placed (peak too low to trigger BE-SL). All three are distinct conditions; do not merge or confuse them.
24. **Judge hold-time mismatch (V3.2.57 FIX)** — Prior versions told Judge "4-5H planning horizon" but TIER_CONFIG kills at 1.5-3H. Judge was picking unreachable TP targets. V3.2.57 adds explicit HOLD TIME LIMITS section to Judge prompt with actual per-tier limits. Do not re-add "4-5H" references.
25. **Plan orders endpoint 404 (V3.2.64 FIX)** — `_fetch_plan_order_ids()` and `cancel_all_orders_for_symbol()` were using `/capi/v2/order/plan_orders` which returns HTTP 404. Correct endpoint is `/capi/v2/order/currentPlan`. This was causing: (1) plan order IDs stored as None at trade open, (2) orphan TP/SL triggers not being cleaned up on manual close, (3) mismatch detection disabled. The runner code (line 5504) was already using the correct endpoint.
26. **8-EMA snap-back exit mutual exclusion (V3.2.68)** — `ema_snapback` exit fires ONLY when `sl_moved_to_breakeven=False` — mutually exclusive with breakeven SL (same gate as peak_fade). Once BE-SL is placed, WEEX SL handles exit. Do not remove this gate.
27. **FLOW flip boost ordering (V3.2.68)** — `get_chart_context()` MUST run BEFORE persona analysis so `_chart_range_position_cache` is populated when FLOW flip checks range position. Moving `get_chart_context()` after persona analysis makes the flip boost a no-op (cache empty/stale).
28. **FLOW flip boost cap (V3.2.68)** — Flip boost cap is 0.95 (not 0.85). FLOW's internal cap is 0.85, but the flip boost in `MultiPersonaAnalyzer.analyze()` raises this to `min(0.95, conf + 0.15)`. Do not lower the cap back to 0.85 — that makes the boost a no-op.
29. **Judge 2-persona dip rule (V3.2.68)** — When FLOW flip + TECHNICAL oversold + range extreme + WHALE/SENT neutral, 2 personas are sufficient for 85% confidence. Do not re-add the 3-persona requirement for dip scenarios — WHALE (backward-looking) and SENTIMENT (qualitative) are structurally blind to real-time 30-60 min dips.
30. **Range gate 2H override (V3.2.69)** — The 12H range gate (55/45) is bypassed when TECHNICAL's 2H range_pos < 30% (LONG) or > 70% (SHORT). This is critical for dip-bounce entries in uptrends where the 12H range says "upper half" but a genuine 1-2H dip just happened. Do not remove the override — it fixes the most common trade-blocking scenario observed in V3.2.68 (5 trades blocked in 2 cycles). The 30%/70% thresholds are intentionally conservative (not 50/50) to ensure only genuine dips/peaks override.
31. **FLOW confidence gate (V3.2.72)** — `MIN_FLOW_CONFIDENCE_GATE = 0.60`. FLOW must be >= 60% in the same direction as the trade signal before execution. Without this, Judge can reach 85-90% on stale WHALE (hours-old on-chain) + narrative SENTIMENT (news headlines) alone, with FLOW at 46% (zero orderbook confirmation). FLOW is the only persona with real-time data. Do not lower below 60% — that's barely above coin-flip and means the orderbook shows essentially nothing.
32. **EMA snapback giveback (V3.2.72)** — `EMA_SNAPBACK_GIVEBACK_PCT = 0.50`. EMA snap-back exit requires 50% giveback from peak before firing. In trending moves, the 8-EMA on 5m candles converges toward price — any tiny pause triggers a false "cross" even though the trade is still moving in the right direction. LTC SHORT was at +0.24% and actively climbing when the EMA caught up to within $0.006, killing a trade headed for 0.69% TP. The giveback ensures an actual reversal, not EMA convergence. Do not remove — without it, every steady trend gets killed at 0.20-0.39% (the kill zone between snapback arm and BE-SL placement).
33. **FLOW contra exit (V3.2.74)** — Closes underwater positions when FLOW taker ratio shows extreme opposite pressure (< 0.15 for LONG, > 7.0 for SHORT). Uses same age gate as velocity exit per tier (T1=75m, T2=60m, T3=50m). Only fires when pnl < 0 and no BE-SL placed. Fixes scenario where ADA LONG sat at -0.18% with FLOW taker ratio 0.11 (extreme selling) and no exit mechanism fired. Do not lower taker thresholds (0.15/7.0 are already extreme) or remove age gate (trade needs time to develop).
34. **Catalyst drive rule (V3.2.74)** — Judge prompt: SENTIMENT named catalyst (ETF inflow, partnership, protocol upgrade, institutional adoption) + FLOW >= 60% same direction = 85%+ confidence. Bypasses 3-persona requirement because news moves markets before WHALE/TECHNICAL react. Previously ETH with BNP Paribas catalyst + FLOW 51% would WAIT because only 2 of 4 personas agreed. Do not raise FLOW threshold above 60% — catalysts create flow, so FLOW confirmation may be building (60%) rather than fully established (70%+).
35. **Continuation hold thesis check (V3.2.74)** — Judge prompt: when re-evaluating a pair with an open position in the same direction, Judge explicitly told to re-evaluate the thesis honestly. If signals degraded (FLOW flipped, TECHNICAL reversed), return WAIT/opposite. Do not inflate confidence just because a position is already open. The daemon uses the Judge's re-evaluation to inform exit decisions.
36. **Range gate 2H override thresholds (V3.2.74 revert)** — V3.2.73 widened thresholds from 30/70 to 45/55, which effectively disabled the 12H range gate (any sub-midpoint reading triggered override). V3.2.74 reverted to 30/70. Do not widen past 30/70 — the thresholds are intentionally conservative to ensure only genuine dips/peaks override the 12H gate.
37. **Near-TP grace for max_hold (V3.2.88)** — `NEAR_TP_GRACE_PCT = 0.60`, `NEAR_TP_GRACE_MINUTES = 30` (V3.2.88: was 15). If trade is >= 60% toward TP when max_hold fires, grant 30-min grace. Applied in both `monitor_positions()` and pre-cycle sweep. Do not remove — killing a trade at 60%+ toward TP wastes the move and all fees paid. After grace expires, max_hold fires normally (no infinite grace).
38. **Judge thesis degradation exit (V3.2.76)** — When Judge returns WAIT (structured enum, not string parse) for a pair with an open position, and trade is past `early_exit_hours` AND PnL < 0.4% AND no BE-SL placed → close with `thesis_degraded`. Uses ONLY the structured `decision` field — NO reasoning text parsing. This is the OPPOSITE of ANTI-WAIT (V3.2.37): ANTI-WAIT parsed reasoning to override WAIT into a trade; thesis exit RESPECTS WAIT to inform an exit. Do not add string parsing of Judge reasoning — that was the ANTI-WAIT disaster. Zero cooldown (slot freed immediately for better opportunity).
39. **TECHNICAL momentum conflict (V3.2.88 HARD BLOCK, V3.2.93 threshold fix)** — When TECHNICAL's mean-reversion signals conflict with 1h momentum > 0.40% (V3.2.93: was 0.20%), TECHNICAL returns NEUTRAL. This is a HARD BLOCK — Judge sees "TECHNICAL: NEUTRAL" and counts fewer agreeing personas. V3.2.93 raised from 0.20% to 0.40%: at 0.20%, TECHNICAL was NEUTRAL on 100% of analyses because any positive 1h movement (normal crypto oscillation) blocked all SHORT signals — effectively removing the persona. 0.40% still blocks in genuine strong trends but allows signals in mild oscillation. The daemon's execution gate (0.10% dual 1h+15m check) provides a second safety layer. Do not remove the 1h momentum gate — without it, TECHNICAL stacks 5 signals to 85% in every uptrend. Do not soften to a confidence cap — at 65%, Judge still counts TECHNICAL as partially agreeing, inflating consensus.
40. **Opposite swap range pre-check (V3.2.78)** — Before closing an existing position for an opposite swap, the range gate is pre-checked. If the replacement trade would be blocked (e.g. LONG at 91% of 12H range with 2H=88%), the existing position is kept instead. Without this, the bot closes the existing position (taking a loss), then the replacement is blocked by the range gate, leaving zero positions and a realized loss with nothing to show for it. Uses the same 12H 55/45 gate with 2H 30/70 override thresholds as `execute_trade()`. Do not move the pre-check after the close — the whole point is to avoid closing when the replacement can't open.
41. **Thesis exit same-direction false positive (V3.2.84)** — When Judge returns LONG 89% but a LONG already exists, the "already have same direction" block converts it to WAIT 0% before returning to the daemon. The thesis exit then sees WAIT and closes the position thinking signals degraded — but Judge actually *confirmed* the thesis. Fix: `same_direction_hold=True` flag and `judge_raw_decision`/`judge_raw_confidence` are preserved on the WAIT decision. Thesis exit checks this flag and logs "thesis ALIVE (skip exit)" instead of closing. Do not remove the flag or fall back to string parsing of the WAIT reason — that's the ANTI-WAIT disaster (V3.2.37) in reverse.
42. **Zero-cooldown exit blacklist leak (V3.2.84)** — thesis_degraded, velocity_exit, and flow_contra exits all have 0.0 cooldown multiplier (slot freed immediately). But the loss blacklist at `close_trade()` line 5465 checked `pnl_pct < -0.1` independently of exit reason, applying a 2h blacklist even on zero-cooldown exits. BTC SHORT was thesis-exited at -0.32% and got a 2h blacklist despite the exit being designed for immediate re-entry. Fix: `is_zero_cooldown_exit` flag exempts these reasons from the loss blacklist. Do not remove — the blacklist and cooldown are separate mechanisms, and both must respect zero-cooldown exit semantics.
43. **Opposite swap SR pre-check (V3.2.86)** — Before closing an existing position for an opposite swap, `find_chart_based_tp_sl()` is called with the current price and replacement direction. If no valid TP exists (`tp_not_found=True` or `method == "fallback"`), the swap is skipped and the existing position is kept. Without this, the bot closes the existing position (taking a loss + blacklist), then the replacement trade is discarded because chart SR found no valid TP — resulting in zero positions, a realized loss, and a 2h blacklist. Triggered by LTC SHORT closed at -$40, LONG replacement failed ("Chart SR returned no valid TP"), left with nothing. Do not move the pre-check after the close — the whole point is to verify the replacement can open before destroying the existing position.
44. **Stale confidence comparison removed (V3.2.87)** — The old "existing conf > new conf = hold" logic for opposite swaps has been removed. It predated all modern swap guards: age gate (V3.1.100), TP proximity (V3.1.100), range pre-check (V3.2.78), SR pre-check (V3.2.86), FLOW gate (V3.2.72), and thesis exit (V3.2.76). The confidence comparison blocked legitimate flips — e.g. BNB LONG at 88% (from 17 min ago) blocked SHORT 85% when FLOW had violently flipped to 95% SHORT (taker ratio 0.09 = extreme selling). Now: if Judge says opposite at 85%+, the swap gates (age, TP proximity, range, SR) decide. Do not re-add confidence comparison — stale numbers from 10-30 min ago don't reflect current market conditions. The confidence decay hack (V3.2.73) was also removed as it was a band-aid on this broken logic.
45. **TP ceilings must match hold times (V3.2.89)** — PAIR_TP_CEILING was 0.50-0.80% (scalp-era). With V3.2.88 swing hold times (4-8H), ATR-SAFETY widens SL to 1.5% but the old TP ceiling kept TP at 0.80%, producing 0.53:1 R:R. ADA: chart SR found 1.35% TP (excellent), ceiling capped to 0.80%, SL widened to 1.50% → R:R destroyed. New ceilings: BTC/ETH/SOL 2.0%, alts 1.5%. Do not lower ceilings back to scalp levels — that's incompatible with swing holds.
46. **MIN_RR_RATIO must reject bad R:R (V3.2.89)** — MIN_RR_RATIO was 0.33 (from scalp era: "strategy exits via snap-back, not TP"). With EMA snapback disabled and swing holds, TP IS the exit. 0.33 R:R means TP < SL/3, requiring >75% win rate. New: 0.67 (TP must be >= 67% of SL). Do not lower below 0.50 — that allows trades where the loss is 2x the win.
47. **Momentum gate dual check (V3.2.89)** — Old momentum gate (V3.2.88) only checked 15m at 0.15% threshold. ADA LONG entered with 1h mom -0.14% (opposing) + 15m -0.071% (also opposing), passing the 0.15% gate. New: checks BOTH 1h and 15m. Block when 1h opposes (> 0.10%) AND 15m hasn't turned favorable. Allow when 1h opposes but 15m IS turning (the shift is starting). Do not remove the 1h check — it catches cases where the 15m noise says "neutral" but the underlying hourly trend still opposes.
48. **FLOW flip boost minimum confidence gate (V3.2.89)** — FLOW flip boost (+15%, cap 0.95) previously applied to any FLOW signal at a range extreme. SOL: taker ratio 0.84 (mild selling) → FLOW ~80% → boosted to 95% SHORT. That's extreme-conviction labeling from moderate data. New: FLOW base must be >= 65% before boost applies. Below 65%, the flip is logged but no boost. Do not lower below 65% — that's barely above neutral and doesn't justify a +15% boost.
49. **Execution sort by persona agreement (V3.2.89)** — Old sort: confidence desc, then tier desc (T3>T2>T1). When BNB (88%, 3 personas agreeing) and ADA (88%, 2 personas + WHALE opposing) tied on confidence, ADA was executed first because T3>T2. ADA had worse R:R and weaker consensus. New: confidence desc, then persona agreement count desc. At equal confidence, prefer trades with more personas agreeing. Do not re-add tier bias — it was a scalp-era preference for volatile alts that made sense for quick bounces but not for swing trades.
50. **TP_HAIRCUT for swing trades (V3.2.89)** — TP_HAIRCUT changed 0.90→0.95. In scalp mode, targeting 90% of SR made sense (price rarely reaches exact SR on quick bounces). In swing mode with 4-8H holds, price has time to test the actual SR level. 95% captures nearly the full move. Do not lower back to 0.90 — that shaves 5% off every TP unnecessarily for swing timeframes.
51. **Opposite swap SR pre-check was silently broken (V3.2.90)** — `find_chart_based_tp_sl` was never added to the daemon's import list when V3.2.86 introduced the SR pre-check. Every opposite swap since V3.2.86 hit a `NameError`, fell through to "proceeding cautiously" (no block), and the safety net was dead. ETH SHORT at +$16.33 was closed for a LONG swap, the SR pre-check NameError fell through, then the range gate blocked the LONG replacement — lost profit + zero positions. Two fixes: (1) added `find_chart_based_tp_sl` to daemon imports, (2) changed the except handler from fallthrough to `continue` (BLOCK swap). The error handler MUST block — "proceed cautiously" on an error means the safety net doesn't exist. Do not change the except handler back to fallthrough.
52. **FLOW flip boost bypasses volume floor (V3.2.91)** — The V3.2.81 volume floor caps FLOW confidence when minority taker side < 3% of total (noise detection). But the V3.2.68 flip boost (+15%) applied AFTER the cap, re-inflating the noise signal. XRP: taker ratio 271.40 (sell side = 50 units = 0.4% of total = pure noise). Volume floor correctly capped FLOW to 70%, then flip boost took 70%→85%, presenting noise as "extreme taker buying" to Judge (who gave 88% LONG — only R:R guard prevented execution). Fix: `vol_noise` flag propagated from FLOW persona return dict to `MultiPersonaAnalyzer.analyze()`; flip boost checks `flow_vote.get("vol_noise", False)` and skips boost when True. Do not remove this gate — if the underlying volume data is noise, a flip based on that noise is also noise.
53. **R:R guard ignores round-trip fees (V3.2.92)** — R:R was calculated as raw `tp_pct / sl_pct`, ignoring the 0.16% round-trip taker fee (0.08%/side). BNB: TP 0.78% / SL 1.01% = raw R:R 0.77:1 (passed 0.67 minimum). After fees: net TP = 0.78% - 0.16% = 0.62%, net SL = 1.01% + 0.16% = 1.17%, true R:R = 0.53:1 (should have been rejected). On tight TPs (< 1.0%), fees are 20%+ of profit — a trade that looks viable raw is actually uneconomic. Fix: R:R calculation subtracts `_ROUND_TRIP_FEE_PCT = 0.16` from TP and adds to SL before comparing against `MIN_RR_RATIO`. Effective minimum TP: ~0.94% at 1.0% SL, ~1.27% at 1.5% SL. Do not remove fee adjustment — raw R:R is misleading for any TP below ~1.5%.
54. **TECHNICAL momentum threshold too tight (V3.2.93)** — `_MOMENTUM_TREND_THRESH` was 0.20%, which blocked TECHNICAL signals on 100% of analyses (21/21 across 3 cycles). Any positive 1h movement > 0.20% (normal crypto noise) caused TECHNICAL to return NEUTRAL for all SHORT setups. With TECHNICAL always NEUTRAL, Judge could only reach 85% when the other 3 personas (WHALE, SENTIMENT, FLOW) unanimously agreed — which is rare. BTC SHORT had all 4 personas agreeing at 90% but only because 1h momentum was exactly 0.20% (not >0.20%). Raised to 0.40%: still blocks in strong trends, allows TECHNICAL to participate in mild oscillation. Daemon execution gate (0.10% dual check) is the second layer. Do not lower below 0.30% — that approaches the noise floor where the gate becomes meaningless.
55. **TIER_CONFIG TP values misaligned with ceiling architecture (V3.2.93)** — TIER_CONFIG `tp_pct` was 3.0/3.5/3.0% (vestigial from pre-V3.2.89 when there were no per-pair ceilings). PAIR_TP_CEILING is 2.0/1.5/2.0%. Though TIER_CONFIG tp_pct is never used in the execution path (chart SR is primary, discard on failure), it's used in: (1) startup banner display, (2) AI log uploads for WAIT decisions (`tier_config["tp_pct"]` at daemon line 1312). The startup banner showed "TP: 3.0%" (misleading), and WEEX competition AI logs showed `tp_pct: 3.0` for WAIT analyses. Fixed to 2.0/1.5/2.0% matching PAIR_TP_CEILING max per tier. Do not raise above ceiling values — that's misleading.

## Version Naming

Format: `V3.{MAJOR}.{N}` where N increments with each fix/feature.
Major bumps for strategy pivots (V3.1.x → V3.2.x for dip-signal strategy).
Bump the version number in the daemon startup banner and any new scripts.
Current: V3.2.93. Next change should be V3.2.94.

**Recent version history (last 5):**
- V3.2.93: (**CURRENT**) TECHNICAL THRESHOLD + TIER ALIGN.
  TECHNICAL momentum block threshold raised from 0.20% to 0.40%. At 0.20%, TECHNICAL returned NEUTRAL
  on 100% of analyses (21/21 pair-analyses across 3 signal cycles) because any positive 1h movement
  > 0.20% blocked all SHORT signals. A gate that fires 100% of the time removes the persona entirely.
  0.40% still blocks in genuine strong trends but allows TECHNICAL signals in mild oscillation (0.20-0.40%
  1h movement is normal noise in crypto). The daemon's execution gate (0.10% dual 1h+15m check) provides
  a second safety layer. Also: TIER_CONFIG tp_pct/take_profit aligned with PAIR_TP_CEILING (was
  3.0/3.5/3.0% — vestigial scalp values that predated V3.2.89 ceiling architecture). Now 2.0/1.5/2.0%
  matching the max ceiling per tier. These are fallback/display values only (chart SR is primary), but
  the startup banner and WEEX AI log uploads were showing misleading 3.0-3.5% TP values.
  `PIPELINE_VERSION = "SMT-v3.2.93-TechnicalThresholdAlign"`.
- V3.2.92: FEE-AWARE R:R GUARD.
  R:R calculation now subtracts 0.16% round-trip taker fees from TP and adds to SL before ratio check.
  BNB V3.2.91 bug: TP 0.78% / SL 1.01% = raw R:R 0.77:1 (passed 0.67), but after fees: net TP 0.62%,
  net SL 1.17%, true R:R 0.53:1 (should have been rejected). On tight TPs (< 1.0%), fees are 20%+ of
  profit — the guard must account for this. Effective minimum TP: ~0.94% at 1.0% SL, ~1.27% at 1.5% SL.
  `PIPELINE_VERSION = "SMT-v3.2.92-FeeAwareRR"`.
- V3.2.91: FLIP BOOST VOL NOISE GATE.
  FLOW flip boost (+15%) now blocked when volume floor fired (minority side < 3% of total taker volume).
  XRP V3.2.90 bug: taker ratio 271.40 from noise (sell side = 50 units = 0.4% of total). Volume floor
  correctly capped FLOW to 70%, but flip boost took 70%→85%, presenting noise as "extreme taker buying"
  to Judge (who gave 88% — only R:R guard saved it). Fix: `vol_noise` flag propagated from FLOW persona
  to MultiPersonaAnalyzer; flip boost checks flag before applying.
  `PIPELINE_VERSION = "SMT-v3.2.91-FlipBoostVolNoiseGate"`.
- V3.2.90: FIX OPPOSITE SWAP SR PRE-CHECK.
  (1) `find_chart_based_tp_sl` was missing from daemon imports since V3.2.86 → NameError on every SR
  pre-check (the feature was silently broken since introduction). (2) SR pre-check error handler changed
  from "proceed cautiously" (fallthrough) to BLOCK (continue).
  `PIPELINE_VERSION = "SMT-v3.2.90-FixOppositeSRPrecheck"`.
- V3.2.89: SWING TRADE GUARDS.
  TP ceilings raised from scalp-era 0.50-0.80% to swing-level 1.5-2.0%. R:R guard raised 0.33→0.67.
  TP haircut 0.90→0.95. Momentum gate: dual 1h+15m. FLOW flip boost gate: requires 65% base confidence.
  Execution sort: persona agreement count tiebreak (was tier desc).
  `PIPELINE_VERSION = "SMT-v3.2.89-SwingTradeGuards"`.

**CRITICAL RULE (V3.2.57): The 85% confidence floor is ABSOLUTE.**
Never add session discounts, contrarian boosts, or any other override that
lowers the trading threshold below 85%. In 1-slot mode, every trade must use 35-50% sizing to justify slot occupancy.

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
