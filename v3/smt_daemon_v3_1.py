#!/usr/bin/env python3
"""
SMT Trading Daemon V3.2.17 - 7 pairs, 4 slots, stale exit removed (slot swap handles it)
=========================
CRITICAL FIX: HARD STOP was killing regime-aligned trades.

V3.1.23 Changes (REGIME FIX):
- REMOVED unconditional $30 HARD STOP that killed SHORTs in BEARISH markets
- HARD STOP now ONLY fires for positions FIGHTING the regime
- SHORT in BEARISH/NEUTRAL = let it run, trust the 2% SL
- LONG in BEARISH/NEUTRAL losing >$50 = cut it
- SHORT in BULLISH losing >$50 = cut it
- Removed "opposite winning" exit logic - too aggressive

V3.1.20 Changes (PREDATOR MODE):
- DISABLED all RUNNER_CONFIG - no more partial closes
- MIN_CONFIDENCE_TO_TRADE: 60% -> 70%
- Fewer trades, bigger wins, less fee bleed

V3.1.9 Changes:
- CRITICAL FIX: Fixed undefined btc_trend variable in regime filter
- CRITICAL FIX: Regime filter now applies to ALL pairs including BTC
- Stricter thresholds: BEARISH at -1% (was -2%), BULLISH at +1.5% (was +2%)
- Block LONGs when BTC 24h change is even slightly negative (-0.5%)
- Reduced regime cache from 15min to 5min for faster response
- MAX_OPEN_POSITIONS: 8 -> 5
- MIN_CONFIDENCE_TO_TRADE: 55% -> 65%
- Tier 3 SL: 1.5% -> 2.0% (stop whipsaw)
- Tier 3 TP: 2.5% -> 3.0% (better R:R)
- Added BTC trend filter (don't LONG when BTC dumps)

Tier Config:
- Tier 1 (BTC, ETH, BNB, LTC): 4% TP, 2% SL, 48h hold
- Tier 2 (SOL): 3% TP, 1.75% SL, 12h hold  
- Tier 3 (DOGE, XRP, ADA): 3% TP, 2% SL, 4h hold

Run: python3 smt_daemon_v3_1.py
"""

import os
import sys
import json
import time
import signal
import logging
import traceback
import requests
from datetime import datetime, timezone, timedelta
from threading import Event
from typing import Dict, List, Optional
# V3.1.23: RL Data Collection
try:
    from rl_data_collector import RLDataCollector
    rl_collector = RLDataCollector()
    RL_ENABLED = True
except ImportError:
    rl_collector = None
    RL_ENABLED = False


def fill_rl_outcomes_inline():
    """V3.1.36: Auto-fill RL outcomes from trade_state after closes."""
    if not RL_ENABLED:
        return
    try:
        import glob as _glob
        state_file = None
        for c in ["trade_state_v3_1_7.json", "trade_state_v3_1_4.json"]:
            if os.path.exists(c):
                state_file = c
                break
        if not state_file or not os.path.exists("rl_training_data"):
            return

        with open(state_file, 'r') as f:
            ts = json.load(f)
        closed = ts.get('closed', [])
        if not closed:
            return

        unfilled = {}
        for fp in _glob.glob("rl_training_data/exp_*.jsonl"):
            with open(fp, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get('outcome') is None and e.get('action') in ('LONG','SHORT'):
                            unfilled[e['id']] = {'entry': e, 'file': fp}
                    except:
                        continue
        if not unfilled:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        filled = 0
        for trade in closed[-20:]:
            t_opened = trade.get('opened_at','')
            t_side = trade.get('side','')
            if not t_opened or not t_side:
                continue
            try:
                opened_dt = datetime.fromisoformat(t_opened.replace('Z','+00:00'))
            except:
                continue
            if opened_dt < cutoff:
                continue
            for eid, data in unfilled.items():
                ent = data['entry']
                if ent.get('action') != t_side:
                    continue
                try:
                    rl_ts = datetime.fromisoformat(ent['ts'].replace('Z','+00:00'))
                except:
                    continue
                diff = opened_dt - rl_ts
                if timedelta(0) <= diff <= timedelta(minutes=10):
                    cd = trade.get('close_data',{})
                    pos_usdt = trade.get('position_usdt',0)
                    pnl_usd = cd.get('pnl',0) or 0
                    pnl_pct = (pnl_usd/pos_usdt)*100 if pos_usdt and pnl_usd else 0
                    try:
                        closed_dt = datetime.fromisoformat(trade.get('closed_at','').replace('Z','+00:00'))
                        hh = (closed_dt - opened_dt).total_seconds()/3600
                    except:
                        hh = 0
                    outcome = {
                        "pnl": round(pnl_pct,4), "hours": round(hh,2),
                        "reason": cd.get('reason','unknown'),
                        "reward": rl_collector._calc_reward(pnl_pct,hh,0,0) if rl_collector else 0,
                        "pnl_usd": round(pnl_usd,2), "win": pnl_usd > 0,
                    }
                    try:
                        lines = []
                        with open(data['file'],'r') as f:
                            for l in f:
                                l = l.strip()
                                if not l: continue
                                try:
                                    x = json.loads(l)
                                    if x.get('id') == eid:
                                        x['outcome'] = outcome
                                        filled += 1
                                    lines.append(json.dumps(x, default=str))
                                except:
                                    lines.append(l)
                        with open(data['file'],'w') as f:
                            f.write('\n'.join(lines)+'\n')
                    except:
                        pass
                    break
        if filled > 0:
            logger.info(f"[RL] Auto-filled {filled} outcome(s)")
    except Exception as e:
        logger.debug(f"RL auto-fill error: {e}")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# V3.1.1 CONFIGURATION
# ============================================================

# Timing
SIGNAL_CHECK_INTERVAL = 10 * 60  # V3.1.84: 10 min (was 15). Competition needs faster turnover.
POSITION_MONITOR_INTERVAL = 2 * 60  # 2 minutes (check more often for tier 3)
HEALTH_CHECK_INTERVAL = 60
CLEANUP_CHECK_INTERVAL = 30

# V3.1.100: Opposite Swap TP Proximity Gate
OPPOSITE_MIN_AGE_MIN = 20          # Don't flip positions younger than 20 minutes
OPPOSITE_TP_PROGRESS_BLOCK = 30    # Block flip if position is >= 30% toward TP
DEFERRED_FLIP_MAX_AGE_MIN = 30     # Deferred signal expires after 30 minutes
# V3.2.51: Emergency flip — 90%+ confidence opposite bypasses the age gate only.
# TP proximity gate (>= 30% toward TP) is preserved regardless of confidence:
# abandoning a winning position costs real fees + foregone TP profit.
EMERGENCY_FLIP_CONFIDENCE = 0.90   # Age gate bypass threshold for opposite signals


# Competition
COMPETITION_START = datetime(2026, 2, 8, 15, 0, 0, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 23, 23, 59, 0, tzinfo=timezone.utc)  # V3.1.77: Fixed - competition ends Feb 23

# Retry
MAX_RETRIES = 3
RETRY_DELAY = 30

# Logging
LOG_DIR = "logs"
LOG_LEVEL = logging.INFO


# ============================================================
# LOGGING
# ============================================================

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"daemon_v3_1_7_{datetime.now().strftime('%Y%m%d')}.log")
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    
    
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)
    logger.handlers = []
    logger.addHandler(file_handler)
    
    # V3.1.51: Redirect stdout/stderr to log file so print() from nightly goes here too
    import sys as _sys
    _sys.stdout = open(log_file, "a")
    _sys.stderr = open(log_file, "a")

    return logger

logger = setup_logging()


# ============================================================
# IMPORTS
# ============================================================

try:
    from smt_nightly_trade_v3_1 import (
        WEEX_BASE_URL,
        # Config
        TEST_MODE, TRADING_PAIRS, MAX_LEVERAGE, STARTING_BALANCE,
        PIPELINE_VERSION, MODEL_NAME, get_max_positions_for_equity,
        MIN_CONFIDENCE_TO_TRADE, ADX_FLOOR_GATE,  # Added for trade filtering
        TIER_CONFIG, get_tier_for_symbol, get_tier_config,
        RUNNER_CONFIG, get_runner_config,
        
        # WEEX API
        get_price, get_balance, get_open_positions,
        get_account_equity,  # V3.1.19: For proper equity calculation
        upload_ai_log_to_weex,
        _rate_limit_gemini,
        weex_headers, round_price_to_tick,  # V3.2.46: for breakeven SL
        
        # Position management
        check_position_status, cancel_all_orders_for_symbol,
        close_position_manually, execute_runner_partial_close,
        get_recent_close_order_id, _fetch_plan_order_ids,
        TradeTracker,
        
        # Competition
        get_competition_status,
        
        # Multi-persona
        MultiPersonaAnalyzer,

        # Trading
        execute_trade,

        # V3.2.59: Dynamic event detection
        detect_macro_events, _check_dynamic_blackout,

        # Logging
        save_local_log,
    )
    logger.info("Trade module imports OK")
except ImportError as e:
    logger.error(f"Import error: {e}")
    logger.error(traceback.format_exc())
    sys.exit(1)

# V3.1.29: Pyramiding system import
try:
    from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid
    pass  # Pyramiding loaded
except ImportError:
    logger.warning("Pyramiding system not available")
    move_sl_to_breakeven = lambda *args, **kwargs: False
    should_pyramid = lambda *args, **kwargs: {"should_add": False}
    execute_pyramid = lambda *args, **kwargs: {"success": False}



# ============================================================
# DAEMON STATE
# ============================================================


# ============================================================
# V3.1.61: GEMINI TIMEOUT WRAPPER
# ============================================================

def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout to prevent daemon hangs."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Gemini call timed out after {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini call timed out after {timeout}s")


def _gemini_full_call_daemon(model, contents, config, timeout=90):
    """V3.1.69: BULLETPROOF Gemini call for daemon - wraps EVERYTHING."""
    import concurrent.futures
    
    def _do_call():
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client()
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Gemini full call timed out after {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini full call timed out after {timeout}s")

class DaemonState:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.last_signal_check = None
        self.last_position_check = None
        
        self.signals_checked = 0
        self.trades_opened = 0
        self.trades_closed = 0
        self.early_exits = 0
        self.runners_triggered = 0  # V3.1.2: Track runner partial closes
        self.errors = 0

        self.is_running = True
        self.shutdown_event = Event()
    
    def to_dict(self) -> Dict:
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_hours": (datetime.now(timezone.utc) - self.started_at).total_seconds() / 3600,
            "signals_checked": self.signals_checked,
            "trades_opened": self.trades_opened,
            "trades_closed": self.trades_closed,
            "early_exits": self.early_exits,
            "runners_triggered": self.runners_triggered,
            "errors": self.errors,
        }

state = DaemonState()
tracker = TradeTracker(state_file="trade_state_v3_1_7.json")

# ============================================================
# V3.1.69: INTERNAL WATCHDOG - detect and recover from hangs
# ============================================================
import threading

_last_progress_time = time.time()

def _mark_progress():
    """Called by main loop to indicate the daemon is making progress.
    V3.1.82 FIX: Use file-based heartbeat instead of threading variable.
    The global variable approach had a mystery bug where _last_progress_time
    was never updated despite _mark_progress() being called (possibly due to
    subprocess re-imports creating variable shadowing).
    """
    global _last_progress_time
    _last_progress_time = time.time()
    # File-based heartbeat: write timestamp to file so watchdog can read it
    # This is immune to any threading/import/variable shadowing issues
    try:
        with open("/tmp/smt_daemon_heartbeat", "w") as f:
            f.write(str(_last_progress_time))
    except Exception:
        pass  # Non-critical, don't crash on heartbeat write

def _internal_watchdog():
    """Background thread that kills the process if it hangs > 20 minutes.
    V3.1.82 FIX: Read heartbeat from FILE instead of shared variable.
    """
    HANG_TIMEOUT = 1200  # 20 minutes
    while True:
        time.sleep(60)
        try:
            with open("/tmp/smt_daemon_heartbeat", "r") as f:
                last_beat = float(f.read().strip())
            elapsed = time.time() - last_beat
        except Exception:
            # Can't read heartbeat file — don't kill, just skip this check
            continue
        if elapsed > HANG_TIMEOUT:
            logger.error(f"INTERNAL WATCHDOG: No heartbeat for {elapsed:.0f}s. Force exit!")
            logger.error("The external watchdog.sh will restart us.")
            os._exit(1)  # Hard exit, watchdog.sh will restart

# Start internal watchdog as daemon thread
_watchdog_thread = threading.Thread(target=_internal_watchdog, daemon=True)
_watchdog_thread.start()


analyzer = MultiPersonaAnalyzer()

# V3.1.65: GLOBAL TRADE COOLDOWN - prevent fee bleed from rapid trading
_last_trade_opened_at = 0  # unix timestamp
GLOBAL_TRADE_COOLDOWN = 600  # V3.2.57: 10 min (was 900/15min — final stretch velocity, adds ~5-6 windows in 72h)
TAKER_FEE_RATE = 0.0008       # V3.2.50: 0.08%/side taker fee (WEEX standard). At 20x → 1.6% margin/side, 3.2% round-trip

# V3.1.93: Last signal cycle summary for PM context
_last_signal_summary = {}

# ============================================================
# V3.2.48: MACRO BLACKOUT WINDOWS — block new entries during
# high-impact scheduled macro data releases.
# Format: (start_utc, end_utc, label)
# Strict window: bot skips ALL signal cycles within the window.
# The 14:00 re-entry lets the 30-min post-data candle settle.
# ============================================================
MACRO_BLACKOUT_WINDOWS = [
    ("2026-02-20 13:15", "2026-02-20 14:00", "US Core PCE Price Index"),
]

def _is_macro_blackout() -> tuple:
    """V3.2.48: Check if current UTC time falls inside a macro blackout window.
    Returns (is_blacked_out: bool, label: str)."""
    now = datetime.now(timezone.utc)
    for start_str, end_str, label in MACRO_BLACKOUT_WINDOWS:
        start = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        if start <= now < end:
            return True, label
    return False, ""

# ============================================================
# V3.2.48: WEEKEND + HOLIDAY LIQUIDITY MODE
# During thin-liquidity periods (Sat/Sun + known bank holidays),
# restrict trading to deep-book pairs only: BTC, ETH, SOL.
# Altcoins (BNB, LTC, XRP, ADA) have 20-25% lower weekend volume
# → wider spreads, more fakeout wicks, worse fills.
# Emperor's Birthday (Feb 23 Mon) extends thin-book conditions
# as Japan is a major crypto liquidity provider and CME is closed.
# ============================================================
WEEKEND_ALLOWED_PAIRS = {"BTC", "ETH", "SOL"}

# Bank holidays that extend weekend-like thin liquidity into weekdays
# Format: "YYYY-MM-DD" UTC date. Only the hours < 12 UTC are restricted
# (Asian + European session thin, US session restores liquidity).
HOLIDAY_THIN_LIQUIDITY = {
    "2026-02-23": "Emperor's Birthday (Japan) — CME + TradFi Asia closed",
}

def _is_weekend_liquidity_mode() -> tuple:
    """V3.2.48: Check if we're in weekend/holiday thin-liquidity mode.
    Returns (is_thin: bool, reason: str)."""
    now = datetime.now(timezone.utc)
    # Saturday = 5, Sunday = 6
    if now.weekday() in (5, 6):
        return True, f"Weekend (UTC {now.strftime('%A')})"
    # Bank holiday thin hours (before 12 UTC — Asian/European session)
    date_str = now.strftime("%Y-%m-%d")
    if date_str in HOLIDAY_THIN_LIQUIDITY and now.hour < 12:
        return True, HOLIDAY_THIN_LIQUIDITY[date_str]
    return False, ""

# V3.2.6: Per-pair signal persistence tracking moved into TradeTracker.signal_history
# (persisted to trade_state JSON so counts survive daemon restarts)
# Structure: {pair: {"direction": "SHORT", "confidence": 0.88, "count": 2, "entry_time": ISO, "last_seen": ISO}}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def is_competition_active() -> bool:
    now = datetime.now(timezone.utc)
    if "--force" in sys.argv:
        return True
    if COMPETITION_START <= now <= COMPETITION_END:
        return True
    if now < COMPETITION_START:
        try:
            if get_balance() > 0:
                return True
        except:
            pass
    return False


