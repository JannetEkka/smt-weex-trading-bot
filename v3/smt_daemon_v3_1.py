#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1.4
=========================
Tier-based trading daemon with smart exits per tier.
CRITICAL FIXES: Reduced positions, higher confidence, market trend filter.

V3.1.4 Changes:
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
from datetime import datetime, timezone, timedelta
from threading import Event
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# V3.1.1 CONFIGURATION
# ============================================================

# Timing
SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes
POSITION_MONITOR_INTERVAL = 2 * 60  # 2 minutes (check more often for tier 3)
HEALTH_CHECK_INTERVAL = 60
CLEANUP_CHECK_INTERVAL = 30

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
    log_file = os.path.join(LOG_DIR, f"daemon_v3_1_4_{datetime.now().strftime('%Y%m%d')}.log")
    
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
        MIN_CONFIDENCE_TO_TRADE,  # Added for trade filtering
        TIER_CONFIG, get_tier_for_symbol, get_tier_config,
        RUNNER_CONFIG, get_runner_config,
        
        # WEEX API
        get_price, get_balance, get_open_positions,
        upload_ai_log_to_weex,
        
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
    logger.info("V3.1.4 imports successful (Market Trend Filter + Stricter Signals)")
except ImportError as e:
    logger.error(f"Import error: {e}")
    logger.error(traceback.format_exc())
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
tracker = TradeTracker(state_file="trade_state_v3_1_4.json")
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
# V3.1.4 SIGNAL CHECKING - HEDGE MODE SUPPORT
# ============================================================

def check_trading_signals():
    """V3.1.4: Tier-based signal check with HEDGE MODE
    
    ALWAYS analyzes ALL pairs and uploads AI logs.
    HEDGE MODE: Can open LONG while SHORT is running (and vice versa)
    WEEX supports bidirectional positions on same pair!
    """
    
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    logger.info("=" * 60)
    logger.info(f"V3.1.4 SIGNAL CHECK - {run_timestamp}")
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
        can_open_new = available_slots > 0
        
        if not can_open_new:
            logger.info(f"Max positions ({len(open_positions)}/{MAX_OPEN_POSITIONS}) - Analysis only")
        
        ai_log = {
            "run_id": f"v3_1_4_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v3.1.4-hedge",
            "tier_config": TIER_CONFIG,
            "analyses": [],
            "trades": [],
        }
        
        trade_opportunities = []
        
        # Confidence thresholds
        COOLDOWN_OVERRIDE_CONFIDENCE = 0.85
        HEDGE_CONFIDENCE_THRESHOLD = 0.75  # Need 75%+ to open opposite direction
        
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
                # ALWAYS analyze
                decision = run_with_retry(
                    analyzer.analyze,
                    pair, pair_info, balance, competition, open_positions
                )
                
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
                
                # Determine tradability
                can_trade_this = False
                trade_type = "none"
                
                if signal == "LONG":
                    if has_long:
                        logger.info(f"    -> Already LONG")
                    elif has_short:
                        # HEDGE opportunity!
                        if confidence >= HEDGE_CONFIDENCE_THRESHOLD and can_open_new:
                            can_trade_this = True
                            trade_type = "hedge"
                            logger.info(f"    -> HEDGE: Can open LONG while SHORT running!")
                        else:
                            logger.info(f"    -> Has SHORT, need {HEDGE_CONFIDENCE_THRESHOLD:.0%}+ to hedge (have {confidence:.0%})")
                    else:
                        # No position - normal trade
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new"
                
                elif signal == "SHORT":
                    if has_short:
                        logger.info(f"    -> Already SHORT")
                    elif has_long:
                        # HEDGE opportunity!
                        if confidence >= HEDGE_CONFIDENCE_THRESHOLD and can_open_new:
                            can_trade_this = True
                            trade_type = "hedge"
                            logger.info(f"    -> HEDGE: Can open SHORT while LONG running!")
                        else:
                            logger.info(f"    -> Has LONG, need {HEDGE_CONFIDENCE_THRESHOLD:.0%}+ to hedge (have {confidence:.0%})")
                    else:
                        # No position - normal trade
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
                    stage=f"V3.1.4 Analysis - {pair} (Tier {tier})",
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
                
                # Add to opportunities if tradeable
                if can_trade_this and signal in ("LONG", "SHORT"):
                    if on_cooldown and confidence < COOLDOWN_OVERRIDE_CONFIDENCE:
                        logger.info(f"    -> Skip (cooldown)")
                    elif confidence >= MIN_CONFIDENCE_TO_TRADE:
                        if on_cooldown:
                            logger.info(f"    -> COOLDOWN OVERRIDE")
                        trade_opportunities.append({
                            "pair": pair,
                            "pair_info": pair_info,
                            "decision": decision,
                            "tier": tier,
                            "trade_type": trade_type,
                        })
                
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Error analyzing {pair}: {e}")
        
