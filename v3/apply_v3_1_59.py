#!/usr/bin/env python3
"""
V3.1.59 Patch Applier
=====================
Smart PnL Monitor + Cryptoracle Historical + Confidence-Tiered Leverage + AI Log Coverage

Run on VM:
  cd ~/smt-weex-trading-bot/v3
  python3 apply_v3_1_59.py

Then:
  pkill -9 -f smt_daemon_v3_1
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  tail -f logs/daemon_v3_1_7_$(date +%Y%m%d).log
"""

import sys
import os
import shutil
from datetime import datetime

def backup_file(filepath):
    """Create timestamped backup."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{filepath}.bak_{ts}"
    shutil.copy2(filepath, backup)
    print(f"  Backed up: {backup}")
    return backup

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def replace_once(content, old, new, label):
    """Replace exactly once. Fail if not found or found multiple times."""
    count = content.count(old)
    if count == 0:
        print(f"  WARNING: '{label}' - pattern NOT FOUND. Skipping.")
        return content, False
    if count > 1:
        print(f"  WARNING: '{label}' - pattern found {count} times. Replacing first occurrence only.")
    result = content.replace(old, new, 1)
    print(f"  OK: {label}")
    return result, True


def patch_leverage_manager():
    """FILE 1: Replace leverage_manager.py entirely."""
    print("\n=== PATCHING leverage_manager.py ===")
    filepath = "leverage_manager.py"
    
    if os.path.exists(filepath):
        backup_file(filepath)
    
    new_content = '''"""
Smart Leverage Manager V3.1.59 - Confidence-Tiered
Leverage scales with signal confidence, NOT just tier.
High confidence (90%+) gets more leverage. Low confidence stays conservative.
Safety: SL always triggers well before liquidation distance.

Liquidation distances:
  18x = ~5.0% (SL at 2.5% = 2.5% buffer)
  15x = ~6.0% (SL at 2.5% = 3.5% buffer)
  12x = ~7.5% (SL at 2.0% = 5.5% buffer)
  10x = ~9.0% (SL at 2.0% = 7.0% buffer)