def run_with_retry(func, *args, max_retries=MAX_RETRIES, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ============================================================
# V3.1.82: SLOT SWAP HELPER
# ============================================================

def _find_weakest_position(open_positions, new_symbol, position_map, fear_greed=50):
    """V3.1.87: Find the weakest position that can be swapped out for a stronger signal.

    Returns dict with position info if a viable swap target exists, None otherwise.

    V3.1.84 CHANGES (STOP BURNING MONEY ON SWAPS):
    - Require PnL < -0.5% (was < +0.5%). Don't kill barely-negative positions.
    - Require position age >= 45 minutes. Young trades haven't had time to develop.
    - The old logic swapped -0.18% BTC after 20min for -$25.9 loss + fees. Never again.

    V3.1.87: Regime-aware swap gate.
    - Normal: -0.5% (don't kill positions that might recover)
    - Capitulation (F&G < 20): -0.25% (opportunity cost of blocked slots > swap cost)
    """
    weakest = None
    weakest_pnl_pct = 999
    now_ms = int(time.time() * 1000)
    MIN_AGE_MS = 45 * 60 * 1000  # 45 minutes minimum before eligible for swap
    # V3.1.87: Regime-aware swap threshold
    if fear_greed < 20:
        MIN_PNL_FOR_SWAP = -0.10  # V3.2.11: Capitulation: tighter gate — free stale slots sooner
        logger.info(f"  [SWAP] Regime gate: F&G={fear_greed}, threshold={MIN_PNL_FOR_SWAP}%")
    else:
        MIN_PNL_FOR_SWAP = -0.5   # Normal: don't kill positions that might recover

    for pos in open_positions:
        pos_symbol = pos.get("symbol", "")
        pos_side = pos.get("side", "").upper()
        pos_entry = float(pos.get("entry_price", 0))
        pos_upnl = float(pos.get("unrealized_pnl", 0))
        pos_size = float(pos.get("size", 0))

        # Skip if same symbol as the new signal
        if pos_symbol == new_symbol:
            continue

        # V3.1.84: Check position age - don't swap young positions
        pos_ctime = pos.get("ctime", "")
        if pos_ctime:
            try:
                age_ms = now_ms - int(pos_ctime)
                if age_ms < MIN_AGE_MS:
                    age_min = age_ms / 60000
                    logger.debug(f"  [SWAP] Skip {pos_symbol}: too young ({age_min:.0f}min < 45min)")
                    continue
            except (ValueError, TypeError):
                pass  # If ctime parse fails, allow swap (conservative)

        # Calculate PnL %
        if pos_entry > 0 and pos_size > 0:
            notional = pos_entry * pos_size
            pnl_pct = (pos_upnl / notional) * 100 if notional > 0 else 0
        else:
            pnl_pct = 0

        # V3.1.84: Only consider positions meaningfully losing (< -0.5%)
        # Was: < +0.5% which killed barely-negative positions for no reason
        if pnl_pct < MIN_PNL_FOR_SWAP and pnl_pct < weakest_pnl_pct:
            weakest_pnl_pct = pnl_pct
            weakest = {
                "symbol": pos_symbol,
                "side": pos_side,
                "size": pos_size,
                "entry_price": pos_entry,
                "unrealized_pnl": pos_upnl,
                "pnl_pct": pnl_pct,
            }

    return weakest


# V3.1.9 SIGNAL CHECKING - HEDGE MODE SUPPORT
# ============================================================

def check_trading_signals():
    """V3.1.9: Tier-based signal check with HEDGE MODE
    
    ALWAYS analyzes ALL pairs and uploads AI logs.
    HEDGE MODE: Can open LONG while SHORT is running (and vice versa)
    WEEX supports bidirectional positions on same pair!
    """
    
    global _last_trade_opened_at
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    logger.info("=" * 60)
    logger.info(f"SIGNAL CHECK - {run_timestamp}")
    logger.info("=" * 60)
    
    state.signals_checked += 1
    state.last_signal_check = datetime.now(timezone.utc)

    # V3.2.48: MACRO BLACKOUT — skip entire signal cycle during high-impact data releases.
    # Strict window: no new entries. Existing positions are monitored normally by position_monitor.
    _blacked_out, _blackout_label = _is_macro_blackout()
    if _blacked_out:
        logger.warning(f"[MACRO BLACKOUT] {_blackout_label} — skipping signal cycle. Re-entry after window closes.")
        upload_ai_log_to_weex(
            stage=f"Macro Blackout: {_blackout_label}",
            input_data={"event": _blackout_label, "utc_time": datetime.now(timezone.utc).isoformat()},
            output_data={"action": "BLACKOUT_SKIP", "reason": "Scheduled macro data release"},
            explanation=f"Signal cycle skipped: {_blackout_label} data release imminent. "
                        f"Initial 10-30 min post-release is dominated by algorithmic slippage and fakeout wicks. "
                        f"Bot will re-enter after the blackout window closes and the first clean candle settles."
        )
        return

    # V3.2.59: DYNAMIC BLACKOUT — Gemini-detected HIGH-impact events within 15 min.
    # This catches events NOT in the hardcoded MACRO_BLACKOUT_WINDOWS list.
    # detect_macro_events() is cached (30min TTL) — costs 1 Gemini call per 3 cycles.
    try:
        _detected_events = detect_macro_events()
        _dyn_blacked, _dyn_label = _check_dynamic_blackout(_detected_events.get("events", []))
        if _dyn_blacked:
            logger.warning(f"[DYNAMIC BLACKOUT] {_dyn_label} — Gemini detected imminent HIGH-impact event. Skipping signal cycle.")
            upload_ai_log_to_weex(
                stage=f"Dynamic Blackout: {_dyn_label}",
                input_data={"event": _dyn_label, "utc_time": datetime.now(timezone.utc).isoformat(),
                            "detected_events": [e.get("name") for e in _detected_events.get("events", [])]},
                output_data={"action": "DYNAMIC_BLACKOUT_SKIP", "reason": "Gemini-detected imminent macro event"},
                explanation=f"Signal cycle skipped: {_dyn_label}. Gemini Search detected HIGH-impact event "
                            f"within 15 minutes. Bot will resume after event + 30min settlement window."
            )
            return
    except Exception as _dyn_err:
        logger.warning(f"[DYNAMIC BLACKOUT] Event detection error: {_dyn_err} — continuing without dynamic blackout")

    try:
        # V3.1.19: Get proper account info with equity from API
        account_info = get_account_equity()
        balance = account_info["available"]
        equity = account_info["equity"]
        total_upnl = account_info["unrealized_pnl"]
        
        open_positions = get_open_positions()

        # V3.2.25: Cycle-start housekeeping — dust + orphan sweep every signal cycle
        try:
            cleanup_dust_positions()
            open_positions = get_open_positions()  # Refresh after dust cleanup
        except Exception as _dust_err:
            logger.warning(f"Cycle dust cleanup error: {_dust_err}")
        _open_syms = {p.get('symbol') for p in open_positions}
        for _tracked_sym in list(tracker.get_active_symbols()):
            _base_sym = _tracked_sym.split(':')[0]
            if _base_sym not in _open_syms:
                try:
                    cancel_all_orders_for_symbol(_base_sym)
                    logger.info(f"  [ORPHAN] Swept orders for closed {_base_sym}")
                except Exception as _oe:
                    logger.debug(f"  [ORPHAN] Sweep error {_base_sym}: {_oe}")

        # V3.1.19: LIQUIDATION PROTECTION
        # Safety thresholds based on ACTUAL equity from WEEX
        CRITICAL_EQUITY = 150.0  # Below this = EMERGENCY MODE (close all)
        LOW_EQUITY = 300.0       # Below this = NO NEW TRADES
        MIN_AVAILABLE = 50.0     # Need at least $50 available margin for new trades
        
        emergency_mode = equity < CRITICAL_EQUITY and equity > 0
        low_equity_mode = (equity < LOW_EQUITY and equity > 0) or balance < MIN_AVAILABLE
        
        if emergency_mode:
            logger.warning(f"EMERGENCY MODE: Equity ${equity:.2f} < ${CRITICAL_EQUITY}")
            logger.warning(f"Closing ALL positions to prevent liquidation!")
            # Close all positions
            for pos in open_positions:
                symbol = pos.get('symbol', pos.get('contractId', ''))
                side = pos.get('side', '')
                size = float(pos.get('size', 0))
                if size > 0:
                    close_type = "3" if side == "LONG" else "4"
                    try:
                        place_order(symbol, close_type, size, tp_price=None, sl_price=None)
                        logger.warning(f"  Emergency closed {symbol} {side}")
                    except Exception as e:
                        logger.error(f"  Failed to close {symbol}: {e}")
            return
        
        if low_equity_mode:
            logger.warning(f"LOW EQUITY MODE: Equity ${equity:.2f}, Available ${balance:.2f}")
            logger.warning(f"NO NEW TRADES - monitoring existing positions only")
        
        competition = get_competition_status(balance)
        
        logger.info(f"Balance: {balance:.2f} USDT | Equity: {equity:.2f} USDT | UPnL: {total_upnl:+.2f}")
        logger.info(f"Open positions: {len(open_positions)}")
        logger.info(f"Days left: {competition['days_left']}")
        
        # V3.1.19: SMART SLOT SYSTEM
        # Base slots + bonus for each "risk-free" position (runner triggered)
        # A position is "risk-free" if its SL has been moved to entry (break-even)
        risk_free_count = 0
        for pos in open_positions:
            # Check if this position has had runner triggered (tracked in state)
            symbol = pos.get('symbol', pos.get('contractId', ''))
            if tracker.active_trades.get(symbol, {}).get('runner_triggered', False):
                risk_free_count += 1
        
        BASE_SLOTS = get_max_positions_for_equity(equity)  # V3.1.78: Equity-tiered (was static 5)
        MAX_BONUS_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.53: Count positions from WEEX (the truth), not tracker
        weex_position_count = len(open_positions)  # This comes from allPosition API

        available_slots = effective_max_positions - weex_position_count
        
        # V3.1.53: Confidence override constants (restored)
        CONFIDENCE_OVERRIDE_THRESHOLD = 0.85
        MAX_CONFIDENCE_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute
        CONFIDENCE_EXTRA_SLOT = 0.90  # V3.2.39: 90%+ signals may open 5th slot when all 4 full
        # V3.2.46: With 1-slot cross-margin strategy, disable extra slot bypass entirely.
        # Full account is buffer for one 20x trade — no concurrent positions.

        # V3.2.46: 1-slot hard cap — cross margin = full account as buffer for single trade
        can_open_new = not low_equity_mode and available_slots > 0

        if low_equity_mode:
            logger.info(f"Low equity mode - no new trades allowed")
        
        ai_log = {
            "run_id": f"v3_1_7_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v3.1.55-opposite-whale",
            "tier_config": TIER_CONFIG,
            "analyses": [],
            "trades": [],
        }
        
        trade_opportunities = []
        chop_blocked_count = 0  # V3.1.80: Track how many slots freed by chop filter

        # Confidence thresholds
        COOLDOWN_OVERRIDE_CONFIDENCE = 0.85
        HEDGE_CONFIDENCE_THRESHOLD = 0.0  # Gemini decides hedging  # V4.0: Lower threshold for more opportunities  # V3.1.37: Lowered from 90% to allow hedging
        
        # V3.1.44: Disable hedging during Capitulation - pick a side, don't fight yourself
        # Fetch F&G early so we can use it for hedge decisions
        _fg_value = 50  # V3.1.74: default F&G if fetch fails
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _fg_check = get_fear_greed_index()
            _fg_value = _fg_check.get("value", 50)
            if _fg_value < 15:
                HEDGE_CONFIDENCE_THRESHOLD = 0.0  # Gemini decides hedging  # V3.1.45: Allow hedges at 95%+ even in capitulation
                logger.info(f"CAPITULATION: F&G={_fg_value} < 15, hedging DISABLED (pick a side)")
        except:
            pass
        # V3.1.88: Always log F&G in cycle header for observability
        _fg_label = "EXTREME FEAR" if _fg_value < 20 else "FEAR" if _fg_value < 40 else "NEUTRAL" if _fg_value < 60 else "GREED" if _fg_value < 80 else "EXTREME GREED"
        logger.info(f"F&G: {_fg_value} ({_fg_label}) | Positions open: {weex_position_count}/{effective_max_positions} slots")

        # V3.2.48: Weekend/holiday liquidity mode — restrict to deep-book pairs only
        # V3.2.67: DISABLED — competition final stretch, all 7 pairs active regardless of day.
        # Altcoins are moving; restricting to BTC/ETH/SOL costs opportunities.
        _thin_liquidity = False
        _thin_reason = ""
        # _thin_liquidity, _thin_reason = _is_weekend_liquidity_mode()
        # if _thin_liquidity:
        #     logger.info(f"[WEEKEND MODE] {_thin_reason} — restricting to {', '.join(sorted(WEEKEND_ALLOWED_PAIRS))} only (thin altcoin books)")

        # V3.2.52: PRE-CYCLE EXIT SWEEP — close expired positions BEFORE the 7-pair
        # Gemini analysis starts. Signal check blocks for ~6 minutes; without this,
        # a position can breach max_hold/force_exit mid-cycle and only get closed by
        # monitor_positions AFTER the full analysis completes.
        # Checks max_hold (timestamp only), force_exit_loss, and early_exit in seconds.
        _pre_exits = 0
        for _pre_sym in list(tracker.get_active_symbols()):
            _pre_trade = tracker.get_active_trade(_pre_sym)
            if not _pre_trade:
                continue
            _pre_real_sym = _pre_sym.split(":")[0] if ":" in _pre_sym else _pre_sym
            _pre_clean = _pre_real_sym.replace("cmt_", "").upper()
            try:
                _pre_opened = datetime.fromisoformat(_pre_trade["opened_at"].replace("Z", "+00:00"))
                _pre_hours = (datetime.now(timezone.utc) - _pre_opened).total_seconds() / 3600
            except Exception:
                continue
            _pre_tier = _pre_trade.get("tier", 2)
            _pre_tc = get_tier_config(_pre_tier)
            _pre_max_hold = _pre_tc["max_hold_hours"]
            _pre_should_exit = False
            _pre_exit_reason = ""
            _pre_pnl_pct = 0.0
            # Gate 1: max_hold — timestamp only, no API call needed
            if _pre_hours >= _pre_max_hold:
                _pre_should_exit = True
                _pre_exit_reason = f"max_hold_T{_pre_tier} ({_pre_hours:.1f}h > {_pre_max_hold}h)"
            else:
                # Gates 2+3: PnL-based exits (early_exit, force_stop) — need current price
                try:
                    _pre_entry = _pre_trade.get("entry_price", 0)
                    _pre_side = _pre_trade.get("side", "LONG")
                    if _pre_entry > 0:
                        _pre_cur = get_price(_pre_real_sym)
                        if _pre_cur and _pre_cur > 0:
                            _pre_pnl_pct = ((_pre_cur - _pre_entry) / _pre_entry * 100) if _pre_side == "LONG" else ((_pre_entry - _pre_cur) / _pre_entry * 100)
                            _pre_early_h = _pre_tc["early_exit_hours"]
                            _pre_early_loss = _pre_tc["early_exit_loss_pct"]
                            _pre_force_loss = _pre_tc["force_exit_loss_pct"]
                            if _pre_hours >= _pre_early_h and _pre_pnl_pct <= _pre_early_loss:
                                _pre_should_exit = True
                                _pre_exit_reason = f"early_exit_T{_pre_tier} ({_pre_pnl_pct:.2f}% after {_pre_hours:.1f}h)"
                            elif _pre_pnl_pct <= _pre_force_loss:
                                _pre_should_exit = True
                                _pre_exit_reason = f"force_stop_T{_pre_tier} ({_pre_pnl_pct:.2f}%)"
                except Exception as _pre_pnl_err:
                    logger.debug(f"[PRE-CYCLE] PnL check error {_pre_sym}: {_pre_pnl_err}")
            if not _pre_should_exit:
                continue
            logger.warning(f"[PRE-CYCLE] {_pre_clean}: {_pre_exit_reason} — closing before signal analysis")
            try:
                _pre_pos = check_position_status(_pre_sym)
                if not _pre_pos.get("is_open"):
                    logger.info(f"[PRE-CYCLE] {_pre_clean}: already closed, cleaning tracker")
                    tracker.close_trade(_pre_sym, {"reason": _pre_exit_reason, "tier": _pre_tier, "hours_open": _pre_hours, "final_pnl_pct": _pre_pnl_pct})
                    state.trades_closed += 1
                    _pre_exits += 1
                    continue
                _pre_close = close_position_manually(
                    symbol=_pre_real_sym,
                    side=_pre_pos.get("side", _pre_trade.get("side", "LONG")),
                    size=float(_pre_pos.get("size", _pre_trade.get("size", 0)))
                )
                tracker.close_trade(_pre_sym, {"reason": _pre_exit_reason, "tier": _pre_tier, "hours_open": _pre_hours, "final_pnl_pct": _pre_pnl_pct, "close_result": _pre_close})
                state.trades_closed += 1
                _pre_oid = _pre_close.get("order_id") if _pre_close else None
                logger.info(f"  [PRE-CYCLE] Smart Exit close orderId: {_pre_oid or 'not found'}")
                upload_ai_log_to_weex(
                    stage=f"Smart Exit - {_pre_clean}",
                    input_data={"symbol": _pre_sym, "tier": _pre_tier, "hours_open": round(_pre_hours, 2)},
                    output_data={"reason": _pre_exit_reason, "pnl_pct": round(_pre_pnl_pct, 4)},
                    explanation=f"Tier {_pre_tier} pre-cycle exit: {_pre_exit_reason}. Closed before 7-pair signal analysis to prevent stale hold."[:1000],
                    order_id=int(_pre_oid) if _pre_oid and str(_pre_oid).isdigit() else None,
                )
                _pre_exits += 1
                # Refresh slot state so the freed slot is visible to pair analysis below
                open_positions = get_open_positions()
                weex_position_count = len(open_positions)
                available_slots = effective_max_positions - weex_position_count
                can_open_new = not low_equity_mode and available_slots > 0
            except Exception as _pre_close_err:
                logger.error(f"[PRE-CYCLE] Error closing {_pre_sym}: {_pre_close_err}")
        if _pre_exits > 0:
            logger.info(f"[PRE-CYCLE] Swept {_pre_exits} expired position(s) before signal analysis")

        # Build map: symbol -> {side: position}
        # This tracks BOTH long and short for each symbol
        position_map = {}
        for pos in open_positions:
            symbol = pos.get("symbol")
            side = pos.get("side", "").upper()
            if symbol not in position_map:
                position_map[symbol] = {}
            position_map[symbol][side] = pos
        
        # V3.1.100: Execute deferred opposite flips for positions that closed
        _execute_deferred_flips(position_map, balance)

        # ANALYZE ALL PAIRS
        # V3.1.88: Cycle stats counters for end-of-cycle summary
        _cycle_signals = 0       # Pairs that returned LONG/SHORT (any confidence)
        _cycle_above_80 = 0      # Signals at 80%+ (tradeable)
        _cycle_blocked = 0       # Blocked by cooldown/blacklist/loss-streak
        _cycle_wait = 0          # Analyzer returned WAIT
        # V3.1.93: Track signal landscape for PM
        _best_unexecuted = None  # Best signal that couldn't trade (slots full, already positioned, etc.)
        _chop_blocked_pairs = []
        for pair, pair_info in TRADING_PAIRS.items():
            symbol = pair_info["symbol"]
            tier = pair_info.get("tier", 2)
            tier_config = get_tier_config(tier)

            # V3.2.48: Weekend/holiday thin-liquidity filter — skip altcoins with thin books.
            # Existing positions on filtered pairs are still MONITORED (position_monitor runs normally).
            # Only NEW signal analysis + entry is skipped for illiquid pairs.
            if _thin_liquidity and pair not in WEEKEND_ALLOWED_PAIRS:
                # Still monitor existing positions — don't skip if we already have a position
                symbol_positions = position_map.get(symbol, {})
                if not symbol_positions:
                    logger.info(f"  {pair} (T{tier}): WEEKEND SKIP — thin altcoin book ({_thin_reason})")
                    continue
                # If we have an existing position, fall through to analyze (monitor + opposite swap still works)

            # Check existing positions on this symbol
            symbol_positions = position_map.get(symbol, {})
            has_long = "LONG" in symbol_positions
            has_short = "SHORT" in symbol_positions
            long_pnl = symbol_positions.get("LONG", {}).get("unrealized_pnl", 0) if has_long else 0
            short_pnl = symbol_positions.get("SHORT", {}).get("unrealized_pnl", 0) if has_short else 0
            
            # Check cooldown
            on_cooldown = tracker.is_on_cooldown(symbol)
            cooldown_remaining = tracker.get_cooldown_remaining(symbol) if on_cooldown else 0
            
            try:
                # V3.1.73: Mark progress PER-PAIR so watchdog doesn't kill during long signal checks
                _mark_progress()

                # ALWAYS analyze
                decision = run_with_retry(
                    analyzer.analyze,
                    pair, pair_info, balance, competition, open_positions
                )

                # V3.1.73: Mark progress after each analysis completes
                _mark_progress()

                confidence = decision.get("confidence", 0)
                signal = decision.get("decision", "WAIT")
                
                # Build status string
                status_parts = []
                if has_long:
                    status_parts.append(f"L:{long_pnl:+.1f}")
                if has_short:
                    status_parts.append(f"S:{short_pnl:+.1f}")
                if on_cooldown:
                    status_parts.append(f"CD:{cooldown_remaining:.1f}h")
                
                status_str = f" [{', '.join(status_parts)}]" if status_parts else ""
                logger.info(f"  {pair} (T{tier}): {signal} ({confidence:.0%}){status_str}")

                # V3.1.88: Log WHY signal is WAIT (from analyzer) for observability
                if decision.get("adx_gate_blocked"):
                    _cycle_wait += 1
                    _adx_orig = decision.get("adx_gate_original_decision", "?")
                    _adx_conf = decision.get("adx_gate_original_confidence", 0)
                    _adx_val = decision.get("chop_data", {}).get("adx", 0)
                    logger.info(f"    -> ADX GATE: was {_adx_orig} {_adx_conf:.0%} but ADX={_adx_val:.1f} < {ADX_FLOOR_GATE} (flatline)")
                elif signal == "WAIT" and confidence == 0:
                    _cycle_wait += 1
                    _raw_reason = decision.get("reasoning", "")[:80]
                    if _raw_reason:
                        logger.info(f"    -> reason: {_raw_reason}")
                elif signal in ("LONG", "SHORT") and confidence < 0.80:
                    _cycle_signals += 1
                    logger.info(f"    -> below 80% floor ({confidence:.0%})")
                elif signal in ("LONG", "SHORT") and confidence >= 0.80:
                    _cycle_signals += 1
                    _cycle_above_80 += 1

                # V3.2.58: Shorts toggle (hot-reloadable via smt_settings.json).
                # Set {"enable_shorts": false} in smt_settings.json to disable without restart.
                # Default: enabled (all pairs, since V3.2.18).
                _shorts_enabled = True
                try:
                    from hot_reload import is_direction_enabled
                    _shorts_enabled = is_direction_enabled("SHORT")
                except Exception:
                    _shorts_enabled = True  # Default to enabled if hot_reload unavailable
                if signal == "SHORT" and not _shorts_enabled:
                    logger.info(f"    -> SHORT disabled (LONG-only mode)")
                    signal = "WAIT"
                    confidence = 0
                    decision["decision"] = "WAIT"
                    decision["confidence"] = 0

                # V3.1.97: REMOVED BTC fear shield, consecutive loss block.
                # Ensemble already sees F&G + regime. 80% floor + chop filter are the only gates.

                # V3.1.104: Signal persistence tracking.
                # Record the pre-filter signal each cycle (use chop_original_decision when blocked).
                # If ensemble gives same direction ≥80% for 2+ consecutive cycles → persistent.
                _raw_dir = None
                _raw_conf = 0.0
                if decision.get("chop_blocked"):
                    _raw_dir = decision.get("chop_original_decision")
                    _raw_conf = decision.get("chop_pre_penalty_confidence", 0.0)
                elif decision.get("adx_gate_blocked"):
                    # V3.2.67: ADX gate blocked — preserve original signal for persistence tracking
                    _raw_dir = decision.get("adx_gate_original_decision")
                    _raw_conf = decision.get("adx_gate_original_confidence", 0.0)
                elif signal in ("LONG", "SHORT") and confidence >= MIN_CONFIDENCE_TO_TRADE:
                    _raw_dir = signal
                    _raw_conf = confidence

                if _raw_dir and _raw_conf >= MIN_CONFIDENCE_TO_TRADE:
                    _now_iso = datetime.now(timezone.utc).isoformat()
                    _prev = tracker.signal_history.get(pair, {})
                    if _prev.get("direction") == _raw_dir:
                        tracker.signal_history[pair] = {
                            "direction": _raw_dir,
                            "confidence": _raw_conf,
                            "count": _prev.get("count", 0) + 1,
                            "entry_time": _prev.get("entry_time", _now_iso),  # first time this direction appeared
                            "last_seen": _now_iso,
                            "prev_direction": _prev.get("prev_direction"),  # V3.2.68: preserve previous direction
                        }
                    else:
                        # V3.2.68: Store the previous direction when a flip happens.
                        # Judge needs to know "was SHORT last cycle, now LONG" = dip bounce signal.
                        _prev_dir = _prev.get("direction")  # What it WAS before this flip
                        tracker.signal_history[pair] = {
                            "direction": _raw_dir,
                            "confidence": _raw_conf,
                            "count": 1,
                            "entry_time": _now_iso,
                            "last_seen": _now_iso,
                            "prev_direction": _prev_dir,  # V3.2.68: what direction was BEFORE this flip
                        }
                else:
                    tracker.signal_history.pop(pair, None)

                # V3.1.104 / V3.2.6: Medium chop persistence override.
                # If the same direction at ≥80% has appeared 2+ consecutive cycles,
                # the ensemble has been consistently right — override MEDIUM chop block.
                # HARD chop still blocks unconditionally.
                _persist_count = tracker.signal_history.get(pair, {}).get("count", 0)
                if (decision.get("chop_blocked") and
                        decision.get("chop_data", {}).get("severity") == "medium" and
                        _persist_count >= 2):
                    _ov_dir = decision.get("chop_original_decision")
                    _ov_conf = decision.get("chop_pre_penalty_confidence", 0.0)
                    logger.info(f"    -> [PERSIST] {pair} {_ov_dir} {_ov_conf:.0%} — {_persist_count} consecutive cycles, medium chop overridden")
                    decision["decision"] = _ov_dir
                    decision["confidence"] = _ov_conf
                    decision["chop_blocked"] = False
                    signal = _ov_dir
                    confidence = _ov_conf

                # V3.1.80: Track chop-blocked trades for fallback logic
                if decision.get("chop_blocked"):
                    chop_blocked_count += 1
                    _chop_data = decision.get("chop_data", {})
                    logger.info(f"    -> CHOP BLOCKED: {_chop_data.get('reason', 'choppy market')} (fallback slot available)")
                    upload_ai_log_to_weex(
                        stage=f"Chop Filter: {pair} {decision.get('chop_original_decision', 'TRADE')} blocked",
                        input_data={"pair": pair, "symbol": symbol, "chop_data": _chop_data},
                        output_data={"action": "CHOP_BLOCKED", "severity": _chop_data.get("severity", "unknown"),
                                     "adx": _chop_data.get("adx", 0), "bb_width": _chop_data.get("bb_width_pct", 0)},
                        explanation=f"Chop filter blocked {pair} entry. Market is sideways/range-bound: {_chop_data.get('reason', 'N/A')}. Slot freed for next trending candidate at 75%+ confidence."
                    )
                    # V3.1.93: Track chop-blocked signal details for PM
                    _chop_blocked_pairs.append({
                        "pair": pair,
                        "direction": decision.get("chop_original_decision", signal),
                        "pre_chop_conf": decision.get("chop_pre_penalty_confidence", 0),
                    })

# Telegram alerts for ALL tradeable signals
#                if signal in ("LONG", "SHORT") and confidence >= 0.75:
#                    try:
#                        from telegram_alerts import send_telegram_alert
#                        tier_cfg = TIER_CONFIG[f"Tier {tier}"]
#                        current_price = get_price(f"cmt_{pair.lower()}usdt")
#                        
#                        # Calculate targets
#                        if signal == "LONG":
#                            tp_price = current_price * (1 + tier_cfg["take_profit"]/100)
#                            sl_price = current_price * (1 - tier_cfg["stop_loss"]/100)
#                        else:  # SHORT
#                            tp_price = current_price * (1 - tier_cfg["take_profit"]/100)
#                            sl_price = current_price * (1 + tier_cfg["stop_loss"]/100)
#                        
#                        alert_msg = f"""🚨 <b>SMT SIGNAL - {pair}</b>
#
#Direction: <b>{signal}</b>
#Confidence: <b>{confidence:.0%}</b>
#Tier: {tier} ({f"Tier {tier}"})
#
#Entry: ${current_price:,.2f}
#TP: ${tp_price:,.2f} ({tier_cfg["take_profit"]*100}%)
#SL: ${sl_price:,.2f} ({tier_cfg["stop_loss"]*100}%)
#
#Reasoning:
#{decision.get('reasoning', 'N/A')[:400]}"""
#                        
#                        send_telegram_alert(alert_msg)
#                        logger.info(f"[TELEGRAM] {pair} {signal} alert sent")
#                    except Exception as e:
#                        logger.error(f"[TELEGRAM] Alert failed: {e}")
                
                # V3.1.97: REMOVED blacklist/cooldown entry block.
                # If ensemble says 80%+ and chop is clear, we trade.

                # V3.2.1: Capture below-floor near-miss for RL logging.
                # Persist tracking (above) sets _raw_dir for chop-blocked + ≥80% cases.
                # Below-floor (e.g. 78% LONG from analyzer) slips through — capture it here.
                if _raw_dir is None and signal in ("LONG", "SHORT"):
                    _raw_dir = signal
                    _raw_conf = confidence

                # Determine tradability
                can_trade_this = False
                trade_type = "none"

                if signal == "LONG":
                    if has_long:
                        logger.info(f"    -> Already LONG")
                    elif has_short:
                        # V3.1.53: OPPOSITE - tighten SHORT SL + open LONG
                        short_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:SHORT")
                        existing_conf = short_trade.get("confidence", 0.75) if short_trade else 0.75
                        if confidence >= existing_conf:  # V3.1.74: >= (was > which blocked 85% vs 85% flips)
                            # V3.1.100: Check TP proximity + age gates before flipping
                            _opp_blocked, _opp_reason = _check_opposite_swap_gates(
                                symbol=symbol, existing_side="SHORT", new_signal="LONG",
                                new_confidence=confidence, opportunity={"pair_info": pair_info, "decision": decision}
                            )
                            if _opp_blocked:
                                logger.info(f"    -> OPPOSITE BLOCKED: {_opp_reason}")
                                upload_ai_log_to_weex(
                                    stage=f"Opposite Blocked: {symbol.replace('cmt_','').upper()}",
                                    input_data={"symbol": symbol, "existing_side": "SHORT", "new_signal": "LONG", "new_conf": confidence},
                                    output_data={"action": "DEFERRED", "reason": _opp_reason},
                                    explanation=f"AI blocked opposite flip on {symbol.replace('cmt_','').upper()}. {_opp_reason}. Signal queued for deferred execution after existing position closes."[:1000]
                                )
                            else:
                                can_trade_this = True
                                trade_type = "opposite"
                                _flip_tag = "EMERGENCY FLIP" if confidence >= EMERGENCY_FLIP_CONFIDENCE else "OPPOSITE"
                                logger.info(f"    -> {_flip_tag}: LONG {confidence:.0%} >= SHORT {existing_conf:.0%}. Closing SHORT, opening LONG")
                        else:
                            logger.info(f"    -> Has SHORT at {existing_conf:.0%}, LONG {confidence:.0%} not stronger. Hold.")
                            upload_ai_log_to_weex(
                                stage=f"Hold: {symbol.replace('cmt_','').upper()} SHORT kept",
                                input_data={"symbol": symbol, "existing_side": "SHORT", "existing_conf": existing_conf, "new_signal": "LONG", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain SHORT position. Existing SHORT confidence ({existing_conf:.0%}) > new LONG signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        # V3.2.59: Always collect 85%+ signals — slot check deferred to execution
                        # time where positions are re-queried (catches TP/SL during analysis window).
                        if can_open_new or confidence >= MIN_CONFIDENCE_TO_TRADE:
                            can_trade_this = True
                            trade_type = "new"

                elif signal == "SHORT":
                    # V3.2.18: Shorts allowed for ALL pairs (was LTC only since V3.2.13)
                    if has_short:
                        logger.info(f"    -> Already SHORT")
                    elif has_long:
                        # V3.1.53: OPPOSITE - tighten LONG SL + open SHORT
                        long_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:LONG")
                        existing_conf = long_trade.get("confidence", 0.75) if long_trade else 0.75
                        if confidence >= existing_conf:  # V3.1.74: >= (was > which blocked 85% vs 85% flips)
                            # V3.1.100: Check TP proximity + age gates before flipping
                            _opp_blocked, _opp_reason = _check_opposite_swap_gates(
                                symbol=symbol, existing_side="LONG", new_signal="SHORT",
                                new_confidence=confidence, opportunity={"pair_info": pair_info, "decision": decision}
                            )
                            if _opp_blocked:
                                logger.info(f"    -> OPPOSITE BLOCKED: {_opp_reason}")
                                upload_ai_log_to_weex(
                                    stage=f"Opposite Blocked: {symbol.replace('cmt_','').upper()}",
                                    input_data={"symbol": symbol, "existing_side": "LONG", "new_signal": "SHORT", "new_conf": confidence},
                                    output_data={"action": "DEFERRED", "reason": _opp_reason},
                                    explanation=f"AI blocked opposite flip on {symbol.replace('cmt_','').upper()}. {_opp_reason}. Signal queued for deferred execution after existing position closes."[:1000]
                                )
                            else:
                                can_trade_this = True
                                trade_type = "opposite"
                                _flip_tag = "EMERGENCY FLIP" if confidence >= EMERGENCY_FLIP_CONFIDENCE else "OPPOSITE"
                                logger.info(f"    -> {_flip_tag}: SHORT {confidence:.0%} >= LONG {existing_conf:.0%}. Closing LONG, opening SHORT")
                        else:
                            logger.info(f"    -> Has LONG at {existing_conf:.0%}, SHORT {confidence:.0%} not stronger. Hold.")
                            upload_ai_log_to_weex(
                                stage=f"Hold: {symbol.replace('cmt_','').upper()} LONG kept",
                                input_data={"symbol": symbol, "existing_side": "LONG", "existing_conf": existing_conf, "new_signal": "SHORT", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain LONG position. Existing LONG confidence ({existing_conf:.0%}) > new SHORT signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        # V3.2.59: Always collect 85%+ signals — slot check deferred to execution
                        if can_open_new or confidence >= MIN_CONFIDENCE_TO_TRADE:
                            can_trade_this = True
                            trade_type = "new"

                # V3.1.93: Track best non-executed signal for PM context
                if signal in ("LONG", "SHORT") and confidence >= 0.85 and not can_trade_this:
                    if _best_unexecuted is None or confidence > _best_unexecuted.get("confidence", 0):
                        _best_unexecuted = {"pair": pair, "direction": signal, "confidence": confidence}

                # Build comprehensive vote details with FULL reasoning
                vote_details = []
                market_analysis = ""

                persona_votes = decision.get("persona_votes", [])
                
                # If no persona_votes, try to get from vote_summary
                if not persona_votes and decision.get("vote_summary"):
                    vote_details = decision.get("vote_summary", [])
                else:
                    for vote in persona_votes:
                        persona = vote.get("persona", "?")
                        vote_signal = vote.get("signal", "?")
                        conf = vote.get("confidence", 0)
                        reason = vote.get("reasoning", "")[:80]  # Allow more chars per persona
                        vote_details.append(f"{persona}={vote_signal}({conf:.0%}): {reason}")
                        
                        # Capture Sentiment's market context (Gemini analysis)
                        if persona == "SENTIMENT" and vote.get("market_context"):
                            market_analysis = ""
                
                judge_summary = decision.get("reasoning", "")
                
                # Build full explanation with ALL details
                explanation_parts = [judge_summary]
                
                if vote_details:
                    explanation_parts.append(f"\n\nPersona Votes:\n" + "\n".join(f"- {v}" for v in vote_details))
                
                if market_analysis:
                    explanation_parts.append(f"\n\nMarket Analysis (Gemini):\n{market_analysis}")
                
                full_explanation = "".join(explanation_parts)
                
                # Upload AI log with comprehensive explanation
                upload_ai_log_to_weex(
                    stage=f"Analysis - {pair} (Tier {tier})",
                    input_data={
                        "pair": pair,
                        "tier": tier,
                        "balance": balance,
                        "has_long": has_long,
                        "has_short": has_short,
                        "on_cooldown": on_cooldown,
                    },
                    output_data={
                        "decision": signal,
                        "confidence": confidence,
                        "tp_pct": tier_config["tp_pct"],
                        "sl_pct": tier_config["sl_pct"],
                        "can_trade": can_trade_this,
                        "trade_type": trade_type,
                    },
                    explanation=full_explanation[:1000]  # WEEX allows 500 words
                )
                
                # Save to local log
                ai_log["analyses"].append({
                    "pair": pair,
                    "tier": tier,
                    "decision": signal,
                    "confidence": confidence,
                    "has_long": has_long,
                    "has_short": has_short,
                    "trade_type": trade_type,
                })
                # V3.1.24: Log decision for RL training
                if RL_ENABLED and rl_collector:
                    try:
                        # Get regime data for RL logging
                        try:
                            rl_regime = get_market_regime_for_exit()
                        except Exception:
                            rl_regime = {"change_24h": 0, "change_4h": 0, "regime": "NEUTRAL"}
                        
                        persona_dict = {}
                        for v in decision.get("persona_votes", []):
                            persona_dict[v.get("persona", "?")] = {
                                "signal": v.get("signal", "WAIT"),
                                "confidence": v.get("confidence", 0.5)
                            }
                        
                        rl_collector.log_decision(
                            symbol=symbol,
                            action=signal,
                            confidence=confidence,
                            persona_votes=persona_dict,
                            market_state={
                                "btc_24h": rl_regime.get("change_24h", 0),
                                "btc_4h": rl_regime.get("change_4h", 0),
                                "regime": rl_regime.get("regime", "NEUTRAL"),
                                # V3.2.1: Near-miss context — captures pre-filter direction when
                                # the signal was blocked by chop, or was below the 80% floor.
                                "near_miss_signal": _raw_dir,
                                "near_miss_confidence": _raw_conf,
                                "chop_blocked": bool(decision.get("chop_blocked")),
                            },
                            portfolio_state={
                                "num_positions": len(open_positions),
                                "long_exposure": sum(1 for p in open_positions if p.get("side") == "LONG") / 8,
                                "short_exposure": sum(1 for p in open_positions if p.get("side") == "SHORT") / 8,
                                "upnl_pct": sum(float(p.get("unrealized_pnl", 0)) for p in open_positions) / max(balance, 1) * 100,
                            },
                            tier=tier,
                        )
                    except Exception as e:
                        logger.warning(f"RL log error: {e}")

                
                # Add to opportunities if tradeable
                if can_trade_this:
                    trade_opportunities.append({
                        "pair": pair,
                        "pair_info": pair_info,
                        "decision": decision,
                        "tier": tier,
                        "trade_type": trade_type,
                    })
                
                time.sleep(2)
                _mark_progress()  # V3.1.74: per-pair progress mark (was only after full signal check)

            except Exception as e:
                logger.error(f"Error analyzing {pair}: {e}")
        
# V3.2.38: Execute qualifying trades — 4-slot hard cap enforced via can_open_new
        trades_executed = 0  # V3.2.36 fix: initialize before if/else so it's always defined

        if trade_opportunities:
            # V3.2.58: Sort by confidence desc, then tier desc (altcoin tiebreak — T3 > T2 > T1 at equal confidence)
            trade_opportunities.sort(key=lambda x: (x["decision"]["confidence"], x["pair_info"].get("tier", 1)), reverse=True)

            # V3.2.59: Re-query positions BEFORE execution — catches TP/SL that fired during
            # the ~7 min analysis window. Without this, slots show as occupied when positions
            # already closed on WEEX, blocking new trades (ADA 85% blocked by closed SOL/XRP bug).
            _fresh_positions = get_open_positions()
            _fresh_count = len(_fresh_positions)
            if _fresh_count != len(open_positions):
                logger.info(f"  [SLOT REFRESH] Positions changed during analysis: {len(open_positions)} → {_fresh_count}")
                open_positions = _fresh_positions
                weex_position_count = _fresh_count
                available_slots = effective_max_positions - weex_position_count
                can_open_new = not low_equity_mode and available_slots > 0
                # Rebuild position_map for execution
                position_map = {}
                for pos in open_positions:
                    _sym = pos.get("symbol")
                    _side = pos.get("side", "").upper()
                    if _sym not in position_map:
                        position_map[_sym] = {}
                    position_map[_sym][_side] = pos

            current_positions = _fresh_count
            logger.info(f"  Executing {len(trade_opportunities)} opportunit{'y' if len(trade_opportunities)==1 else 'ies'} ({current_positions} positions currently open, {available_slots} slots free)")

            trades_executed_count_ref = 0  # kept for slot_swap compat below

            # V3.1.41: Count directional exposure BEFORE executing
            long_count = sum(1 for p in _fresh_positions if p.get("side","").upper() == "LONG")
            short_count = sum(1 for p in _fresh_positions if p.get("side","").upper() == "SHORT")
            MAX_SAME_DIRECTION = 5  # V3.1.55: was 99, caused all-LONG pileup  # V3.1.42: Recovery - match Gemini Judge rule 9
            # V3.1.43: Allow 7 LONGs during Capitulation (F&G < 15)
            if trade_opportunities:
                first_fg = trade_opportunities[0]["decision"].get("fear_greed", 50)
                if first_fg < 15:
                    MAX_SAME_DIRECTION = 7  # V3.1.56: capitulation allows 7
                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, keeping hard cap at BASE_SLOTS={BASE_SLOTS}")
            
            for opportunity in trade_opportunities:
                confidence = opportunity["decision"]["confidence"]

                # V3.2.59: FALLBACK GATE REMOVED — 85% is the absolute floor.
                # Old fallback_only path (80-84%) is dead code since MIN_CONFIDENCE_TO_TRADE=0.85.
                # All trades reaching this point are 85%+. No chop fallback needed.

                # V3.2.25: No slot-bypass needed — all 80%+ signals execute; resolve_opposite_sides() at cycle end cleans up
                trade_type_check = opportunity.get("trade_type", "none")

                # V3.1.82: SLOT SWAP - close weakest position to free slot for stronger signal
                if trade_type_check == "slot_swap":
                    _swap_target = _find_weakest_position(open_positions, opportunity["pair_info"]["symbol"], position_map, fear_greed=_fg_value)
                    if _swap_target:
                        _swap_sym = _swap_target["symbol"]
                        _swap_side = _swap_target["side"]
                        _swap_size = _swap_target["size"]
                        _swap_pnl = _swap_target["unrealized_pnl"]
                        _swap_pnl_pct = _swap_target["pnl_pct"]
                        _swap_clean = _swap_sym.replace("cmt_", "").upper()

                        logger.info(f"SLOT SWAP: Closing {_swap_side} {_swap_clean} (PnL: {_swap_pnl_pct:+.2f}%, ${_swap_pnl:+.1f}) for {opportunity['pair']} {opportunity['decision']['decision']} at {confidence:.0%}")

                        try:
                            close_result = close_position_manually(
                                symbol=_swap_sym,
                                side=_swap_side,
                                size=_swap_size
                            )

                            # Close in tracker (try both key formats)
                            _swap_key = f"{_swap_sym}:{_swap_side}"
                            if _swap_key in tracker.active_trades:
                                tracker.close_trade(_swap_key, {
                                    "reason": f"slot_swap_for_{opportunity['pair'].lower()}",
                                    "pnl": _swap_pnl,
                                    "final_pnl_pct": _swap_pnl_pct,
                                })
                            elif _swap_sym in tracker.active_trades:
                                tracker.close_trade(_swap_sym, {
                                    "reason": f"slot_swap_for_{opportunity['pair'].lower()}",
                                    "pnl": _swap_pnl,
                                    "final_pnl_pct": _swap_pnl_pct,
                                })

                            _swap_oid = close_result.get("order_id") if close_result else None
                            logger.info(f"  [AI-LOG] Slot Swap close orderId: {_swap_oid or 'not found'}")
                            upload_ai_log_to_weex(
                                stage=f"Slot Swap: {_swap_clean} {_swap_side} closed for {opportunity['pair']}",
                                input_data={
                                    "closed_symbol": _swap_sym, "closed_side": _swap_side,
                                    "closed_pnl_pct": round(_swap_pnl_pct, 2),
                                    "new_signal": opportunity["decision"]["decision"],
                                    "new_pair": opportunity["pair"], "new_confidence": confidence,
                                },
                                output_data={"action": "SLOT_SWAP_CLOSE", "freed_for": opportunity["pair"]},
                                explanation=(
                                    f"PM slot optimization: {_swap_clean} {_swap_side} at {_swap_pnl_pct:+.2f}% is near breakeven with fading momentum. "
                                    f"Closing to free slot for {opportunity['pair']} {opportunity['decision']['decision']} at {confidence:.0%} confidence. "
                                    f"Better risk-adjusted opportunity identified."
                                ),
                                order_id=int(_swap_oid) if _swap_oid and str(_swap_oid).isdigit() else None,
                            )
                            state.trades_closed += 1
                            available_slots += 1
                            # V3.1.82 FIX: Remove closed position from open_positions so
                            # next slot swap can't target the same (already-closed) position.
                            # Without this, LTC gets "closed" twice → 4 positions on 3 slots.
                            open_positions = [p for p in open_positions if p.get("symbol") != _swap_sym]
                            time.sleep(5)  # V3.1.82 FIX: 5s wait (was 2s). WEEX needs time to settle
                            # the close order before new trade can set_leverage on a different symbol.
                            # At 2s, "open orders" from the close block set_leverage → wrong leverage.
                        except Exception as _swap_err:
                            logger.error(f"SLOT SWAP FAILED: {_swap_err}")
                            continue
                    else:
                        logger.info(f"SLOT SWAP: No weak position found for {opportunity['pair']}, skipping")
                        continue

                # V3.1.41: DIRECTIONAL LIMIT CHECK
                sig_check = opportunity["decision"]["decision"]
                if sig_check == "LONG" and long_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {long_count} LONGs already open, skipping {opportunity['pair']} LONG")
                    continue
                if sig_check == "SHORT" and short_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {short_count} SHORTs already open, skipping {opportunity['pair']} SHORT")
                    continue

                # V3.2.18: Shorts allowed for ALL pairs (LTC-only restriction removed)

                # V3.1.51: SESSION-AWARE TRADING with confidence adjustments
                import datetime as _dt_module
                utc_hour = _dt_module.datetime.now(_dt_module.timezone.utc).hour
                opp_confidence = opportunity["decision"]["confidence"]
                opp_fear_greed = opportunity["decision"].get("fear_greed", 50)
                is_extreme_fear = opp_fear_greed < 20
                
                # V3.1.85: HARD 80% FLOOR - no session discounts.
                # Quality > quantity. One 85% win beats three 70% coinflips.
                # $3.6k -> $20k needs consistent wins, not volume.
                if 13 <= utc_hour < 16:
                    session_name = "US_OPEN"
                elif 0 <= utc_hour < 3:
                    session_name = "ASIA_OPEN"
                elif 6 <= utc_hour < 9:
                    session_name = "DEAD_HOURS"
                elif 0 <= utc_hour < 6:
                    session_name = "ASIA"
                else:
                    session_name = "ACTIVE"
                session_min_conf = 0.85  # V3.2.59: Matches MIN_CONFIDENCE_TO_TRADE (was 0.80)
                
                # V3.1.85: CONTRARIAN BOOST REMOVED - 80% hard floor applies always.
                # Even in extreme fear/greed, only trade with 80%+ confidence.
                # Low-confidence contrarian trades bleed fees and kill compounding.
                if opp_fear_greed < 20 or opp_fear_greed > 80:
                    logger.info(f"EXTREME F&G={opp_fear_greed} but 80% floor enforced for {opportunity['pair']} ({opp_confidence:.0%})")

                if opp_confidence < session_min_conf:
                    logger.warning(f"SESSION FILTER [{session_name}]: {utc_hour}:00 UTC, {opp_confidence:.0%} < {session_min_conf:.0%}, skipping {opportunity['pair']}")
                    continue
                else:
                    logger.info(f"SESSION [{session_name}]: {utc_hour}:00 UTC, {opp_confidence:.0%} >= {session_min_conf:.0%}, proceeding {opportunity['pair']}")
                
                # V3.1.75: REGIME VETO + F&G SANITY CHECK
                # Rule 1: F&G < 15 = CAPITULATION = LONG ONLY (no shorts into bounces)
                # Rule 2: F&G > 85 = EUPHORIA = SHORT ONLY (no longs into tops)
                # Rule 3: Regime-based veto with WHALE+FLOW override (not WHALE alone)
                try:
                    _regime_now = get_market_regime_for_exit()
                except Exception as _re:
                    logger.warning(f"REGIME VETO: regime check failed ({_re}), allowing trade")
                    _regime_now = {"regime": "NEUTRAL"}
                _regime_label = _regime_now.get("regime", "NEUTRAL")
                _opp_signal = opportunity["decision"]["decision"]
                _regime_vetoed = False

                # V3.1.97: REMOVED regime veto, F&G veto, global cooldown.
                # Ensemble sees regime + F&G. 80% floor + chop filter are the only gates.

                tier = opportunity["tier"]
                tier_config = get_tier_config(tier)
                trade_type = opportunity["trade_type"]
                pair = opportunity["pair"]
                signal = opportunity["decision"]["decision"]
                confidence = opportunity["decision"]["confidence"]

                # V3.2.59: Execution-time slot check for new trades.
                # Signals are collected during analysis regardless of can_open_new,
                # but slot availability is enforced HERE with fresh position data.
                if trade_type == "new" and available_slots <= 0:
                    if confidence >= CONFIDENCE_EXTRA_SLOT and not low_equity_mode:
                        logger.info(f"  90%+ EXTRA SLOT: {pair} {signal} {confidence:.0%} — bypassing slot cap")
                    else:
                        logger.info(f"  SLOT BLOCKED: {pair} {signal} {confidence:.0%} — {available_slots} slots, need 90%+ for extra")
                        continue

                logger.info(f"")
                type_label = "[FLIP] " if trade_type == "flip" else ""
                logger.info(f"EXECUTING {type_label}{pair} {signal} (T{tier}) - {confidence:.0%}")
                
                # V3.2.35: OPPOSITE SIDE - close existing position first, then open new direction
                if trade_type == "opposite":
                    try:
                        opp_side = "SHORT" if signal == "LONG" else "LONG"
                        sym = opportunity["pair_info"]["symbol"]
                        sym_positions = position_map.get(sym, {})
                        opp_pos = sym_positions.get(opp_side)

                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))

                            from smt_nightly_trade_v3_1 import close_position_manually

                            logger.info(f"  [OPPOSITE] Closing {opp_side} {pair} (entry=${opp_entry:.4f}, PnL={opp_pnl:.2f}) before opening {signal}")
                            try:
                                close_result = close_position_manually(sym, opp_side, opp_size)
                                logger.info(f"  [OPPOSITE] Closed {opp_side} {pair}: {close_result.get('order_id', 'no order id')}")

                                # Remove from tracker
                                _opp_pnl_pct = (opp_pnl / (opp_entry * opp_size) * 100) if opp_entry and opp_size else 0
                                for key in [f"{sym}:{opp_side}", sym]:
                                    if key in tracker.active_trades:
                                        tracker.close_trade(key, {
                                            "reason": f"opposite_signal_{signal.lower()}",
                                            "pnl": opp_pnl,
                                            "final_pnl_pct": _opp_pnl_pct,
                                        })
                                        break

                                _dir_shift_oid = close_result.get("order_id")
                                _is_emergency_flip = confidence >= EMERGENCY_FLIP_CONFIDENCE
                                _flip_stage = "Emergency Flip" if _is_emergency_flip else "Directional Shift"
                                logger.info(f"  [AI-LOG] {_flip_stage} close orderId: {_dir_shift_oid or 'not found'}")
                                upload_ai_log_to_weex(
                                    stage=f"{_flip_stage}: {pair} {opp_side} closed for {signal}",
                                    input_data={
                                        "symbol": sym,
                                        "closed_side": opp_side,
                                        "closed_size": opp_size,
                                        "closed_entry": opp_entry,
                                        "closed_pnl": round(opp_pnl, 2),
                                        "new_signal": signal,
                                        "new_confidence": confidence,
                                        "emergency_flip": _is_emergency_flip,
                                    },
                                    output_data={
                                        "action": "OPPOSITE_CLOSE",
                                        "reason": "emergency_opposite_signal" if _is_emergency_flip else "opposite_signal_stronger",
                                    },
                                    explanation=f"{'EMERGENCY: age gate bypassed at ' + str(round(confidence*100)) + '%+ conviction. ' if _is_emergency_flip else ''}AI closed {opp_side} {pair} (entry ${opp_entry:.4f}, PnL {opp_pnl:.2f}) to open {signal} at {confidence:.0%} confidence."[:1000],
                                    order_id=int(_dir_shift_oid) if _dir_shift_oid and str(_dir_shift_oid).isdigit() else None,
                                )
                            except Exception as close_err:
                                logger.error(f"  [OPPOSITE] Error closing {opp_side} {pair}: {close_err}")
                                logger.error(traceback.format_exc())
                    except Exception as e:
                        logger.error(f"  [OPPOSITE] Error in opposite handler: {e}")
                        logger.error(traceback.format_exc())

                
                try:
                    trade_result = run_with_retry(
                        execute_trade,
                        opportunity["pair_info"], opportunity["decision"], balance
                    )
                    
                    if trade_result.get("executed"):
                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        _pos_usdt = trade_result.get("position_usdt", 0)
                        if _pos_usdt > 0:
                            _open_fee = _pos_usdt * 20 * TAKER_FEE_RATE
                            logger.info(f"  [FEE] Open: ~${_open_fee:.2f} (0.08% × ${_pos_usdt*20:.0f} notional)")
                        
                        trade_result["confidence"] = confidence  # V3.1.41: Store for profit guard
                        # V3.1.51: Store whale confidence for Smart Hold protection
                        whale_conf = 0.0
                        whale_dir = "NEUTRAL"
                        for pv in opportunity.get("decision", {}).get("persona_votes", []):
                            if pv.get("persona") == "WHALE":
                                whale_conf = pv.get("confidence", 0.0)
                                whale_dir = pv.get("signal", "NEUTRAL")
                                break
                        trade_result["whale_confidence"] = whale_conf
                        trade_result["whale_direction"] = whale_dir
                        tracker.add_trade(opportunity["pair_info"]["symbol"], trade_result)
                        state.trades_opened += 1
                        _last_trade_opened_at = time.time()  # V3.1.65: Update global cooldown
                        ai_log["trades"].append(trade_result)
                        # V3.1.41: Update directional count
                        if signal == "LONG": long_count += 1
                        elif signal == "SHORT": short_count += 1
                        trades_executed += 1

                        # V3.2.47: Decrement available_slots after each successful trade
                        # BUG FIX: Without this, the loop executes ALL collected opportunities
                        # even when slots are full (available_slots was never decremented)
                        available_slots -= 1

                        # Update available balance for next trade
                        balance = get_balance()
                        
                    else:
                        logger.warning(f"Trade failed: {trade_result.get('reason')}")
                        
                except Exception as e:
                    logger.error(f"Error executing {pair}: {e}")
                
                time.sleep(3)  # V3.1.68: 3s delay between trades (Gemini rate limit)

                # V3.2.47: Stop executing if all slots are filled
                if available_slots <= 0:
                    remaining = len(trade_opportunities) - (trade_opportunities.index(opportunity) + 1)
                    if remaining > 0:
                        logger.info(f"  SLOTS FULL after {trades_executed} trade(s) — skipping {remaining} remaining opportunit{'y' if remaining==1 else 'ies'}")
                    break

            if trades_executed > 0:
                logger.info(f"")
                logger.info(f"Executed {trades_executed} trades this cycle")
        else:
            logger.info(f"")
            logger.info("No trade opportunities")

        # V3.2.25: Resolve opposite sides immediately after execution (not deferred to 2-min loop)
        if trades_executed > 0:
            try:
                resolve_opposite_sides()
            except Exception as _re:
                logger.warning(f"Cycle-end opposite resolution error: {_re}")

        # V3.1.88: Cycle-end summary for observability
        _opp_count = len(trade_opportunities) if trade_opportunities else 0
        logger.info(f"--- Cycle summary: {_cycle_signals} signals ({_cycle_above_80} at 80%+), {_cycle_wait} WAIT, {_cycle_blocked} blocked, {_opp_count} opportunities ---")
        # V3.1.93: Update signal summary for PM
        global _last_signal_summary
        _last_signal_summary = {
            "timestamp": time.time(),
            "signals_above_80": _cycle_above_80,
            "chop_blocked": _chop_blocked_pairs,
            "best_unexecuted": _best_unexecuted,
            "all_wait": _cycle_above_80 == 0 and chop_blocked_count == 0,
        }
        # V3.2.6: Persist signal_history at end of every cycle (even no-trade cycles)
        # so consecutive-cycle counts survive daemon restarts.
        tracker.save_state()
        save_local_log(ai_log, run_timestamp)
        
    except Exception as e:
        logger.error(f"Signal check error: {e}")
        logger.error(traceback.format_exc())
        state.errors += 1


# ============================================================
# V3.2.46: BREAKEVEN TRAILING STOP
# ============================================================
BREAKEVEN_TRIGGER_PCT = 0.4  # Move SL to entry when trade reaches +0.4%
# V3.2.54: Tiered peak-fade soft stop — per-tier params to match liquidity profile
# T1 (BTC/ETH): deep liquidity → tighter fade; floor exit 0.15% → 3.0% gross ROE (break-even at fees)
# T2 (BNB/LTC/XRP): high-beta altcoin → needs room; floor exit 0.20% → 4.0% gross - 3.2% fees = +0.8%
# T3 (SOL/ADA): most volatile → widest gate; same 0.45/0.25 as T2 (prevents whipsaw wicks)
# Gate: SL not yet moved to breakeven (WEEX BE-SL covers the >= 0.40% case).
# exit_reason "peak_fade" → COOLDOWN_MULTIPLIERS["profit_lock"] = 0.0 → zero cooldown.
PEAK_FADE_MIN_PEAK   = {1: 0.30, 2: 0.45, 3: 0.45}  # Min peak% per tier to activate fade
PEAK_FADE_TRIGGER_PCT = {1: 0.15, 2: 0.25, 3: 0.25}  # Drop threshold per tier (current <= peak - trigger)
# V3.2.67: Tiered velocity exit — matches actual hold times per tier.
# Charts show 0.5-1% dip-bounces happening over 60-120 min windows.
# 40 min was killing trades before the bounce could play out (4/7 velocity exits last session).
# T1 (BTC/ETH): 3h hold → 75 min velocity exit (25% of hold)
# T2 (BNB/LTC/XRP): 2h hold → 60 min velocity exit (50% of hold)
# T3 (SOL/ADA): 1.5h hold → 50 min velocity exit (56% of hold)
VELOCITY_EXIT_MINUTES = {1: 75, 2: 60, 3: 50}  # Per-tier velocity exit (was flat 40)
VELOCITY_MIN_PEAK_PCT = 0.10    # V3.2.67: Lowered from 0.15% → 0.10%. If peak < 0.10% in full velocity window, truly dead.

def _move_sl_to_breakeven(symbol: str, side: str, entry_price: float, tp_price: float, size: float) -> bool:
    """V3.2.46: Cancel existing SL and replace with breakeven SL at entry price.
    Preserves the original TP. Returns True on success."""
    try:
        real_symbol = symbol.split(":")[0] if ":" in symbol else symbol
        # Step 1: Cancel all existing plan orders (TP + SL triggers)
        cancel_all_orders_for_symbol(real_symbol)
        time.sleep(0.5)  # Let WEEX process cancellations

        # Step 2: Determine close type for plan orders
        close_type = "3" if side == "LONG" else "4"  # 3=close long, 4=close short

        # Step 3: Place new SL at breakeven (entry price)
        breakeven_price = entry_price  # Exact entry — fees already paid, just protect capital
        plan_order_endpoint = '/capi/v2/order/plan_order'

        sl_body = json.dumps({
            'symbol': real_symbol,
            'client_oid': f'smt_be_sl_{int(time.time()*1000)}',
            'size': str(size),
            'type': close_type,
            'match_type': '1',
            'execute_price': '0',
            'trigger_price': str(round_price_to_tick(breakeven_price, real_symbol))
        })
        sl_r = requests.post(f"{WEEX_BASE_URL}{plan_order_endpoint}",
                            headers=weex_headers('POST', plan_order_endpoint, sl_body),
                            data=sl_body, timeout=10)
        sl_ok = sl_r.status_code == 200 and sl_r.json().get("code") in ("200", "00000")

        # Step 4: Re-place TP at original level
        tp_ok = False
        if tp_price and tp_price > 0:
            tp_body = json.dumps({
                'symbol': real_symbol,
                'client_oid': f'smt_be_tp_{int(time.time()*1000)}',
                'size': str(size),
                'type': close_type,
                'match_type': '1',
                'execute_price': '0',
                'trigger_price': str(round_price_to_tick(tp_price, real_symbol))
            })
            tp_r = requests.post(f"{WEEX_BASE_URL}{plan_order_endpoint}",
                                headers=weex_headers('POST', plan_order_endpoint, tp_body),
                                data=tp_body, timeout=10)
            tp_ok = tp_r.status_code == 200 and tp_r.json().get("code") in ("200", "00000")

        symbol_clean = real_symbol.replace("cmt_", "").upper()
        logger.info(f"  [BREAKEVEN] {symbol_clean}: SL moved to ${breakeven_price:.4f} (entry) | SL={sl_ok} TP={tp_ok}")
        return sl_ok
    except Exception as e:
        logger.error(f"  [BREAKEVEN] Failed for {symbol}: {e}")
        return False


# ============================================================
# V3.1.9 TIER-BASED POSITION MONITORING
# ============================================================

def monitor_positions():
    """V3.1.1: Tier-based position monitoring with smart exits"""
    
    state.last_position_check = datetime.now(timezone.utc)
    
    try:
        active_symbols = tracker.get_active_symbols()
        
        if not active_symbols:
            return
        
        for symbol in active_symbols:
            trade = tracker.get_active_trade(symbol)
            if not trade:
                continue
            
            # V3.1.53: Extract real symbol (strip :SIDE suffix for API calls)
            real_symbol = symbol.split(":")[0] if ":" in symbol else symbol
            
            try:
                position = check_position_status(symbol)
                
                if not position.get("is_open"):
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(real_symbol)
                    
                    # V3.1.26: Calculate ACTUAL PnL from trade data
                    entry_price = trade.get("entry_price", 0)
                    side = trade.get("side", "LONG")
                    position_usdt = trade.get("position_usdt", 0)
                    actual_pnl = 0
                    pnl_pct = 0
                    
                    try:
                        current_price = get_price(symbol)
                        if entry_price > 0 and current_price > 0:
                            if side == "LONG":
                                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                            else:
                                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                            actual_pnl = position_usdt * (pnl_pct / 100)
                        if position_usdt > 0:
                            _rt_fee = position_usdt * 20 * TAKER_FEE_RATE * 2  # both sides
                            _net_pnl = actual_pnl - _rt_fee
                            logger.info(f"  Gross PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%) | Fees (R/T): ~${_rt_fee:.2f} | Net: ~${_net_pnl:.2f}")
                        else:
                            logger.info(f"  PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%)")
                    except Exception as e:
                        logger.debug(f"PnL calc error: {e}")
                    
                    # Log RL outcome with ACTUAL PnL
                    if RL_ENABLED and rl_collector:
                        try:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, "TP_SL")
                        except Exception as e:
                            logger.debug(f"RL outcome log error: {e}")
                    
                    # V3.2.60: Detect breakeven SL — if SL was moved to entry and gross PnL < round-trip fees,
                    # this was breakeven SL close, not a real TP. Fees ate the profit → net loss.
                    _is_breakeven_sl = False
                    if trade.get("sl_moved_to_breakeven", False) and actual_pnl > 0 and position_usdt > 0:
                        _notional = position_usdt * 20
                        _rt_fees = _notional * TAKER_FEE_RATE * 2
                        if actual_pnl < _rt_fees:
                            _is_breakeven_sl = True
                            logger.info(f"  [BREAKEVEN_SL] Gross ${actual_pnl:.2f} < fees ${_rt_fees:.2f} — classifying as BREAKEVEN_SL (not TP)")

                    _close_reason = "breakeven_sl" if _is_breakeven_sl else "tp_sl_hit"
                    tracker.close_trade(symbol, {
                        "reason": _close_reason,
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "pnl": actual_pnl,
                        "final_pnl_pct": pnl_pct,  # V3.1.82 FIX: was "pnl_pct" but close_trade reads "final_pnl_pct"
                    })
                    state.trades_closed += 1

                    # V3.1.59: Clean PnL history for closed position
                    if symbol in _pnl_history:
                        del _pnl_history[symbol]

                    # V3.1.36: AI log for TP/SL closes
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    # V3.2.60: Breakeven SL is not a real TP — classify correctly
                    if _is_breakeven_sl:
                        hit_tp = False
                        exit_type = "BREAKEVEN_SL"
                    elif actual_pnl > 0:
                        hit_tp = True
                        exit_type = "TAKE_PROFIT"
                    else:
                        hit_tp = False
                        exit_type = "STOP_LOSS"
                    try:
                        hours_open = 0
                        if trade.get("opened_at"):
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600

                        # V3.2.50: Use plan order ID stored at trade open — deterministic, no sleep needed.
                        # Falls back to get_recent_close_order_id() for legacy trades without stored IDs.
                        _stored_plan_id = trade.get("tp_plan_order_id") if hit_tp else trade.get("sl_plan_order_id")
                        if _stored_plan_id:
                            _tpsl_close_oid = int(_stored_plan_id) if str(_stored_plan_id).isdigit() else None
                            logger.info(f"  [AI-LOG] {exit_type} plan_order_id for {symbol_clean}: {_tpsl_close_oid} (stored at open)")
                        else:
                            # Fallback: legacy trade or plan ID fetch failed at open
                            time.sleep(1.5)
                            _tpsl_close_oid = get_recent_close_order_id(real_symbol)
                            logger.info(f"  [AI-LOG] {exit_type} close orderId for {symbol_clean}: {_tpsl_close_oid or 'not found (graceful)'}")

                        upload_ai_log_to_weex(
                            stage=f"{exit_type}: {side} {symbol_clean}",
                            input_data={
                                "symbol": symbol,
                                "side": side,
                                "entry_price": entry_price,
                                "position_usdt": position_usdt,
                                "hours_open": round(hours_open, 2),
                            },
                            output_data={
                                "action": "CLOSED",
                                "exit_type": exit_type,
                                "pnl_usd": round(actual_pnl, 2),
                                "pnl_pct": round(pnl_pct, 2),
                            },
                            explanation=f"Position closed via {exit_type}. {side} {symbol_clean} held {hours_open:.1f}h. PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%). Entry: ${entry_price:.4f}.",
                            order_id=_tpsl_close_oid,
                        )
                    except Exception as e:
                        logger.debug(f"AI log error for TP/SL close: {e}")
                    
                    continue
                
                # Get tier config for this position
                tier = trade.get("tier", get_tier_for_symbol(symbol))
                tier_config = get_tier_config(tier)
                
                # Calculate metrics
                opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                
                entry_price = trade.get("entry_price", position["entry_price"])
                current_price = get_price(real_symbol)
                
                if entry_price > 0 and current_price > 0:
                    if trade.get("side") == "LONG":
                        pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    else:
                        pnl_pct = ((entry_price - current_price) / entry_price) * 100
                else:
                    pnl_pct = 0
                
                pnl_usdt = position.get("unrealized_pnl", 0)
                
                # Get tier-specific exit parameters
                max_hold = tier_config["max_hold_hours"]
                early_exit_hours = tier_config["early_exit_hours"]
                early_exit_loss = tier_config["early_exit_loss_pct"]
                force_exit_loss = tier_config["force_exit_loss_pct"]
                
                
                # ===== V3.1.2: RUNNER LOGIC (check FIRST before exits) =====
                runner_config = get_runner_config(tier)
                runner_triggered = trade.get("runner_triggered", False)
                
                if runner_config.get("enabled") and not runner_triggered and pnl_pct > 0:
                    trigger_pct = runner_config.get("trigger_pct", 2.0)
                    
                    if pnl_pct >= trigger_pct:
                        symbol_clean = symbol.replace("cmt_", "").upper()
                        logger.info(f"{symbol_clean}: RUNNER TRIGGERED at +{pnl_pct:.2f}%!")
                        
                        runner_result = execute_runner_partial_close(
                            symbol=symbol,
                            side=position["side"],
                            current_size=position["size"],
                            entry_price=entry_price,
                            current_price=current_price
                        )
                        
                        if runner_result.get("executed"):
                            logger.info(f"  [RUNNER] Closed {runner_result['closed_size']} units, locked ${runner_result['profit_locked']:.2f}")
                            logger.info(f"  [RUNNER] Remaining {runner_result['remaining_size']} units running free")
                            
                            # Update trade state to mark runner as triggered
                            trade["runner_triggered"] = True
                            trade["runner_closed_size"] = runner_result["closed_size"]
                            trade["runner_profit_locked"] = runner_result["profit_locked"]
                            trade["new_sl_price"] = runner_result.get("new_sl_price")
                            tracker.save_state()
                            
                            # Update state counter
                            state.runners_triggered = getattr(state, 'runners_triggered', 0) + 1
                        else:
                            logger.warning(f"  [RUNNER] Failed: {runner_result.get('reason')}")
                        
                        continue  # Skip exit checks this cycle, let runner run
                
                # ===== TIER-BASED SMART EXIT LOGIC =====
                
                should_exit = False
                exit_reason = ""
                
                # Track peak PnL for trailing protection
                peak_pnl_pct = trade.get("peak_pnl_pct", 0)
                if pnl_pct > peak_pnl_pct:
                    trade["peak_pnl_pct"] = pnl_pct
                    peak_pnl_pct = pnl_pct
                    tracker.save_state()
                
                # V3.2.46: Breakeven trailing stop — move SL to entry when +0.4% reached
                if not trade.get("sl_moved_to_breakeven", False) and pnl_pct >= BREAKEVEN_TRIGGER_PCT:
                    _be_tp = trade.get("tp_price", 0)
                    _be_size = position.get("size", trade.get("size", 0))
                    _be_ok = _move_sl_to_breakeven(symbol, trade.get("side", "LONG"), entry_price, _be_tp, _be_size)
                    if _be_ok:
                        trade["sl_moved_to_breakeven"] = True
                        trade["sl_price"] = entry_price  # Update tracker
                        tracker.save_state()
                        logger.info(f"  [BREAKEVEN] {symbol}: SL moved to entry at +{pnl_pct:.2f}% (trigger: {BREAKEVEN_TRIGGER_PCT}%)")

                # V3.1.41: Get entry confidence for adaptive guards
                entry_confidence = trade.get("confidence", 0.75)
                confidence_multiplier = 1.3 if entry_confidence >= 0.85 else 1.0  # High-conf trades get 30% wider guards

                _be_tag = " [BE]" if trade.get("sl_moved_to_breakeven") else ""
                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%) conf={entry_confidence:.0%}{_be_tag}")
                
                # V3.1.59: Record PnL trajectory for Smart Monitor
                if symbol not in _pnl_history:
                    _pnl_history[symbol] = []
                _pnl_history[symbol].append({
                    "pnl_pct": round(pnl_pct, 3),
                    "peak": round(peak_pnl_pct, 3),
                    "ts": datetime.now(timezone.utc).strftime("%H:%M"),
                })
                if len(_pnl_history[symbol]) > _PNL_HISTORY_MAX:
                    _pnl_history[symbol] = _pnl_history[symbol][-_PNL_HISTORY_MAX:]
                # V3.1.78: PROFIT GUARD REMOVED - trust exchange TP/SL orders fully
                # Thesis-broken, TP-overrun, catastrophic-reversal rules were closing
                # positions on normal noise (ADA closed at -0.17%, BNB at -0.33%).
                # SL on exchange is the safety net. Let trades breathe.

                # V3.2.56: MACRO BLACKOUT EXIT — close unprofitable positions when blackout window activates.
                # News algos cause 1-2% instant spikes; a negative position is exposed to SL whipsaw.
                # Profitable positions (pnl_pct >= 0) are let ride — they have a buffer, SL protects.
                # Position monitor runs every 2min → out within 2min of window start (15min before data drop).
                _is_blacked_out, _blackout_label = _is_macro_blackout()
                if _is_blacked_out and pnl_pct < 0:
                    should_exit = True
                    exit_reason = f"macro_blackout_exit ({_blackout_label}, pnl={pnl_pct:.2f}%)"

                # 1. Max hold time exceeded (tier-specific)
                elif hours_open >= max_hold:
                    should_exit = True
                    exit_reason = f"max_hold_T{tier} ({hours_open:.1f}h > {max_hold}h)"
                
                # 2. Early exit if losing after tier-specific hours
                elif hours_open >= early_exit_hours and pnl_pct <= early_exit_loss:
                    should_exit = True
                    exit_reason = f"early_exit_T{tier} ({pnl_pct:.2f}% after {hours_open:.1f}h)"
                    state.early_exits += 1
                
                # 3. Force exit on large loss (universal -4%)
                elif pnl_pct <= force_exit_loss:
                    should_exit = True
                    exit_reason = f"force_stop_T{tier} ({pnl_pct:.2f}%)"
                    state.early_exits += 1

                # 4. V3.1.102 stale exit REMOVED (V3.2.17) — slot swap handles underperformers

                # 5. V3.2.54: Tiered peak-fade soft stop
                # Position had a meaningful peak but reversed before hitting TP. Exit to lock
                # in a fraction of the gain rather than riding it back to near-zero at max_hold.
                # Gate: SL not yet moved to breakeven (WEEX BE-SL covers the >= 0.40% case).
                # Tier-specific thresholds: T1 (BTC/ETH) tighter; T2/T3 (altcoins) wider for volatility.
                elif not trade.get("sl_moved_to_breakeven", False):
                    _pf_min  = PEAK_FADE_MIN_PEAK.get(tier, 0.45)
                    _pf_trig = PEAK_FADE_TRIGGER_PCT.get(tier, 0.25)
                    if peak_pnl_pct >= _pf_min and pnl_pct <= peak_pnl_pct - _pf_trig:
                        should_exit = True
                        exit_reason = f"peak_fade_T{tier} ({peak_pnl_pct:.2f}%→{pnl_pct:.2f}%, fade={peak_pnl_pct - pnl_pct:.2f}%, thr={_pf_min:.2f}/{_pf_trig:.2f})"
                    # V3.2.67: VELOCITY EXIT — tiered per-tier timing (was flat 40 min, too aggressive).
                    # Charts show dip-bounces over 60-120 min. Give trades time to catch the move.
                    elif peak_pnl_pct < VELOCITY_MIN_PEAK_PCT:
                        _age_min = hours_open * 60  # hours_open already computed above
                        _vel_limit = VELOCITY_EXIT_MINUTES.get(tier, 60)  # Per-tier velocity window
                        if _age_min >= _vel_limit:
                            should_exit = True
                            exit_reason = f"velocity_exit ({_age_min:.0f}m open, peak={peak_pnl_pct:.2f}% never reached {VELOCITY_MIN_PEAK_PCT:.2f}%, T{tier} limit={_vel_limit}m)"

                # V3.2.68: 8-EMA SNAP-BACK EXIT — mean-reversion exit for dip-bounce trades.
                # The natural dip-bounce exit is when price snaps back to the short-term mean.
                # 8-period EMA on 5m candles = 40 min of data = matches the dip-bounce timeframe.
                # Logic: if trade is profitable (peak >= 0.20%) AND price crosses back through the 8-EMA
                # in the direction of mean reversion → exit. This catches the natural bounce completion
                # instead of waiting for a fixed TP that may be too far.
                # Only fires when: (1) trade is >= 10 min old, (2) peak was meaningful (>= 0.20%),
                # (3) NOT already covered by breakeven SL (BE-SL handles the >= 0.40% case).
                # Zero cooldown (profit was taken).
                if not should_exit and not trade.get("sl_moved_to_breakeven", False):
                    _ema_age_min = hours_open * 60
                    if _ema_age_min >= 10 and peak_pnl_pct >= 0.20 and pnl_pct > 0:
                        try:
                            _ema_url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={real_symbol}&granularity=5m&limit=10"
                            _ema_r = requests.get(_ema_url, timeout=8)
                            _ema_candles = _ema_r.json()
                            if isinstance(_ema_candles, list) and len(_ema_candles) >= 9:
                                # 5m candles newest first. Compute 8-EMA from chronological order.
                                _ema_closes = [float(c[4]) for c in reversed(_ema_candles[:9])]  # 9 candles chron
                                _ema_mult = 2.0 / (8 + 1)  # EMA smoothing factor for period 8
                                _ema_val = _ema_closes[0]
                                for _ec in _ema_closes[1:]:
                                    _ema_val = (_ec - _ema_val) * _ema_mult + _ema_val
                                _live_price = float(_ema_candles[0][4])  # Most recent close
                                _side = trade.get("side", "LONG")
                                # LONG: entered below EMA (dip). Snap-back = price crosses ABOVE EMA.
                                # SHORT: entered above EMA (peak). Snap-back = price crosses BELOW EMA.
                                _snapped_back = False
                                if _side == "LONG" and _live_price >= _ema_val:
                                    # Confirm: previous candle was BELOW EMA (just crossed)
                                    _prev_close = float(_ema_candles[1][4])
                                    if _prev_close < _ema_val:
                                        _snapped_back = True
                                elif _side == "SHORT" and _live_price <= _ema_val:
                                    _prev_close = float(_ema_candles[1][4])
                                    if _prev_close > _ema_val:
                                        _snapped_back = True
                                if _snapped_back:
                                    should_exit = True
                                    exit_reason = f"ema_snapback ({_side}, price={_live_price:.4f} crossed 8-EMA={_ema_val:.4f}, pnl={pnl_pct:+.2f}%, peak={peak_pnl_pct:.2f}%)"
                                    logger.info(f"  [EMA-SNAP] {symbol}: {exit_reason}")
                        except Exception as _ema_err:
                            logger.debug(f"  [EMA-SNAP] {symbol}: check failed: {_ema_err}")

                if should_exit:
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    logger.warning(f"{symbol_clean}: Force close - {exit_reason}")
                    
                    close_result = close_position_manually(
                        symbol=symbol,
                        side=position["side"],
                        size=position["size"]
                    )
                    
                    # V3.1.25: Log RL outcome
                    if RL_ENABLED and rl_collector:
                        try:
                            exit_type = "TIMEOUT" if "max_hold" in exit_reason else \
                                       "EARLY_EXIT" if "early_exit" in exit_reason else \
                                       "FORCE_STOP"
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, exit_type)
                        except Exception as e:
                            logger.debug(f"RL outcome log error: {e}")
                    
                    tracker.close_trade(symbol, {
                        "reason": exit_reason,
                        "tier": tier,
                        "hours_open": hours_open,
                        "final_pnl_pct": pnl_pct,
                        "close_result": close_result,
                    })
                    
                    state.trades_closed += 1
                    
                    _smart_exit_oid = close_result.get("order_id") if close_result else None
                    logger.info(f"  [AI-LOG] Smart Exit close orderId: {_smart_exit_oid or 'not found'}")
                    upload_ai_log_to_weex(
                        stage=f"Smart Exit - {symbol_clean}",
                        input_data={"symbol": symbol, "tier": tier, "hours_open": hours_open},
                        output_data={"reason": exit_reason, "pnl_pct": pnl_pct},
                        explanation=f"Tier {tier} smart exit: {exit_reason}. PnL: {pnl_pct:+.2f}%",
                        order_id=int(_smart_exit_oid) if _smart_exit_oid and str(_smart_exit_oid).isdigit() else None,
                    )
                    
            except Exception as e:
                logger.error(f"Monitor error {symbol}: {e}")
        
    except Exception as e:
        logger.error(f"Position monitor error: {e}")
        state.errors += 1


