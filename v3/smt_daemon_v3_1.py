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
        
        BASE_SLOTS = 8  # V3.1.35d: match MAX_OPEN_POSITIONS  # V3.1.31: Competition mode - more exposure
        MAX_BONUS_SLOTS = 2  # Can earn up to 2 extra slots from risk-free positions
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.26: High confidence override
        CONFIDENCE_OVERRIDE_THRESHOLD = 0.85  # 85%+ signals can exceed normal limits
        MAX_CONFIDENCE_SLOTS = 2  # Up to 2 extra slots for high conviction trades
        
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
        HEDGE_CONFIDENCE_THRESHOLD = 0.80  # V3.1.37: Lowered from 90% to allow hedging
        
        # V3.1.44: Disable hedging during Capitulation - pick a side, don't fight yourself
        # Fetch F&G early so we can use it for hedge decisions
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _fg_check = get_fear_greed_index()
            _fg_value = _fg_check.get("value", 50)
            if _fg_value < 15:
                HEDGE_CONFIDENCE_THRESHOLD = 0.95  # V3.1.45: Allow hedges at 95%+ even in capitulation
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
                
# Telegram alerts for ALL tradeable signals
                if signal in ("LONG", "SHORT") and confidence >= 0.75:
                    try:
                        from telegram_alerts import send_telegram_alert
                        tier_cfg = get_tier_config(tier)
                        current_price = get_price(f"cmt_{pair.lower()}usdt")
                        
                        # Calculate targets
                        if signal == "LONG":
                            tp_price = current_price * (1 + tier_cfg["tp_pct"]/100)
                            sl_price = current_price * (1 - tier_cfg["sl_pct"]/100)
                        else:  # SHORT
                            tp_price = current_price * (1 - tier_cfg["tp_pct"]/100)
                            sl_price = current_price * (1 + tier_cfg["sl_pct"]/100)
                        
                        alert_msg = f"""🚨 <b>SMT SIGNAL - {pair}</b>

Direction: <b>{signal}</b>
Confidence: <b>{confidence:.0%}</b>
Tier: {tier} ({tier_cfg['name']})

Entry: ${current_price:,.2f}
TP: ${tp_price:,.2f} ({tier_cfg['tp_pct']}%)
SL: ${sl_price:,.2f} ({tier_cfg['sl_pct']}%)

Reasoning:
{decision.get('reasoning', 'N/A')[:400]}"""
                        
                        send_telegram_alert(alert_msg)
                        logger.info(f"[TELEGRAM] {pair} {signal} alert sent")
                    except Exception as e:
                        logger.error(f"[TELEGRAM] Alert failed: {e}")
                
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
                # V3.1.24: Log decision for RL training
                if RL_ENABLED and rl_collector:
                    try:
                        # Get regime data for RL logging
                        rl_regime = get_market_regime_for_exit()
                        
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
            
            # V3.1.26: Track confidence override slots used
            confidence_slots_used = 0
            
            trades_executed = 0
            
            # V3.1.41: Count directional exposure BEFORE executing
            long_count = sum(1 for p in get_open_positions() if p.get("side","").upper() == "LONG")
            short_count = sum(1 for p in get_open_positions() if p.get("side","").upper() == "SHORT")
            MAX_SAME_DIRECTION = 6  # V3.1.42: Recovery - match Gemini Judge rule 9
            # V3.1.43: Allow 7 LONGs during Capitulation (F&G < 15)
            if trade_opportunities:
                first_fg = trade_opportunities[0]["decision"].get("fear_greed", 50)
                if first_fg < 15:
                    MAX_SAME_DIRECTION = 7
                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, raising directional limit to 7")
            
            for opportunity in trade_opportunities:
                confidence = opportunity["decision"]["confidence"]
                
                # Check if we still have slots
                if trades_executed >= available_slots:
                    # V3.1.26: High confidence override - can use extra slots
                    if confidence >= CONFIDENCE_OVERRIDE_THRESHOLD and confidence_slots_used < MAX_CONFIDENCE_SLOTS:
                        logger.info(f"CONFIDENCE OVERRIDE: {confidence:.0%} >= 85% - using conviction slot {confidence_slots_used + 1}/{MAX_CONFIDENCE_SLOTS}")
                        confidence_slots_used += 1
                    else:
                        # V3.1.42: SWAP LOGIC - replace worst loser with better signal
                        SWAP_MIN_CONFIDENCE = 0.80
                        if confidence >= SWAP_MIN_CONFIDENCE:
                            signal_dir = opportunity['decision']['decision']
                            # Find worst same-direction position that is losing
                            worst_pos = None
                            worst_pnl = 0
                            for p in open_positions:
                                p_side = p.get('side', '').upper()
                                p_pnl = float(p.get('unrealized_pnl', 0))
                                p_sym = p.get('symbol', '')
                                new_sym = opportunity['pair_info']['symbol']
                                # Only swap same direction, only swap losers, never swap the same symbol
                                if p_side == signal_dir and p_pnl < 0 and p_sym != new_sym:
                                    if worst_pos is None or p_pnl < worst_pnl:
                                        worst_pos = p
                                        worst_pnl = p_pnl
                            
                            if worst_pos:
                                w_sym = worst_pos.get('symbol', '')
                                w_side = worst_pos.get('side', '')
                                w_size = float(worst_pos.get('size', 0))
                                w_entry = float(worst_pos.get('entry_price', 0))
                                w_clean = w_sym.replace('cmt_', '').replace('usdt', '').upper()
                                new_pair = opportunity['pair']
                                
                                logger.warning(f"""SWAP: Closing {w_clean} {w_side} (PnL: ${worst_pnl:.2f}) to open {new_pair} {signal_dir} ({confidence:.0%})""")
                                
                                try:
                                    from smt_nightly_trade_v3_1 import close_position_manually, cancel_all_orders_for_symbol
                                    
                                    # Cancel existing orders on the position being swapped
                                    cancel_all_orders_for_symbol(w_sym)
                                    time.sleep(0.5)
                                    
                                    # Close the worst position
                                    close_result = close_position_manually(w_sym, w_side, w_size)
                                    close_oid = close_result.get('order_id')
                                    
                                    if close_oid:
                                        logger.info(f"SWAP: Closed {w_clean} {w_side}, order {close_oid}")
                                        
                                        # Log the swap to WEEX
                                        upload_ai_log_to_weex(
                                            stage=f"V3.1.42 SWAP: Close {w_side} {w_clean} for {new_pair}",
                                            input_data={
                                                'closed_symbol': w_sym,
                                                'closed_side': w_side,
                                                'closed_pnl': worst_pnl,
                                                'closed_entry': w_entry,
                                                'new_pair': new_pair,
                                                'new_signal': signal_dir,
                                                'new_confidence': confidence,
                                            },
                                            output_data={
                                                'action': 'POSITION_SWAP',
                                                'close_order_id': close_oid,
                                            },
                                            explanation=f"AI Swap: {w_clean} {w_side} (PnL: ${worst_pnl:.2f}) replaced by {new_pair} {signal_dir} at {confidence:.0%} conviction. Upgrading portfolio quality."
                                        )
                                        
                                        # Update tracker
                                        try:
                                            tracker.close_trade(w_sym, {'reason': f'swap_for_{new_pair}', 'pnl': worst_pnl})
                                        except:
                                            pass
                                        
                                        state.trades_closed += 1
                                        time.sleep(1)
                                        balance = get_balance()
                                        # Don't break - let the new trade execute in the normal flow below
                                    else:
                                        logger.warning(f"SWAP: Failed to close {w_clean}, skipping swap")
                                        continue
                                except Exception as e:
                                    logger.error(f"SWAP error: {e}")
                                    continue
                            else:
                                logger.info(f"Max positions reached, no losing same-direction position to swap, skipping")
                                break
                        else:
                            logger.info(f"Max positions reached, {confidence:.0%} < 80% swap threshold, skipping")
                            break
                
                # V3.1.41: DIRECTIONAL LIMIT CHECK
                sig_check = opportunity["decision"]["decision"]
                if sig_check == "LONG" and long_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {long_count} LONGs already open, skipping {opportunity['pair']} LONG")
                    continue
                if sig_check == "SHORT" and short_count >= MAX_SAME_DIRECTION:
                    logger.warning(f"DIRECTIONAL LIMIT: {short_count} SHORTs already open, skipping {opportunity['pair']} SHORT")
                    continue
                
                # V3.1.42: ASIAN SESSION FILTER (00-06 UTC) with EXTREME FEAR override
                import datetime as _dt_module
                utc_hour = _dt_module.datetime.now(_dt_module.timezone.utc).hour
                opp_confidence = opportunity["decision"]["confidence"]
                # V3.1.42: Pull fear_greed from decision dict (added in Judge return)
                opp_fear_greed = opportunity["decision"].get("fear_greed", 50)
                is_extreme_fear = opp_fear_greed < 20
                if 0 <= utc_hour < 6 and opp_confidence < 0.85 and not is_extreme_fear:
                    logger.warning(f"ASIAN SESSION FILTER: {utc_hour}:00 UTC, confidence {opp_confidence:.0%} < 85%, F&G={opp_fear_greed}, skipping {opportunity['pair']}")
                    continue
                elif 0 <= utc_hour < 6 and is_extreme_fear:
                    logger.info(f"EXTREME FEAR OVERRIDE: F&G={opp_fear_greed} < 20, bypassing Asian filter for {opportunity['pair']} ({opp_confidence:.0%})")
                
                tier = opportunity["tier"]
                tier_config = get_tier_config(tier)
                trade_type = opportunity["trade_type"]
                pair = opportunity["pair"]
                signal = opportunity["decision"]["decision"]
                confidence = opportunity["decision"]["confidence"]
                
                logger.info(f"")
                type_label = "[HEDGE] " if trade_type == "hedge" else ""
                logger.info(f"EXECUTING {type_label}{pair} {signal} (T{tier}) - {confidence:.0%}")
                
                # V3.1.38: Hedge partial close - reduce 50% of opposite position
                if trade_type == "hedge":
                    try:
                        opp_side = "SHORT" if signal == "LONG" else "LONG"
                        sym = opportunity["pair_info"]["symbol"]
                        sym_positions = position_map.get(sym, {})
                        opp_pos = sym_positions.get(opp_side)
                        
                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))
                            
                            # Calculate 50% close size
                            # V3.1.44 FIX: Import place_order + round_size_to_step
                            from smt_nightly_trade_v3_1 import round_size_to_step, place_order
                            close_size = round_size_to_step(opp_size * 0.5, sym)
                            
                            if close_size > 0:
                                close_type = "3" if opp_side == "LONG" else "4"
                                logger.info(f"  [HEDGE REDUCE] Closing 50% of {opp_side} {pair}: {close_size}/{opp_size} units")
                                
                                close_result = place_order(sym, close_type, close_size, tp_price=None, sl_price=None)
                                close_oid = close_result.get("order_id")
                                
                                if close_oid:
                                    logger.info(f"  [HEDGE REDUCE] Closed 50%: order {close_oid}")
                                    
                                    # Upload AI log for hedge partial close
                                    upload_ai_log_to_weex(
                                        stage=f"V3.1.38 Hedge Reduce: {opp_side} {pair}",
                                        input_data={
                                            "symbol": sym,
                                            "existing_side": opp_side,
                                            "existing_size": opp_size,
                                            "existing_entry": opp_entry,
                                            "existing_pnl": opp_pnl,
                                            "new_signal": signal,
                                            "new_confidence": confidence,
                                        },
                                        output_data={
                                            "action": "HEDGE_PARTIAL_CLOSE",
                                            "close_size": close_size,
                                            "remaining_size": opp_size - close_size,
                                            "close_order_id": close_oid,
                                        },
                                        explanation=f"AI Hedge: {signal} signal at {confidence:.0%} detected while {opp_side} is open (PnL: ${opp_pnl:.2f}). Reducing {opp_side} by 50% ({close_size} units) to free margin and reduce losing exposure before opening {signal}."
                                    )
                                    
                                    state.trades_closed += 1
                                else:
                                    logger.warning(f"  [HEDGE REDUCE] Partial close failed: {close_result}")
                            else:
                                logger.info(f"  [HEDGE REDUCE] Position too small to split, skipping partial close")
                    except Exception as e:
                        logger.error(f"  [HEDGE REDUCE] Error: {e}")
                        # Continue to open hedge even if partial close fails
                
                try:
                    trade_result = run_with_retry(
                        execute_trade,
                        opportunity["pair_info"], opportunity["decision"], balance
                    )
                    
                    if trade_result.get("executed"):
                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        
                        trade_result["confidence"] = confidence  # V3.1.41: Store for profit guard
                        tracker.add_trade(opportunity["pair_info"]["symbol"], trade_result)
                        state.trades_opened += 1
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
                
                # V3.1.41: Get entry confidence for adaptive guards
                entry_confidence = trade.get("confidence", 0.75)
                confidence_multiplier = 1.3 if entry_confidence >= 0.85 else 1.0  # High-conf trades get 30% wider guards
                
                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%) conf={entry_confidence:.0%}")
                # V3.1.46: PROFIT GUARDS DISABLED - Recovery mode
                # Problem: Guards close at +0.5-1.3% (capturing $5-30) but losses hit $50-299
                # Solution: Let TP orders do their job. Need +5-8% wins to recover.
                # Guards were cutting winners before they could become big wins.
                # V3.1.46: ALL PROFIT GUARDS DISABLED - Recovery mode
                # We need wins of $100-300, not $15-50. Let TP orders handle exits.
                # fade_pct = peak_pnl_pct - pnl_pct
                # if tier == 3: T3_profit_guard ... DISABLED
                # elif tier == 2: T2_profit_guard ... DISABLED
                # else: T1_profit_guard ... DISABLED
                
                # V3.1.46: TIME-BASED TIGHTENING DISABLED - Let winners run
                # Was closing positions that peaked at 0.5-1% after 2h. These need time to hit 5%+ TP.
                # if not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:
                #     if pnl_pct < peak_pnl_pct * 0.35:
                #         should_exit = True
                #         exit_reason = f"time_fade_guard ..."
                pass  # V3.1.46: Disabled
                

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
PORTFOLIO_REVIEW_INTERVAL = 300  # Every 5 minutes

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
            peak_data.append(f"  peak={peak:+.1f}%")
        
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
        
        prompt = f"""You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
You have learned from 40+ iterations of rules. Apply ALL of these rules strictly.

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

=== MANDATORY RULES (from 40+ iterations of battle-tested experience) ===

RULE 1 - DIRECTIONAL CONCENTRATION LIMIT:
Max 5 positions in the same direction normally. If 6+ LONGs or 6+ SHORTs, close the WEAKEST ones
(lowest PnL% or most faded from peak) until we have max 5. All-same-direction = cascade
liquidation risk in cross margin.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 7 LONGs. Violent bounces move ALL alts together,
so being long across the board IS the correct play. Only enforce max 5 if F&G >= 15.

RULE 2 - LET WINNERS RUN (V3.1.46 RECOVERY MODE):
Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours.
Our biggest problem is NOT fading profits -- it is that our biggest win ($55) is
5x smaller than our biggest loss ($299). We need $100-300 wins to recover.

RULE 3 - BREAKEVEN PATIENCE (V3.1.46 RECOVERY MODE):
If a position has faded to breakeven, DO NOT CLOSE. The SL order is our safety net.
Crypto is volatile - a position at 0% can rally to +5% in the next hour.
Only the SL should close losing/breakeven positions. Do NOT close manually.

RULE 4 - F&G CONTRADICTION CHECK (V3.1.47 RECOVERY):
If F&G < 20 (extreme fear), DO NOT CLOSE ANY POSITIONS unless they hit their SL order on WEEX.
Extreme fear creates the best entries. Every position we close at a loss during extreme fear
has historically bounced back within hours. Our SL orders are on the exchange - trust them.
The ONLY exception: If F&G > 80 (extreme greed) and all positions are LONG, close the weakest 1.
During extreme fear: NO closes. Period. Let SL handle risk.

RULE 5 - CORRELATED PAIR LIMIT:
BTC, ETH, SOL, DOGE all move together. If BTC LONG is open, max 2 more altcoin LONGs
in the same direction normally. Close the weakest correlated altcoin positions.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 6 correlated altcoin LONGs alongside BTC.
During capitulation bounces, correlation is your FRIEND - everything bounces together.
Only enforce the strict 3-altcoin limit when F&G >= 15.

RULE 6 - TIME-BASED PATIENCE (V3.1.46 RECOVERY MODE):
Do NOT close positions just because they have been open 2-4 hours.
Our TP targets are 5-8%. These moves take TIME to develop (4-12 hours for alts, 12-48h for BTC).
Only close if: max_hold_hours exceeded AND position is negative. Positive positions get extra time.

RULE 7 - WEEKEND/LOW LIQUIDITY (check if Saturday or Sunday):
On weekends, max 3 positions. Thinner books = more manipulation. Close extras.

RULE 8 - FUNDING COST AWARENESS:
If funding rate is positive and we are LONG, we PAY every 8h. If position is barely
profitable (+0.1-0.3%) and funding eats the profit, close it.
If funding rate is negative and we are SHORT, same logic.

RULE 9 - SLOT PATIENCE (V3.1.47 RECOVERY):
Do NOT close positions to free slots. Our SL orders protect against catastrophic loss.
A position at -0.3% after 1 hour can be at +3% after 4 hours. Crypto is volatile.
The ONLY reason to free a slot is if we have 8 positions AND a 90%+ conviction signal waiting.
Never close a position just because it is flat or slightly negative.

RULE 10 - GRACE PERIOD FOR HIGH-CONVICTION ENTRIES:
Positions opened < 30 minutes ago with confidence >= 85% get a GRACE PERIOD.
Do NOT close them unless they are losing more than -1%. Give the trade time to work.
EXTREME FEAR EXTENSION: If F&G < 20, the grace period extends to 120 MINUTES (2 hours)
for ALL positions opened during this period, regardless of confidence level.
Capitulation reversals are choppy - the first 1-2 hours often show red before the real
bounce materializes. Closing a -0.3% position at 45 minutes kills the reversal play.
Only close Extreme Fear entries within the first 2h if they breach -2% (hard stop).
Positions outside grace period (> 30min normal, > 2h extreme fear) have no protection.

=== YOUR JOB ===
Apply ALL 10 rules above. For each position, check every rule. Be PATIENT with winners.
Our biggest problem is NOT fading profits -- it is that we close winners too early.
Our biggest win is $55 but biggest loss is $299. We need $100+ wins to recover.
CRITICAL V3.1.47 RULE: Do NOT close ANY position at a loss. We have SL orders on WEEX.
Every time the PM closes a losing position, we lock in a loss AND pay fees AND lose the bounce.
Our data shows: -$267 lost in 8 hours from PM closing losers that would have recovered.
The ONLY acceptable closes are:
(a) Position past max_hold_hours AND losing more than -3% = stale loser, SL probably broken
(b) 8+ positions in same direction creating liquidation cascade risk
(c) Winning positions that hit max_hold_hours (take the profit)
NEVER close: positions under 6 hours old, positions losing less than -3%, positions during F&G < 20.

Respond with JSON ONLY (no markdown, no backticks):
{{"closes": [{{"symbol": "DOGEUSDT", "side": "SHORT", "reason": "Rule X: brief reason"}}, ...], "keep_reasons": "brief summary of why others are kept, referencing rule numbers"}}

If nothing should be closed, return:
{{"closes": [], "keep_reasons": "brief summary referencing which rules were checked"}}"""

        _rate_limit_gemini()
        
        from google import genai
        from google.genai.types import GenerateContentConfig
        
        client = genai.Client()
        config = GenerateContentConfig(temperature=0.1)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config
        )
        
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
                            tier_cfg = get_tier_config(trade.get("tier", get_tier_for_symbol(symbol)))
                            est_pnl = tier_cfg.get("tp_pct", 2.0)
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
        
        spike_msg = " SPIKE!" if regime.get('spike') else ""
        logger.info(f"[REGIME] Market: {regime['regime']} | 1h: {regime.get('change_1h', 0):+.1f}% | 4h: {regime['change_4h']:+.1f}% | 24h: {regime['change_24h']:+.1f}%{spike_msg}")
        
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
                gemini_portfolio_review()  # V3.1.40: Gemini reviews portfolio, closes bad positions
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
