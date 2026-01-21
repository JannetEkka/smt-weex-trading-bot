#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1.22 - CAPITAL PROTECTION
=========================
No partial closes. Higher conviction trades only.

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
    log_file = os.path.join(LOG_DIR, f"daemon_v3_1_7_{datetime.now().strftime('%Y%m%d')}.log")
    
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
tracker = TradeTracker(state_file="trade_state_v3_1_7.json")
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
# V3.1.9 SIGNAL CHECKING - HEDGE MODE SUPPORT
# ============================================================

def check_trading_signals():
    """V3.1.9: Tier-based signal check with HEDGE MODE
    
    ALWAYS analyzes ALL pairs and uploads AI logs.
    HEDGE MODE: Can open LONG while SHORT is running (and vice versa)
    WEEX supports bidirectional positions on same pair!
    """
    
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
        
        BASE_SLOTS = 5
        MAX_BONUS_SLOTS = 2  # Can earn up to 2 extra slots from risk-free positions
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        available_slots = effective_max_positions - len(open_positions)
        
        # V3.1.19: Override slot availability if low equity
        can_open_new = available_slots > 0 and not low_equity_mode
        
        if bonus_slots > 0:
            logger.info(f"Smart Slots: {len(open_positions)}/{effective_max_positions} (base {BASE_SLOTS} + {bonus_slots} risk-free bonus)")
        
        if low_equity_mode:
            logger.info(f"Low equity mode - no new trades allowed")
        elif not can_open_new:
            logger.info(f"Max positions ({len(open_positions)}/{effective_max_positions}) - Analysis only")
        
        ai_log = {
            "run_id": f"v3_1_7_{run_timestamp}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "v3.1.7-hedge",
            "tier_config": TIER_CONFIG,
            "analyses": [],
            "trades": [],
        }
        
        trade_opportunities = []
        
        # Confidence thresholds
        COOLDOWN_OVERRIDE_CONFIDENCE = 0.85
        HEDGE_CONFIDENCE_THRESHOLD = 0.90  # Need 75%+ to open opposite direction
        
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
            
            # V3.1.19: Use smart slot calculation (same as above)
            current_positions = len(open_positions)
            available_slots = effective_max_positions - current_positions
            
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
                
                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%)")
                # V3.1.22 TIER-AWARE PROFIT GUARD
                # Tier 3 (DOGE, XRP, ADA): Tighter - they reverse fast
                # Tier 1/2 (BTC, ETH, SOL): More room to breathe
                if tier == 3:
                    # Tier 3: Exit if peak >= 2% and drops to 0.5%
                    if peak_pnl_pct >= 2.0 and pnl_pct < 0.5:
                        should_exit = True
                        exit_reason = f"T3_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1
                elif tier == 2:
                    # Tier 2: Exit if peak >= 2.5% and drops to 1.0%
                    if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:
                        should_exit = True
                        exit_reason = f"T2_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1
                else:
                    # Tier 1: Exit if peak >= 3.0% and drops to 1.5%
                    if peak_pnl_pct >= 3.0 and pnl_pct < 1.5:
                        should_exit = True
                        exit_reason = f"T1_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1
                

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
        f"V3.1.9 HEALTH | Up: {uptime_str} | "
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






# ============================================================
# V3.1.9: REGIME-AWARE SMART EXIT
# ============================================================