# ============================================================
# QUICK CLEANUP + SIGNAL TRIGGER
# ============================================================


# ============================================================
# V3.1.40: GEMINI PORTFOLIO MANAGER
# ============================================================

_last_portfolio_review = 0
PORTFOLIO_REVIEW_INTERVAL = 900  # Every 5 minutes

# V3.1.59: PnL trajectory history for Smart Monitor
# Stores last 10 readings per symbol: [{"pnl_pct": x, "peak": y, "ts": z}, ...]
_pnl_history = {}
_PNL_HISTORY_MAX = 10

def get_trade_history_summary(tracker) -> str:
    """V3.1.63: Build summary of last 10 closed trades for Judge context."""
    closed = tracker.closed_trades[-10:] if tracker.closed_trades else []
    if not closed:
        return "No closed trade history available yet."
    
    lines = []
    wins = 0
    losses = 0
    for t in closed:
        sym = t.get("close_data", {}).get("symbol", "?") if t.get("close_data") else "?"
        if sym == "?":
            # Try to extract from the trade key or other fields
            sym = t.get("symbol", t.get("order_id", "?"))
        side = t.get("side", "?")
        pnl = t.get("close_data", {}).get("final_pnl_pct", 0) if t.get("close_data") else 0
        reason = t.get("close_data", {}).get("reason", "?") if t.get("close_data") else "?"
        conf = t.get("confidence", 0)
        whale_d = t.get("whale_direction", "?")
        
        if pnl > 0:
            wins += 1
            result = "WIN"
        elif pnl < 0:
            losses += 1
            result = "LOSS"
        else:
            result = "FLAT"
        
        lines.append(f"  {sym} {side} -> {result} ({pnl:+.1f}%) conf={conf:.0%} whale={whale_d} reason={reason}")
    
    win_rate = wins / max(wins + losses, 1) * 100
    summary = f"Last {len(closed)} trades: {wins}W/{losses}L ({win_rate:.0f}% win rate)\n"
    summary += "\n".join(lines[-5:])  # Show last 5 only to save tokens
    return summary


