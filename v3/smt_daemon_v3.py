#!/usr/bin/env python3
"""
SMT Trading Daemon V3
=====================
Always-running trading service for WEEX AI Wars competition.

Runs continuously on VM:
- Signal check: Every 4 hours (configurable)
- Position monitor: Every 5 minutes
- Cleanup: Immediately when positions close
- Auto-recovery on errors

Run directly: python3 smt_daemon_v3.py
Run as service: sudo systemctl start smt-trading

Logs: logs/daemon_YYYYMMDD.log
"""

import os
import sys
import json
import time
import signal
import logging
import traceback
from datetime import datetime, timezone, timedelta
from threading import Thread, Event
from typing import Dict, List, Optional

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# CONFIGURATION
# ============================================================

# Timing (in seconds)
SIGNAL_CHECK_INTERVAL = 4 * 60 * 60      # 4 hours - check for new trading signals
POSITION_MONITOR_INTERVAL = 5 * 60        # 5 minutes - check position status
HEALTH_CHECK_INTERVAL = 60                # 1 minute - log heartbeat
CLEANUP_CHECK_INTERVAL = 30               # 30 seconds - quick check for closed positions

# Competition dates
COMPETITION_START = datetime(2026, 1, 12, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 2, 23, 59, 59, tzinfo=timezone.utc)

# Max hold times (hours)
MAX_HOLD_HOURS_DEFAULT = 48
MAX_HOLD_HOURS_FINAL_DAYS = 6  # When < 3 days left

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 30  # seconds

# Log settings
LOG_DIR = "logs"
LOG_LEVEL = logging.INFO

# ============================================================
# LOGGING SETUP
# ============================================================

def setup_logging():
    """Setup logging to file and console"""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    log_file = os.path.join(LOG_DIR, f"daemon_{datetime.now().strftime('%Y%m%d')}.log")
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(formatter)
    
    # Root logger
    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ============================================================
# IMPORT TRADING FUNCTIONS
# ============================================================

try:
    from smt_nightly_trade_v3 import (
        # Config
        TEST_MODE, TRADING_PAIRS, MAX_LEVERAGE, STARTING_BALANCE,
        PROJECT_ID, PIPELINE_VERSION, MODEL_NAME,
        
        # WEEX API
        get_price, get_balance, get_open_positions,
        set_leverage, place_order, upload_ai_log_to_weex,
        
        # Position management
        check_position_status, get_pending_orders, get_pending_plan_orders,
        cancel_order, cancel_plan_order, cancel_all_orders_for_symbol,
        close_position_manually, TradeTracker,
        
        # Competition
        get_competition_status, calculate_position_size,
        
        # Whale discovery
        fetch_recent_large_transactions, discover_whales_from_transactions,
        fetch_whale_transaction_history, extract_features,
        WhaleClassifier, analyze_whale_flow, generate_whale_signal,
        
        # Gemini
        validate_with_gemini,
        
        # Trading
        execute_trade,
        
        # BigQuery
        save_to_bigquery,
        
        # Logging
        save_local_log,
    )
    logger.info("Successfully imported trading functions from smt_nightly_trade_v3")
except ImportError as e:
    logger.error(f"Failed to import trading functions: {e}")
    logger.error("Make sure smt_nightly_trade_v3.py is in the same directory")
    sys.exit(1)

# ============================================================
# DAEMON STATE
# ============================================================

class DaemonState:
    """Track daemon state and statistics"""
    
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.last_signal_check = None
        self.last_position_check = None
        self.last_cleanup = None
        
        self.signals_checked = 0
        self.trades_opened = 0
        self.trades_closed = 0
        self.errors = 0
        
        self.is_running = True
        self.shutdown_event = Event()
    
    def to_dict(self) -> Dict:
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_hours": (datetime.now(timezone.utc) - self.started_at).total_seconds() / 3600,
            "last_signal_check": self.last_signal_check.isoformat() if self.last_signal_check else None,
            "last_position_check": self.last_position_check.isoformat() if self.last_position_check else None,
            "signals_checked": self.signals_checked,
            "trades_opened": self.trades_opened,
            "trades_closed": self.trades_closed,
            "errors": self.errors,
            "is_running": self.is_running,
        }

state = DaemonState()
tracker = TradeTracker()

# ============================================================
# CORE FUNCTIONS
# ============================================================

