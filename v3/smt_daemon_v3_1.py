#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1.26 - HYBRID REGIME + RL DATA COLLECTION
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
SIGNAL_CHECK_INTERVAL = 15 * 60  # V3.1.34: 15 min - catch moves earlier
POSITION_MONITOR_INTERVAL = 2 * 60  # 2 minutes (check more often for tier 3)
HEALTH_CHECK_INTERVAL = 60
CLEANUP_CHECK_INTERVAL = 30

# Competition
COMPETITION_START = datetime(2026, 2, 8, 15, 0, 0, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 24, 20, 0, 0, tzinfo=timezone.utc)

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
        PIPELINE_VERSION, MODEL_NAME, MAX_OPEN_POSITIONS,
        MIN_CONFIDENCE_TO_TRADE,  # Added for trade filtering
        TIER_CONFIG, get_tier_for_symbol, get_tier_config,
        RUNNER_CONFIG, get_runner_config,
        
        # WEEX API
        get_price, get_balance, get_open_positions,
        get_account_equity,  # V3.1.19: For proper equity calculation
        upload_ai_log_to_weex,
        _rate_limit_gemini,
        
        # Position management
        check_position_status, cancel_all_orders_for_symbol,
        close_position_manually, execute_runner_partial_close,
        TradeTracker,
        
        # Competition
        get_competition_status,
        
        # Multi-persona
        MultiPersonaAnalyzer,
        
        # Trading
        execute_trade,
        
        # Logging
        save_local_log,
    )
    logger.info("V3.1.9 imports successful (Market Trend Filter + Stricter Signals)")
except ImportError as e:
    logger.error(f"Import error: {e}")
    logger.error(traceback.format_exc())
    sys.exit(1)

# V3.1.29: Pyramiding system import
try:
    from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid
    logger.info("Pyramiding system loaded")
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
_progress_lock = threading.Lock()

def _mark_progress():
    """Called by main loop to indicate the daemon is making progress."""
    global _last_progress_time
    with _progress_lock:
        _last_progress_time = time.time()

def _internal_watchdog():
    """Background thread that kills the process if it hangs > 10 minutes."""
    HANG_TIMEOUT = 1200  # V3.1.75: 20 minutes (was 10min, killed daemon mid-cycle during 8-pair analysis)
    while True:
        time.sleep(60)
        with _progress_lock:
            elapsed = time.time() - _last_progress_time
        if elapsed > HANG_TIMEOUT:
            logger.error(f"INTERNAL WATCHDOG: No progress for {elapsed:.0f}s. Force exit!")
            logger.error("The external watchdog.sh will restart us.")
            os._exit(1)  # Hard exit, watchdog.sh will restart

# Start internal watchdog as daemon thread
_watchdog_thread = threading.Thread(target=_internal_watchdog, daemon=True)
_watchdog_thread.start()


analyzer = MultiPersonaAnalyzer()

