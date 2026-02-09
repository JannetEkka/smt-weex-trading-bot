#!/usr/bin/env python3
"""
PATCH V3.1.36 - Fix Flow Capping + AI Log for ALL Closes
=========================================================

TWO FIXES:

1. FLOW CAPPING FIX (smt_nightly_trade_v3_1.py):
   - V3.1.18 capped extreme/heavy taker buying to NEUTRAL in BEARISH regime
   - Reasoning was "short covering, not reversal"
   - PROBLEM: This blinds the bot to genuine buying. BNB had taker ratio 3.37
     but Flow said NEUTRAL 50%. The bot can't see the bounce coming.
   - FIX: Remove the cap entirely. Let the raw signal through.
     The JUDGE already has regime-aware weights (V3.1.35) that discount
     counter-trend signals. Double-discounting kills all LONGs.

2. AI LOG FOR CLOSES (smt_daemon_v3_1.py):
   - TP/SL closes in monitor_positions() - NO ai_log uploaded
   - TP/SL closes in quick_cleanup_check() - NO ai_log uploaded
   - Smart exits already have ai_log (good)
   - Regime exits already have ai_log (good)
   - FIX: Add upload_ai_log_to_weex() call after every close type

RUN ON VM:
    cd ~/smt-weex-trading-bot/v3
    python3 patch_v3_1_36.py
    # Then restart daemon
"""

import re
import sys
import shutil
from datetime import datetime

NIGHTLY_FILE = "smt_nightly_trade_v3_1.py"
DAEMON_FILE = "smt_daemon_v3_1.py"

def backup(filename):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{filename}.bak_{ts}"
    shutil.copy2(filename, backup_name)
    print(f"  Backed up: {backup_name}")

def patch_flow_capping():
    """Remove BEARISH regime capping of taker buying signals."""
    print("\n=== PATCH 1: Remove Flow Capping ===")
    
    with open(NIGHTLY_FILE, "r") as f:
        content = f.read()
    
    # ---- FIX extreme_buying block ----
    # OLD: In BEARISH, cap extreme buying to NEUTRAL
    old_extreme = '''            elif extreme_buying:
                # V3.1.18: In BEARISH regime, extreme buying is likely SHORT COVERING
                # Don't trust it as a reversal signal
                if is_bearish:
                    print(f"  [FLOW] BEARISH regime: Capping extreme buying signal (short covering likely)")
                    signals.append(("NEUTRAL", 0.50, f"Taker buying {taker_ratio:.2f} but BEARISH regime (short cover)"))
                else:
                    signals.append(("LONG", 0.85, f"EXTREME taker buying: {taker_ratio:.2f}"))'''
    
    new_extreme = '''            elif extreme_buying:
                # V3.1.36: Let extreme buying signal through regardless of regime
                # Judge V3.1.35 already has regime-aware weights that discount counter-trend
                # Double-discounting was killing all LONG signals in BEARISH
                if is_bearish:
                    print(f"  [FLOW] BEARISH regime: Extreme buying {taker_ratio:.2f} (letting signal through)")
                signals.append(("LONG", 0.85, f"EXTREME taker buying: {taker_ratio:.2f}"))'''
    
    if old_extreme in content:
        content = content.replace(old_extreme, new_extreme)
        print("  [OK] Patched extreme_buying block")
    else:
        print("  [SKIP] extreme_buying block not found (may already be patched)")
    
    # ---- FIX heavy_buying block ----
    old_heavy = '''            elif heavy_buying:
                # V3.1.18: In BEARISH regime, cap heavy buying too
                if is_bearish:
                    print(f"  [FLOW] BEARISH regime: Reducing heavy buying signal")
                    signals.append(("NEUTRAL", 0.40, f"Taker buying {taker_ratio:.2f} but BEARISH (bounce?)"))'''
    
    new_heavy = '''            elif heavy_buying:
                # V3.1.36: Let heavy buying signal through regardless of regime
                if is_bearish:
                    print(f"  [FLOW] BEARISH regime: Heavy buying {taker_ratio:.2f} (letting signal through)")'''
    
    if old_heavy in content:
        content = content.replace(old_heavy, new_heavy)
        print("  [OK] Patched heavy_buying block")
    else:
        print("  [SKIP] heavy_buying block not found (may already be patched)")
    
    # Now we need to make sure the LONG signal is appended after the heavy_buying elif
    # The original code after the is_bearish block has an else clause for non-bearish:
    # We need to find what comes after and ensure LONG signal always appends
    
    # Look for the pattern after heavy_buying where the non-bearish path appends LONG
    old_heavy_else = '''                    signals.append(("NEUTRAL", 0.40, f"Taker buying {taker_ratio:.2f} but BEARISH (bounce?)"))
                else:
                    signals.append(("LONG", 0.50, f"Taker buy pressure: {taker_ratio:.2f}"))'''
    
    new_heavy_else = '''                    print(f"  [FLOW] BEARISH regime: Heavy buying {taker_ratio:.2f} (letting signal through)")
                signals.append(("LONG", 0.50, f"Taker buy pressure: {taker_ratio:.2f}"))'''
    
    if old_heavy_else in content:
        content = content.replace(old_heavy_else, new_heavy_else)
        print("  [OK] Patched heavy_buying else block")
    else:
        # Try the already-patched version with just the else
        old_heavy_else2 = '''                    print(f"  [FLOW] BEARISH regime: Heavy buying {taker_ratio:.2f} (letting signal through)")
                else:
                    signals.append(("LONG", 0.50, f"Taker buy pressure: {taker_ratio:.2f}"))'''
        
        new_heavy_else2 = '''                    print(f"  [FLOW] BEARISH regime: Heavy buying {taker_ratio:.2f} (letting signal through)")
                signals.append(("LONG", 0.50, f"Taker buy pressure: {taker_ratio:.2f}"))'''
        
        if old_heavy_else2 in content:
            content = content.replace(old_heavy_else2, new_heavy_else2)
            print("  [OK] Patched heavy_buying else block (variant 2)")
        else:
            print("  [WARN] heavy_buying else block not found - check manually")
    
    with open(NIGHTLY_FILE, "w") as f:
        f.write(content)
    
    print("  Flow capping fix complete")


