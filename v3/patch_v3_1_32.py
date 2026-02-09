#!/usr/bin/env python3
"""V3.1.32 Patch: ATR-based dynamic SL to stop getting clipped by noise"""

import re

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

# ============================================================
# CHANGE 1: Widen tier config SL/TP + raise force exit
# ============================================================
old_tier = '''TIER_CONFIG = {
    1: {  # BTC, ETH, BNB, LTC - Stable, slow movers
        "name": "STABLE",
        "tp_pct": 2.5,           # V3.1.31: Tighter TP for faster compounding (was 4%)
        "sl_pct": 1.5,           # 1.5% SL - protect capital
        "max_hold_hours": 72,    # Keep 72h (3 days)
        "early_exit_hours": 12,  # Check early exit after 12h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 12h
        "force_exit_loss_pct": -2.0,  # Hard stop at -2%
    },
    2: {  # SOL - Mid volatility
        "name": "MID",
        "tp_pct": 2.0,           # V3.1.31: Tighter TP for faster compounding (was 3.5%)
        "sl_pct": 1.5,           # 1.5% SL
        "max_hold_hours": 48,    # Keep 48h (2 days)
        "early_exit_hours": 6,   # Check early exit after 6h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h
        "force_exit_loss_pct": -2.0,  # Hard stop at -2%
    },
    3: {  # DOGE, XRP, ADA - Fast movers
        "name": "FAST",
        "tp_pct": 1.5,           # V3.1.31: Quick scalps, fast compound (was 3%)
        "sl_pct": 1.5,           # 1.5% SL
        "max_hold_hours": 24,    # Keep 24h
        "early_exit_hours": 6,   # Check early exit after 6h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h
        "force_exit_loss_pct": -2.0,  # Hard stop at -2%
    },
}'''

new_tier = '''TIER_CONFIG = {
    1: {  # BTC, ETH, BNB, LTC - Stable, slow movers
        "name": "STABLE",
        "tp_pct": 4.0,           # V3.1.32: 1.6x SL, wider to survive 4h ATR noise
        "sl_pct": 2.5,           # V3.1.32: BTC 4h ATR ~2.1%, need SL outside noise
        "max_hold_hours": 72,
        "early_exit_hours": 12,
        "early_exit_loss_pct": -2.0,  # V3.1.32: Raised to match wider SL
        "force_exit_loss_pct": -3.5,  # V3.1.32: Raised so daemon doesn't kill before WEEX SL
    },
    2: {  # SOL - Mid volatility
        "name": "MID",
        "tp_pct": 3.5,           # V3.1.32: 1.75x SL
        "sl_pct": 2.0,           # V3.1.32: SOL swings 2-3% on 4h easily
        "max_hold_hours": 48,
        "early_exit_hours": 6,
        "early_exit_loss_pct": -1.8,  # V3.1.32: Raised
        "force_exit_loss_pct": -3.0,  # V3.1.32: Raised
    },
    3: {  # DOGE, XRP, ADA - Fast movers
        "name": "FAST",
        "tp_pct": 3.0,           # V3.1.32: 1.5x SL, still fast but survivable
        "sl_pct": 2.0,           # V3.1.32: Was 1.5% = stopped on every bounce
        "max_hold_hours": 24,
        "early_exit_hours": 6,
        "early_exit_loss_pct": -1.8,  # V3.1.32: Raised
        "force_exit_loss_pct": -3.0,  # V3.1.32: Raised
    },
}'''

if old_tier in code:
    code = code.replace(old_tier, new_tier)
    print("[OK] CHANGE 1: Tier config updated (wider SL/TP, raised force exit)")
