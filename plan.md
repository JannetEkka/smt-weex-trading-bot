# V3.2.73 Fix Plan: Dip Detection + TECHNICAL Alignment + Stale Confidence

## Problems Identified (from V3.2.72 first cycle analysis)

### P1: TECHNICAL Persona Opposes Dip-Bounce Entries (CRITICAL)
TECHNICAL produces SHORT/NEUTRAL when FLOW shows LONG at dip bottoms because:
- By the time FLOW flips to LONG (buying returns), the price has already started recovering
- Recovery pushes RSI into 40-60 dead zone, momentum positive (generates SHORT signals), range_pos upward
- The "2-persona dip rule" (FLOW + TECHNICAL) is structurally impossible to satisfy
- In the first cycle: BTC TECHNICAL=SHORT 70% vs FLOW=LONG 85%, BNB TECHNICAL=NEUTRAL 40% vs FLOW=LONG 85%, LTC TECHNICAL=SHORT 30% vs FLOW=LONG 85%

### P2: No FLOW Flips on First Cycle Post-Restart (HIGH)
`_prev_flow_direction` is empty on daemon restart → no flips detected on cycle 1.
The core dip signal (FLOW flip boost) structurally cannot fire.

### P3: ETH LONG Held Against 4-Persona SHORT at 85% (HIGH)
Stale 90% confidence from 50 min ago blocks an opposite flip when all 4 personas now say SHORT.
`confidence >= existing_conf` comparison uses stored value from trade open, not live re-evaluation.
ETH taker ratio was 0.13 (extreme selling) — position likely heading for SL.

### P4: Signal Check Discards Signals Before Monitor Frees Slots (MEDIUM)
BTC velocity exit happened in monitor phase (after signals). By then, 3 good signals (BNB, LTC, XRP)
were already discarded as slot-blocked. The pre-cycle exit sweep only handles max_hold/force_exit/early_exit,
not velocity exit.

### P5: Range Gate Blocks Valid Dips in Uptrends (MEDIUM)
BNB LONG at 12H=73%/2H=56% and XRP LONG at 12H=62%/2H=95% would both be range-gate killed.
The 55% 12H threshold + 30% 2H override threshold is too restrictive. The 2H override only fires
at <30%, but dip-bounce entries in uptrends often sit at 40-60% of the 2H range (not deeply oversold).

---

## Proposed Fixes (V3.2.73)

### Fix 1: TECHNICAL — Add "Recovery from Dip" Signal Logic
**File:** `v3/smt_nightly_trade_v3_1.py` — `TechnicalPersona.analyze()` (line ~3374)

**Problem:** TECHNICAL only detects *being* in a dip (RSI<35), not *recovering from* one.
At the moment FLOW flips, the dip is recovering — RSI is climbing back through 40-55, momentum is
turning positive, and range_pos is rising from the bottom.

**Change:** Add a "dip recovery" signal that detects:
- RSI was recently oversold but is now recovering (RSI in 40-55 AND 30m momentum is positive AND 1h momentum is negative = V-shaped recovery pattern)
- Or: range_pos is in the 20-50% zone (not extreme, but below midpoint) with positive short-term momentum = bounce in progress

Specifically, add after the range_pos signal block (~line 3411):

```python
# DIP RECOVERY — TECHNICAL detects that a dip JUST happened and price is bouncing
# This is the missing signal: FLOW flips to LONG at the bottom, but by then RSI
# is climbing out of oversold (40-55), momentum is positive. These are LONG signals
# for dip-bounce, not SHORT signals.
# Condition: range still in lower half (< 50%) + positive short-term momentum
# = price bounced from a dip but hasn't fully recovered yet
if range_pos < 50 and momentum_30m > 0.05 and momentum_1h < 0:
    signals.append(("LONG", 0.45, f"Dip recovery: range {range_pos:.0f}% + 30m bounce {momentum_30m:+.2f}% (1h still negative {momentum_1h:+.2f}%)"))
elif range_pos > 50 and momentum_30m < -0.05 and momentum_1h > 0:
    signals.append(("SHORT", 0.45, f"Peak reversal: range {range_pos:.0f}% + 30m fade {momentum_30m:+.2f}% (1h still positive {momentum_1h:+.2f}%)"))
```

This detects the V-shape: 1h momentum negative (the dip happened) but 30m momentum positive (the bounce started). Price still in the lower half = recovery is early, not completed.

### Fix 2: Seed `_prev_flow_direction` from Signal History on Startup
**File:** `v3/smt_nightly_trade_v3_1.py` — near `_prev_flow_direction` initialization (~line 4467)

**Problem:** `_prev_flow_direction = {}` on cold start means no flips can be detected on cycle 1.

**Change:** At the start of `MultiPersonaAnalyzer.analyze()`, check if `_prev_flow_direction` is empty
and if so, seed it from `signal_history` in the trade state (which persists across restarts).
Signal history already tracks per-pair direction from the previous cycle. Use
`signal_history[pair]["direction"]` as the previous FLOW direction if available.