# Execute ALL qualifying trades (up to available slots)
        if trade_opportunities:
            # Sort by confidence (highest first)
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            
            # Calculate available slots
            current_positions = len(open_positions)
            available_slots = MAX_OPEN_POSITIONS - current_positions
            
            trades_executed = 0
            
            for opportunity in trade_opportunities:
                # Check if we still have slots
                if trades_executed >= available_slots:
                    logger.info(f"Max positions reached, skipping remaining opportunities")
                    break
                
                tier = opportunity["tier"]
                tier_config = get_tier_config(tier)
                trade_type = opportunity["trade_type"]
                pair = opportunity["pair"]
                signal = opportunity["decision"]["decision"]
                confidence = opportunity["decision"]["confidence"]
                
                logger.info(f"")
                type_label = "[HEDGE] " if trade_type == "hedge" else ""
                logger.info(f"EXECUTING {type_label}{pair} {signal} (T{tier}) - {confidence:.0%}")
                
                try:
                    trade_result = run_with_retry(
                        execute_trade,
                        opportunity["pair_info"], opportunity["decision"], balance
                    )
                    
                    if trade_result.get("executed"):
                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        
                        tracker.add_trade(opportunity["pair_info"]["symbol"], trade_result)
                        state.trades_opened += 1
                        ai_log["trades"].append(trade_result)
                        trades_executed += 1
                        
                        # Update available balance for next trade
                        balance = get_balance()
                        
                    else:
                        logger.warning(f"Trade failed: {trade_result.get('reason')}")
                        
                except Exception as e:
                    logger.error(f"Error executing {pair}: {e}")
                
                time.sleep(1)  # Small delay between trades
            
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
# V3.1.4 TIER-BASED POSITION MONITORING
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
            
            try:
                position = check_position_status(symbol)
                
                if not position.get("is_open"):
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                    state.trades_closed += 1
                    continue
                
                # Get tier config for this position
                tier = trade.get("tier", get_tier_for_symbol(symbol))
                tier_config = get_tier_config(tier)
                
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
                
                # Get tier-specific exit parameters
                max_hold = tier_config["max_hold_hours"]
                early_exit_hours = tier_config["early_exit_hours"]
                early_exit_loss = tier_config["early_exit_loss_pct"]
                force_exit_loss = tier_config["force_exit_loss_pct"]
                
                logger.debug(f"  {symbol} (T{tier}): {hours_open:.1f}h, PnL: {pnl_pct:+.2f}%")
                
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
                    
                    tracker.close_trade(symbol, {
                        "reason": exit_reason,
                        "tier": tier,
                        "hours_open": hours_open,
                        "final_pnl_pct": pnl_pct,
                        "close_result": close_result,
                    })
                    
                    state.trades_closed += 1
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1.4 Smart Exit - {symbol_clean}",
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
        f"V3.1.4 HEALTH | Up: {uptime_str} | "
        f"Signals: {state.signals_checked} | "
        f"Trades: {state.trades_opened}/{state.trades_closed} | "
        f"Runners: {state.runners_triggered} | "
        f"Active: {active}"
    )