# V3.1.77: RL-based pair performance analysis for Judge and PM
_rl_performance_cache = {}
_rl_performance_cache_time = 0

def get_rl_pair_performance() -> dict:
    """V3.1.77: Read RL training data to compute per-pair win rates and performance.
    Returns dict like: {"BTCUSDT": {"wins": 1, "losses": 32, "win_rate": 3.0, "avg_pnl": -3.41}, ...}
    """
    global _rl_performance_cache, _rl_performance_cache_time
    import glob as _glob

    now = time.time()
    if now - _rl_performance_cache_time < 1800 and _rl_performance_cache:  # 30min cache
        return _rl_performance_cache

    pair_stats = {}
    try:
        rl_files = sorted(_glob.glob("rl_training_data/exp_*.jsonl"))
        # Only look at last 3 days of data for recency
        rl_files = rl_files[-3:] if len(rl_files) > 3 else rl_files

        for fp in rl_files:
            try:
                with open(fp, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                            if e.get('action') not in ('LONG', 'SHORT'):
                                continue
                            outcome = e.get('outcome')
                            if outcome is None:
                                continue
                            sym = e.get('symbol', '').replace('cmt_', '').upper()
                            if not sym:
                                continue
                            if sym not in pair_stats:
                                pair_stats[sym] = {"wins": 0, "losses": 0, "total_pnl": 0, "trades": 0, "avg_hours": 0}
                            pair_stats[sym]["trades"] += 1
                            if outcome.get("win", False):
                                pair_stats[sym]["wins"] += 1
                            else:
                                pair_stats[sym]["losses"] += 1
                            pair_stats[sym]["total_pnl"] += outcome.get("pnl", 0)
                            pair_stats[sym]["avg_hours"] += outcome.get("hours", 0)
                        except (json.JSONDecodeError, KeyError):
                            continue
            except IOError:
                continue

        for sym in pair_stats:
            n = pair_stats[sym]["trades"]
            if n > 0:
                pair_stats[sym]["win_rate"] = round(pair_stats[sym]["wins"] / n * 100, 1)
                pair_stats[sym]["avg_pnl"] = round(pair_stats[sym]["total_pnl"] / n, 2)
                pair_stats[sym]["avg_hours"] = round(pair_stats[sym]["avg_hours"] / n, 1)
            else:
                pair_stats[sym]["win_rate"] = 0
                pair_stats[sym]["avg_pnl"] = 0

        _rl_performance_cache = pair_stats
        _rl_performance_cache_time = now
    except Exception as e:
        logger.debug(f"RL performance read error: {e}")

    return pair_stats


def get_rl_performance_summary() -> str:
    """V3.1.77: Human-readable RL performance summary for Judge/PM prompts."""
    stats = get_rl_pair_performance()
    if not stats:
        return "No historical performance data available."

    lines = ["PAIR PERFORMANCE (last 3 days of RL data):"]
    for sym in sorted(stats.keys()):
        s = stats[sym]
        if s["trades"] == 0:
            continue
        emoji = "PROFITABLE" if s["avg_pnl"] > 0 else "LOSING"
        lines.append(f"  {sym}: {s['wins']}W/{s['losses']}L ({s['win_rate']}% WR), avg PnL: {s['avg_pnl']:+.2f}%, avg hold: {s['avg_hours']:.1f}h [{emoji}]")

    total_trades = sum(s["trades"] for s in stats.values())
    total_wins = sum(s["wins"] for s in stats.values())
    overall_wr = round(total_wins / max(total_trades, 1) * 100, 1)
    lines.append(f"  OVERALL: {total_wins}/{total_trades} ({overall_wr}% WR)")
    return "\n".join(lines)


def get_pair_sizing_multiplier(symbol: str) -> float:
    """V3.1.77: Reduce position sizing for consistently losing pairs.
    BTC with 3% win rate should not get full-size positions.
    """
    stats = get_rl_pair_performance()
    sym = symbol.replace('cmt_', '').upper()
    if sym not in stats or stats[sym]["trades"] < 5:
        return 1.0  # Not enough data, use default

    wr = stats[sym]["win_rate"]
    if wr >= 15:
        return 1.0   # Performing OK
    elif wr >= 10:
        return 0.7   # Below average
    elif wr >= 5:
        return 0.5   # Poor performer
    else:
        return 0.3   # Serial loser (like BTC at 3%)


# V3.1.60: Track symbols with recent SL tightens - prevent resolve_opposite from killing new trades
_sl_tightened_symbols = {}

# V3.1.100: Deferred opposite flip queue
# When a flip is blocked due to TP proximity, store the signal here.
# Format: {symbol: {"signal": "SHORT", "confidence": 0.85, "queued_at": datetime, "pair_info": {...}, "decision": {...}}}
_deferred_opposite_queue = {}


def _check_opposite_swap_gates(symbol, existing_side, new_signal, new_confidence, opportunity):
    """V3.1.100: Gate opposite swaps based on TP proximity and position age.
    V3.2.51: Age gate (Gate A) bypassed when new_confidence >= EMERGENCY_FLIP_CONFIDENCE.
    A 90%+ Judge signal has already passed the HIGH NOISE RISK filter in the prompt
    (WHALE+FLOW both >75% required to flip), so it is genuine conviction, not noise.
    TP proximity gate (Gate B) is preserved regardless of confidence — don't disturb
    a position that is >= 30% toward its TP even for a 90%+ opposite signal.

    Returns: (blocked: bool, reason: str)
    """
    global _deferred_opposite_queue

    existing_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:{existing_side}")
    if not existing_trade:
        return False, ""  # No tracker data, allow swap

    is_emergency = new_confidence >= EMERGENCY_FLIP_CONFIDENCE

    # Gate A: Minimum age — don't flip positions younger than 20 minutes.
    # V3.2.51: Bypassed for emergency flips (>= 90% confidence) — wrong entry,
    # get out now rather than waiting for the age gate to expire with losses mounting.
    opened_at = existing_trade.get("opened_at")
    if opened_at:
        try:
            age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 60
            if age_min < OPPOSITE_MIN_AGE_MIN:
                if is_emergency:
                    logger.info(f"  [EMERGENCY FLIP] {existing_side} only {age_min:.0f}m old — age gate bypassed at {new_confidence:.0%} (>= {EMERGENCY_FLIP_CONFIDENCE:.0%} threshold)")
                else:
                    return True, f"{existing_side} only {age_min:.0f}m old (need {OPPOSITE_MIN_AGE_MIN}m)"
        except Exception:
            pass  # If timestamp parsing fails, skip age gate

    # Gate B: TP proximity — don't flip if position is >= 30% toward TP
    entry_price = existing_trade.get("entry_price", 0)
    tp_price = existing_trade.get("tp_price", 0)
    if entry_price > 0 and tp_price > 0:
        try:
            current_price = get_price(symbol)
            if current_price and current_price > 0:
                if existing_side == "LONG":
                    if tp_price > entry_price:
                        progress = ((current_price - entry_price) / (tp_price - entry_price)) * 100
                    else:
                        progress = 0
                else:  # SHORT
                    if entry_price > tp_price:
                        progress = ((entry_price - current_price) / (entry_price - tp_price)) * 100
                    else:
                        progress = 0

                if progress >= OPPOSITE_TP_PROGRESS_BLOCK:
                    # Queue the deferred flip
                    _deferred_opposite_queue[symbol] = {
                        "signal": new_signal,
                        "confidence": new_confidence,
                        "queued_at": datetime.now(timezone.utc),
                        "pair_info": opportunity.get("pair_info", {}),
                        "decision": opportunity.get("decision", {}),
                        "blocked_side": existing_side,
                        "tp_progress": progress,
                    }
                    return True, f"{existing_side} is {progress:.0f}% toward TP (threshold: {OPPOSITE_TP_PROGRESS_BLOCK}%), queued {new_signal} for deferred execution"
        except Exception as e:
            logger.debug(f"  [OPPOSITE GATE] Price check error for {symbol}: {e}")

    return False, ""  # All gates passed, allow swap


def _execute_deferred_flips(position_map, balance):
    """V3.1.100: Execute queued opposite flips after old position closes.

    Called at the start of each signal check cycle. If the blocked position
    has closed (via TP/SL/monitor), executes the queued opposite signal.
    """
    global _deferred_opposite_queue, _last_trade_opened_at

    if not _deferred_opposite_queue:
        return

    expired = []
    executed = []

    for symbol, queued in list(_deferred_opposite_queue.items()):
        age_min = (datetime.now(timezone.utc) - queued["queued_at"]).total_seconds() / 60
        pair_label = symbol.replace("cmt_", "").upper()

        # Expired?
        if age_min > DEFERRED_FLIP_MAX_AGE_MIN:
            logger.info(f"  [DEFERRED] {pair_label} {queued['signal']} expired after {age_min:.0f}m")
            expired.append(symbol)
            continue

        # Old position still open?
        blocked_side = queued["blocked_side"]
        sym_positions = position_map.get(symbol, {})
        if blocked_side in sym_positions:
            # Still open, keep waiting
            logger.debug(f"  [DEFERRED] {pair_label}: {blocked_side} still open, waiting ({age_min:.0f}m queued)")
            continue

        # Old position closed! Execute the deferred flip
        logger.info(f"  [DEFERRED EXECUTE] {pair_label}: {blocked_side} closed, executing queued {queued['signal']} ({queued['confidence']:.0%}, queued {age_min:.0f}m ago, was {queued.get('tp_progress', 0):.0f}% toward TP)")

        try:
            trade_result = run_with_retry(
                execute_trade,
                queued["pair_info"], queued["decision"], balance
            )

            if trade_result and trade_result.get("executed"):
                logger.info(f"  [DEFERRED] Executed: {trade_result.get('order_id')}")
                logger.info(f"  [DEFERRED] TP: {trade_result.get('tp_pct', 0):.1f}%, SL: {trade_result.get('sl_pct', 0):.1f}%")

                # Store confidence + whale data like normal trades
                trade_result["confidence"] = queued["confidence"]
                whale_conf = 0.0
                whale_dir = "NEUTRAL"
                for pv in queued.get("decision", {}).get("persona_votes", []):
                    if pv.get("persona") == "WHALE":
                        whale_conf = pv.get("confidence", 0.0)
                        whale_dir = pv.get("signal", "NEUTRAL")
                        break
                trade_result["whale_confidence"] = whale_conf
                trade_result["whale_direction"] = whale_dir

                tracker.add_trade(symbol, trade_result)
                _last_trade_opened_at = time.time()

                upload_ai_log_to_weex(
                    stage=f"Deferred Flip: {pair_label} {queued['signal']}",
                    input_data={"symbol": symbol, "signal": queued["signal"], "confidence": queued["confidence"], "queued_min_ago": round(age_min, 1), "blocked_side": blocked_side},
                    output_data={"action": "DEFERRED_EXECUTED", "order_id": trade_result.get("order_id")},
                    explanation=f"AI executed deferred {queued['signal']} on {pair_label}. Original flip was blocked {age_min:.0f}m ago because {blocked_side} was {queued.get('tp_progress', 0):.0f}% toward TP. {blocked_side} has now closed, entering {queued['signal']}."[:1000]
                )
            else:
                reason = trade_result.get("reason", "unknown") if trade_result else "no result"
                logger.info(f"  [DEFERRED] Trade not executed: {reason}")
        except Exception as e:
            logger.error(f"  [DEFERRED] Error executing {pair_label}: {e}")
            import traceback as tb
            logger.error(tb.format_exc())

        executed.append(symbol)

    # Cleanup expired + executed entries
    for sym in expired + executed:
        _deferred_opposite_queue.pop(sym, None)


def cleanup_dust_positions():
    """V3.1.53: Close dust positions (<$5 margin) that waste slots"""
    try:
        positions = get_open_positions()
        for pos in positions:
            margin = float(pos.get("margin", 0))
            size = float(pos.get("size", 0))
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            
            if margin < 5.0 and size > 0:
                symbol_clean = symbol.replace("cmt_", "").upper()
                logger.info(f"  [DUST] Closing {side} {symbol_clean}: margin=${margin:.2f}, size={size}")
                
                close_type = "3" if side == "LONG" else "4"
                from smt_nightly_trade_v3_1 import place_order, round_size_to_step
                close_size = round_size_to_step(size, symbol)
                
                if close_size > 0:
                    result = place_order(symbol, close_type, close_size, tp_price=None, sl_price=None)
                    oid = result.get("order_id")
                    
                    upload_ai_log_to_weex(
                        stage=f"Position Optimization: Close dust {side} {symbol_clean}",
                        input_data={"symbol": symbol, "side": side, "margin": margin, "size": size},
                        output_data={"action": "CLOSE_DUST", "order_id": oid},
                        explanation=f"AI closing negligible {side} {symbol_clean} position (margin: ${margin:.2f}). Position too small to generate meaningful returns. Freeing slot for higher-conviction trades.",
                        order_id=oid
                    )
                    logger.info(f"  [DUST] Closed: order {oid}")
    except Exception as e:
        logger.error(f"  [DUST] Cleanup error: {e}")


def gemini_portfolio_review():
    """V3.1.40: Gemini reviews ALL positions and decides what to close.
    
    Replaces hardcoded exit logic with LLM that sees:
    - All positions + PnL + hold time
    - Market regime, F&G, funding
    - Competition status
    - Frees slots for better opportunities
    """
    global _last_portfolio_review
    
    now = time.time()
    if now - _last_portfolio_review < PORTFOLIO_REVIEW_INTERVAL:
        return
    _last_portfolio_review = now
    
    try:
        positions = get_open_positions()
        if not positions:
            return
        
        # V3.1.74: GRACE PERIOD - F&G-aware (90min in extreme fear, 30min normal)
        # Gemini ignores prompt-based grace periods, so we enforce in code
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _pm_fg = get_fear_greed_index().get("value", 50)
        except:
            _pm_fg = 50
        grace_limit = 90 if _pm_fg < 20 else 30  # V3.1.74: 90min in extreme fear (was always 30min)
        grace_positions = []
        for p in positions:
            sym = p.get('symbol', '')
            # V3.1.81: Don't hide recently force-stopped symbols from PM
            # V3.1.95: BUT if position opened AFTER the last loss, it's a legitimate
            # new entry (blacklist expired, all filters passed) — give normal grace
            if tracker.was_recently_force_stopped(sym, within_hours=4):
                _last_fs = tracker.last_force_stop_time(sym)
                _trade = tracker.get_active_trade(sym)
                _opened_at = _trade.get('opened_at', '') if _trade else ''
                if _last_fs and _opened_at and _opened_at > _last_fs:
                    # V3.1.95: Position opened after loss — legitimate entry, normal grace
                    logger.info(f'[PORTFOLIO] GRACE OK: {sym.replace("cmt_","").upper()} opened after last loss, normal grace period')
                else:
                    # Original V3.1.81: zombie re-entry or no timing data — PM must review
                    logger.info(f'[PORTFOLIO] NO GRACE: {sym.replace("cmt_","").upper()} was recently force-stopped, PM must review')
                    grace_positions.append(p)
                    continue
            trade = tracker.get_active_trade(sym)
            if trade and trade.get('opened_at'):
                try:
                    from datetime import datetime, timezone
                    opened_at = datetime.fromisoformat(trade['opened_at'].replace('Z', '+00:00'))
                    minutes_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
                    if minutes_open < grace_limit:
                        logger.info(f'[PORTFOLIO] Grace period: {sym.replace("cmt_","").upper()} opened {minutes_open:.0f}m ago (limit {grace_limit}m, F&G={_pm_fg}), hiding from PM')
                        continue
                except:
                    pass
            grace_positions.append(p)
        positions = grace_positions
        if not positions:
            logger.info('[PORTFOLIO] All positions in grace period, skipping review')
            return
        
        account_info = get_account_equity()
        balance = account_info["available"]
        equity = account_info["equity"]
        
        from smt_nightly_trade_v3_1 import get_enhanced_market_regime
        regime = get_enhanced_market_regime()
        
        # Build position details
        pos_details = []
        for p in positions:
            sym = p.get("symbol", "?").replace("cmt_", "").upper()
            side = p.get("side", "?")
            entry = float(p.get("entry_price", 0))
            pnl = float(p.get("unrealized_pnl", 0))
            size = float(p.get("size", 0))
            margin = float(p.get("margin", 0))
            
            # Get hold time from tracker
            hours_open = 0
            trade = tracker.get_active_trade(p.get("symbol", ""))
            if trade and trade.get("opened_at"):
                try:
                    opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                    hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                except:
                    pass
            
            tier = get_tier_for_symbol(p.get("symbol", ""))
            pnl_pct = 0
            if entry > 0 and size > 0:
                if side == "LONG":
                    # FIX: Calculate Price Change % based on Notional (Entry * Size), not Margin
                    notional = entry * size
                    if notional > 0:
                        pnl_pct = (pnl / notional) * 100
                    else:
                        pnl_pct = 0
                else:
                    # V3.1.43 FIX: SHORT also uses notional, not margin (margin = ROE, inflated by leverage)
                    notional = entry * size
                    if notional > 0:
                        pnl_pct = (pnl / notional) * 100
                    else:
                        pnl_pct = (pnl / (margin if margin > 0 else 1)) * 100
            
            # V3.1.81: Flag recently force-stopped symbols so PM knows the history
            # V3.1.95: Advisory only — don't force close legitimate new entries
            _fs_flag = ""
            if tracker.was_recently_force_stopped(p.get("symbol", ""), within_hours=4):
                _fs_count = tracker.consecutive_losses(p.get("symbol", ""), side, hours=24)
                _fs_trade = tracker.get_active_trade(p.get("symbol", ""))
                _fs_opened = _fs_trade.get('opened_at', '') if _fs_trade else ''
                _fs_last = tracker.last_force_stop_time(p.get("symbol", ""))
                if _fs_last and _fs_opened and _fs_opened > _fs_last:
                    # V3.1.95: Position opened after loss — new entry, advisory only
                    _fs_flag = f" [HAD {_fs_count} LOSS(ES) in 24h — NEW ENTRY, EVALUATE ON MERITS]"
                else:
                    # Original: position predates the loss or no timing data — close recommended
                    _fs_flag = f" [FORCE_STOPPED x{_fs_count} in 24h - CLOSE THIS]"

            pos_details.append(
                f"- {sym} {side}: PnL=${pnl:.2f} ({pnl_pct:+.1f}%), entry=${entry:.4f}, margin=${margin:.1f}, held={hours_open:.1f}h, tier={tier}{_fs_flag}"
            )
        
        positions_text = "\n".join(pos_details)
        
        # Competition status
        now_utc = datetime.now(timezone.utc)
        days_left = (COMPETITION_END - now_utc).days
        total_pnl = equity - STARTING_BALANCE
        
        # V3.1.41: Build peak data for portfolio manager
        peak_data = []
        for p in positions:
            sym = p.get("symbol", "")
            trade = tracker.get_active_trade(sym)
            peak = trade.get("peak_pnl_pct", 0) if trade else 0
            whale_c = trade.get("whale_confidence", 0) if trade else 0
            whale_d = trade.get("whale_direction", "?") if trade else "?"
            # V3.1.59: Include PnL trajectory
            trajectory = _pnl_history.get(p.get("symbol", ""), [])
            traj_str = " -> ".join([f"{r['pnl_pct']:+.2f}%" for r in trajectory[-5:]]) if trajectory else "no history"
            peak_data.append(f"  peak={peak:+.1f}%, whale={whale_d}@{whale_c:.0%}, traj=[{traj_str}]")
        
        # Enhance positions_text with peak data
        enhanced_pos = []
        for i, detail in enumerate(pos_details):
            pk = peak_data[i] if i < len(peak_data) else ""
            enhanced_pos.append(f"{detail},{pk}")
        positions_text_enhanced = chr(10).join(enhanced_pos)
        
        # Count directional concentration
        long_count = sum(1 for p in positions if p.get("side","").upper() == "LONG")
        short_count = sum(1 for p in positions if p.get("side","").upper() == "SHORT")
        long_margin = sum(float(p.get("margin",0)) for p in positions if p.get("side","").upper() == "LONG")
        short_margin = sum(float(p.get("margin",0)) for p in positions if p.get("side","").upper() == "SHORT")
        
        # V3.1.59: Fetch Cryptoracle historical context for PM
        cryptoracle_context = ""
        try:
            from cryptoracle_client import fetch_sentiment, fetch_prediction_market
            cr_data = fetch_sentiment(["BTC", "ETH"], hours_back=4, time_type="1h")
            pm_data = fetch_prediction_market(minutes_back=10)
            
            if cr_data:
                cr_parts = []
                for tok, vals in cr_data.items():
                    net = vals.get("net_sentiment", 0.5)
                    mom = vals.get("sentiment_momentum", 0)
                    gap = vals.get("sentiment_price_gap", 0)
                    trend = vals.get("trend_1h", "?")
                    cr_parts.append(f"{tok}: sent={net:.2f}, mom={mom:.2f}, gap={gap:.2f}, trend={trend}")
                cryptoracle_context += "Sentiment (1h): " + " | ".join(cr_parts)
            
            if pm_data:
                pm_val = pm_data.get("pm_sentiment", 0)
                pm_sig = pm_data.get("pm_signal", "?")
                pm_str = pm_data.get("pm_strength", "?")
                cryptoracle_context += f"\nPrediction Market (BTC): {pm_val:+.4f} ({pm_sig} {pm_str})"
            
            if not cryptoracle_context:
                cryptoracle_context = "No data available"
        except Exception as e:
            cryptoracle_context = f"Unavailable: {e}"

        # V3.1.77: RL performance for PM
        try:
            rl_performance_text = get_rl_performance_summary()
        except Exception:
            rl_performance_text = "Historical performance data unavailable."

        # V3.1.59: AI log for PM review start
        upload_ai_log_to_weex(
            stage=f"Portfolio Review Start",
            input_data={
                "positions": len(positions),
                "equity": round(equity, 2),
                "balance": round(balance, 2),
                "regime": regime.get("regime", "NEUTRAL"),
                "fear_greed": regime.get("fear_greed", 50),
                "cryptoracle": cryptoracle_context[:200],
            },
            output_data={
                "action": "PM_REVIEW",
                "long_count": long_count,
                "short_count": short_count,
            },
            explanation=f"Portfolio Manager reviewing {len(positions)} positions. Equity: ${equity:.0f}. {cryptoracle_context[:400]}"
        )
        
        # V3.1.88: Compute slot info locally for PM prompt (was referencing check_trading_signals locals)
        weex_position_count = len(positions)
        effective_max_positions = get_max_positions_for_equity(equity)

        # V3.1.93: Build signal landscape context for PM
        signal_landscape = ""
        _sig_age = time.time() - _last_signal_summary.get("timestamp", 0)
        if _last_signal_summary and _sig_age < 900:  # Only use if < 15 min old
            sig = _last_signal_summary
            if sig.get("all_wait"):
                _chop_str = ""
                if sig.get("chop_blocked"):
                    _chop_names = [f"{c['pair']} {c['direction']} (was {c['pre_chop_conf']:.0%} pre-chop)" for c in sig["chop_blocked"]]
                    _chop_str = f"\nChop-blocked: {', '.join(_chop_names)}"
                signal_landscape = f"""=== SIGNAL LANDSCAPE (last cycle) ===
NO TRADEABLE SIGNALS: All 8 pairs returned WAIT or were blocked.{_chop_str}
IMPLICATION: If you close a position now, the freed slot will likely sit empty.
Factor this into Rule 9 decisions — an occupied slot (even at -0.5%) may be better than an empty one.
"""
            elif sig.get("signals_above_80", 0) > 0:
                _best = sig.get("best_unexecuted")
                _best_str = f"Best waiting: {_best['pair']} {_best['direction']} at {_best['confidence']:.0%}" if _best else "All 80%+ signals were executed"
                signal_landscape = f"""=== SIGNAL LANDSCAPE (last cycle) ===
Tradeable signals: {sig['signals_above_80']} pair(s) at 80%+ confidence
{_best_str}
IMPLICATION: Replacement candidates exist. Freeing a slot for a stronger signal is reasonable.
"""
            else:
                signal_landscape = """=== SIGNAL LANDSCAPE (last cycle) ===
Some signals found but none reached 80% trading threshold.
IMPLICATION: Freed slots may sit empty for 1-2 cycles. Be conservative with Rule 9 closures.
"""

        prompt = f"""You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
Your job is DISCIPLINED portfolio management. Apply the rules strictly and without emotion.
- Let WINNERS run to their TP targets. Do NOT close winning positions early.
- Cut LOSERS that are fighting the trend or held past their max time.
- Be PATIENT with positions that are near breakeven - they need time to develop.

=== PORTFOLIO (ALL OPEN POSITIONS) ===
{positions_text_enhanced}

Directional exposure: {long_count} LONGs (${long_margin:.0f} margin) | {short_count} SHORTs (${short_margin:.0f} margin)
Total positions: {len(positions)}
Available balance: ${balance:.0f}
Equity: ${equity:.0f} (started: ${STARTING_BALANCE:.0f}, PnL: ${total_pnl:.0f})

=== MARKET ===
Regime: {regime.get('regime', 'NEUTRAL')}
BTC 24h: {regime.get('change_24h', 0):+.1f}%
BTC 4h: {regime.get('change_4h', 0):+.1f}%
Fear & Greed: {regime.get('fear_greed', 50)}
Funding rate: {regime.get('avg_funding', 0):.6f}
Days left in competition: {days_left}

=== CRYPTORACLE INTELLIGENCE ===
{cryptoracle_context}

=== HISTORICAL PAIR PERFORMANCE (RL data, last 3 days) ===
{rl_performance_text}
Use this to judge which positions to cut first. Pairs with <10% win rate are chronic losers - cut them faster.

=== PNL TRAJECTORY PATTERNS ===
Each position shows its last 5 PnL readings (newest last).
Look for: fading from peak (consider tightening), accelerating (let run), flat (stale trade).
If Cryptoracle sentiment supports the position direction, be more patient even if fading.

{signal_landscape}
=== MANDATORY RULES (V3.1.59 - 47+ iterations of battle-tested experience) ===

RULE 1 - OPPOSITE SIDE RESOLUTION (HIGHEST PRIORITY):
If the SAME symbol has BOTH a LONG and SHORT position open, IMMEDIATELY close the losing side.
This is NOT optional. Two-sided positions on the same pair waste margin, cancel each other out,
and occupy 2 slots instead of 1. Close the side with worse PnL. No exceptions.

RULE 2 - DIRECTIONAL CONCENTRATION LIMIT:
Max 5 positions in the same direction normally. If 6+ LONGs or 6+ SHORTs, close the WEAKEST ones
(lowest PnL% or highest loss) until we have max 5.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 7 LONGs. Violent bounces move all alts together.
NOTE: Extreme F&G values are already factored into signal generation. Evaluate positions on their own merits.

RULE 3 - LET WINNERS REACH TP:
DO NOT close winning positions early. WEEX has TP orders at 2.5-3.5%. Let them trigger.
At 20x leverage: TP hit at 2.5% = $690 profit. Closing at 0.75% = $207. That's 3x less.
A trade at +1.5% pulling back to +0.7% is 60% of the way to TP — that's a NORMAL pullback, not a close signal.
Only close a winner if: (a) it peaked green then went NEGATIVE (thesis broken), or (b) it peaked PAST TP but exchange order didn't fill (TP overrun).

RULE 3b - WINNERS ARE UNTOUCHABLE (V3.1.91):
Code will block any attempt to close a profitable position. Do not suggest closing winners.
Focus your analysis on LOSING positions only. Winners ride to TP or SL — that's the system.
Closing a winner at +1.24% instead of letting 3.5% TP hit costs us $500+ per trade.

RULE 4 - F&G EXTREME FEAR (UPDATED V3.1.55):
If F&G < 20 (extreme fear), be patient with positions BUT you CAN still close if:
(a) Same pair has both LONG and SHORT open (Rule 1 overrides)
(b) Position losing > -2% AND whale confidence >= 70% AGAINST position direction
(c) Position held longer than max_hold_hours AND losing
(d) 4+ total positions clogging slots (close weakest loser to free capital)
(e) V3.1.63: If any position is losing > -2% AND held > 4 hours, close it. At 20x that's -40% ROE. Cut the bleed.
Extreme fear does NOT mean blindly hold everything. Our SL orders are the last defense,
but the PM should still trim obvious bad positions.

RULE 5 - WHALE DISAGREE EXIT (UPDATED V3.1.56):
If whale confidence >= 70% in the OPPOSITE direction AND position is losing MORE THAN -1.0%,
CLOSE IT. Smart money has turned against this trade.
CRITICAL: Do NOT close positions losing less than -1.0% on whale disagreement alone.
Small losses (-0.01% to -0.9%) are normal noise. Only act on whale signal when loss is meaningful.
Examples:
- LONG position losing -1.5%, whale says SHORT@73% -> CLOSE (loss > 1%)
- SHORT position losing -0.3%, whale says LONG@78% -> KEEP (loss < 1%, too early)
- LONG position losing -0.01%, whale says SHORT@72% -> KEEP (basically breakeven)
Exception: if position is WINNING despite whale disagreement, ALWAYS keep it.

RULE 6 - BREAKEVEN PATIENCE (UPDATED V3.1.82 - COMPETITION MODE):
If a position has faded to breakeven (within +/- 0.3%), normally DO NOT CLOSE.
EXCEPTION (V3.1.82 COMPETITION): If ALL slots are full ({weex_position_count}/{effective_max_positions}) AND the position
has been declining from its peak for 3+ readings AND there are higher-conviction signals waiting,
CLOSE the weakest breakeven position to free a slot. In competition mode, a dying +0.1% position
occupying a slot is worse than freeing it for a 75%+ confidence entry.
IMPORTANT: This override ONLY applies when slots are completely full. If slots are available, keep patience.

RULE 7 - TIME-BASED PATIENCE:
Do NOT close positions just because they have been open 2-4 hours.
Our TP targets are 2.5-3.5% (50-70% ROE at 20x). These moves take 1-4h typically.
Only close if: max_hold_hours exceeded AND position is negative.

RULE 8 - WEEKEND/LOW LIQUIDITY (check if Saturday or Sunday):
On weekends, max 4 positions. Thinner books = more manipulation.

RULE 9 - SLOT MANAGEMENT (UPDATED V3.1.82 - COMPETITION):
If ALL {effective_max_positions} slots are full AND any position is at breakeven (<+0.3%) or declining
from its peak, consider closing the WEAKEST to free capital for better entries.
In competition mode with only {effective_max_positions} slots, every slot matters.
Close the position with: worst PnL% + whale disagreement + longest hold time past max.
If slots are available, be patient - SL orders protect us.

RULE 10 - GRACE PERIOD:
Positions opened < 30 minutes ago with confidence >= 85% get a GRACE PERIOD.
Do NOT close them unless losing more than -1.5%. Give the trade time to work.
If F&G < 20, grace period extends to 90 minutes.

RULE 11 - WHALE HOLD PROTECTION:
If whale confidence >= 70% in the SAME direction as the position,
do NOT close it. Smart money agrees. Let it run to full TP.
Only exception: max_hold_hours exceeded AND losing more than -3%.

RULE 12 - FUNDING COST AWARENESS:
If funding rate is positive and we are LONG, we PAY every 8h. If position is barely
profitable (+0.1-0.3%) and funding eats the profit, close it. Same for negative funding + SHORT.

RULE 13 - STALE POSITION CLEANUP (USE THESE EXACT THRESHOLDS):
TIER HOLD LIMITS (from TIER_CONFIG - do NOT guess these values):
  Tier 1 (BTC,ETH,BNB): max_hold = 24h, early_exit = 6h if losing > 1%
  Tier 2 (LTC,SOL,XRP): max_hold = 12h, early_exit = 4h if losing > 1%
  Tier 3 (DOGE,ADA):    max_hold = 8h,  early_exit = 3h if losing > 1%
If held > max_hold_hours AND losing ANY amount: close it. Dead capital.
If held > early_exit_hours AND losing > 1%: close it. Thesis is failing.
If winning past max_hold: let it run but tighten expectations.
CRITICAL: A Tier 1 position held 5h is NOT stale (max is 24h). Do NOT close it.

Respond with JSON ONLY (no markdown, no backticks):
{{"closes": [{{"symbol": "DOGEUSDT", "side": "SHORT", "reason": "Rule X: brief reason"}}, ...], "keep_reasons": "brief summary of why others are kept, referencing rule numbers"}}

If nothing should be closed, return:
{{"closes": [], "keep_reasons": "brief summary referencing which rules were checked"}}"""

        _rate_limit_gemini()
        
        from google.genai.types import GenerateContentConfig
        
        config = GenerateContentConfig(temperature=0.1)
        
        response = _gemini_full_call_daemon("gemini-2.5-flash", prompt, config, timeout=90)
        
        clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        
        closes = data.get("closes", [])
        keep_reasons = data.get("keep_reasons", "")
        
        if not closes:
            logger.info(f"[PORTFOLIO] Gemini: Keep all. {keep_reasons[:400]}")
            return
        
        logger.info(f"[PORTFOLIO] Gemini wants to close {len(closes)} position(s)")
        
        for close_req in closes:
            close_symbol_raw = close_req.get("symbol", "").upper().replace("USDT", "")
            close_side = close_req.get("side", "").upper()
            close_reason = close_req.get("reason", "AI decision")
            
            # Find matching position
            for p in positions:
                sym = p.get("symbol", "")
                sym_clean = sym.replace("cmt_", "").replace("usdt", "").upper()
                p_side = p.get("side", "").upper()
                
                if sym_clean == close_symbol_raw and p_side == close_side:
                    size = float(p.get("size", 0))
                    pnl = float(p.get("unrealized_pnl", 0))
                    entry = float(p.get("entry_price", 0))

                    if size <= 0:
                        continue

                    # V3.1.91: PROTECT WINNERS — never close profitable positions
                    # TP/SL triggers on WEEX handle profit-taking. Gemini closing winners
                    # at +1.24% instead of letting 3.5% TP hit cost us $500+ per trade.
                    if pnl > 0:
                        logger.info(f"[PORTFOLIO] PROTECTED: {sym_clean} {close_side} is profitable (+${pnl:.2f}). Let TP handle it.")
                        break

                    logger.warning(f"[PORTFOLIO] Closing {sym_clean} {close_side}: {close_reason}")
                    
                    close_result = close_position_manually(sym, close_side, size)
                    order_id = close_result.get("order_id")
                    logger.info(f"[AI-LOG] Portfolio Manager close orderId for {sym_clean}: {order_id or 'not found'}")

                    if order_id:
                        logger.info(f"[PORTFOLIO] Closed {sym_clean} {close_side}: order {order_id}")
                        
                        # Upload AI log
                        upload_ai_log_to_weex(
                            stage=f"Portfolio Manager: Close {close_side} {sym_clean}",
                            input_data={
                                "symbol": sym,
                                "side": close_side,
                                "size": size,
                                "entry_price": entry,
                                "unrealized_pnl": pnl,
                                "regime": regime.get("regime", "NEUTRAL"),
                                "fear_greed": regime.get("fear_greed", 50),
                                "total_positions": len(positions),
                                "equity": equity,
                            },
                            output_data={
                                "action": "PORTFOLIO_CLOSE",
                                "order_id": order_id,
                                "reason": close_reason,
                                "ai_model": "gemini-2.5-flash",
                            },
                            explanation=f"AI Portfolio Manager closed {close_side} {sym_clean}: {close_reason}. Equity: ${equity:.0f}, F&G: {regime.get('fear_greed',50)}, Regime: {regime.get('regime','NEUTRAL')}. Freeing slot for better opportunities.",
                            order_id=int(order_id) if order_id and str(order_id).isdigit() else None,
                        )
                        
                        # Update tracker
                        try:
                            # V3.1.82 FIX: Include final_pnl_pct for correct cooldown calculation
                            _pm_pnl_pct = (pnl / (entry * size) * 100) if entry > 0 and size > 0 else 0
                            tracker.close_trade(sym, {
                                "reason": f"portfolio_manager_{close_reason[:30]}",
                                "pnl": pnl,
                                "final_pnl_pct": _pm_pnl_pct,
                            })
                        except:
                            pass
                        
                        state.trades_closed += 1
                    else:
                        logger.warning(f"[PORTFOLIO] Close failed for {sym_clean}: {close_result}")
                    
                    time.sleep(1)
                    break
        
        logger.info(f"[PORTFOLIO] Review complete. Keep reasons: {keep_reasons[:400]}")
        
        # V3.1.59: AI log for PM decision
        upload_ai_log_to_weex(
            stage=f"Portfolio Review Decision",
            input_data={
                "positions_reviewed": len(positions),
                "equity": round(equity, 2),
            },
            output_data={
                "closes_requested": len(closes),
                "keep_reasons": keep_reasons[:200] if keep_reasons else "none",
            },
            explanation=f"PM decided to close {len(closes)} position(s). {keep_reasons[:200]}"
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"[PORTFOLIO] Gemini JSON error: {e}")
    except Exception as e:
        logger.error(f"[PORTFOLIO] Error: {e}")
        logger.error(traceback.format_exc())


def quick_cleanup_check():
    """Quick check for closed positions"""
    
    position_closed = False
    
    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                
                # V3.1.25: Log RL outcome
                if RL_ENABLED and rl_collector:
                    try:
                        trade = tracker.get_trade(symbol)
                        if trade:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            tier_cfg = TIER_CONFIG[f"Tier {trade.get('tier', get_tier_for_symbol(symbol))}"]
                            est_pnl = tier_cfg.get("take_profit", 2.0)
                            rl_collector.log_outcome(symbol, est_pnl, hours_open, "TP_SL")
                    except Exception as e:
                        logger.debug(f"RL outcome log error: {e}")
                
                cleanup = cancel_all_orders_for_symbol(symbol)
                
                # V3.1.25: Calculate actual PnL for RL matching
                trade = tracker.active.get(symbol, {})
                entry_price = trade.get("entry_price", 0)
                side = trade.get("side", "LONG")
                position_usdt = trade.get("position_usdt", 0)
                pnl_usd = 0
                pnl_pct = 0  # V3.1.82: Initialize before try block
                hit_tp = False

                if entry_price > 0 and position_usdt > 0:
                    try:
                        current_price = get_price(symbol)
                        if side == "LONG":
                            pnl_pct = ((current_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - current_price) / entry_price) * 100
                        pnl_usd = (pnl_pct / 100) * position_usdt
                        hit_tp = pnl_usd > 0
                    except:
                        pass

                # V3.2.60: Detect breakeven SL (same logic as monitor_positions)
                _is_breakeven_sl = False
                if trade.get("sl_moved_to_breakeven", False) and pnl_usd > 0 and position_usdt > 0:
                    _notional = position_usdt * 20
                    _rt_fees = _notional * TAKER_FEE_RATE * 2
                    if pnl_usd < _rt_fees:
                        _is_breakeven_sl = True
                        hit_tp = False
                        logger.info(f"  [BREAKEVEN_SL] {symbol}: Gross ${pnl_usd:.2f} < fees ${_rt_fees:.2f} — BREAKEVEN_SL")

                _close_reason = "breakeven_sl" if _is_breakeven_sl else "tp_sl_hit"
                tracker.close_trade(symbol, {
                    "reason": _close_reason,
                    "cleanup": cleanup,
                    "symbol": symbol,
                    "pnl": round(pnl_usd, 2),
                    "final_pnl_pct": pnl_pct,  # V3.1.82 FIX: was missing final_pnl_pct
                    "hit_tp": hit_tp,
                })
                state.trades_closed += 1
                position_closed = True

                # V3.1.36: AI log for quick cleanup closes
                symbol_clean = symbol.replace("cmt_", "").upper()
                # V3.2.60: Breakeven SL classified correctly
                if _is_breakeven_sl:
                    exit_type = "BREAKEVEN_SL"
                elif hit_tp:
                    exit_type = "TAKE_PROFIT"
                else:
                    exit_type = "STOP_LOSS"
                try:
                    hours_open = 0
                    if trade.get("opened_at"):
                        opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                        hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600

                    # V3.2.50: Use plan order ID stored at trade open (same as monitor_positions path).
                    _stored_plan_id = trade.get("tp_plan_order_id") if hit_tp else trade.get("sl_plan_order_id")
                    if _stored_plan_id:
                        _sync_close_oid = int(_stored_plan_id) if str(_stored_plan_id).isdigit() else None
                        logger.info(f"  [AI-LOG] Sync {exit_type} plan_order_id for {symbol_clean}: {_sync_close_oid} (stored at open)")
                    else:
                        time.sleep(1.5)
                        _sync_close_oid = get_recent_close_order_id(symbol)
                        logger.info(f"  [AI-LOG] Sync {exit_type} orderId for {symbol_clean}: {_sync_close_oid or 'not found (graceful)'}")

                    upload_ai_log_to_weex(
                        stage=f"{exit_type}: {side} {symbol_clean}",
                        input_data={
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry_price,
                            "position_usdt": position_usdt,
                            "hours_open": round(hours_open, 2),
                        },
                        output_data={
                            "action": "CLOSED",
                            "exit_type": exit_type,
                            "pnl_usd": round(pnl_usd, 2),
                            "pnl_pct": round(pnl_pct, 2) if pnl_pct else 0,
                        },
                        explanation=f"Position closed via {exit_type}. {side} {symbol_clean} held {hours_open:.1f}h. PnL: ${pnl_usd:.2f}.",
                        order_id=int(_sync_close_oid) if _sync_close_oid and str(_sync_close_oid).isdigit() else None
                    )
                except Exception as e:
                    logger.debug(f"AI log error for quick cleanup close: {e}")
        
        if position_closed:
            logger.info("Position closed - checking for new opportunity")
            check_trading_signals()
                
    except Exception as e:
        logger.debug(f"Quick check error: {e}")


# ============================================================
# HEALTH CHECK
# ============================================================

def log_health():
    # V3.1.82: Mark progress INSIDE health check as safety net
    # The watchdog was killing the daemon despite the main loop running.
    # Health logs were printing but _mark_progress at line 3021 wasn't updating.
    _mark_progress()

    uptime = datetime.now(timezone.utc) - state.started_at
    uptime_str = str(uptime).split('.')[0]
    active = len(tracker.get_active_symbols())

    logger.info(
        f"HEALTH | Up: {uptime_str} | "
        f"Signals: {state.signals_checked} | "
        f"Trades: {state.trades_opened}/{state.trades_closed} | "
        f"Active: {active}"
    )

    # V3.1.82: Mark again after health log (belt and suspenders)
    _mark_progress()

    # V3.1.36: Auto-fill RL outcomes every health check
    if state.trades_closed > 0:
        fill_rl_outcomes_inline()


# ============================================================
# MAIN LOOP

# ============================================================
# POSITION SYNC ON STARTUP
# ============================================================

def sync_tracker_with_weex():
    """V3.1.55: Sync TradeTracker with actual WEEX positions on startup.
    
    Uses symbol:SIDE keys (e.g. cmt_bnbusdt:LONG) so both sides of same
    pair can be tracked independently. Falls back to plain symbol key for
    backward compat with existing tracker lookups.
    """
    logger.info("Syncing tracker with WEEX positions...")
    
    try:
        positions = get_open_positions()
        
        # V3.1.55: Build set of symbol:SIDE keys from WEEX
        weex_keys = set()
        for p in positions:
            sym = p['symbol']
            side = p.get('side', 'LONG').upper()
            weex_keys.add(f"{sym}:{side}")
            weex_keys.add(sym)  # also track plain symbol for compat
        
        tracker_symbols = set(tracker.get_active_symbols())
        
        # Build tracker keys (both plain and symbol:SIDE)
        tracker_keys = set()
        for s in tracker_symbols:
            tracker_keys.add(s)
        
        # Find positions on WEEX but not in tracker (check both key formats)
        added = 0
        for pos in positions:
            sym = pos['symbol']
            side = pos.get('side', 'LONG').upper()
            key_sided = f"{sym}:{side}"
            
            # Check if tracked under either key format
            if key_sided not in tracker_keys and sym not in tracker_keys:
                tier = get_tier_for_symbol(sym)
                tier_config = get_tier_config(tier)
                
                # V3.1.81: Use WEEX position ctime for accurate hold time tracking
                # ctime is millisecond epoch from WEEX. Fall back to NOW only if unavailable.
                _weex_ctime = pos.get('ctime', '')
                if _weex_ctime:
                    try:
                        _opened_at = datetime.fromtimestamp(int(_weex_ctime) / 1000, tz=timezone.utc).isoformat()
                    except (ValueError, TypeError):
                        _opened_at = datetime.now(timezone.utc).isoformat()
                else:
                    _opened_at = datetime.now(timezone.utc).isoformat()

                tracker.active_trades[key_sided] = {
                    "opened_at": _opened_at,
                    "side": side,
                    "entry_price": float(pos.get('entry_price', 0)),
                    "tier": tier,
                    "max_hold_hours": tier_config['max_hold_hours'],
                    "synced": True,
                    "confidence": 0.75,
                }
                added += 1
                logger.info(f"  Added {key_sided} (Tier {tier}, {side} @ {float(pos.get('entry_price',0)):.4f}, opened_at={_opened_at})")
            elif key_sided not in tracker_keys and sym in tracker_keys:
                # Tracked under plain symbol but side might be wrong - check
                existing = tracker.active_trades.get(sym, {})
                existing_side = existing.get('side', '').upper()
                if existing_side and existing_side != side:
                    # Different side! Track this one separately
                    # V3.1.81: Use WEEX ctime
                    _weex_ctime2 = pos.get('ctime', '')
                    if _weex_ctime2:
                        try:
                            _opened_at2 = datetime.fromtimestamp(int(_weex_ctime2) / 1000, tz=timezone.utc).isoformat()
                        except (ValueError, TypeError):
                            _opened_at2 = datetime.now(timezone.utc).isoformat()
                    else:
                        _opened_at2 = datetime.now(timezone.utc).isoformat()

                    tracker.active_trades[key_sided] = {
                        "opened_at": _opened_at2,
                        "side": side,
                        "entry_price": float(pos.get('entry_price', 0)),
                        "tier": get_tier_for_symbol(sym),
                        "max_hold_hours": get_tier_config(get_tier_for_symbol(sym))['max_hold_hours'],
                        "synced": True,
                        "confidence": 0.75,
                    }
                    added += 1
                    logger.info(f"  Added opposite side {key_sided} (existing {sym} is {existing_side})")
        
        if added > 0:
            logger.warning(f"Added {added} untracked positions")
            tracker.save_state()
        
        # Find orphan trades in tracker (position closed but tracker didn't know)
        orphan_count = 0
        for tracker_key in list(tracker_symbols):
            # Check if this tracker entry has a matching WEEX position
            found = False
            for pos in positions:
                sym = pos['symbol']
                side = pos.get('side', 'LONG').upper()
                if tracker_key == sym or tracker_key == f"{sym}:{side}":
                    found = True
                    break
            if not found:
                tracker.close_trade(tracker_key, {"reason": "sync_cleanup", "note": "Position not found on WEEX"})
                logger.info(f"  Removed orphan {tracker_key}")
                orphan_count += 1
        
        total_tracked = len(tracker.get_active_symbols())
        total_weex = len(positions)
        logger.info(f"Sync complete. Tracking {total_tracked} entries for {total_weex} WEEX positions.")

        # V3.1.82: Debug log all tracked positions with their opened_at and hold time
        _now_sync = datetime.now(timezone.utc)
        for _tk, _tv in tracker.active_trades.items():
            _opened_str = _tv.get("opened_at", "unknown")
            _hold_h = 0
            try:
                _opened_dt = datetime.fromisoformat(_opened_str.replace("Z", "+00:00"))
                _hold_h = (_now_sync - _opened_dt).total_seconds() / 3600
            except:
                pass
            _tier = _tv.get("tier", "?")
            # V3.2.49: Always read max_hold from LIVE TIER_CONFIG, not stale trade state
            try:
                _max_h = TIER_CONFIG[int(_tier)]["max_hold_hours"]
            except:
                _max_h = _tv.get("max_hold_hours", "?")
            _synced = "synced" if _tv.get("synced") else "original"
            logger.info(f"  [{_tk}] T{_tier} {_tv.get('side','?')} opened={_opened_str[:19]} hold={_hold_h:.1f}h/{_max_h}h ({_synced})")

        # V3.2.64: Backfill plan order IDs for positions that have None (e.g. opened with broken endpoint).
        # Missing order IDs in AI logs caused competition suspension — ALWAYS fetch if missing.
        _backfill_count = 0
        for _tk, _tv in tracker.active_trades.items():
            _has_tp_id = _tv.get("tp_plan_order_id") is not None
            _has_sl_id = _tv.get("sl_plan_order_id") is not None
            if _has_tp_id and _has_sl_id:
                continue  # Already have both IDs
            _tp = _tv.get("tp_price")
            _sl = _tv.get("sl_price")
            _sym = _tk.split(":")[0] if ":" in _tk else _tk
            if not _tp or not _sl or not _sym:
                continue
            try:
                _plan_ids = _fetch_plan_order_ids(_sym, float(_tp), float(_sl))
                if _plan_ids.get("tp_plan_order_id") or _plan_ids.get("sl_plan_order_id"):
                    _tv["tp_plan_order_id"] = _plan_ids.get("tp_plan_order_id")
                    _tv["sl_plan_order_id"] = _plan_ids.get("sl_plan_order_id")
                    _backfill_count += 1
                    logger.info(f"  [BACKFILL] {_sym.replace('cmt_','').upper()}: tp_plan_id={_plan_ids.get('tp_plan_order_id')}, sl_plan_id={_plan_ids.get('sl_plan_order_id')}")
            except Exception as _bf_e:
                logger.warning(f"  [BACKFILL] Failed for {_sym}: {_bf_e}")
        if _backfill_count > 0:
            tracker.save_state()
            logger.info(f"  [BACKFILL] Updated plan order IDs for {_backfill_count} position(s)")

        # V3.1.82: Clean stale cooldowns for symbols with no open positions
        # The pnl_pct key bug (fixed now) gave wins false cooldowns. Clear cooldowns
        # for any symbol that has NO open position on WEEX (already closed).
        _weex_syms = {p['symbol'] for p in positions}
        _stale_cds = []
        for _cd_key in list(tracker.cooldowns.keys()):
            _cd_plain = _cd_key.split(":")[0] if ":" in _cd_key else _cd_key
            if _cd_plain not in _weex_syms:
                _stale_cds.append(_cd_key)
        if _stale_cds:
            for _sk in _stale_cds:
                del tracker.cooldowns[_sk]
            tracker.save_state()
            logger.info(f"  Cleared {len(_stale_cds)} stale cooldown(s): {', '.join(s.replace('cmt_','').upper() for s in _stale_cds)}")

        # V3.1.83: Cancel orphan trigger orders on symbols with NO open position.
        # When positions are manually closed or force-closed, TP/SL trigger orders
        # can be left behind on WEEX. These orphan triggers cause:
        #   1. Leverage set failures ("open orders" blocking)
        #   2. Old SL/TP executing at wrong prices on new positions
        #   3. The DOGE SL bug: bot set $0.0999 but WEEX showed $0.0936 (old trigger)
        _all_trading_syms = {info["symbol"] for info in TRADING_PAIRS.values()}
        _orphan_cleaned = 0
        for _check_sym in _all_trading_syms:
            if _check_sym not in _weex_syms:
                # No open position on this symbol — any trigger orders are orphans
                try:
                    _orphan_result = cancel_all_orders_for_symbol(_check_sym)
                    if _orphan_result.get("cancelled"):
                        _n_cancelled = len(_orphan_result["cancelled"])
                        _orphan_cleaned += _n_cancelled
                        logger.warning(f"  Cancelled {_n_cancelled} orphan trigger(s) on {_check_sym.replace('cmt_','').upper()} (no open position)")
                except Exception:
                    pass
        if _orphan_cleaned > 0:
            logger.warning(f"  Total orphan triggers cleaned: {_orphan_cleaned}")

    except Exception as e:
        logger.error(f"Sync error: {e}")


def resolve_opposite_sides():
    """V3.2.21: If same symbol has BOTH Long and Short open, close the OLDER side.

    The newer position represents the current signal — close the stale one.
    Falls back to closing the losing side if ctime is unavailable.

    This is a mechanical rule - no Gemini needed. Two sides on same pair is
    capital-inefficient and indicates the system changed its mind but didn't
    clean up.
    """
    try:
        positions = get_open_positions()
        
        # Group by symbol
        by_symbol = {}
        for p in positions:
            sym = p.get('symbol', '')
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(p)
        
        for sym, pos_list in by_symbol.items():
            if len(pos_list) < 2:
                continue
            
            # Both sides exist
            long_pos = None
            short_pos = None
            for p in pos_list:
                if p.get('side', '').upper() == 'LONG':
                    long_pos = p
                elif p.get('side', '').upper() == 'SHORT':
                    short_pos = p
            
            if not long_pos or not short_pos:
                continue
            
            # V3.2.22: No wait gate — close older side immediately.
            # Clean up any stale tighten record for this symbol.
            _sl_tightened_symbols.pop(sym, None)

            long_pnl = float(long_pos.get('unrealized_pnl', 0))
            short_pnl = float(short_pos.get('unrealized_pnl', 0))
            long_ctime = int(long_pos.get('ctime', 0) or 0)
            short_ctime = int(short_pos.get('ctime', 0) or 0)
            sym_clean = sym.replace('cmt_', '').upper()

            logger.info(f"  [OPPOSITE] {sym_clean}: LONG PnL=${long_pnl:.2f} age={long_ctime}, SHORT PnL=${short_pnl:.2f} age={short_ctime}")

            # V3.2.21: Close the OLDER side — newer position = current signal.
            # Fall back to closing the losing side if ctime unavailable.
            if long_ctime and short_ctime:
                if long_ctime <= short_ctime:
                    # LONG is older (smaller ctime = opened earlier)
                    close_side = "LONG"
                    close_pos = long_pos
                    keep_side = "SHORT"
                    keep_pnl = short_pnl
                else:
                    # SHORT is older
                    close_side = "SHORT"
                    close_pos = short_pos
                    keep_side = "LONG"
                    keep_pnl = long_pnl
            else:
                # Fallback: close losing side if ctime unavailable
                if long_pnl <= short_pnl:
                    close_side = "LONG"
                    close_pos = long_pos
                    keep_side = "SHORT"
                    keep_pnl = short_pnl
                else:
                    close_side = "SHORT"
                    close_pos = short_pos
                    keep_side = "LONG"
                    keep_pnl = long_pnl
            
            close_pnl = float(close_pos.get('unrealized_pnl', 0))
            close_size = float(close_pos.get('size', 0))
            
            if close_size <= 0:
                continue
            
            logger.info(f"  [OPPOSITE] Closing {close_side} {sym_clean} (PnL=${close_pnl:.2f}), keeping {keep_side} (PnL=${keep_pnl:.2f})")
            
            from smt_nightly_trade_v3_1 import place_order, round_size_to_step
            close_type = "3" if close_side == "LONG" else "4"
            rounded_size = round_size_to_step(close_size, sym)
            
            if rounded_size > 0:
                # Cancel any pending orders for this side first
                cancel_all_orders_for_symbol(sym)
                
                result = place_order(sym, close_type, rounded_size, tp_price=None, sl_price=None)
                oid = result.get("order_id")
                
                upload_ai_log_to_weex(
                    stage=f"Opposite-Side Resolution: Close {close_side} {sym_clean}",
                    input_data={
                        "symbol": sym,
                        "long_pnl": long_pnl,
                        "short_pnl": short_pnl,
                        "closing_side": close_side,
                        "keeping_side": keep_side,
                    },
                    output_data={"action": "CLOSE_OPPOSITE", "order_id": oid},
                    explanation=f"AI detected both LONG (PnL=${long_pnl:.2f}) and SHORT (PnL=${short_pnl:.2f}) open on {sym_clean}. Closing {close_side} (older position) — newer {keep_side} represents the current signal. Eliminated capital-inefficient hedge.",
                    order_id=oid
                )
                
                # Remove from tracker
                # V3.1.82 FIX: Include final_pnl_pct for correct cooldown calc
                _opp_entry = float(close_pos.get("entry_price", 1))
                _opp_size = float(close_pos.get("size", 1))
                _opp_pnl_pct = (close_pnl / (_opp_entry * _opp_size) * 100) if _opp_entry and _opp_size else 0
                for key in [f"{sym}:{close_side}", sym]:
                    if key in tracker.active_trades:
                        tracker.close_trade(key, {"reason": "opposite_side_resolution", "pnl": close_pnl, "final_pnl_pct": _opp_pnl_pct})
                        break
                
                logger.info(f"  [OPPOSITE] Closed {close_side} {sym_clean}: order {oid}")
    
    except Exception as e:
        logger.error(f"  [OPPOSITE] Resolution error: {e}")


# ============================================================






# ============================================================
# V3.1.9: REGIME-AWARE SMART EXIT
# ============================================================


def get_market_regime_for_exit():
    """
    V3.1.25: HYBRID regime detection
    - Slow (4h/24h candles) for trend
    - Fast (1h candles) for spike detection
    
    SPIKE_UP: BTC pumped >1.5% in 1h - danger for SHORTs
    SPIKE_DOWN: BTC dumped >1.5% in 1h - danger for LONGs
    """
    try:
        # === SLOW TREND (existing logic) ===
        url_4h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url_4h, timeout=10)
        data_4h = r.json()
        
        change_24h = 0
        change_4h = 0
        
        if isinstance(data_4h, list) and len(data_4h) >= 7:
            closes = [float(c[4]) for c in data_4h]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
        
        # === FAST SPIKE DETECTION (new) ===
        url_1h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=1h&limit=2"
        r = requests.get(url_1h, timeout=10)
        data_1h = r.json()
        
        change_1h = 0
        if isinstance(data_1h, list) and len(data_1h) >= 2:
            closes_1h = [float(c[4]) for c in data_1h]
            change_1h = ((closes_1h[0] - closes_1h[1]) / closes_1h[1]) * 100
        
        # === REGIME DECISION ===
        
        # V3.1.25: SPIKE detection takes priority (fast override)
        # This catches sudden pumps/dumps that slow detection misses
        if change_1h > 1.5:
            return {
                "regime": "SPIKE_UP",
                "change_24h": change_24h,
                "change_4h": change_4h,
                "change_1h": change_1h,
                "spike": True,
            }
        elif change_1h < -1.5:
            return {
                "regime": "SPIKE_DOWN",
                "change_24h": change_24h,
                "change_4h": change_4h,
                "change_1h": change_1h,
                "spike": True,
            }
        
        # No spike - use slow trend detection
        if change_24h < -1.0 or change_4h < -1.0:
            regime = "BEARISH"
        elif change_24h > 1.5 or change_4h > 1.0:
            regime = "BULLISH"
        else:
            regime = "NEUTRAL"
        
        return {
            "regime": regime,
            "change_24h": change_24h,
            "change_4h": change_4h,
            "change_1h": change_1h,
            "spike": False,
        }
        
    except Exception as e:
        logger.error(f"[REGIME] API error: {e}")
    
    return {"regime": "UNKNOWN", "change_24h": 0, "change_4h": 0, "change_1h": 0, "spike": False}