# V3.1.65: GLOBAL TRADE COOLDOWN - prevent fee bleed from rapid trading
_last_trade_opened_at = 0  # unix timestamp
GLOBAL_TRADE_COOLDOWN = 900  # V3.1.66c: 15 minutes (was 30min, too slow for competition)


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
    logger.info(f"V3.1.9 SIGNAL CHECK - {run_timestamp}")
    logger.info("=" * 60)
    
    state.signals_checked += 1
    state.last_signal_check = datetime.now(timezone.utc)
    
    try:
        # V3.1.19: Get proper account info with equity from API
        account_info = get_account_equity()
        balance = account_info["available"]
        equity = account_info["equity"]
        total_upnl = account_info["unrealized_pnl"]
        
        open_positions = get_open_positions()
        
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
        
        BASE_SLOTS = 5  # V3.1.75: Match MAX_OPEN_POSITIONS - 3 was starving the bot of diversification
        MAX_BONUS_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.53: Count positions from WEEX (the truth), not tracker
        weex_position_count = len(open_positions)  # This comes from allPosition API
        
        available_slots = effective_max_positions - weex_position_count
        
        # V3.1.53: Confidence override constants (restored)
        CONFIDENCE_OVERRIDE_THRESHOLD = 0.85
        MAX_CONFIDENCE_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute
        
        # V3.1.19: Override slot availability if low equity
        can_open_new = available_slots > 0 and not low_equity_mode
        
        if bonus_slots > 0:
            logger.info(f"Smart Slots: {weex_position_count}/{effective_max_positions} (base {BASE_SLOTS} + {bonus_slots} risk-free bonus)")
        
        if low_equity_mode:
            logger.info(f"Low equity mode - no new trades allowed")
        elif not can_open_new:
            logger.info(f"Max positions ({weex_position_count}/{effective_max_positions}) - Analysis only")
        
        ai_log = {
            "run_id": f"v3_1_7_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v3.1.55-opposite-whale",
            "tier_config": TIER_CONFIG,
            "analyses": [],
            "trades": [],
        }
        
        trade_opportunities = []
        
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
        
        # Build map: symbol -> {side: position}
        # This tracks BOTH long and short for each symbol
        position_map = {}
        for pos in open_positions:
            symbol = pos.get("symbol")
            side = pos.get("side", "").upper()
            if symbol not in position_map:
                position_map[symbol] = {}
            position_map[symbol][side] = pos
        
        # ANALYZE ALL PAIRS
        for pair, pair_info in TRADING_PAIRS.items():
            symbol = pair_info["symbol"]
            tier = pair_info.get("tier", 2)
            tier_config = get_tier_config(tier)
            
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

                # V3.1.74: EXTREME FEAR BTC SHIELD - BTC is too slow for shorts in fear bounces
                # F&G < 20 = contrarian BUY signal. BTC shorts at 20x leverage lose on fear bounces.
                # Altcoins (SOL, DOGE etc) are volatile enough for shorts to work.
                if signal == "SHORT" and pair == "BTC" and _fg_value < 20:
                    logger.info(f"    -> FEAR SHIELD: BTC SHORT blocked (F&G={_fg_value}). Contrarian BUY only for BTC in extreme fear.")
                    signal = "WAIT"
                    confidence = 0

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
                            can_trade_this = True
                            trade_type = "opposite"
                            logger.info(f"    -> OPPOSITE: LONG {confidence:.0%} >= SHORT {existing_conf:.0%}. Tighten SHORT SL + open LONG")
                        else:
                            logger.info(f"    -> Has SHORT at {existing_conf:.0%}, LONG {confidence:.0%} not stronger. Hold.")
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 HOLD: {symbol.replace('cmt_','').upper()} SHORT kept",
                                input_data={"symbol": symbol, "existing_side": "SHORT", "existing_conf": existing_conf, "new_signal": "LONG", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain SHORT position. Existing SHORT confidence ({existing_conf:.0%}) > new LONG signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new"

                elif signal == "SHORT":
                    if has_short:
                        logger.info(f"    -> Already SHORT")
                    elif has_long:
                        # V3.1.53: OPPOSITE - tighten LONG SL + open SHORT
                        long_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:LONG")
                        existing_conf = long_trade.get("confidence", 0.75) if long_trade else 0.75
                        if confidence >= existing_conf:  # V3.1.74: >= (was > which blocked 85% vs 85% flips)
                            can_trade_this = True
                            trade_type = "opposite"
                            logger.info(f"    -> OPPOSITE: SHORT {confidence:.0%} >= LONG {existing_conf:.0%}. Tighten LONG SL + open SHORT")
                        else:
                            logger.info(f"    -> Has LONG at {existing_conf:.0%}, SHORT {confidence:.0%} not stronger. Hold.")
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 HOLD: {symbol.replace('cmt_','').upper()} LONG kept",
                                input_data={"symbol": symbol, "existing_side": "LONG", "existing_conf": existing_conf, "new_signal": "SHORT", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain LONG position. Existing LONG confidence ({existing_conf:.0%}) > new SHORT signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new"
                
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
                    stage=f"V3.1.9 Analysis - {pair} (Tier {tier})",
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
        
# Execute ALL qualifying trades (up to available slots)
        if trade_opportunities:
            # Sort by confidence (highest first)
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            
            # V3.1.19: Use smart slot calculation (same as above)
            current_positions = len(open_positions)
            available_slots = effective_max_positions - current_positions
            # V3.1.64: HARD CAP - never exceed BASE_SLOTS total regardless of mode
            if current_positions >= BASE_SLOTS:
                available_slots = 0
            
            # V3.1.26: Track confidence override slots used
            confidence_slots_used = 0
            
            trades_executed = 0
            
            # V3.1.41: Count directional exposure BEFORE executing
            long_count = sum(1 for p in get_open_positions() if p.get("side","").upper() == "LONG")
            short_count = sum(1 for p in get_open_positions() if p.get("side","").upper() == "SHORT")
            MAX_SAME_DIRECTION = 5  # V3.1.55: was 99, caused all-LONG pileup  # V3.1.42: Recovery - match Gemini Judge rule 9
            # V3.1.43: Allow 7 LONGs during Capitulation (F&G < 15)
            if trade_opportunities:
                first_fg = trade_opportunities[0]["decision"].get("fear_greed", 50)
                if first_fg < 15:
                    MAX_SAME_DIRECTION = 7  # V3.1.56: capitulation allows 7
                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, keeping hard cap at BASE_SLOTS={BASE_SLOTS}")
            
            for opportunity in trade_opportunities:
                confidence = opportunity["decision"]["confidence"]
                
                # Check if we still have slots
                trade_type_check = opportunity.get("trade_type", "none")

                # V3.1.73: Opposite trades bypass slot check by closing existing position first
                # Only if existing position is profitable (avoids realizing losses + fees)
                if trade_type_check == "opposite" and trades_executed >= available_slots:
                    opp_symbol = opportunity["pair_info"]["symbol"]
                    opp_signal = opportunity["decision"]["decision"]
                    existing_side = "SHORT" if opp_signal == "LONG" else "LONG"

                    # Find existing position PnL
                    existing_pos = None
                    for _p in open_positions:
                        if _p.get("symbol") == opp_symbol and _p.get("side", "").upper() == existing_side:
                            existing_pos = _p
                            break

                    existing_pnl = float(existing_pos.get("unrealized_pnl", 0)) if existing_pos else 0

                    if existing_pnl > 0:
                        # Close profitable existing position to free slot for opposite trade
                        logger.info(f"OPPOSITE SWAP: Closing {opp_symbol} {existing_side} (PnL ${existing_pnl:+.1f}) to flip to {opp_signal}")
                        close_result = close_position_manually(
                            symbol=opp_symbol,
                            side=existing_side,
                            size=existing_pos.get("size", 0)
                        )
                        tracker.close_trade(opp_symbol, {
                            "reason": f"opposite_swap_{opp_signal.lower()}",
                            "pnl": existing_pnl,
                            "final_pnl_pct": 0,  # Will be filled by RL
                        })
                        upload_ai_log_to_weex(
                            stage=f"V3.1.73 OPPOSITE SWAP: {opp_symbol.replace('cmt_','').upper()} {existing_side} -> {opp_signal}",
                            input_data={"symbol": opp_symbol, "old_side": existing_side, "new_signal": opp_signal, "old_pnl": existing_pnl},
                            output_data={"action": "SWAP", "confidence": confidence},
                            explanation=f"Closed profitable {existing_side} (PnL ${existing_pnl:+.1f}) to flip to {opp_signal} at {confidence:.0%} confidence."
                        )
                        state.trades_closed += 1
                        available_slots += 1  # Freed a slot
                    else:
                        logger.info(f"OPPOSITE TRADE: {opportunity['pair']} {existing_side} losing ${existing_pnl:.1f}, won't realize loss (tighten SL instead)")
                        # TODO: could tighten SL on losing position instead
                        continue

                if trades_executed >= available_slots:
                    # V3.1.26: High confidence override - can use extra slots
                    if confidence >= CONFIDENCE_OVERRIDE_THRESHOLD and confidence_slots_used < MAX_CONFIDENCE_SLOTS:
                        logger.info(f"CONFIDENCE OVERRIDE: {confidence:.0%} >= 85% - using conviction slot {confidence_slots_used + 1}/{MAX_CONFIDENCE_SLOTS}")
                        confidence_slots_used += 1
                    else:
                        logger.info(f"Max positions reached, skipping {opportunity['pair']}")
                        break
                
                # V3.1.41: DIRECTIONAL LIMIT CHECK
                sig_check = opportunity["decision"]["decision"]
                if sig_check == "LONG" and long_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {long_count} LONGs already open, skipping {opportunity['pair']} LONG")
                    continue
                if sig_check == "SHORT" and short_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {short_count} SHORTs already open, skipping {opportunity['pair']} SHORT")
                    continue
                
                # V3.1.51: SESSION-AWARE TRADING with confidence adjustments
                import datetime as _dt_module
                utc_hour = _dt_module.datetime.now(_dt_module.timezone.utc).hour
                opp_confidence = opportunity["decision"]["confidence"]
                opp_fear_greed = opportunity["decision"].get("fear_greed", 50)
                is_extreme_fear = opp_fear_greed < 20
                
                # Session classification and confidence threshold
                if 13 <= utc_hour < 16:
                    session_name = "US_OPEN"
                    session_min_conf = 0.70  # Best liquidity, lower bar
                elif 0 <= utc_hour < 3:
                    session_name = "ASIA_OPEN"
                    session_min_conf = 0.75  # Good liquidity
                elif 6 <= utc_hour < 9:
                    session_name = "DEAD_HOURS"
                    session_min_conf = 0.82  # Low liquidity, higher bar
                elif 0 <= utc_hour < 6:
                    session_name = "ASIA"
                    session_min_conf = 0.78
                else:
                    session_name = "ACTIVE"
                    session_min_conf = 0.70  # Normal hours
                
                # V3.1.64a: REMOVED extreme fear floor override
                # 85% confidence floor is absolute - no exceptions
                if is_extreme_fear:
                    logger.info(f"EXTREME FEAR: F&G={opp_fear_greed}, but 85% floor stays for {opportunity['pair']}")
                
                if opp_confidence < session_min_conf:
                    logger.warning(f"SESSION FILTER [{session_name}]: {utc_hour}:00 UTC, {opp_confidence:.0%} < {session_min_conf:.0%}, skipping {opportunity['pair']}")
                    continue
                else:
                    logger.info(f"SESSION [{session_name}]: {utc_hour}:00 UTC, {opp_confidence:.0%} >= {session_min_conf:.0%}, proceeding {opportunity['pair']}")
                
                # V3.1.75: REGIME VETO - only active in NORMAL conditions
                # DISABLED in extreme fear (F&G<20): contrarian buys ARE the strategy,
                # vetoing LONGs in BEARISH+extreme fear contradicts our edge
                # Only block on SPIKE events (sudden 1h moves) which are genuine danger
                try:
                    _regime_now = get_market_regime_for_exit()
                except Exception as _re:
                    logger.warning(f"REGIME VETO: regime check failed ({_re}), allowing trade")
                    _regime_now = {"regime": "NEUTRAL"}
                _regime_label = _regime_now.get("regime", "NEUTRAL")
                _opp_signal = opportunity["decision"]["decision"]
                _regime_vetoed = False

                # V3.1.75: Skip regime veto entirely in extreme fear - contrarian mode
                _fg_for_veto = opportunity["decision"].get("fear_greed", 50)
                if _fg_for_veto < 20:
                    logger.info(f"REGIME VETO SKIP: F&G={_fg_for_veto} (extreme fear), allowing {_opp_signal} on {opportunity['pair']}")
                else:
                    # Only veto on SPIKE events (sudden 1h moves)
                    if _regime_label in ("SPIKE_UP",) and _opp_signal == "SHORT":
                        _regime_vetoed = True
                        logger.warning(f"REGIME VETO: SPIKE_UP, blocking SHORT on {opportunity['pair']}")
                    elif _regime_label in ("SPIKE_DOWN",) and _opp_signal == "LONG":
                        _regime_vetoed = True
                        logger.warning(f"REGIME VETO: SPIKE_DOWN, blocking LONG on {opportunity['pair']}")

                if _regime_vetoed:
                    upload_ai_log_to_weex(
                        stage=f"V3.1.75 REGIME VETO: {_opp_signal} {opportunity['pair']} blocked",
                        input_data={"regime": _regime_label, "signal": _opp_signal, "pair": opportunity['pair']},
                        output_data={"action": "VETOED", "reason": f"SPIKE event in {_regime_label}"},
                        explanation=f"V3.1.75: Only SPIKE events trigger regime veto. Blocked {_opp_signal} on {opportunity['pair']} during {_regime_label}."
                    )
                    continue

                # V3.1.68: INTER-CYCLE COOLDOWN (not intra-cycle)
                # Only block if the last trade was from a PREVIOUS cycle
                _now_cooldown = time.time()
                _cooldown_elapsed = _now_cooldown - _last_trade_opened_at
                if _cooldown_elapsed < GLOBAL_TRADE_COOLDOWN and _cooldown_elapsed > 120:
                    # More than 2min since last trade = different cycle, apply cooldown
                    _cd_remaining = GLOBAL_TRADE_COOLDOWN - _cooldown_elapsed
                    logger.info(f"GLOBAL COOLDOWN: {_cd_remaining:.0f}s remaining, skipping {opportunity['pair']}")
                    continue
                # Within same cycle (< 2min gap) = allow multiple trades

                tier = opportunity["tier"]
                tier_config = get_tier_config(tier)
                trade_type = opportunity["trade_type"]
                pair = opportunity["pair"]
                signal = opportunity["decision"]["decision"]
                confidence = opportunity["decision"]["confidence"]
                
                logger.info(f"")
                type_label = "[FLIP] " if trade_type == "flip" else ""
                logger.info(f"EXECUTING {type_label}{pair} {signal} (T{tier}) - {confidence:.0%}")
                
                # V3.1.53: OPPOSITE SIDE - tighten SL on existing + open new direction
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
                            current_price = get_price(sym)
                            
                            # TIGHTEN SL: Move SL to 50% of original distance from current price
                            # This gives the old position a short leash - it'll close itself if wrong
                            from smt_nightly_trade_v3_1 import round_price_to_tick, cancel_all_orders_for_symbol, place_order, round_size_to_step
                            
                            if opp_side == "LONG":
                                # LONG position - tighten SL upward (closer to current price)
                                old_sl_dist = opp_entry * 0.02  # Original ~2% SL
                                new_sl = round_price_to_tick(current_price * 0.992, sym)  # 0.8% SL
                                # Don't set SL above entry (would be instant loss lock)
                                new_sl = max(new_sl, round_price_to_tick(opp_entry * 0.995, sym))
                            else:
                                # SHORT position - tighten SL downward (closer to current price)
                                new_sl = round_price_to_tick(current_price * 1.008, sym)  # 0.8% SL
                                # Don't set SL below entry
                                new_sl = min(new_sl, round_price_to_tick(opp_entry * 1.005, sym))
                            
                            logger.info(f"  [SL TIGHTEN] {opp_side} {pair}: Moving SL to ${new_sl:.4f} (entry: ${opp_entry:.4f}, current: ${current_price:.4f})")
                            
                            # Cancel old TP/SL and place new tighter ones
                            try:
                                # We can't easily modify just the SL on WEEX, 
                                # so we cancel all orders and re-place with tight SL
                                cancel_result = cancel_all_orders_for_symbol(sym)
                                
                                # Re-place the existing position's TP/SL with tighter SL
                                # Use plan order or just let the monitor handle it
                                # For now, the position will be monitored with tighter threshold
                                
                                # Update tracker with tighter SL
                                trade_key = sym
                                existing_trade = tracker.get_active_trade(trade_key)
                                if not existing_trade:
                                    trade_key = f"{sym}:{opp_side}"
                                    existing_trade = tracker.get_active_trade(trade_key)
                                
                                if existing_trade:
                                    existing_trade["sl_tightened"] = True
                                    existing_trade["sl_tightened_at"] = datetime.now(timezone.utc).isoformat()
                                    existing_trade["sl_price"] = new_sl
                                    existing_trade["tighten_reason"] = f"Opposite {signal} at {confidence:.0%}"
                                    tracker.save_state()
                                

                                # V3.1.56: ACTUALLY PLACE SL ORDER ON WEEX
                                try:
                                    close_type = "3" if opp_side == "LONG" else "4"  # 3=close long, 4=close short
                                    opp_size = float(opp_pos.get("size", 0))
                                    plan_order_endpoint = "/capi/v2/order/plan_order"
                                    sl_body = json.dumps({
                                        "symbol": sym,
                                        "client_oid": f"smt_tighten_{int(time.time()*1000)}",
                                        "size": str(opp_size),
                                        "type": close_type,
                                        "match_type": "1",
                                        "execute_price": "0",
                                        "trigger_price": str(new_sl)
                                    })
                                    import requests as req_mod
                                    from smt_nightly_trade_v3_1 import WEEX_BASE_URL, weex_headers
                                    sl_r = req_mod.post(f"{WEEX_BASE_URL}{plan_order_endpoint}",
                                                       headers=weex_headers("POST", plan_order_endpoint, sl_body),
                                                       data=sl_body, timeout=15)
                                    logger.info(f"  [SL TIGHTEN] Placed SL order on WEEX: {sl_r.status_code} {sl_r.text[:100]}")
                                except Exception as place_err:
                                    logger.warning(f"  [SL TIGHTEN] Could not place SL order: {place_err}")
                                logger.info(f"  [SL TIGHTEN] Done. {opp_side} will close soon via tight SL.")
                                _sl_tightened_symbols[sym] = datetime.now(timezone.utc)
                            except Exception as sl_err:
                                logger.warning(f"  [SL TIGHTEN] Could not tighten: {sl_err}")
                            
                            # Upload AI log for SL tightening
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 Directional Shift: {pair} {opp_side}->SL tightened",
                                input_data={
                                    "symbol": sym,
                                    "existing_side": opp_side,
                                    "existing_size": opp_size,
                                    "existing_entry": opp_entry,
                                    "existing_pnl": round(opp_pnl, 2),
                                    "new_signal": signal,
                                    "new_confidence": confidence,
                                },
                                output_data={
                                    "action": "SL_TIGHTENED",
                                    "new_sl": new_sl,
                                    "reason": "opposite_signal_stronger",
                                },
                                explanation=f"AI detected directional shift on {pair}. New {signal} signal at {confidence:.0%} is stronger than existing {opp_side}. Tightened {opp_side} stop-loss to ${new_sl:.4f} (was wider). {opp_side} will exit soon. Opening {signal} to capture new direction."
                            )
                    except Exception as e:
                        logger.error(f"  [OPPOSITE] Error tightening SL: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                
                try:
                    trade_result = run_with_retry(
                        execute_trade,
                        opportunity["pair_info"], opportunity["decision"], balance
                    )
                    
                    if trade_result.get("executed"):
                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        
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
                        
                        # Update available balance for next trade
                        balance = get_balance()
                        
                    else:
                        logger.warning(f"Trade failed: {trade_result.get('reason')}")
                        
                except Exception as e:
                    logger.error(f"Error executing {pair}: {e}")
                
                time.sleep(3)  # V3.1.68: 3s delay between trades (Gemini rate limit)
            
            if trades_executed > 0:
                logger.info(f"")
                logger.info(f"Executed {trades_executed} trades this cycle")
        else:
            logger.info(f"")
            logger.info("No trade opportunities")        
        save_local_log(ai_log, run_timestamp)
        
    except Exception as e:
        logger.error(f"Signal check error: {e}")
        logger.error(traceback.format_exc())
        state.errors += 1


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
                    
                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "pnl": actual_pnl,
                        "pnl_pct": pnl_pct
                    })
                    state.trades_closed += 1
                    
                    # V3.1.59: Clean PnL history for closed position
                    if symbol in _pnl_history:
                        del _pnl_history[symbol]
                    
                    # V3.1.36: AI log for TP/SL closes
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    hit_tp = actual_pnl > 0
                    exit_type = "TAKE_PROFIT" if hit_tp else "STOP_LOSS"
                    try:
                        hours_open = 0
                        if trade.get("opened_at"):
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                        
                        upload_ai_log_to_weex(
                            stage=f"V3.1.36 {exit_type}: {side} {symbol_clean}",
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
                            explanation=f"Position closed via {exit_type}. {side} {symbol_clean} held {hours_open:.1f}h. PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%). Entry: ${entry_price:.4f}."
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
                
                # V3.1.41: Get entry confidence for adaptive guards
                entry_confidence = trade.get("confidence", 0.75)
                confidence_multiplier = 1.3 if entry_confidence >= 0.85 else 1.0  # High-conf trades get 30% wider guards
                
                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%) conf={entry_confidence:.0%}")
                
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
                # V3.1.75: PROFIT LOCK - less aggressive, let winners breathe
                # Previous version closed at 1% peak with 40% fade - way too tight
                fade_pct = peak_pnl_pct - pnl_pct if peak_pnl_pct > 0 else 0

                # Rule 1: High peak, deep fade -> lock profits
                # If peaked > 2.0% and dropped more than 50% from peak
                if not should_exit and peak_pnl_pct >= 2.0 and pnl_pct < peak_pnl_pct * 0.50 and pnl_pct > 0:
                    should_exit = True
                    exit_reason = f"V3.1.75_peak_fade T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%)"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")

                # Rule 2: Any significant peak that went negative -> thesis broken
                elif not should_exit and peak_pnl_pct >= 1.5 and pnl_pct <= 0:
                    should_exit = True
                    exit_reason = f"V3.1.75_peak_to_loss T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}%"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")
                

                # 1. Max hold time exceeded (tier-specific)
                if hours_open >= max_hold:
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
                            exit_type = "PROFIT_GUARD" if "profit_guard" in exit_reason else \
                                       "TIMEOUT" if "max_hold" in exit_reason else \
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
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1.9 Smart Exit - {symbol_clean}",
                        input_data={"symbol": symbol, "tier": tier, "hours_open": hours_open},
                        output_data={"reason": exit_reason, "pnl_pct": pnl_pct},
                        explanation=f"Tier {tier} smart exit: {exit_reason}. PnL: {pnl_pct:+.2f}%"
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