def get_market_regime_for_exit():
    """Get BTC 24h trend for regime-based exits - V3.1.9 stricter thresholds"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            # V3.1.9: STRICTER thresholds
            # BEARISH if 24h down >1% OR 4h down >1%
            if change_24h < -1.0 or change_4h < -1.0:
                return {"regime": "BEARISH", "change_24h": change_24h, "change_4h": change_4h}
            # BULLISH if 24h up >1.5% OR 4h up >1%
            elif change_24h > 1.5 or change_4h > 1.0:
                return {"regime": "BULLISH", "change_24h": change_24h, "change_4h": change_4h}
            else:
                return {"regime": "NEUTRAL", "change_24h": change_24h, "change_4h": change_4h}
        else:
            logger.warning(f"[REGIME] API returned unexpected data: {type(data)}")
    except Exception as e:
        logger.error(f"[REGIME] API error: {e}")
    
    return {"regime": "UNKNOWN", "change_24h": 0, "change_4h": 0}


def regime_aware_exit_check():
    """
    V3.1.9: AI cuts positions fighting the market regime.
    
    Logic:
    - BEARISH market + LONG losing > $8 = AI closes position
    - BULLISH market + SHORT losing > $8 = AI closes position
    
    This frees margin for regime-aligned trades.
    """
    try:
        positions = get_open_positions()
        if not positions:
            return
        
        regime = get_market_regime_for_exit()
        
        logger.info(f"[REGIME] Market: {regime['regime']} | 24h: {regime['change_24h']:+.1f}% | 4h: {regime['change_4h']:+.1f}%")
        
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
            
            # V3.1.14: Calculate portfolio context
            total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in positions if p['side'] == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
            total_long_gain = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'LONG' and float(p.get('unrealized_pnl', 0)) > 0)
            total_short_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in positions if p['side'] == 'SHORT' and float(p.get('unrealized_pnl', 0)) < 0)
            total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
            shorts_winning = total_short_gain > 30 and total_long_loss > 20  # V3.1.20: Raised from 20/15
            longs_winning = total_long_gain > 30 and total_short_loss > 20  # V3.1.20: Raised from 20/15
            
            # V3.1.20 PREDATOR: Only exit on SEVERE losses, let SL do its job
            # LONG losing in BEARISH market - raised from $5 to $15
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -15:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market - raised from $5 to $15
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -15:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.20 PREDATOR: Removed NEUTRAL weak market exit - trust the SL
            
            # V3.1.20 PREDATOR: HARD STOP raised to $30 - let SL work
            elif side == "LONG" and pnl < -30:
                should_close = True
                reason = f"HARD STOP: LONG losing ${abs(pnl):.1f}"
            
            elif side == "SHORT" and pnl < -30:
                should_close = True
                reason = f"HARD STOP: SHORT losing ${abs(pnl):.1f}"
            
            # V3.1.20 PREDATOR: Raised from $6 to $12 when opposite winning
            elif side == "LONG" and pnl < -12 and shorts_winning:
                should_close = True
                reason = f"LONG -${abs(pnl):.1f} while SHORTs winning"
            
            elif side == "SHORT" and pnl < -12 and longs_winning:
                should_close = True
                reason = f"SHORT -${abs(pnl):.1f} while LONGs winning"
            
            if should_close:
                logger.warning(f"[REGIME EXIT] {symbol_clean}: {reason}")
                
                # Close the position
                close_result = close_position_manually(symbol, side, size)
                order_id = close_result.get("order_id")
                
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
    logger.info("SMT Daemon V3.1.14 - NO FLOOR + BUG FIX")
    logger.info("=" * 60)
    logger.info("V3.1.9 CRITICAL FIXES:")
    logger.info("  - FIXED: undefined btc_trend bug blocking regime filter")
    logger.info("  - FIXED: Regime filter now applies to ALL pairs incl BTC")
    logger.info("  - BEARISH threshold: -1% (was -2%)")
    logger.info("  - Block LONGs when BTC 24h < -0.5%")
    logger.info("  - Regime cache: 5min (was 15min)")
    logger.info("Tier Configuration:")
    for tier, config in TIER_CONFIG.items():
        pairs = [p for p, info in TRADING_PAIRS.items() if info["tier"] == tier]
        runner = RUNNER_CONFIG.get(tier, {})
        runner_str = f"Runner: +{runner.get('trigger_pct', 0)}% -> close 50%" if runner.get("enabled") else "No Runner"
        logger.info(f"  Tier {tier} ({config['name']}): {', '.join(pairs)}")
        logger.info(f"    TP: {config['tp_pct']}%, SL: {config['sl_pct']}%, Hold: {config['max_hold_hours']}h | {runner_str}")
    logger.info("Cooldown Override: 85%+ confidence bypasses cooldown")
    logger.info("=" * 60)

    # V3.1.9: Sync with WEEX on startup
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
                regime_aware_exit_check()  # V3.1.9: Check for regime-fighting positions
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