"""

# V3.1.59: Confidence-tiered leverage matrix
# Key: (tier, confidence_bracket) -> leverage
# Confidence brackets: "ultra" (90%+), "high" (80-89%), "normal" (<80%)
LEVERAGE_MATRIX = {
    (1, "ultra"):  18,  # T1 Blue Chip, 90%+ confidence
    (1, "high"):   15,  # T1 Blue Chip, 80-89%
    (1, "normal"): 12,  # T1 Blue Chip, <80%
    (2, "ultra"):  15,  # T2 Mid Cap, 90%+
    (2, "high"):   12,  # T2 Mid Cap, 80-89%
    (2, "normal"): 10,  # T2 Mid Cap, <80%
    (3, "ultra"):  12,  # T3 Small Cap, 90%+
    (3, "high"):   10,  # T3 Small Cap, 80-89%
    (3, "normal"):  8,  # T3 Small Cap, <80%
}


class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 8
        self.MAX_LEVERAGE = 18  # V3.1.59: Up from 15, but only for ultra-conf
        self.MAX_POSITION_PCT = 0.35  # V3.1.59: Up from 0.20
        self.MIN_LIQUIDATION_DISTANCE = 4  # 4% min buffer above SL

    def calculate_safe_leverage(self, pair_tier: int, volatility: float = 2.0,
                                 regime: str = "NEUTRAL", confidence: float = 0.75) -> int:
        """V3.1.59: Confidence-tiered leverage selection."""

        # Determine confidence bracket
        if confidence >= 0.90:
            bracket = "ultra"
        elif confidence >= 0.80:
            bracket = "high"
        else:
            bracket = "normal"

        base = LEVERAGE_MATRIX.get((pair_tier, bracket), 10)

        # Reduce in high volatility
        if volatility > 4.0:
            base -= 2
        elif volatility > 3.0:
            base -= 1

        # Reduce in uncertain regime (only for non-ultra)
        if regime == "NEUTRAL" and bracket != "ultra":
            base -= 1

        return max(self.MIN_LEVERAGE, min(base, self.MAX_LEVERAGE))

    def check_liquidation_distance(self, entry_price: float, current_price: float,
                                   side: str, leverage: int) -> dict:
        liq_pct = 90 / leverage
        if side == "LONG":
            liq_price = entry_price * (1 - liq_pct / 100)
            distance_pct = ((current_price - liq_price) / current_price) * 100
        else:
            liq_price = entry_price * (1 + liq_pct / 100)
            distance_pct = ((liq_price - current_price) / current_price) * 100

        return {
            "liquidation_price": liq_price,
            "distance_pct": distance_pct,
            "safe": distance_pct > self.MIN_LIQUIDATION_DISTANCE
        }


# Singleton
_manager = LeverageManager()


def get_safe_leverage(tier: int, volatility: float = 2.0, regime: str = "NEUTRAL",
                      confidence: float = 0.75) -> int:
    return _manager.calculate_safe_leverage(tier, volatility, regime, confidence)
'''
    write_file(filepath, new_content)
    print("  DONE: leverage_manager.py replaced")


def patch_cryptoracle_client():
    """FILE 2: Append fetch_sentiment_history to cryptoracle_client.py."""
    print("\n=== PATCHING cryptoracle_client.py ===")
    filepath = "cryptoracle_client.py"
    
    if not os.path.exists(filepath):
        print("  ERROR: cryptoracle_client.py not found!")
        return
    
    backup_file(filepath)
    content = read_file(filepath)
    
    # Check if already patched
    if "fetch_sentiment_history" in content:
        print("  SKIP: fetch_sentiment_history already exists")
        return
    
    addition = '''


# ============================================================
# V3.1.59: Cryptoracle Historical Time-Series
# ============================================================

def fetch_sentiment_history(token: str, hours_back: int = 4, time_type: str = "1h") -> Optional[list]:
    """
    V3.1.59: Fetch sentiment TIME SERIES (all periods, not just latest).
    Returns list of dicts sorted oldest-first:
    [
        {"time": "2026-02-11 03:00", "net_sentiment": 0.58, "momentum": 0.82, "price_gap": 1.95},
        {"time": "2026-02-11 04:00", "net_sentiment": 0.61, ...},
        ...
    ]
    """
    cache_key = f"hist_{token}_{time_type}_{hours_back}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < _CACHE_TTL:
            return cached_data

    try:
        _rate_limit()

        end_time = _utc8_now()
        start_time = _utc8_hours_ago(hours_back)

        payload = {
            "endpoints": SENTIMENT_ENDPOINTS,
            "startTime": start_time,
            "endTime": end_time,
            "timeType": time_type,
            "token": [token.upper()]
        }

        headers = {
            "X-API-KEY": CRYPTORACLE_API_KEY,
            "Content-Type": "application/json"
        }

        resp = requests.post(
            f"{CRYPTORACLE_BASE_URL}/v2.1/endpoint",
            json=payload,
            headers=headers,
            timeout=15
        )

        if resp.status_code != 200:
            return None

        raw = resp.json()
        if raw.get("code") != 200:
            return None

        data_list = raw.get("data", [])
        if not data_list:
            return None

        token_data = None
        for item in data_list:
            if item.get("token", "").upper() == token.upper():
                token_data = item
                break

        if not token_data:
            return None

        periods = token_data.get("timePeriods", [])

        # Build time series (periods come newest-first, we reverse)
        series = []
        for period in reversed(periods):
            values = {}
            for d in period.get("data", []):
                ep = d.get("endpoint", "")
                try:
                    values[ep] = float(d.get("value", "0"))
                except (ValueError, TypeError):
                    values[ep] = 0.0

            series.append({
                "time": period.get("startTime", period.get("time", "?")),
                "net_sentiment": round(values.get("CO-A-02-03", 0.5), 4),
                "momentum": round(values.get("CO-S-01-01", 0.0), 4),
                "price_gap": round(values.get("CO-S-01-05", 0.0), 4),
            })

        _cache[cache_key] = (time.time(), series)
        return series

    except Exception as e:
        print(f"  [CRYPTORACLE] History error: {e}")
        return None
'''
    
    content += addition
    write_file(filepath, content)
    print("  DONE: fetch_sentiment_history appended")


def patch_smt_nightly():
    """FILE 3: Patch smt_nightly_trade_v3_1.py."""
    print("\n=== PATCHING smt_nightly_trade_v3_1.py ===")
    filepath = "smt_nightly_trade_v3_1.py"
    
    if not os.path.exists(filepath):
        print("  ERROR: smt_nightly_trade_v3_1.py not found!")
        return
    
    backup_file(filepath)
    content = read_file(filepath)
    changes = 0
    
    # --- Change 1: Pass confidence to leverage manager ---
    old_leverage = """    # V3.1.31: Dynamic leverage with regime awareness
    try:
        from leverage_manager import get_safe_leverage
        regime_data = REGIME_CACHE.get("regime", 300)
        current_regime = regime_data.get("regime", "NEUTRAL") if regime_data else "NEUTRAL"
        safe_leverage = get_safe_leverage(tier, regime=current_regime)
        print(f"  [LEVERAGE] Tier {tier} ({current_regime}): Using {safe_leverage}x")
    except Exception as e:
        safe_leverage = 10  # Competition fallback
        print(f"  [LEVERAGE] Fallback to {safe_leverage}x: {e}")"""
    
    new_leverage = """    # V3.1.59: Confidence-tiered leverage
    trade_confidence = decision.get("confidence", 0.75)
    try:
        from leverage_manager import get_safe_leverage
        regime_data = REGIME_CACHE.get("regime", 300)
        current_regime = regime_data.get("regime", "NEUTRAL") if regime_data else "NEUTRAL"
        safe_leverage = get_safe_leverage(tier, regime=current_regime, confidence=trade_confidence)
        conf_bracket = "ULTRA" if trade_confidence >= 0.90 else "HIGH" if trade_confidence >= 0.80 else "NORMAL"
        print(f"  [LEVERAGE] Tier {tier} ({current_regime}, {conf_bracket} {trade_confidence:.0%}): Using {safe_leverage}x")
    except Exception as e:
        safe_leverage = 10  # Competition fallback
        print(f"  [LEVERAGE] Fallback to {safe_leverage}x: {e}")"""
    
    content, ok = replace_once(content, old_leverage, new_leverage, "Nightly Change 1: Confidence-tiered leverage")
    if ok: changes += 1
    
    # --- Change 2: Position sizing with FLOW+WHALE alignment ---
    old_sizing = """            # Position sizing
            base_size = balance * 0.20  # V3.1.42: Recovery - 20% base
            if confidence > 0.85:
                position_usdt = base_size * 1.5  # 30% of balance
            elif confidence > 0.75:
                position_usdt = base_size * 1.25  # 25% of balance
            else:
                position_usdt = base_size * 1.0  # 20% of balance"""
    
    new_sizing = """            # V3.1.59: Confidence-tiered position sizing with FLOW+WHALE alignment
            flow_whale_aligned = False
            flow_vote = next((v for v in persona_votes if v.get("persona") == "FLOW"), None)
            whale_vote = next((v for v in persona_votes if v.get("persona") == "WHALE"), None)
            if flow_vote and whale_vote:
                if (flow_vote.get("signal") == decision == whale_vote.get("signal")
                    and flow_vote.get("confidence", 0) >= 0.60
                    and whale_vote.get("confidence", 0) >= 0.60):
                    flow_whale_aligned = True

            base_size = balance * 0.22  # V3.1.59: Slight bump from 20%
            if confidence >= 0.90 and flow_whale_aligned:
                position_usdt = base_size * 1.6  # 35% of balance - ultra conviction
                print(f"  [SIZING] ULTRA: 90%+ conf + FLOW/WHALE aligned -> 35%")
            elif confidence > 0.85:
                position_usdt = base_size * 1.4  # 31% of balance
            elif confidence > 0.75:
                position_usdt = base_size * 1.2  # 26% of balance
            else:
                position_usdt = base_size * 1.0  # 22% of balance"""
    
    content, ok = replace_once(content, old_sizing, new_sizing, "Nightly Change 2: FLOW+WHALE sizing")
    if ok: changes += 1
    
    # --- Change 3: AI log for leverage decision ---
    # Add AI log right after the leverage print line in execute_trade
    old_notional = """    notional_usdt = position_usdt * safe_leverage
    raw_size = notional_usdt / current_price
    
    if raw_size <= 0:"""
    
    new_notional = """    notional_usdt = position_usdt * safe_leverage
    raw_size = notional_usdt / current_price

    # V3.1.59: AI log for leverage/sizing decision
    upload_ai_log_to_weex(
        stage=f"V3.1.59 Leverage Decision: {signal} {symbol.replace('cmt_', '').upper()}",
        input_data={
            "tier": tier,
            "confidence": trade_confidence,
            "regime": current_regime if 'current_regime' in dir() else "UNKNOWN",
            "balance": balance,
            "position_usdt_margin": round(position_usdt, 2),
        },
        output_data={
            "leverage": safe_leverage,
            "notional_usdt": round(notional_usdt, 2),
            "conf_bracket": conf_bracket if 'conf_bracket' in dir() else "UNKNOWN",
        },
        explanation=f"V3.1.59 Confidence-tiered leverage: {safe_leverage}x for Tier {tier} at {trade_confidence:.0%} confidence. Margin: ${position_usdt:.0f}, Notional: ${notional_usdt:.0f}."
    )

    if raw_size <= 0:"""
    
    content, ok = replace_once(content, old_notional, new_notional, "Nightly Change 3: AI log for leverage decision")
    if ok: changes += 1
    
    write_file(filepath, content)
    print(f"  DONE: {changes}/3 changes applied to smt_nightly_trade_v3_1.py")