Actually, the signal_history stores the *overall* signal direction, not per-persona. Better approach:
seed `_prev_flow_direction` from the trade state's `signal_history` field at daemon startup
(in `smt_daemon_v3_1.py` after `sync_tracker_with_weex()`), using each pair's last signal
direction as a proxy for FLOW's last direction. This isn't perfect (FLOW might have disagreed with
the Judge's final call), but it's better than empty — and the flip boost still requires range position
confirmation, so false flips at mid-range are harmless (neutral, no boost/discount).

**File:** `v3/smt_daemon_v3_1.py` — after sync, before first signal check

```python
# V3.2.73: Seed FLOW direction history from persisted signal_history
# so FLOW flips can be detected on the first cycle after restart.
from smt_nightly_trade_v3_1 import _prev_flow_direction
_sh = tracker.state.get("signal_history", {})
for _pair_key, _sh_data in _sh.items():
    if isinstance(_sh_data, dict) and _sh_data.get("direction") in ("LONG", "SHORT"):
        _sym = None
        for _tp in TRADING_PAIRS:
            if _tp["pair"] == _pair_key:
                _sym = _tp["symbol"]
                break
        if _sym:
            _prev_flow_direction[_sym] = _sh_data["direction"]
            logger.info(f"  [FLOW-SEED] {_pair_key}: seeded prev direction = {_sh_data['direction']} from signal_history")
```

### Fix 3: Decay Stale Confidence for Opposite Flip Comparison
**File:** `v3/smt_daemon_v3_1.py` — opposite flip logic (~line 1157)

**Problem:** ETH LONG opened at 90% confidence 50 min ago. Now all 4 personas say SHORT 85%.
The `confidence >= existing_conf` check compares fresh 85% vs stale 90%, blocking the flip.

**Change:** Apply a time decay to stored confidence. After 30+ minutes, the market has moved and
the original confidence is stale. Decay: -5% per 30 minutes past the first 20 minutes (matching
the opposite swap age gate). Cap minimum at 75%.

```python
# V3.2.73: Decay stale confidence — original confidence from 60+ min ago
# doesn't reflect current market conditions.
_trade_opened = trade.get("opened_at") or trade.get("opened")
if _trade_opened:
    if isinstance(_trade_opened, str):
        _trade_opened = datetime.fromisoformat(_trade_opened.replace("Z", "+00:00"))
    _age_min = (datetime.now(timezone.utc) - _trade_opened).total_seconds() / 60
    if _age_min > 20:
        _decay = 0.05 * ((_age_min - 20) / 30)  # -5% per 30 min past 20 min
        existing_conf = max(0.75, existing_conf - _decay)
```

With this: ETH LONG at 50 min old → `_age_min=50`, decay = 0.05 * (30/30) = 0.05 → existing_conf = 90% - 5% = 85%. Now SHORT 85% >= 85% → **flip allowed**.

### Fix 4: Run Velocity Exit in Pre-Cycle Sweep (Before Signals)
**File:** `v3/smt_daemon_v3_1.py` — pre-cycle exit sweep in `check_trading_signals()`

**Problem:** Velocity exit only runs in `monitor_positions()` which runs AFTER signal check.
BTC was velocity-exited in monitor, but by then 3 signals were already slot-blocked.

**Change:** Add velocity exit check to the pre-cycle sweep (alongside max_hold/force_exit/early_exit).
The pre-cycle sweep at the START of `check_trading_signals()` already checks for expired positions.
Add the velocity exit condition there too:

```python
# V3.2.73: Velocity exit in pre-cycle sweep — frees slots BEFORE signal analysis
_vel_limit = VELOCITY_EXIT_MINUTES.get(tier, 60)
if _age_min >= _vel_limit and peak_pnl_pct < VELOCITY_MIN_PEAK_PCT:
    # Velocity exit: trade never moved
    should_pre_exit = True
    pre_exit_reason = f"velocity_exit ({_age_min:.0f}m open, peak={peak_pnl_pct:.2f}% never reached {VELOCITY_MIN_PEAK_PCT:.2f}%, T{tier} limit={_vel_limit}m)"
```

### Fix 5: Widen 2H Override Threshold for Range Gate
**File:** `v3/smt_nightly_trade_v3_1.py` — range gate (~line 4877)

**Problem:** 2H override for LONG requires `range_pos_2h < 30%`. But in this cycle,
BNB's 2H range was 56% and XRP's was 95%. The 30% threshold is too strict — in an uptrend,
a dip-bounce entry might be at 40-50% of the 2H range (not deeply oversold, but below midpoint).

**Change:** Widen the 2H override threshold from 30% to 45% for LONGs and from 70% to 55% for SHORTs.
This matches the 12H gate thresholds (55/45) — if the 2H range position is below the midpoint,
the dip is real enough to override the 12H gate.

```python
_DIP_OVERRIDE_THRESH = 45  # V3.2.73: was 30, widened to 45 (below midpoint = dip real enough)
_PEAK_OVERRIDE_THRESH = 55  # V3.2.73: was 70, widened to 55 (above midpoint = peak real enough)
```

---

## Version Bump
- V3.2.73
- `PIPELINE_VERSION = "SMT-v3.2.73-DipRecovery-FlowSeed-ConfDecay-VelPreSweep-RangeWiden"`

## Files Modified
1. `v3/smt_nightly_trade_v3_1.py` — Fixes 1, 2 (seed location), 5
2. `v3/smt_daemon_v3_1.py` — Fixes 2 (startup seed), 3, 4

## Risk Assessment
- **Fix 1 (dip recovery):** Low risk. Adds a new LONG signal source that only fires in specific V-shape conditions. Can't exceed 0.85 cap. Judge still makes final call.
- **Fix 2 (FLOW seed):** Very low risk. Only affects cycle 1 after restart. Range position confirmation still gates the boost. Worst case: one false boost that Judge ignores.
- **Fix 3 (confidence decay):** Medium risk. Could allow more flips. But the opposite swap gates (TP proximity, age) still protect. Decay is gradual (5% per 30min) and floors at 75%.
- **Fix 4 (velocity pre-sweep):** Low risk. Identical logic to monitor's velocity exit, just runs earlier. Frees slots for signal analysis that would have been freed anyway 2 minutes later.
- **Fix 5 (range gate widen):** Medium risk. Allows LONGs at 55-73% of 12H range if 2H confirms below midpoint. Could let in some mid-range trades. But the 85% confidence floor + FLOW gate still protect quality.
