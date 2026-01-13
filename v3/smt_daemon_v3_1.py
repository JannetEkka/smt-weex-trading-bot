#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1
=======================
Multi-persona trading daemon with smarter exit logic.

Features:
- 4 Personas: WHALE, SENTIMENT, FLOW, TECHNICAL
- Judge makes final decision based on weighted votes
- Smarter exits: cut losers early, protect profits
- 2-hour signal interval
- AI logs with order_id for "AI order" display

Run: python3 smt_daemon_v3_1.py
Service: sudo systemctl start smt-trading-v31

Logs: logs/daemon_v3_1_YYYYMMDD.log
"""

import os
import sys
import json
import time
import signal
import logging
import traceback
from datetime import datetime, timezone, timedelta
from threading import Event
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# V3.1 CONFIGURATION
# ============================================================

# Timing
SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes       # 2 hours (balanced - not too aggressive)
POSITION_MONITOR_INTERVAL = 3 * 60         # 3 minutes (check more often for exits)
HEALTH_CHECK_INTERVAL = 60                 # 1 minute
CLEANUP_CHECK_INTERVAL = 30                # 30 seconds

# Smart Exit Thresholds
EARLY_EXIT_CHECK_HOURS = 3                 # Start checking after 3 hours
EARLY_EXIT_LOSS_PCT = -2.5                 # Exit if losing more than 2.5% after 3h
MAX_HOLD_HOURS_DEFAULT = 24                # Reduced from 48h - faster turnover
MAX_HOLD_HOURS_FINAL = 6                   # Final days of competition

# Competition
COMPETITION_START = datetime(2026, 1, 12, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 2, 23, 59, 59, tzinfo=timezone.utc)

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
    log_file = os.path.join(LOG_DIR, f"daemon_v3_1_{datetime.now().strftime('%Y%m%d')}.log")
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)
    logger.handlers = []
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()


# ============================================================
# IMPORTS
# ============================================================

try:
    from smt_nightly_trade_v3_1 import (
        # Config
        TEST_MODE, TRADING_PAIRS, MAX_LEVERAGE, STARTING_BALANCE,
        PIPELINE_VERSION, MODEL_NAME, MAX_OPEN_POSITIONS,
        
        # WEEX API
        get_price, get_balance, get_open_positions,
        upload_ai_log_to_weex,
        
        # Position management
        check_position_status, cancel_all_orders_for_symbol,
        close_position_manually, TradeTracker,
        
        # Competition
        get_competition_status,
        
        # V3.1: Multi-persona
        MultiPersonaAnalyzer,
        
        # Trading
        execute_trade,
        
        # Logging
        save_local_log,
    )
    logger.info("V3.1 imports successful")
except ImportError as e:
    logger.error(f"Import error: {e}")
    sys.exit(1)


# ============================================================
# DAEMON STATE
# ============================================================

class DaemonState:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.last_signal_check = None
        self.last_position_check = None
        
        self.signals_checked = 0
        self.trades_opened = 0
        self.trades_closed = 0
        self.early_exits = 0
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
            "errors": self.errors,
        }

state = DaemonState()
tracker = TradeTracker(state_file="trade_state_v3_1.json")
analyzer = MultiPersonaAnalyzer()


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


def get_max_hold_hours() -> int:
    now = datetime.now(timezone.utc)
    days_left = (COMPETITION_END - now).days
    if days_left <= 3:
        return MAX_HOLD_HOURS_FINAL
    return MAX_HOLD_HOURS_DEFAULT


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
# V3.1 SIGNAL CHECKING (Multi-Persona)
# ============================================================

def check_trading_signals():
    """V3.1: Multi-persona signal check"""
    
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    logger.info("=" * 60)
    logger.info(f"V3.1 SIGNAL CHECK - {run_timestamp}")
    logger.info("=" * 60)
    
    state.signals_checked += 1
    state.last_signal_check = datetime.now(timezone.utc)
    
    try:
        balance = get_balance()
        open_positions = get_open_positions()
        competition = get_competition_status(balance)
        
        logger.info(f"Balance: {balance:.2f} USDT")
        logger.info(f"Open positions: {len(open_positions)}")
        logger.info(f"Days left: {competition['days_left']}")
        
        available_slots = MAX_OPEN_POSITIONS - len(open_positions)
        if available_slots <= 0:
            logger.info("Max positions reached")
            return
        
        
        ai_log = {
            "run_id": f"v3_1_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v3.1",
            "personas": ["WHALE", "SENTIMENT", "FLOW", "TECHNICAL", "JUDGE"],
            "trades": [],
        }
        
        # Evaluate all pairs with multi-persona
        trade_opportunities = []
        
        for pair, pair_info in TRADING_PAIRS.items():
            # Skip if already have position
            if any(p.get("symbol") == pair_info["symbol"] for p in open_positions):
                # logger.info(f"  {pair}: Already have position")  # DISABLED
                continue
            
            try:
                # Multi-persona analysis
                decision = run_with_retry(
                    analyzer.analyze,
                    pair, pair_info, balance, competition, open_positions
                )
                
                logger.info(f"  {pair}: {decision.get('decision')} ({decision.get('confidence', 0):.0%})")
                
                if decision.get("decision") in ("LONG", "SHORT") and decision.get("confidence", 0) >= 0.60:
                    trade_opportunities.append({
                        "pair": pair,
                        "pair_info": pair_info,
                        "decision": decision,
                    })
                
                # Upload analysis log (no order_id yet)
                # Build explanation with BOTH judge summary AND market context
                judge_summary = decision.get("reasoning", "")
                market_ctx = ""
                for vote in decision.get("persona_votes", []):
                    if vote.get("persona") == "SENTIMENT" and vote.get("market_context"):
                        market_ctx = vote.get("market_context", "")[:300]
                        break
                
                full_explanation = f"{judge_summary} | Market: {market_ctx}" if market_ctx else judge_summary
                
                upload_ai_log_to_weex(
                    stage=f"V3.1 Analysis - {pair}",
                    input_data={
                        "pair": pair,
                        "balance": balance,
                        "personas": ["WHALE", "SENTIMENT", "FLOW", "TECHNICAL"],
                    },
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence", 0),
                        "votes": decision.get("vote_breakdown", {}),
                    },
                    explanation=full_explanation[:500]
                )
                
                time.sleep(2)  # Rate limit
                
            except Exception as e:
                logger.error(f"Error analyzing {pair}: {e}")
        
        # Execute best trade
        if trade_opportunities:
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            best = trade_opportunities[0]
            
            logger.info(f"Executing: {best['pair']} {best['decision']['decision']}")
            
            trade_result = run_with_retry(
                execute_trade,
                best["pair_info"], best["decision"], balance
            )
            
            if trade_result.get("executed"):
                logger.info(f"Trade executed: {trade_result.get('order_id')}")
                
                tracker.add_trade(best["pair_info"]["symbol"], trade_result)
                state.trades_opened += 1
                ai_log["trades"].append(trade_result)
            else:
                logger.warning(f"Trade failed: {trade_result.get('reason')}")
        else:
            logger.info("No trade opportunities")
        
        save_local_log(ai_log, run_timestamp)
        
    except Exception as e:
        logger.error(f"Signal check error: {e}")
        logger.error(traceback.format_exc())
        state.errors += 1


# ============================================================
# V3.1 SMART POSITION MONITORING
# ============================================================

def monitor_positions():
    """V3.1: Smart position monitoring with early exit logic"""
    
    state.last_position_check = datetime.now(timezone.utc)
    
    try:
        active_symbols = tracker.get_active_symbols()
        
        if not active_symbols:
            return
        
        max_hold = get_max_hold_hours()
        
        for symbol in active_symbols:
            trade = tracker.get_active_trade(symbol)
            if not trade:
                continue
            
            try:
                position = check_position_status(symbol)
                
                if not position.get("is_open"):
                    # Position closed (TP/SL hit)
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                    state.trades_closed += 1
                    continue
                
                # Calculate metrics
                opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                
                entry_price = trade.get("entry_price", position["entry_price"])
                current_price = get_price(symbol)
                
                if entry_price > 0 and current_price > 0:
                    if trade.get("side") == "LONG":
                        pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    else:
                        pnl_pct = ((entry_price - current_price) / entry_price) * 100
                else:
                    pnl_pct = 0
                
                pnl_usdt = position.get("unrealized_pnl", 0)
                
                logger.debug(f"  {symbol}: {hours_open:.1f}h, PnL: {pnl_pct:+.2f}% (${pnl_usdt:+.2f})")
                
                # ===== SMART EXIT LOGIC =====
                
                should_exit = False
                exit_reason = ""
                
                # 1. Max hold time exceeded
                if hours_open >= max_hold:
                    should_exit = True
                    exit_reason = f"max_hold ({hours_open:.1f}h > {max_hold}h)"
                
                # 2. Early exit if losing after EARLY_EXIT_CHECK_HOURS
                elif hours_open >= EARLY_EXIT_CHECK_HOURS and pnl_pct <= EARLY_EXIT_LOSS_PCT:
                    should_exit = True
                    exit_reason = f"early_exit_loss ({pnl_pct:.2f}% after {hours_open:.1f}h)"
                    state.early_exits += 1
                
                # 3. Exit if losing badly (> 4%) regardless of time
                elif pnl_pct <= -4.0:
                    should_exit = True
                    exit_reason = f"stop_loss_override ({pnl_pct:.2f}%)"
                    state.early_exits += 1
                
                if should_exit:
                    logger.warning(f"{symbol}: Force close - {exit_reason}")
                    
                    close_result = close_position_manually(
                        symbol=symbol,
                        side=position["side"],
                        size=position["size"]
                    )
                    
                    tracker.close_trade(symbol, {
                        "reason": exit_reason,
                        "hours_open": hours_open,
                        "final_pnl_pct": pnl_pct,
                        "close_result": close_result,
                    })
                    
                    state.trades_closed += 1
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1 Smart Exit - {symbol}",
                        input_data={"symbol": symbol, "hours_open": hours_open},
                        output_data={"reason": exit_reason, "pnl_pct": pnl_pct},
                        explanation=f"Smart exit: {exit_reason}. PnL: {pnl_pct:+.2f}%"
                    )
                    
            except Exception as e:
                logger.error(f"Monitor error {symbol}: {e}")
        
    except Exception as e:
        logger.error(f"Position monitor error: {e}")
        state.errors += 1


# ============================================================
# QUICK CLEANUP + SIGNAL TRIGGER
# ============================================================

def quick_cleanup_check():
    """Quick check for closed positions"""
    
    position_closed = False
    
    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                position_closed = True
        
        # Trigger new signal check when position closes
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
        f"V3.1 HEALTH | Up: {uptime_str} | "
        f"Signals: {state.signals_checked} | "
        f"Trades: {state.trades_opened}/{state.trades_closed} | "
        f"Early exits: {state.early_exits} | "
        f"Active: {active}"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def run_daemon():
    logger.info("=" * 60)
    logger.info("SMT Daemon V3.1 - Multi-Persona + Smart Exits")
    logger.info(f"Signal interval: {SIGNAL_CHECK_INTERVAL // 60}m")
    logger.info(f"Early exit after: {EARLY_EXIT_CHECK_HOURS}h if < {EARLY_EXIT_LOSS_PCT}%")
    logger.info(f"Max hold: {MAX_HOLD_HOURS_DEFAULT}h")
    logger.info("=" * 60)
    
    last_signal = 0
    last_position = 0
    last_health = 0
    last_cleanup = 0
    
    if is_competition_active():
        logger.info("Competition ACTIVE - initial signal check")
        check_trading_signals()
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
                last_signal = now
            
            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                last_position = now
            
            if now - last_cleanup >= CLEANUP_CHECK_INTERVAL:
                quick_cleanup_check()
                last_cleanup = now
            
            if now - last_health >= HEALTH_CHECK_INTERVAL:
                log_health()
                last_health = now
            
            time.sleep(5)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            state.errors += 1
            time.sleep(30)
    
    logger.info("V3.1 Daemon shutdown")
    logger.info(f"Stats: {json.dumps(state.to_dict(), indent=2)}")


def handle_shutdown(signum, frame):
    logger.info(f"Signal {signum} - shutting down")
    state.is_running = False
    state.shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    run_daemon()