else:
    print("[WARN] CHANGE 1: Tier config not found exactly - trying partial match")
    # Try matching just the structure
    if '"sl_pct": 1.5,' in code and '"force_exit_loss_pct": -2.0,' in code:
        code = code.replace('"tp_pct": 2.5,           # V3.1.31: Tighter TP for faster compounding (was 4%)', '"tp_pct": 4.0,           # V3.1.32: 1.6x SL, wider to survive 4h ATR noise')
        code = code.replace('"tp_pct": 2.0,           # V3.1.31: Tighter TP for faster compounding (was 3.5%)', '"tp_pct": 3.5,           # V3.1.32: 1.75x SL')
        code = code.replace('"tp_pct": 1.5,           # V3.1.31: Quick scalps, fast compound (was 3%)', '"tp_pct": 3.0,           # V3.1.32: 1.5x SL, still fast but survivable')
        code = code.replace('"sl_pct": 1.5,           # 1.5% SL - protect capital', '"sl_pct": 2.5,           # V3.1.32: BTC 4h ATR ~2.1%, need SL outside noise')
        code = code.replace('"sl_pct": 1.5,           # 1.5% SL\n        "max_hold_hours": 48,', '"sl_pct": 2.0,           # V3.1.32: SOL swings 2-3% on 4h easily\n        "max_hold_hours": 48,')
        code = code.replace('"sl_pct": 1.5,           # 1.5% SL\n        "max_hold_hours": 24,', '"sl_pct": 2.0,           # V3.1.32: Was 1.5% = stopped on every bounce\n        "max_hold_hours": 24,')
        # Fix early_exit and force_exit
        code = code.replace('"early_exit_loss_pct": -1.5,  # Exit if -1.5% after 12h', '"early_exit_loss_pct": -2.0,  # V3.1.32: Raised to match wider SL')
        code = code.replace('"early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h\n        "force_exit_loss_pct": -2.0,  # Hard stop at -2%\n    },\n    3:', '"early_exit_loss_pct": -1.8,  # V3.1.32: Raised\n        "force_exit_loss_pct": -3.0,  # V3.1.32: Raised\n    },\n    3:')
        code = code.replace('"early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h\n        "force_exit_loss_pct": -2.0,  # Hard stop at -2%\n    },\n}', '"early_exit_loss_pct": -1.8,  # V3.1.32: Raised\n        "force_exit_loss_pct": -3.0,  # V3.1.32: Raised\n    },\n}')
        code = code.replace('"force_exit_loss_pct": -2.0,  # Hard stop at -2%\n    },\n    2:', '"force_exit_loss_pct": -3.5,  # V3.1.32: Raised so daemon doesn\'t kill before WEEX SL\n    },\n    2:')
        print("[OK] CHANGE 1: Tier config updated via partial match")
    else:
        print("[FAIL] CHANGE 1: Could not find tier config to patch")


# ============================================================
# CHANGE 2: Dynamic ATR-based SL in execute_trade()
# ============================================================
old_execute_sl = '''    # V3.1.1: Use tier-based TP/SL from decision (set by Judge)
    tp_pct = decision.get("take_profit_percent", tier_config["tp_pct"]) / 100
    sl_pct = decision.get("stop_loss_percent", tier_config["sl_pct"]) / 100'''

new_execute_sl = '''    # V3.1.32: ATR-based dynamic SL - stops outside normal volatility noise
    try:
        atr_data = get_btc_atr()
        atr_pct = atr_data.get("atr_pct", 0)
        if atr_pct > 0:
            # SL = 1.2x ATR (just outside one 4h candle range), floored by tier config
            dynamic_sl = round(atr_pct * 1.2, 2)
            tier_floor_sl = tier_config["sl_pct"]
            sl_pct_raw = max(dynamic_sl, tier_floor_sl)
            # Cap SL at 4% to prevent huge losses
            sl_pct_raw = min(sl_pct_raw, 4.0)
            # TP = 1.5x SL for positive expectancy
            tp_pct_raw = round(sl_pct_raw * 1.5, 2)
            print(f"  [ATR-SL] ATR: {atr_pct:.2f}% | Dynamic SL: {dynamic_sl:.2f}% | Final SL: {sl_pct_raw:.2f}% | TP: {tp_pct_raw:.2f}%")
        else:
            sl_pct_raw = tier_config["sl_pct"]
            tp_pct_raw = tier_config["tp_pct"]
            print(f"  [ATR-SL] No ATR data, using tier defaults: SL {sl_pct_raw}% TP {tp_pct_raw}%")
    except Exception as e:
        sl_pct_raw = tier_config["sl_pct"]
        tp_pct_raw = tier_config["tp_pct"]
        print(f"  [ATR-SL] Error ({e}), using tier defaults: SL {sl_pct_raw}% TP {tp_pct_raw}%")
    
    tp_pct = tp_pct_raw / 100
    sl_pct = sl_pct_raw / 100'''