def is_competition_active() -> bool:
    """Check if we're within competition dates or have funds to practice"""
    now = datetime.now(timezone.utc)
    
    # Always allow if --force flag is passed
    if "--force" in sys.argv:
        return True
    
    # Check if within official competition dates
    if COMPETITION_START <= now <= COMPETITION_END:
        return True
    
    # Before competition: allow trading if we have balance (practice mode)
    if now < COMPETITION_START:
        try:
            # Import here to avoid circular import at module load
            balance = get_balance()
            if balance > 0:
                logger.info(f"Pre-competition practice mode: Balance = {balance:.2f} USDT")
                return True
        except:
            pass
    
    return False


def get_max_hold_hours() -> int:
    """Get max hold time based on competition timeline"""
    now = datetime.now(timezone.utc)
    days_left = (COMPETITION_END - now).days
    
    if days_left <= 3:
        return MAX_HOLD_HOURS_FINAL_DAYS
    return MAX_HOLD_HOURS_DEFAULT


def run_with_retry(func, *args, max_retries=MAX_RETRIES, **kwargs):
    """Run function with retry on failure"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ============================================================
# SIGNAL CHECKING (Every 4 hours)
# ============================================================

def check_trading_signals():
    """Main signal checking routine - runs every 4 hours"""
    
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    logger.info("=" * 60)
    logger.info(f"SIGNAL CHECK - Run ID: {run_timestamp}")
    logger.info("=" * 60)
    
    state.signals_checked += 1
    state.last_signal_check = datetime.now(timezone.utc)
    
    try:
        # Get account status
        balance = get_balance()
        open_positions = get_open_positions()
        competition = get_competition_status(balance)
        
        logger.info(f"Balance: {balance:.2f} USDT")
        logger.info(f"Open positions: {len(open_positions)}")
        logger.info(f"Days left: {competition['days_left']}")
        logger.info(f"Strategy mode: {competition['strategy_mode']}")
        
        # Check if we can open new positions
        if len(open_positions) >= 5:  # MAX_OPEN_POSITIONS
            logger.info("Max positions reached, skipping signal check")
            return
        
        # Initialize AI log
        ai_log = {
            "run_id": f"daemon_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": PIPELINE_VERSION,
            "daemon_mode": True,
            "stages": [],
            "trades": [],
        }
        
        # ===== Whale Discovery =====
        logger.info("Starting whale discovery...")
        whale_data = None
        
        large_txs = run_with_retry(fetch_recent_large_transactions, 100.0, 6)
        
        if large_txs:
            whale_addresses = discover_whales_from_transactions(large_txs)
            logger.info(f"Found {len(large_txs)} large txs, {len(whale_addresses)} whales")
            
            # Load classifier
            classifier = WhaleClassifier()
            classifier.load_from_gcs()
            
            best_signal = None
            best_whale = None
            
            for addr in whale_addresses[:10]:
                try:
                    txs = run_with_retry(fetch_whale_transaction_history, addr, 90)
                    if not txs:
                        continue
                    
                    features = extract_features(addr, txs)
                    category, conf = classifier.classify(features)
                    
                    recent_txs = [tx for tx in txs if tx["timestamp"] > time.time() - 6*3600]
                    flow = analyze_whale_flow(addr, recent_txs)
                    
                    if flow["direction"] == "mixed":
                        continue
                    
                    signal = generate_whale_signal(category, conf, flow)
                    
                    if signal["signal"] not in ("NEUTRAL", "SKIP"):
                        if not best_signal or signal["confidence"] > best_signal["confidence"]:
                            best_signal = signal
                            best_whale = {
                                "address": addr,
                                "category": category,
                                "class_confidence": conf,
                                "flow": flow,
                                "signal": signal,
                            }
                except Exception as e:
                    logger.warning(f"Error analyzing whale {addr[:10]}: {e}")
            
            if best_whale:
                whale_data = best_whale
                logger.info(f"Best whale signal: {whale_data['signal']['signal']} ({whale_data['signal']['confidence']:.0%})")
        
        # ===== Evaluate All Pairs =====
        logger.info("Evaluating trading pairs...")
        trade_opportunities = []
        
        for pair, pair_info in TRADING_PAIRS.items():
            try:
                # Skip if we already have position in this pair
                if any(p.get("symbol") == pair_info["symbol"] for p in open_positions):
                    logger.info(f"  {pair}: Already have position, skipping")
                    continue
                
                pair_whale = whale_data if pair in ("ETH", "BTC") and whale_data else None
                
                decision = run_with_retry(
                    validate_with_gemini,
                    pair, pair_info, balance, competition, open_positions, pair_whale
                )
                
                logger.info(f"  {pair}: {decision.get('decision')} ({decision.get('confidence', 0):.0%})")
                
                if decision.get("decision") in ("LONG", "SHORT") and decision.get("confidence", 0) >= 0.60:
                    trade_opportunities.append({
                        "pair": pair,
                        "pair_info": pair_info,
                        "decision": decision,
                        "whale_data": pair_whale,
                    })
                
                # Upload AI log
                upload_ai_log_to_weex(
                    stage=f"Signal Analysis - {pair}",
                    input_data={"pair": pair, "balance": balance},
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence", 0),
                    },
                    explanation=decision.get("reasoning", "")[:500]
                )
                
                time.sleep(2)  # Rate limit
                
            except Exception as e:
                logger.error(f"Error evaluating {pair}: {e}")
        
        # ===== Execute Best Trade =====
        if trade_opportunities:
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            best = trade_opportunities[0]
            
            logger.info(f"Executing: {best['pair']} {best['decision']['decision']} ({best['decision']['confidence']:.0%})")
            
            trade_result = run_with_retry(
                execute_trade,
                best["pair_info"], best["decision"], balance
            )
            
            if trade_result.get("executed"):
                logger.info(f"Trade executed! Order ID: {trade_result.get('order_id')}")
                
                # Track the trade
                tracker.add_trade(best["pair_info"]["symbol"], trade_result)
                
                state.trades_opened += 1
                ai_log["trades"].append(trade_result)
                
                # Upload execution log
                upload_ai_log_to_weex(
                    stage="Trade Execution",
                    input_data={"pair": best["pair"], "signal": trade_result["signal"]},
                    output_data={
                        "order_id": trade_result.get("order_id"),
                        "size": trade_result.get("size"),
                        "entry_price": trade_result.get("entry_price"),
                    },
                    explanation=f"Opened {trade_result['signal']} on {best['pair']} at ${trade_result['entry_price']:.2f}"
                )
            else:
                logger.warning(f"Trade not executed: {trade_result.get('reason')}")
        else:
            logger.info("No trade opportunities found")
        
        # Save log
        save_local_log(ai_log, run_timestamp)
        
    except Exception as e:
        logger.error(f"Signal check failed: {e}")
        logger.error(traceback.format_exc())
        state.errors += 1


# ============================================================
# POSITION MONITORING (Every 5 minutes)
# ============================================================

def monitor_positions():
    """Check all active positions and handle closures"""
    
    state.last_position_check = datetime.now(timezone.utc)
    
    try:
        active_symbols = tracker.get_active_symbols()
        
        if not active_symbols:
            return
        
        logger.debug(f"Monitoring {len(active_symbols)} active positions")
        
        max_hold = get_max_hold_hours()
        
        for symbol in active_symbols:
            trade = tracker.get_active_trade(symbol)
            if not trade:
                continue
            
            try:
                position = check_position_status(symbol)
                
                if not position.get("is_open"):
                    # Position closed (TP/SL hit)
                    logger.info(f"Position {symbol} CLOSED - cleaning up orders")
                    
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                    
                    state.trades_closed += 1
                    
                    upload_ai_log_to_weex(
                        stage="Position Closed",
                        input_data={"symbol": symbol},
                        output_data={"reason": "TP/SL hit", "orders_cancelled": len(cleanup.get("plan_cancelled", []))},
                        explanation=f"Position {symbol} closed via TP/SL. Cancelled remaining orders."
                    )
                    
                else:
                    # Check hold time
                    opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                    hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    
                    if hours_open >= max_hold:
                        logger.warning(f"Position {symbol} exceeded max hold ({hours_open:.1f}h > {max_hold}h) - force closing")
                        
                        close_result = close_position_manually(
                            symbol=symbol,
                            side=position["side"],
                            size=position["size"]
                        )
                        
                        tracker.close_trade(symbol, {
                            "reason": "max_hold_exceeded",
                            "hours_open": hours_open,
                            "close_result": close_result,
                        })
                        
                        state.trades_closed += 1
                        
                        upload_ai_log_to_weex(
                            stage="Force Close",
                            input_data={"symbol": symbol, "hours_open": hours_open},
                            output_data={"reason": "max_hold_exceeded"},
                            explanation=f"Force closed {symbol} after {hours_open:.1f} hours"
                        )
                    else:
                        pnl = position.get("unrealized_pnl", 0)
                        logger.debug(f"  {symbol}: Open {hours_open:.1f}h, P&L: ${pnl:.2f}")
                        
            except Exception as e:
                logger.error(f"Error monitoring {symbol}: {e}")
        
    except Exception as e:
        logger.error(f"Position monitoring failed: {e}")
        state.errors += 1


# ============================================================
# QUICK CLEANUP CHECK (Every 30 seconds)
# ============================================================

def quick_cleanup_check():
    """Quick check for closed positions - runs frequently"""
    
    state.last_cleanup = datetime.now(timezone.utc)
    
    try:
        active_symbols = tracker.get_active_symbols()
        
        for symbol in active_symbols:
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed - triggering cleanup")
                
                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                
    except Exception as e:
        logger.debug(f"Quick cleanup check error: {e}")


# ============================================================
# HEALTH CHECK (Every minute)
# ============================================================

def log_health():
    """Log daemon health status"""
    
    uptime = datetime.now(timezone.utc) - state.started_at
    uptime_str = str(uptime).split('.')[0]  # Remove microseconds
    
    active_positions = len(tracker.get_active_symbols())
    
    logger.info(
        f"HEALTH | Uptime: {uptime_str} | "
        f"Signals: {state.signals_checked} | "
        f"Trades: {state.trades_opened}/{state.trades_closed} | "
        f"Errors: {state.errors} | "
        f"Active: {active_positions}"
    )


# ============================================================
# MAIN DAEMON LOOP
# ============================================================

def run_daemon():
    """Main daemon loop"""
    
    logger.info("=" * 60)
    logger.info("SMT Trading Daemon V3 Starting")
    logger.info(f"Competition: {COMPETITION_START.date()} to {COMPETITION_END.date()}")
    logger.info(f"Signal interval: {SIGNAL_CHECK_INTERVAL // 3600}h")
    logger.info(f"Monitor interval: {POSITION_MONITOR_INTERVAL // 60}m")
    logger.info(f"Test mode: {TEST_MODE}")
    logger.info("=" * 60)
    
    # Track last run times
    last_signal_check = 0
    last_position_check = 0
    last_health_check = 0
    last_cleanup_check = 0
    
    # Run initial signal check
    if is_competition_active():
        logger.info("Competition is ACTIVE - running initial signal check")
        check_trading_signals()
        last_signal_check = time.time()
    else:
        logger.info("Competition not active yet - waiting")
    
    # Main loop
    while state.is_running and not state.shutdown_event.is_set():
        try:
            now = time.time()
            
            # Check if competition is active
            if not is_competition_active():
                if datetime.now(timezone.utc) > COMPETITION_END:
                    logger.info("Competition ended - shutting down")
                    break
                else:
                    logger.debug("Waiting for competition to start...")
                    time.sleep(60)
                    continue
            
            # Signal check (every 4 hours)
            if now - last_signal_check >= SIGNAL_CHECK_INTERVAL:
                check_trading_signals()
                last_signal_check = now
            
            # Position monitor (every 5 minutes)
            if now - last_position_check >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                last_position_check = now
            
            # Quick cleanup (every 30 seconds)
            if now - last_cleanup_check >= CLEANUP_CHECK_INTERVAL:
                quick_cleanup_check()
                last_cleanup_check = now
            
            # Health check (every minute)
            if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                log_health()
                last_health_check = now
            
            # Sleep briefly
            time.sleep(5)
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            logger.error(traceback.format_exc())
            state.errors += 1
            time.sleep(30)  # Wait before retrying
    
    # Shutdown
    logger.info("Daemon shutting down...")
    logger.info(f"Final stats: {json.dumps(state.to_dict(), indent=2)}")


# ============================================================
# SIGNAL HANDLERS
# ============================================================

def handle_shutdown(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum} - initiating shutdown")
    state.is_running = False
    state.shutdown_event.set()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Run daemon
    run_daemon()