def regime_aware_exit_check():
    """
    V3.1.9: AI cuts positions fighting the market regime.
    
    Logic:
    - BEARISH market + LONG losing > 2% margin = AI closes position
    - BULLISH market + SHORT losing > 2% margin = AI closes position
    
    This frees margin for regime-aligned trades.
    """
    try:
        positions = get_open_positions()
        if not positions:
            return
        
        regime = get_market_regime_for_exit()
        
        # V3.1.64: Inject F&G into regime for fear shield logic
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _fg_regime = get_fear_greed_index()
            regime["fear_greed"] = _fg_regime.get("value", 50)
        except:
            regime["fear_greed"] = 50
        
        spike_msg = " SPIKE!" if regime.get('spike') else ""
        logger.info(f"[REGIME] Market: {regime['regime']} | 1h: {regime.get('change_1h', 0):+.1f}% | 4h: {regime['change_4h']:+.1f}% | 24h: {regime['change_24h']:+.1f}%{spike_msg} | F&G: {regime.get('fear_greed', 50)}")
        
        # V3.1.10: Log position balance
        long_pnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'LONG')
        short_pnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'SHORT')
        if positions:
            logger.info(f"[REGIME] Position PnL - LONGs: ${long_pnl:+.1f} | SHORTs: ${short_pnl:+.1f}")
        
        if regime["regime"] == "UNKNOWN":
            logger.warning("[REGIME] Could not determine market regime - API issue")
            return
        
        # Don't return on NEUTRAL - we still check for weak market exits below
        
        closed_count = 0
        
        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            pnl = float(pos.get('unrealized_pnl', 0))
            margin = float(pos.get("margin", 500))
            # V3.1.77: RL data shows regime exits average -12.5% PnL (worst close reason).
            # Trust the exchange SL to do its job. Only intervene for catastrophic cases.
            regime_fight_threshold = -(margin * 0.35)   # V3.1.77: 35% margin loss (was 15%). Let SL handle normal cases.
            hard_stop_threshold = -(margin * 0.45)      # V3.1.77: 45% margin loss (was 25%). Near-liquidation emergency only.
            spike_threshold = -(margin * 0.05)           # V3.1.77: 5% of margin (was 1.5%)
            size = float(pos['size'])
            
            symbol_clean = symbol.replace('cmt_', '').upper()
            
            should_close = False
            reason = ""
            
            # V3.1.20 PREDATOR: Check minimum hold time before regime exit
            trade = tracker.get_active_trade(symbol)
            hours_open = 0
            if trade:
                try:
                    opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                    hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                except:
                    pass
            
            # V3.1.20 PREDATOR: No regime exits within first 4 hours - let trades breathe
            if hours_open < 4:
                continue
            
            # V3.1.77: FEAR SHIELD extended to F&G < 30 (was < 20)
            # RL data: regime exits are the most destructive close reason (-12.5% avg PnL)
            _fg_for_regime = regime.get("fear_greed", 50)
            if _fg_for_regime < 30 and pnl > 0:
                logger.info(f"[REGIME] FEAR SHIELD: Skipping {symbol_clean} {side} (profitable ${pnl:+.1f} in F&G={_fg_for_regime})")
                continue
            
            # V3.1.25: SPIKE detection - fast exit on sudden moves
            if regime.get("spike"):
                spike_type = regime["regime"]
                change_1h = regime.get("change_1h", 0)
                
                # SPIKE_UP = danger for SHORTs
                if spike_type == "SPIKE_UP" and side == "SHORT" and pnl < spike_threshold:
                    should_close = True
                    reason = f"SPIKE_UP: BTC +{change_1h:.1f}% in 1h, SHORT losing ${abs(pnl):.1f}"
                
                # SPIKE_DOWN = danger for LONGs
                elif spike_type == "SPIKE_DOWN" and side == "LONG" and pnl < spike_threshold:
                    should_close = True
                    reason = f"SPIKE_DOWN: BTC {change_1h:.1f}% in 1h, LONG losing ${abs(pnl):.1f}"
            
            # V3.1.23: Simplified regime exit logic - only exit positions FIGHTING the regime
            # Trust the 2% SL on WEEX for normal stops
            
            # LONG losing in BEARISH market
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < regime_fight_threshold:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} ({abs(pnl)/margin*100:.1f}% margin) in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < regime_fight_threshold:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} ({abs(pnl)/margin*100:.1f}% margin) in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.23 FIX: HARD STOP only for positions FIGHTING regime (raised to $50)
            # LONG in BEARISH/NEUTRAL losing badly = cut it
            elif side == "LONG" and pnl < hard_stop_threshold and regime["regime"] in ("BEARISH", "NEUTRAL"):
                should_close = True
                reason = f"HARD STOP: LONG losing ${abs(pnl):.1f} in {regime['regime']} market"
            
            # SHORT in BULLISH losing badly = cut it (but NOT in BEARISH/NEUTRAL!)
            elif side == "SHORT" and pnl < hard_stop_threshold and regime["regime"] == "BULLISH":
                should_close = True
                reason = f"HARD STOP: SHORT losing ${abs(pnl):.1f} in BULLISH market"
            
            # V3.1.23: No more unconditional HARD STOP or "opposite winning" exit
            # Trust the 2% SL on WEEX to do its job
            
            if should_close:
                logger.warning(f"[REGIME EXIT] {symbol_clean}: {reason}")
                
                # Close the position
                close_result = close_position_manually(symbol, side, size)
                order_id = close_result.get("order_id")
                
                # V3.1.25: Log RL outcome
                if RL_ENABLED and rl_collector:
                    try:
                        trade = tracker.get_trade(symbol)
                        if trade:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            entry = trade.get("entry_price", 1)
                            pnl_pct = (pnl / (entry * size)) * 100 if entry and size else 0
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, "REGIME_EXIT")
                    except Exception as e:
                        logger.debug(f"RL outcome log error: {e}")
                
                # Update tracker
                # V3.1.82 FIX: Include final_pnl_pct for correct cooldown calc
                _re_entry = tracker.get_active_trade(symbol) or {}
                _re_entry_price = _re_entry.get("entry_price", 1)
                _re_pnl_pct = (pnl / (_re_entry_price * size) * 100) if _re_entry_price and size else 0
                tracker.close_trade(symbol, {
                    "reason": f"regime_exit_{regime['regime'].lower()}",
                    "pnl": pnl,
                    "final_pnl_pct": _re_pnl_pct,
                    "regime": regime["regime"],
                })
                
                state.trades_closed += 1
                state.early_exits += 1
                closed_count += 1
                
                # Upload AI log
                upload_ai_log_to_weex(
                    stage=f"Regime Exit: {side} {symbol_clean}",
                    input_data={
                        "symbol": symbol,
                        "side": side,
                        "size": size,
                        "unrealized_pnl": pnl,
                        "market_regime": regime["regime"],
                        "btc_24h_change": regime["change_24h"],
                        "btc_4h_change": regime["change_4h"],
                    },
                    output_data={
                        "action": "CLOSE",
                        "ai_decision": "REGIME_EXIT",
                        "reason": reason,
                    },
                    explanation=f"AI Regime Exit: {reason}. Cutting position fighting the trend to free margin for {regime['regime']}-aligned opportunities.",
                    order_id=order_id
                )
                
                logger.info(f"[REGIME EXIT] Closed {symbol_clean}, order: {order_id}")
        
        if closed_count > 0:
            logger.info(f"[REGIME EXIT] Closed {closed_count} positions fighting the trend")
            # Trigger signal check to find new opportunities
            logger.info("[REGIME EXIT] Checking for new opportunities...")
            check_trading_signals()
        else:
            logger.info("[REGIME] No positions need regime exit")
            
    except Exception as e:
        logger.error(f"[REGIME EXIT] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())


def run_daemon():
    logger.info("=" * 60)
    logger.info("SMT Daemon V3.2.70 - R:R UNLOCK: MIN_VIABLE 0.30%%→0.50%% (walk to 4H), R:R 0.5→0.33 (unblocks BTC/ETH/BNB/LTC at 1.5%% SL)")
    logger.info("=" * 60)
    # --- Trading pairs & slots ---
    logger.info("PAIRS & SLOTS:")
    logger.info("  Pairs: BTC, ETH, BNB, LTC, XRP, SOL, ADA (7)")
    logger.info("  Max slots: 2 (V3.2.59 — diversification without cascade risk) | Leverage: 20x flat | Shorts: ALL pairs")
    logger.info("  Sizing: 80-84%%=20%%, 85-89%%=35%%, 90%%+=50%% of sizing_base (SOL capped 30%%)")
    logger.info("  Circuit breaker: 60min+ cooldown after SL/force stop | Breakeven SL at +0.4%%")
    # --- Confidence & entry filters ---
    logger.info("ENTRY FILTERS:")
    logger.info("  MIN_CONFIDENCE: 85%% HARD FLOOR — no exceptions, no discounts, no overrides")
    logger.info("  Chop filter: logging only (no penalties since V3.2.18)")
    logger.info("  Consecutive loss block: 2 losses same direction in 24h = block re-entry")
    logger.info("  Freshness filter: block entering after move already happened")
    logger.info("  Regime veto: post-judge filter (not disabled, separate from regime exits)")
    logger.info("  Margin guard: skip trade if available margin < $1000")
    # --- TP/SL V3.2.41 ---
    logger.info("TP/SL (V3.2.41 LARGER GAINS):")
    logger.info("  MIN_VIABLE_TP_PCT: 0.40%% (was 0.20%% — filters micro-moves, pushes to 4H/48H structure)")
    logger.info("  MAX_SL_PCT: 1.50%% ceiling (was no ceiling — 4H anchors can be wide at 20x)")
    logger.info("  PAIR_TP_CEILING: BTC=0.60%%, ETH=0.50%%, BNB=0.50%%, LTC=0.70%%, XRP=0.75%%, SOL=0.75%%, ADA=0.80%% (V3.2.67 tight) + 90%% haircut (V3.2.68)")
    logger.info("  TP anchor: 4H high/low tried first, then 48H walk (was 2H anchor → 48H walk)")
    logger.info("  SOL sizing: 30%% of sizing_base max (was 50%% — high beta risk control)")
    logger.info("  TP priority: Gemini tp_price (4H structure) > 4H anchor > 48H SR walk")
    logger.info("  TP floor: 0.3%% (MIN_TP_PCT) | Fallback TP: 0.5%% all tiers (COMPETITION_FALLBACK_TP)")
    logger.info("  Extreme fear TP cap: 0.5%% when F&G < 20 (both LONG and SHORT)")
    logger.info("  XRP TP cap: 0.70%% (only when no Gemini structural override)")
    logger.info("  SL method: lowest wick in 12H (1H grid) + last 3 4H candles | SL floor: 1.0%%")
    logger.info("  TP method: max high of last 2 complete 1H candles (LONG) / min low (SHORT)")
    logger.info("  FLOW walls in Judge prompt: ask/bid walls from 200-level order book (context, not hard override)")
    logger.info("  Gemini Judge: sees chart structure (1D+4H) + 12H price action candles (T1=1h, T2=30m, T3=15m) + FLOW walls + chop → returns tp_price")
    # --- Position sizing ---
    logger.info("POSITION SIZING (V3.2.46 confidence-scaled):")
    logger.info("  sizing_base = max(min(available, balance * 2.5), 1000)")
    logger.info("  80-84%%: sizing_base * 0.20 | 85-89%%: sizing_base * 0.35 | 90%%+: sizing_base * 0.50")
    logger.info("  SOL cap: 30%% of sizing_base (high beta) | Single slot = full account buffer")
    # --- Opposite swap ---
    logger.info("OPPOSITE SWAP (V3.1.100):")
    logger.info("  Block flip if position < 20min old or >= 30%% toward TP")
    logger.info("  Blocked signal queued → auto-executes after old position closes")
    logger.info("  Deferred flip expires after 30min")
    # --- V3.2.48: Macro defense ---
    logger.info("MACRO DEFENSE (V3.2.48):")
    logger.info("  Blackout windows: PCE 2026-02-20 13:15-14:00 UTC (skip entire signal cycle)")
    logger.info("  Weekend mode: Sat/Sun → BTC,ETH,SOL only (altcoin books too thin)")
    logger.info("  Holiday mode: Emperor's Birthday 2026-02-23 < 12:00 UTC → same restriction")
    logger.info("  Funding hold-cost: per-pair funding rate × 20x = margin drag in Judge prompt")
    logger.info("  Macro events: date-sensitive context (PCE, ZRO unlock, holiday) fed to Judge")
    # --- Timing ---
    logger.info("TIMING:")
    logger.info("  Signal check: 10min | Position monitor: 2min | Orphan sweep: 30s | Health: 60s")
    logger.info("  Global trade cooldown: 10min between trades")
    logger.info("  Gemini: 90s timeout + 8s rate limit between calls")
    # --- Persona weights ---
    logger.info("PERSONA CONFIG:")
    logger.info("  WHALE: 1.0 | Etherscan on-chain + Cryptoracle (always combined for BTC/ETH)")
    logger.info("  SENTIMENT: 1.0 | Gemini 2.5 Flash w/ Search Grounding")
    logger.info("  FLOW: 1.0 | Taker ratio beats depth | 180-flip at dip/peak = +15%% boost (V3.2.68)")
    logger.info("  TECHNICAL: 1.0 (halved when F&G<30) | 5m RSI(14) + VWAP + 30m momentum + volume spike (2x) + entry velocity (0.20%%/15m) (V3.2.68)")
    logger.info("  JUDGE: Gemini aggregator | Receives chart structure (1D+4H) + 12H price action candles for entry timing")
    # --- Disabled features ---
    logger.info("DISABLED:")
    logger.info("  Gemini portfolio review (V3.2.17) | Stale auto-close (V3.2.17)")
    logger.info("  Regime-based auto-exits (V3.2.17) — SL handles exits")
    logger.info("  Chop score penalties (V3.2.18) — logging only")
    logger.info("  Fee tracking: 0.08%%/side taker (V3.2.50), logged per trade + session cumulative in HEALTH")
    # --- Tier table ---
    logger.info("TIER CONFIG:")
    for tier, config in TIER_CONFIG.items():
        tier_config = TIER_CONFIG[tier]
        pairs = [p for p, info in TRADING_PAIRS.items() if info["tier"] == tier]
        runner = RUNNER_CONFIG.get(tier, {})
        runner_str = f"Runner: +{runner.get('trigger_pct', 0)}%% -> close 50%%" if runner.get("enabled") else "No Runner"
        logger.info(f"  Tier {tier}: {', '.join(pairs)}")
        logger.info(f"    TP: {tier_config['take_profit']*100:.1f}%%, SL: {tier_config['stop_loss']*100:.1f}%%, Hold: {tier_config['time_limit']/60:.0f}h | {runner_str}")
    # --- Recent changelog (last 5 versions) ---
    logger.info("CHANGELOG (recent):")
    logger.info("  V3.2.70: R:R UNLOCK — MIN_VIABLE_TP_PCT 0.30%%→0.50%% (forces walk past 2H micro-bounce to 4H structural anchor). MIN_RR_RATIO 0.5→0.33 (unblocks BTC/ETH/BNB/LTC when SL at 1.5%% cap). Break-even 82.3%% win rate, 85%% floor covers. Fixes: TP ceiling vs R:R deadlock that blocked 4/7 pairs.")
    logger.info("  V3.2.69: RANGE GATE 2H OVERRIDE — 12H range gate (55/45) now bypassed when TECHNICAL's 2H range confirms genuine dip (<30%%) or peak (>70%%). Fixes: BNB 90%% blocked at 12H=77%%/2H=11%%, SOL 85%% blocked at 12H=57%%/2H=7%%. Short-term dips in uptrends are valid entries.")
    logger.info("  V3.2.68: DIP DETECTION OVERHAUL — TECHNICAL: 5m RSI(14)+VWAP+30m momentum+volume spike(2x)+entry velocity(0.20%%/15m). FLOW: flip at dip/peak=+15%% boost (was 50%% discount). Range gate 55/45. TP haircut 90%%. 8-EMA snap-back EXIT in daemon (mean-reversion close). Judge: 2-persona dip rule, flip protocol.")
    logger.info("  V3.2.67: VELOCITY EXIT TIERED (T1=75m, T2=60m, T3=50m, was flat 40m — bounces need 60-90min). Peak threshold 0.15%%→0.10%%. ADX gate softened (5, not 10). Weekend restriction DISABLED (all 7 pairs). Signal persistence tracks ADX-gated signals.")
    logger.info("  V3.2.63: 1D candle granularity FIX (1Dutc→1d — was rejected by WEEX API, breaking ALL chart context). 4H fallback when 1D unavailable. datetime.utcfromtimestamp→fromtimestamp(tz=utc). Judge now gets daily+4H S/R levels again.")
    logger.info("  V3.2.62: Chart context FIX — 12H price action fetch now INDEPENDENT of 1D/4H (was silently failing when 1D/4H returned errors). 30m→15m fallback for T2 if WEEX rejects 30m granularity. Fixed datetime.datetime bug (was datetime.utcfromtimestamp). Judge TP ceiling prompt now reads PAIR_TP_CEILING dynamically.")
    logger.info("  V3.2.61: 12H price action candles fed to Judge (T1=1h/12, T2=30m/24, T3=15m/48 — entry timing + range position). TP ceilings raised (BTC/ETH=1.0%%, SOL=1.5%%, others=0.80%%), R:R guard restored (min 0.5:1), Gemini TP override blocked when chart SR returns tp_not_found.")
    logger.info("  V3.2.60: ATR-safety respects MAX_SL_PCT ceiling (was overriding to 1.84%%), breakeven SL classified correctly.")
    logger.info("  V3.2.59: Gemini event detection — detect_macro_events() uses Gemini Search to scan upcoming macro/crypto events (30min cache). Dynamic blackout for HIGH-impact events within 15min. Replaces hardcoded _macro_events dict in Judge prompt. Also: ADX<20 WAIT removed, 2-slot mode, TP fee-floor guard.")
    logger.info("  V3.2.58: Altcoin execution priority — sort key now (confidence desc, tier desc) so T3>T2>T1 at equal confidence. Prevents T1 BTC/ETH crowding out altcoins when same 85%% threshold hit in same cycle.")
    logger.info("  V3.2.57: BlitzMode final 72h — MIN_CONFIDENCE 80%%→85%%; velocity_exit (40min/0.15%% peak, zero cooldown); GLOBAL_TRADE_COOLDOWN 900→600s; sizing floor $1000→$500; Judge BLITZ MODE prompt")
    logger.info("  V3.2.56: Macro blackout exit — monitor_positions() closes unprofitable (pnl<0) positions when blackout window activates; profitable positions ride with SL; AI log sent with order_id. Funding rate direction-aware: Judge now told paying vs receiving side based on persona consensus (fixes bonus-labeled-as-drag for LONG on negative funding)")
    logger.info("  V3.2.55: _fetch_plan_order_ids() retry loop — 2s initial sleep + up to 2 retries (2s apart) on HTTP 404 or empty plan-orders response; eliminates stored-ID=None fallback on WEEX registration lag")
    logger.info("  V3.2.54: Tiered peak-fade — T1(BTC/ETH): MIN=0.30%%,TRIG=0.15%%; T2/T3(altcoins): MIN=0.45%%,TRIG=0.25%%; altcoin wick noise needs 2x breathing room vs majors; exit_reason peak_fade_T{n} includes tier+thresholds")
    logger.info("  V3.2.53: Peak-fade soft stop — PEAK_FADE_MIN_PEAK=0.20%%, PEAK_FADE_TRIGGER=0.12%%; fires when peak>=0.20%% and current<=peak-0.12%%; 'peak_fade' reason = zero cooldown; only when BE-SL not yet placed")
    logger.info("  V3.2.52: Pre-cycle exit sweep — max_hold/force_exit/early_exit checked at START of check_trading_signals(); positions closed with full AI log BEFORE 7-pair Gemini analysis (fixes 6-min blind spot)")
    logger.info("  V3.2.51: Emergency flip — EMERGENCY_FLIP_CONFIDENCE=0.90; age gate bypassed at 90%+ opposite confidence; TP proximity gate preserved; 'EMERGENCY FLIP' tag in logs + AI log")
    logger.info("  V3.2.50: TAKER_FEE_RATE corrected 0.0006→0.0008 (0.08%/side, 3.2%% margin RT); _fetch_plan_order_ids() stores tp/sl plan IDs at open; daemon uses stored IDs for AI log upload (no sleep/guesswork)")
    logger.info("  V3.2.49: Final stretch — T1: 3h/1h, T2: 2h/45m, T3: 1.5h/30m (was 24/12/8h); sync display reads live TIER_CONFIG not stale trade state")
    logger.info("  V3.2.48: Macro blackout (PCE 13:15-14:00 UTC), weekend mode (BTC/ETH/SOL only), Emperor's Birthday thin-liq, funding hold-cost in Judge, ZRO unlock context")
    logger.info("  V3.2.45: Regex JSON parser for SENTIMENT+JUDGE (re.search(r'\\{.*\\}', re.DOTALL) — immune to grounding citation injection [1][2]); dynamic date in SENTIMENT search; no-citation prompt directive")
    logger.info("  V3.2.44: Full Gemini response visibility — removed [:200] on SENTIMENT reasoning, [:600] on JUDGE reasoning print, [:200] on Judge prompt persona summary; market_context no longer capped")
    logger.info("  V3.2.42: Judge progress prints ([JUDGE] Calling Gemini / responded in Xs); sync_tracker AI log now sends order_id via get_recent_close_order_id()")
    logger.info("  V3.2.38: 4-slot hard cap restored (can_open_new checks available_slots > 0)")
    logger.info("  V3.2.37: ANTI-WAIT removed — Gemini Judge WAIT = WAIT; no persona-consensus or keyword override")
    logger.info("  V3.2.36: MIN_VIABLE_TP_PCT=0.20%% — skip SR levels < 0.20%% from entry; entry at resistance = tp_not_found = discard")
    logger.info("  V3.2.35: Opposite signal = close existing position first, then open new side (was SL-tighten + dual-open)")
    logger.info("  V3.2.34: Judge receives WHALE dual-source data (Etherscan+Cryptoracle) separately for BTC/ETH")
    logger.info("  V3.2.33: SHORT TP back to min(lows_1h[1:3]) (deepest wick = real support)")
    logger.info("  V3.2.31: 48H SR lookback (was 12H); 0.5%% TP cap universal ceiling on ALL trades")
    logger.info("  V3.2.29: Walk SR list before discard; 0.5%% TP cap on ALL trades (not just fear); no-SR = no trade (no fallback)")
    logger.info("  V3.2.28: Bad-TP trades discarded (entry at resistance); sizing cache reset after each trade for accurate available")
    logger.info("  V3.2.25: No slot cap (margin guard limits); dust+orphan sweep every cycle; opp-side resolve at cycle end; sizing from available margin")
    logger.info("  V3.2.24: MIN_TP_PCT=0.3%% floor removed — chart SR is the TP, no artificial minimum")
    logger.info("  V3.2.18: Chop penalties removed | Shorts ALL pairs | Trust 80%% floor + 0.5%% TP")
    logger.info("=" * 60)

    # V3.1.9: Sync with WEEX on startup
    sync_tracker_with_weex()
    
    # V3.1.53: Clean dust positions on startup
    try:
        cleanup_dust_positions()
    except Exception as e:
        logger.warning(f'Dust cleanup error: {e}')
    
    # V3.1.55: Resolve opposite-side positions on startup
    try:
        resolve_opposite_sides()
    except Exception as e:
        logger.warning(f'Opposite-side resolution error: {e}')
    
    last_signal = 0
    last_position = 0
    last_health = 0
    last_cleanup = 0
    
    if is_competition_active():
        logger.info("Competition ACTIVE - initial signal check")
        check_trading_signals()
        _mark_progress()  # V3.1.73: Mark progress after initial check (was missing, caused watchdog kills)
        last_signal = time.time()
    
    while state.is_running and not state.shutdown_event.is_set():
        try:
            now = time.time()
            
            if not is_competition_active():
                if datetime.now(timezone.utc) > COMPETITION_END:
                    logger.info("Competition ended")
                    break
                time.sleep(60)
                continue
            
            if now - last_signal >= SIGNAL_CHECK_INTERVAL:
                check_trading_signals()
                _mark_progress()
                last_signal = now
            
            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                _mark_progress()
                resolve_opposite_sides()  # V3.1.55: Close losing side when both exist
                _mark_progress()  # V3.1.74: mark after resolve
                # V3.1.97: DISABLED regime exit + PM. TP/SL on WEEX handle exits.
                # regime_aware_exit_check()
                _mark_progress()
                # gemini_portfolio_review()
                _mark_progress()
                last_position = now
            
            if now - last_cleanup >= CLEANUP_CHECK_INTERVAL:
                quick_cleanup_check()
                last_cleanup = now
            
            if now - last_health >= HEALTH_CHECK_INTERVAL:
                log_health()
                _mark_progress()
                last_health = now
            
            time.sleep(5)
            _mark_progress()  # V3.1.77b: Mark every loop iteration so internal watchdog never fires during normal operation
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            logger.error(traceback.format_exc())
            state.errors += 1
            time.sleep(30)
    
    logger.info("Daemon shutdown")
    logger.info(f"Stats: {json.dumps(state.to_dict(), indent=2)}")


def handle_shutdown(signum, frame):
    logger.info(f"Signal {signum} - shutting down")
    state.is_running = False
    state.shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    run_daemon()