# ============================================================
# MAIN LOOP

# ============================================================
# POSITION SYNC ON STARTUP
# ============================================================

def sync_tracker_with_weex():
    """Sync TradeTracker with actual WEEX positions on startup.
    
    This fixes the issue where daemon restart loses track of positions.
    """
    logger.info("Syncing tracker with WEEX positions...")
    
    try:
        positions = get_open_positions()
        
        weex_symbols = {p['symbol'] for p in positions}
        tracker_symbols = set(tracker.get_active_symbols())
        
        # Find positions on WEEX but not in tracker
        missing = weex_symbols - tracker_symbols
        
        if missing:
            logger.warning(f"Found {len(missing)} untracked positions: {missing}")
            
            for pos in positions:
                if pos['symbol'] in missing:
                    tier = get_tier_for_symbol(pos['symbol'])
                    tier_config = get_tier_config(tier)
                    
                    # Add to tracker with current time (conservative - may exit sooner than needed)
                    tracker.active_trades[pos['symbol']] = {
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "side": pos['side'],
                        "entry_price": pos['entry_price'],
                        "tier": tier,
                        "max_hold_hours": tier_config['max_hold_hours'],
                        "synced": True,
                    }
                    logger.info(f"  Added {pos['symbol']} (Tier {tier}, {pos['side']} @ {pos['entry_price']:.4f})")
            
            tracker.save_state()
        
        # Find orphan trades in tracker (position closed but tracker didn't know)
        orphans = tracker_symbols - weex_symbols
        
        if orphans:
            logger.warning(f"Found {len(orphans)} orphan trades: {orphans}")
            
            for symbol in orphans:
                tracker.close_trade(symbol, {"reason": "sync_cleanup", "note": "Position not found on WEEX"})
                logger.info(f"  Removed orphan {symbol}")
        
        logger.info(f"Sync complete. Tracking {len(tracker.get_active_symbols())} positions.")
        
    except Exception as e:
        logger.error(f"Sync error: {e}")
# ============================================================

def run_daemon():
    logger.info("=" * 60)
    logger.info("SMT Daemon V3.1.4 - Market Trend Filter + Stricter Signals")
    logger.info("=" * 60)
    logger.info("V3.1.4 CRITICAL FIXES:")
    logger.info("  - MAX_POSITIONS: 5 (was 8)")
    logger.info("  - MIN_CONFIDENCE: 65% (was 55%)")
    logger.info("  - Tier 3 SL: 2% (was 1.5%)")
    logger.info("  - BTC Trend Filter: No LONG when BTC dumps")
    logger.info("Tier Configuration:")
    for tier, config in TIER_CONFIG.items():
        pairs = [p for p, info in TRADING_PAIRS.items() if info["tier"] == tier]
        runner = RUNNER_CONFIG.get(tier, {})
        runner_str = f"Runner: +{runner.get('trigger_pct', 0)}% -> close 50%" if runner.get("enabled") else "No Runner"
        logger.info(f"  Tier {tier} ({config['name']}): {', '.join(pairs)}")
        logger.info(f"    TP: {config['tp_pct']}%, SL: {config['sl_pct']}%, Hold: {config['max_hold_hours']}h | {runner_str}")
    logger.info("Cooldown Override: 85%+ confidence bypasses cooldown")
    logger.info("=" * 60)

    # V3.1.5: Sync with WEEX on startup
    sync_tracker_with_weex()
    
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
    
    logger.info("V3.1.4 Daemon shutdown")
    logger.info(f"Stats: {json.dumps(state.to_dict(), indent=2)}")


def handle_shutdown(signum, frame):
    logger.info(f"Signal {signum} - shutting down")
    state.is_running = False
    state.shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    run_daemon()