def patch_smt_daemon():
    """FILE 4: Patch smt_daemon_v3_1.py."""
    print("\n=== PATCHING smt_daemon_v3_1.py ===")
    filepath = "smt_daemon_v3_1.py"
    
    if not os.path.exists(filepath):
        print("  ERROR: smt_daemon_v3_1.py not found!")
        return
    
    backup_file(filepath)
    content = read_file(filepath)
    changes = 0
    
    # --- Change 1: Add PnL history global near top ---
    # Insert after _last_portfolio_review line
    old_pm_global = """_last_portfolio_review = 0
PORTFOLIO_REVIEW_INTERVAL = 900  # Every 5 minutes"""
    
    new_pm_global = """_last_portfolio_review = 0
PORTFOLIO_REVIEW_INTERVAL = 900  # Every 5 minutes

# V3.1.59: PnL trajectory history for Smart Monitor
# Stores last 10 readings per symbol: [{"pnl_pct": x, "peak": y, "ts": z}, ...]
_pnl_history = {}
_PNL_HISTORY_MAX = 10"""
    
    content, ok = replace_once(content, old_pm_global, new_pm_global, "Daemon Change 1: PnL history global")
    if ok: changes += 1
    
    # --- Change 2: Record PnL in monitor_positions ---
    old_monitor_log = """                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%) conf={entry_confidence:.0%}")"""
    
    new_monitor_log = """                logger.info(f"  [MONITOR] {symbol} T{tier}: {pnl_pct:+.2f}% (peak: {peak_pnl_pct:.2f}%) conf={entry_confidence:.0%}")
                
                # V3.1.59: Record PnL trajectory for Smart Monitor
                if symbol not in _pnl_history:
                    _pnl_history[symbol] = []
                _pnl_history[symbol].append({
                    "pnl_pct": round(pnl_pct, 3),
                    "peak": round(peak_pnl_pct, 3),
                    "ts": datetime.now(timezone.utc).strftime("%H:%M"),
                })
                if len(_pnl_history[symbol]) > _PNL_HISTORY_MAX:
                    _pnl_history[symbol] = _pnl_history[symbol][-_PNL_HISTORY_MAX:]"""
    
    content, ok = replace_once(content, old_monitor_log, new_monitor_log, "Daemon Change 2: Record PnL trajectory")
    if ok: changes += 1
    
    # --- Change 3: Enhance peak_data with trajectory ---
    old_peak = """            peak_data.append(f"  peak={peak:+.1f}%, whale={whale_d}@{whale_c:.0%}")"""
    
    new_peak = """            # V3.1.59: Include PnL trajectory
            trajectory = _pnl_history.get(p.get("symbol", ""), [])
            traj_str = " -> ".join([f"{r['pnl_pct']:+.2f}%" for r in trajectory[-5:]]) if trajectory else "no history"
            peak_data.append(f"  peak={peak:+.1f}%, whale={whale_d}@{whale_c:.0%}, traj=[{traj_str}]")"""
    
    content, ok = replace_once(content, old_peak, new_peak, "Daemon Change 3: Peak data with trajectory")
    if ok: changes += 1
    
    # --- Change 4: Add Cryptoracle context + trajectory info to PM prompt ---
    old_days_left = """Days left in competition: {days_left}

=== MANDATORY RULES (V3.1.55 - 45+ iterations of battle-tested experience) ==="""
    
    new_days_left = """Days left in competition: {days_left}

=== CRYPTORACLE INTELLIGENCE ===
{cryptoracle_context}

=== PNL TRAJECTORY PATTERNS ===
Each position shows its last 5 PnL readings (newest last).
Look for: fading from peak (consider tightening), accelerating (let run), flat (stale trade).
If Cryptoracle sentiment supports the position direction, be more patient even if fading.

=== MANDATORY RULES (V3.1.59 - 47+ iterations of battle-tested experience) ==="""
    
    content, ok = replace_once(content, old_days_left, new_days_left, "Daemon Change 4: Cryptoracle + trajectory in PM prompt")
    if ok: changes += 1
    
    # --- Change 5: Add new Rule 3b for trajectory-based decisions ---
    old_rule3 = """RULE 3 - LET WINNERS RUN:
Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours."""
    
    new_rule3 = """RULE 3 - LET WINNERS RUN:
Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours.

RULE 3b - TRAJECTORY-BASED EXIT (V3.1.59):
If a position's PnL trajectory shows 5+ readings of steady decline from a peak > 1.0%
(e.g. +1.8% -> +1.5% -> +1.2% -> +0.9% -> +0.6%), the trade thesis may be invalidating.
Consider closing to lock partial profit UNLESS Cryptoracle sentiment still supports the direction.
If Cryptoracle momentum is positive for our direction, let it ride despite the fade."""
    
    content, ok = replace_once(content, old_rule3, new_rule3, "Daemon Change 5: Rule 3b trajectory exit")
    if ok: changes += 1
    
    # --- Change 6: Build cryptoracle_context before the prompt ---
    # We need to insert the cryptoracle fetch code BEFORE the prompt = f""" line
    # Find the line that starts building the prompt
    old_prompt_start = """        prompt = f\"\"\"You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
You have learned from 40+ iterations of rules. Apply ALL of these rules strictly."""
    
    new_prompt_start = """        # V3.1.59: Fetch Cryptoracle historical context for PM
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
                cryptoracle_context += f"\\nPrediction Market (BTC): {pm_val:+.4f} ({pm_sig} {pm_str})"
            
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
        
        prompt = f\\"\\"\\"You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
You have learned from 40+ iterations of rules. Apply ALL of these rules strictly."""
    
    content, ok = replace_once(content, old_prompt_start, new_prompt_start, "Daemon Change 6: Cryptoracle fetch + AI log before PM prompt")
    if ok: changes += 1
    
    # --- Change 7: AI log for PM decision (after Gemini responds) ---
    # Find the keep_reasons log line
    old_keep_log = """        logger.info(f"[PORTFOLIO] Review complete. Keep reasons: {keep_reasons[:100]}")"""
    
    new_keep_log = """        logger.info(f"[PORTFOLIO] Review complete. Keep reasons: {keep_reasons[:100]}")
        
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
        )"""
    
    content, ok = replace_once(content, old_keep_log, new_keep_log, "Daemon Change 7: AI log for PM decision")
    if ok: changes += 1
    
    # --- Change 8: AI log for regime exit decisions ---
    old_regime_no_exit = """        logger.info(f"[REGIME] No positions need regime exit")"""
    
    # This might appear multiple times, we only want to add the log once near the function end
    # Let's be more specific by including surrounding context
    # Actually let's skip this one - regime exit already has AI logs (line 1306)
    
    # --- Change 9: Clean up _pnl_history when position closes ---
    old_close_cleanup = """                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "pnl": actual_pnl,
                        "pnl_pct": pnl_pct
                    })
                    state.trades_closed += 1"""
    
    new_close_cleanup = """                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "pnl": actual_pnl,
                        "pnl_pct": pnl_pct
                    })
                    state.trades_closed += 1
                    
                    # V3.1.59: Clean PnL history for closed position
                    if symbol in _pnl_history:
                        del _pnl_history[symbol]"""
    
    content, ok = replace_once(content, old_close_cleanup, new_close_cleanup, "Daemon Change 8: Clean PnL history on close")
    if ok: changes += 1
    
    write_file(filepath, content)
    print(f"  DONE: {changes}/8 changes applied to smt_daemon_v3_1.py")