# V3.1.60: Track symbols with recent SL tightens - prevent resolve_opposite from killing new trades
_sl_tightened_symbols = {}


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
                        stage=f"V3.1.53 Position Optimization: Close dust {side} {symbol_clean}",
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
            
            pos_details.append(
                f"- {sym} {side}: PnL=${pnl:.2f} ({pnl_pct:+.1f}%), entry=${entry:.4f}, margin=${margin:.1f}, held={hours_open:.1f}h, tier={tier}"
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
        
        # V3.1.59: AI log for PM review start
        upload_ai_log_to_weex(
            stage=f"V3.1.59 Portfolio Review Start",
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
            explanation=f"Portfolio Manager reviewing {len(positions)} positions. Equity: ${equity:.0f}. {cryptoracle_context[:150]}"
        )
        
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

=== PNL TRAJECTORY PATTERNS ===
Each position shows its last 5 PnL readings (newest last).
Look for: fading from peak (consider tightening), accelerating (let run), flat (stale trade).
If Cryptoracle sentiment supports the position direction, be more patient even if fading.

=== MANDATORY RULES (V3.1.59 - 47+ iterations of battle-tested experience) ===

RULE 1 - OPPOSITE SIDE RESOLUTION (HIGHEST PRIORITY):
If the SAME symbol has BOTH a LONG and SHORT position open, IMMEDIATELY close the losing side.
This is NOT optional. Two-sided positions on the same pair waste margin, cancel each other out,
and occupy 2 slots instead of 1. Close the side with worse PnL. No exceptions.

RULE 2 - DIRECTIONAL CONCENTRATION LIMIT:
Max 5 positions in the same direction normally. If 6+ LONGs or 6+ SHORTs, close the WEAKEST ones
(lowest PnL% or highest loss) until we have max 5.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 7 LONGs. Violent bounces move all alts together.

RULE 3 - LET WINNERS RUN:
PROFIT LOCK RULE (V3.1.64): If a position peaked > 1.0% and faded > 40% from peak (still green), CLOSE IT to lock profit. At 18x leverage, 1% captured = 18% ROE. Do NOT let winners become losers. Banking small wins repeatedly beats waiting for huge TPs.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours.

RULE 3b - TRAJECTORY-BASED EXIT (V3.1.59):
If a position's PnL trajectory shows 5+ readings of steady decline from a peak > 1.0%
(e.g. +1.8% -> +1.5% -> +1.2% -> +0.9% -> +0.6%), the trade thesis may be invalidating.
Consider closing to lock partial profit UNLESS Cryptoracle sentiment still supports the direction.
If Cryptoracle momentum is positive for our direction, let it ride despite the fade.

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

RULE 6 - BREAKEVEN PATIENCE:
If a position has faded to breakeven (within +/- 0.3%), DO NOT CLOSE. Crypto is volatile.
A position at 0% can rally to +5% in the next hour. Only the SL should close breakeven positions.

RULE 7 - TIME-BASED PATIENCE:
Do NOT close positions just because they have been open 2-4 hours.
Our TP targets are 2-2.5%. These moves take TIME (4-12h for alts, 12-24h for BTC).
Only close if: max_hold_hours exceeded AND position is negative.

RULE 8 - WEEKEND/LOW LIQUIDITY (check if Saturday or Sunday):
On weekends, max 4 positions. Thinner books = more manipulation.

RULE 9 - SLOT MANAGEMENT (UPDATED V3.1.55):
If we have 7+ total positions, we SHOULD free slots by closing the weakest position.
7+ positions means we are over-committed and have no room for high-conviction entries.
Close the position with: worst PnL% + whale disagreement + longest hold time past max.
If we have 5 or fewer, be patient - SL orders protect us.

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

RULE 13 - STALE POSITION CLEANUP:
If a position has been held > max_hold_hours AND is losing ANY amount, close it.
Do not wait for -3%. A stale losing position is dead capital. Free it.
If the position is winning past max_hold_hours, let it run but tighten expectations.

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
            logger.info(f"[PORTFOLIO] Gemini: Keep all. {keep_reasons[:100]}")
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
                    
                    logger.warning(f"[PORTFOLIO] Closing {sym_clean} {close_side}: {close_reason}")
                    
                    close_result = close_position_manually(sym, close_side, size)
                    order_id = close_result.get("order_id")
                    
                    if order_id:
                        logger.info(f"[PORTFOLIO] Closed {sym_clean} {close_side}: order {order_id}")
                        
                        # Upload AI log
                        upload_ai_log_to_weex(
                            stage=f"V3.1.40 Portfolio Manager: Close {close_side} {sym_clean}",
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
                            explanation=f"AI Portfolio Manager closed {close_side} {sym_clean}: {close_reason}. Equity: ${equity:.0f}, F&G: {regime.get('fear_greed',50)}, Regime: {regime.get('regime','NEUTRAL')}. Freeing slot for better opportunities."
                        )
                        
                        # Update tracker
                        try:
                            tracker.close_trade(sym, {
                                "reason": f"portfolio_manager_{close_reason[:30]}",
                                "pnl": pnl,
                            })
                        except:
                            pass
                        
                        state.trades_closed += 1
                    else:
                        logger.warning(f"[PORTFOLIO] Close failed for {sym_clean}: {close_result}")
                    
                    time.sleep(1)
                    break
        
        logger.info(f"[PORTFOLIO] Review complete. Keep reasons: {keep_reasons[:100]}")
        
        # V3.1.59: AI log for PM decision
        upload_ai_log_to_weex(
            stage=f"V3.1.59 Portfolio Review Decision",
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
                
                tracker.close_trade(symbol, {
                    "reason": "tp_sl_hit",
                    "cleanup": cleanup,
                    "symbol": symbol,
                    "pnl": round(pnl_usd, 2),
                    "hit_tp": hit_tp,
                })
                state.trades_closed += 1
                position_closed = True
                
                # V3.1.36: AI log for quick cleanup closes
                symbol_clean = symbol.replace("cmt_", "").upper()
                exit_type = "TAKE_PROFIT" if hit_tp else "STOP_LOSS"
                try:
                    hours_open = 0
                    if trade.get("opened_at"):
                        opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                        hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1.36 {exit_type}: {side} {symbol_clean}",
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
                        explanation=f"Position closed via {exit_type}. {side} {symbol_clean} held {hours_open:.1f}h. PnL: ${pnl_usd:.2f}."
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
    uptime = datetime.now(timezone.utc) - state.started_at
    uptime_str = str(uptime).split('.')[0]
    active = len(tracker.get_active_symbols())
    
    logger.info(
        f"V3.1.9 HEALTH | Up: {uptime_str} | "
        f"Signals: {state.signals_checked} | "
        f"Trades: {state.trades_opened}/{state.trades_closed} | "
        f"Runners: {state.runners_triggered} | "
        f"Active: {active}"
    )
    
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
                
                tracker.active_trades[key_sided] = {
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "side": side,
                    "entry_price": float(pos.get('entry_price', 0)),
                    "tier": tier,
                    "max_hold_hours": tier_config['max_hold_hours'],
                    "synced": True,
                    "confidence": 0.75,
                }
                added += 1
                logger.info(f"  Added {key_sided} (Tier {tier}, {side} @ {float(pos.get('entry_price',0)):.4f})")
            elif key_sided not in tracker_keys and sym in tracker_keys:
                # Tracked under plain symbol but side might be wrong - check
                existing = tracker.active_trades.get(sym, {})
                existing_side = existing.get('side', '').upper()
                if existing_side and existing_side != side:
                    # Different side! Track this one separately
                    tracker.active_trades[key_sided] = {
                        "opened_at": datetime.now(timezone.utc).isoformat(),
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
        
    except Exception as e:
        logger.error(f"Sync error: {e}")


def resolve_opposite_sides():
    """V3.1.55: If same symbol has BOTH Long and Short open, close the losing side.
    
    This is a mechanical rule - no Gemini needed. Two sides on same pair is
    capital-inefficient and indicates the system changed its mind but didn't
    clean up. Always close the losing side.
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
            
            # V3.1.60: Skip if SL was recently tightened (opposite trade in progress)
            if sym in _sl_tightened_symbols:
                tighten_time = _sl_tightened_symbols[sym]
                age_min = (datetime.now(timezone.utc) - tighten_time).total_seconds() / 60
                if age_min < 15:
                    sym_c = sym.replace('cmt_', '').upper()
                    logger.info(f"  [OPPOSITE] Skipping {sym_c}: SL tightened {age_min:.0f}m ago, waiting")
                    continue
                else:
                    del _sl_tightened_symbols[sym]
            
            long_pnl = float(long_pos.get('unrealized_pnl', 0))
            short_pnl = float(short_pos.get('unrealized_pnl', 0))
            sym_clean = sym.replace('cmt_', '').upper()
            
            logger.info(f"  [OPPOSITE] {sym_clean}: LONG PnL=${long_pnl:.2f}, SHORT PnL=${short_pnl:.2f}")
            
            # Close the losing side (or smaller PnL if both positive)
            if long_pnl <= short_pnl:
                # Close LONG (it's the loser or smaller winner)
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
                    stage=f"V3.1.55 Opposite-Side Resolution: Close {close_side} {sym_clean}",
                    input_data={
                        "symbol": sym,
                        "long_pnl": long_pnl,
                        "short_pnl": short_pnl,
                        "closing_side": close_side,
                        "keeping_side": keep_side,
                    },
                    output_data={"action": "CLOSE_OPPOSITE", "order_id": oid},
                    explanation=f"AI detected both LONG (PnL=${long_pnl:.2f}) and SHORT (PnL=${short_pnl:.2f}) open on {sym_clean}. Closing {close_side} side to eliminate capital-inefficient hedge and free margin for the winning {keep_side} position.",
                    order_id=oid
                )
                
                # Remove from tracker
                for key in [f"{sym}:{close_side}", sym]:
                    if key in tracker.active_trades:
                        tracker.close_trade(key, {"reason": "opposite_side_resolution", "pnl": close_pnl})
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
            # V3.1.30: Percentage-based exit thresholds
            regime_fight_threshold = -(margin * 0.15)   # V3.1.47: Raised to 15% - trust SL
            hard_stop_threshold = -(margin * 0.25)      # V3.1.47: Raised to 25% - let SL on WEEX handle it
            spike_threshold = -(margin * 0.015)          # -1.5% of margin
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
            
            # V3.1.64: EXTREME FEAR SHIELD - Don't cut profitable positions in capitulation
            # In F&G < 20, regime bounces are noise. Trust whale-aligned positions.
            _fg_for_regime = regime.get("fear_greed", 50)
            if _fg_for_regime < 20 and pnl > 0:
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
                tracker.close_trade(symbol, {
                    "reason": f"regime_exit_{regime['regime'].lower()}",
                    "pnl": pnl,
                    "regime": regime["regime"],
                })
                
                state.trades_closed += 1
                state.early_exits += 1
                closed_count += 1
                
                # Upload AI log
                upload_ai_log_to_weex(
                    stage=f"V3.1.9 Regime Exit: {side} {symbol_clean}",
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
    logger.info("SMT Daemon V3.1.75 - DISCIPLINE RESTORATION")
    logger.info("=" * 60)
    logger.info("V3.1.75 Changes:")
    logger.info("  - REGIME VETO: Only on SPIKE events (was blocking contrarian trades in fear)")
    logger.info("  - SIZING: 12% base, max 20% (was 40%/50% - suicidal)")
    logger.info("  - LEVERAGE: T1=15x T2=12x T3=10x (was 18-20x everything)")
    logger.info("  - PROFIT LOCK: peak>=2.0% fade>50% (was 1.0%/40% too tight)")
    logger.info("  - SLOTS: 5 positions (was 3)")
    logger.info("  - WATCHDOG: 20min internal, 15min external")
    logger.info("  - R:R: T1=3/1.5% T2=3.5/1.5% T3=3/1.8%")
    logger.info("  - Max 5 positions, 85% confidence floor")
    logger.info("Tier Configuration:")
    for tier, config in TIER_CONFIG.items():
        tier_config = TIER_CONFIG[tier]
        pairs = [p for p, info in TRADING_PAIRS.items() if info["tier"] == tier]
        runner = RUNNER_CONFIG.get(tier, {})
        runner_str = f"Runner: +{runner.get('trigger_pct', 0)}% -> close 50%" if runner.get("enabled") else "No Runner"
        logger.info(f"  Tier {tier}: {', '.join(pairs)}")
        logger.info(f"    TP: {tier_config['take_profit']*100:.1f}%, SL: {tier_config['stop_loss']*100:.1f}%, Hold: {tier_config['max_hold_hours']}h | {runner_str}")
    logger.info("Cooldown Override: 85%+ confidence bypasses cooldown")
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
                regime_aware_exit_check()  # V3.1.9: Check for regime-fighting positions
                _mark_progress()  # V3.1.74: mark after regime check
                gemini_portfolio_review()  # V3.1.55: Gemini reviews portfolio with whale-aware rules
                _mark_progress()  # V3.1.74: mark after PM review
                last_position = now
            
            if now - last_cleanup >= CLEANUP_CHECK_INTERVAL:
                quick_cleanup_check()
                last_cleanup = now
            
            if now - last_health >= HEALTH_CHECK_INTERVAL:
                log_health()
                _mark_progress()
                last_health = now
            
            time.sleep(5)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            logger.error(traceback.format_exc())
            state.errors += 1
            time.sleep(30)
    
    logger.info("V3.1.9 Daemon shutdown")
    logger.info(f"Stats: {json.dumps(state.to_dict(), indent=2)}")


def handle_shutdown(signum, frame):
    logger.info(f"Signal {signum} - shutting down")
    state.is_running = False
    state.shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    run_daemon()