def patch_ai_log_for_closes():
    """Add ai_log upload for TP/SL closes in monitor_positions and quick_cleanup_check."""
    print("\n=== PATCH 2: AI Log for All Close Outcomes ===")
    
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    # ---- FIX 1: monitor_positions() TP/SL close ----
    # Find the block where it detects position closed via TP/SL
    # It has tracker.close_trade but NO upload_ai_log_to_weex
    
    old_monitor_close = '''                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,'''
    
    # We need to insert ai_log BEFORE tracker.close_trade in monitor_positions
    # Actually let's insert AFTER the tracker.close_trade block
    # Find the full block including the lines after
    
    # Strategy: find "logger.info(f\"{symbol} CLOSED via TP/SL\")" and then find
    # the tracker.close_trade block that follows, and add ai_log after it
    
    # Let's look for the specific pattern in monitor_positions
    # The key identifier is the TP/SL detection in monitor_positions
    
    old_tpsl_monitor = '''                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "actual_pnl": actual_pnl,
                        "pnl_pct": pnl_pct,
                    })
                    
                    state.trades_closed += 1'''
    
    new_tpsl_monitor = '''                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "actual_pnl": actual_pnl,
                        "pnl_pct": pnl_pct,
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
                        logger.debug(f"AI log error for TP/SL close: {e}")'''
    
    if old_tpsl_monitor in content:
        content = content.replace(old_tpsl_monitor, new_tpsl_monitor)
        print("  [OK] Added ai_log to monitor_positions() TP/SL close")
    else:
        print("  [WARN] monitor_positions TP/SL block not found exactly - trying fuzzy match")
        # Try without actual_pnl/pnl_pct fields (older version)
        old_tpsl_v2 = '''                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                    })
                    
                    state.trades_closed += 1'''
        
        new_tpsl_v2 = '''                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                    })
                    
                    state.trades_closed += 1
                    
                    # V3.1.36: AI log for TP/SL closes
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    hit_tp = pnl_pct > 0
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
                        logger.debug(f"AI log error for TP/SL close: {e}")'''
        
        if old_tpsl_v2 in content:
            content = content.replace(old_tpsl_v2, new_tpsl_v2)
            print("  [OK] Added ai_log to monitor_positions() TP/SL close (v2 match)")
        else:
            print("  [FAIL] Could not find monitor_positions TP/SL block - MANUAL EDIT NEEDED")
    
    # ---- FIX 2: quick_cleanup_check() TP/SL close ----
    # This one closes with tracker.close_trade but also has no ai_log
    
    old_quick_close = '''                tracker.close_trade(symbol, {
                    "reason": "tp_sl_hit",
                    "cleanup": cleanup,
                    "symbol": symbol,
                    "pnl": round(pnl_usd, 2),
                    "hit_tp": hit_tp,
                })
                state.trades_closed += 1
                position_closed = True'''
    
    new_quick_close = '''                tracker.close_trade(symbol, {
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
                    logger.debug(f"AI log error for quick cleanup close: {e}")'''
    
    if old_quick_close in content:
        content = content.replace(old_quick_close, new_quick_close)
        print("  [OK] Added ai_log to quick_cleanup_check() close")
    else:
        print("  [WARN] quick_cleanup_check close block not found exactly")
        # Try without pnl/hit_tp fields
        old_quick_v2 = '''                tracker.close_trade(symbol, {
                    "reason": "tp_sl_hit",
                    "cleanup": cleanup,
                    "symbol": symbol,
                })
                state.trades_closed += 1
                position_closed = True'''
        
        new_quick_v2 = '''                tracker.close_trade(symbol, {
                    "reason": "tp_sl_hit",
                    "cleanup": cleanup,
                    "symbol": symbol,
                })
                state.trades_closed += 1
                position_closed = True
                
                # V3.1.36: AI log for quick cleanup closes
                symbol_clean = symbol.replace("cmt_", "").upper()
                try:
                    hours_open = 0
                    _trade = trade if isinstance(trade, dict) else {}
                    if _trade.get("opened_at"):
                        opened_at = datetime.fromisoformat(_trade["opened_at"].replace("Z", "+00:00"))
                        hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1.36 CLOSED: {symbol_clean}",
                        input_data={"symbol": symbol, "hours_open": round(hours_open, 2)},
                        output_data={"action": "CLOSED", "exit_type": "TP_SL"},
                        explanation=f"Position closed via TP/SL. {symbol_clean} held {hours_open:.1f}h."
                    )
                except Exception as e:
                    logger.debug(f"AI log error for quick cleanup close: {e}")'''
        
        if old_quick_v2 in content:
            content = content.replace(old_quick_v2, new_quick_v2)
            print("  [OK] Added ai_log to quick_cleanup_check() close (v2 match)")
        else:
            print("  [FAIL] Could not find quick_cleanup_check close block - MANUAL EDIT NEEDED")
    
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("  AI log fix complete")