if old_execute_sl in code:
    code = code.replace(old_execute_sl, new_execute_sl)
    print("[OK] CHANGE 2: Dynamic ATR-based SL in execute_trade()")
else:
    print("[FAIL] CHANGE 2: Could not find execute_trade SL block")


# ============================================================
# CHANGE 3: Judge passes ATR-aware SL/TP
# ============================================================
old_judge_sl = '''        # V3.1.4: TIER-BASED TP/SL
        tp_pct = tier_config["tp_pct"]
        sl_pct = tier_config["sl_pct"]
        max_hold = tier_config["max_hold_hours"]'''

new_judge_sl = '''        # V3.1.32: ATR-aware TP/SL (dynamic, not fixed)
        try:
            atr_data = get_btc_atr()
            atr_pct = atr_data.get("atr_pct", 0)
            if atr_pct > 0:
                dynamic_sl = round(atr_pct * 1.2, 2)
                sl_pct = max(dynamic_sl, tier_config["sl_pct"])
                sl_pct = min(sl_pct, 4.0)
                tp_pct = round(sl_pct * 1.5, 2)
            else:
                tp_pct = tier_config["tp_pct"]
                sl_pct = tier_config["sl_pct"]
        except:
            tp_pct = tier_config["tp_pct"]
            sl_pct = tier_config["sl_pct"]
        max_hold = tier_config["max_hold_hours"]'''

if old_judge_sl in code:
    code = code.replace(old_judge_sl, new_judge_sl)
    print("[OK] CHANGE 3: Judge ATR-aware SL/TP")
else:
    print("[FAIL] CHANGE 3: Could not find Judge SL block")


# ============================================================
# CHANGE 4: Profit guard thresholds (daemon references these too)
# Widen the trailing protection peak thresholds
# ============================================================

# T3 profit guard: peak 2.0% -> 2.5%
old_t3_guard = '                    if peak_pnl_pct >= 2.0 and pnl_pct < 0.5:'
new_t3_guard = '                    if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:  # V3.1.32: Wider'
if old_t3_guard in code:
    code = code.replace(old_t3_guard, new_t3_guard)
    print("[OK] CHANGE 4a: T3 profit guard widened")

# T2 profit guard: peak 2.5% -> 3.0%  
old_t2_guard = '                    if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:'
new_t2_guard = '                    if peak_pnl_pct >= 3.0 and pnl_pct < 1.5:  # V3.1.32: Wider'
if old_t2_guard in code:
    code = code.replace(old_t2_guard, new_t2_guard)
    print("[OK] CHANGE 4b: T2 profit guard widened")

# T1 profit guard: peak 3.0% -> 3.5%
old_t1_guard = '                    if peak_pnl_pct >= 3.0 and pnl_pct < 1.5:'
new_t1_guard = '                    if peak_pnl_pct >= 3.5 and pnl_pct < 2.0:  # V3.1.32: Wider'
if old_t1_guard in code:
    code = code.replace(old_t1_guard, new_t1_guard)
    print("[OK] CHANGE 4c: T1 profit guard widened")


# ============================================================
# WRITE
# ============================================================
with open(FILE, 'w') as f:
    f.write(code)

print("\n=== V3.1.32 PATCH COMPLETE ===")
print("Changes:")
print("  1. Tier SL: T1 1.5->2.5%, T2 1.5->2.0%, T3 1.5->2.0%")
print("  2. Tier TP: T1 2.5->4.0%, T2 2.0->3.5%, T3 1.5->3.0%")
print("  3. Force exit: -2.0% -> -3.0/-3.5% (won't kill before WEEX SL)")
print("  4. execute_trade() uses ATR*1.2 for dynamic SL (floored by tier)")
print("  5. Judge passes ATR-aware SL/TP to trades")
print("  6. Profit guards widened to match new TP targets")
print("\nRestart daemon: pkill -f smt_daemon; sleep 2; nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