def verify_patches():
    """Quick verification that patches applied correctly."""
    print("\n=== VERIFICATION ===")
    
    checks = [
        ("leverage_manager.py", "LEVERAGE_MATRIX", "Confidence matrix"),
        ("leverage_manager.py", "confidence: float = 0.75", "Confidence parameter"),
        ("cryptoracle_client.py", "fetch_sentiment_history", "History function"),
        ("smt_nightly_trade_v3_1.py", "trade_confidence = decision.get", "Confidence pass-through"),
        ("smt_nightly_trade_v3_1.py", "flow_whale_aligned", "FLOW+WHALE sizing"),
        ("smt_nightly_trade_v3_1.py", "V3.1.59 Leverage Decision", "Leverage AI log"),
        ("smt_daemon_v3_1.py", "_pnl_history", "PnL history global"),
        ("smt_daemon_v3_1.py", "V3.1.59: Record PnL trajectory", "PnL recording"),
        ("smt_daemon_v3_1.py", "traj=[{traj_str}]", "Trajectory in peak_data"),
        ("smt_daemon_v3_1.py", "CRYPTORACLE INTELLIGENCE", "Cryptoracle in PM prompt"),
        ("smt_daemon_v3_1.py", "RULE 3b", "Trajectory exit rule"),
        ("smt_daemon_v3_1.py", "V3.1.59 Portfolio Review Start", "PM AI log start"),
        ("smt_daemon_v3_1.py", "V3.1.59 Portfolio Review Decision", "PM AI log decision"),
    ]
    
    passed = 0
    failed = 0
    for filepath, pattern, label in checks:
        if os.path.exists(filepath):
            content = read_file(filepath)
            if pattern in content:
                print(f"  OK: {label}")
                passed += 1
            else:
                print(f"  FAIL: {label} - '{pattern}' not found in {filepath}")
                failed += 1
        else:
            print(f"  FAIL: {filepath} not found")
            failed += 1
    
    print(f"\n  Results: {passed} passed, {failed} failed out of {len(checks)}")
    return failed == 0