def verify():
    """Verify patches applied correctly."""
    print("\n=== VERIFICATION ===")
    
    with open(NIGHTLY_FILE, "r") as f:
        nightly = f.read()
    
    with open(DAEMON_FILE, "r") as f:
        daemon = f.read()
    
    # Check flow capping is gone
    if "Capping extreme buying signal (short covering likely)" in nightly:
        print("  [FAIL] Flow capping still present in nightly!")
    elif "letting signal through" in nightly:
        print("  [OK] Flow capping removed - signals pass through")
    else:
        print("  [WARN] Could not verify flow capping fix")
    
    # Check ai_log in daemon closes
    count = daemon.count("V3.1.36")
    if count >= 2:
        print(f"  [OK] Found {count} V3.1.36 ai_log blocks in daemon")
    else:
        print(f"  [WARN] Only {count} V3.1.36 blocks found (expected 2+)")
    
    # Check old capping patterns are gone
    if "Reducing heavy buying signal" in nightly:
        print("  [FAIL] Heavy buying cap still present!")
    else:
        print("  [OK] Heavy buying cap removed")


if __name__ == "__main__":
    print("=" * 60)
    print("PATCH V3.1.36 - Flow Capping Fix + AI Log for Closes")
    print("=" * 60)
    
    backup(NIGHTLY_FILE)
    backup(DAEMON_FILE)
    
    patch_flow_capping()
    patch_ai_log_for_closes()
    verify()
    
    print("\n" + "=" * 60)
    print("DONE. Now restart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
    print("  tail -f daemon.log")
    print("\nThen commit:")
    print("  git add -A && git commit -m 'V3.1.36: Fix flow capping + ai_log for all closes' && git push")
    print("=" * 60)