def main():
    print("=" * 60)
    print("V3.1.59 PATCH APPLIER")
    print("Smart PnL Monitor + Cryptoracle Historical + Confidence Leverage")
    print("=" * 60)
    
    # Check we're in the right directory
    if not os.path.exists("smt_daemon_v3_1.py"):
        print("\nERROR: smt_daemon_v3_1.py not found!")
        print("Make sure you're in ~/smt-weex-trading-bot/v3/")
        sys.exit(1)
    
    print("\nThis will patch 4 files:")
    print("  1. leverage_manager.py (full replace)")
    print("  2. cryptoracle_client.py (append history function)")
    print("  3. smt_nightly_trade_v3_1.py (3 changes)")
    print("  4. smt_daemon_v3_1.py (8 changes)")
    print("\nBackups will be created for all files.")
    print()
    
    # Apply patches
    patch_leverage_manager()
    patch_cryptoracle_client()
    patch_smt_nightly()
    patch_smt_daemon()
    
    # Verify
    all_good = verify_patches()
    
    print("\n" + "=" * 60)
    if all_good:
        print("ALL PATCHES APPLIED SUCCESSFULLY")
    else:
        print("SOME PATCHES FAILED - check warnings above")
        print("Backups are available as .bak_* files")
    
    print("\nNext steps:")
    print("  1. pkill -9 -f smt_daemon_v3_1")
    print("  2. nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &")
    print("  3. tail -f logs/daemon_v3_1_7_$(date +%Y%m%d).log")
    print("  4. git add . && git commit -m 'V3.1.59: Smart PnL Monitor + Cryptoracle Historical + Confidence-Tiered Leverage' && git push")
    print("=" * 60)


if __name__ == "__main__":
    main()
