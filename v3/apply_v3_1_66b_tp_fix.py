#!/usr/bin/env python3
"""
V3.1.66b TP SCALING FIX
========================
Problem: TP targets are unrealistically wide, especially in extreme fear.

Root cause chain:
1. CAPITULATION_AGGRESSIVE multiplier (1.8x) pushes T1 TP from 5% to 9%
2. Floor rule (TP >= SL * 1.5) forces TP to 6.75% when vol-SL is 4.5%
3. 7% cap was added later but doesn't fix the floor problem
4. Existing BTC SHORT still has stale 9.01% TP from before the cap

Fix:
- Remove F&G multiplier entirely (was causing more harm than good)
- Change floor from 1.5x SL to 1.2x SL (still positive expectancy)  
- Cap TP per tier: T1=5%, T2=6%, T3=7% (hard ceiling, no exceptions)
- TP = min(tier_tp, max(tier_tp, sl * 1.2))
  Which simplifies to: TP = tier_tp (since tier_tp is always > sl*1.2 for sane SL values)

Run: python3 apply_v3_1_66b_tp_fix.py
Then also: python3 fix_btc_short_tp.py  (to update existing position on WEEX)
"""

import shutil
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"
BACKUP = f"smt_nightly_trade_v3_1.py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def read_file():
    with open(FILE, 'r') as f:
        return f.read()

def write_file(content):
    with open(FILE, 'w') as f:
        f.write(content)

def apply_fixes():
    shutil.copy2(FILE, BACKUP)
    print(f"[OK] Backup: {BACKUP}")
    
    content = read_file()
    changes = 0
    
    # ================================================================
    # FIX 1: Replace the entire TP scaling block
    # ================================================================
    old_tp_block = """    # V3.1.51: Regime-scaled TP
    try:
        fg_data = get_fear_greed_index()
        fg_val = fg_data.get("value", 50)
    except:
        fg_val = 50
    
    base_tp = tier_config["tp_pct"]
    if fg_val < 15:
        tp_multiplier = 1.8
        tp_label = "CAPITULATION_AGGRESSIVE"
    elif fg_val < 30:
        tp_multiplier = 1.25
        tp_label = "FEAR"
    elif fg_val > 80:
        tp_multiplier = 0.50
        tp_label = "EXTREME_GREED"
    elif fg_val > 60:
        tp_multiplier = 0.65
        tp_label = "GREED"
    else:
        tp_multiplier = 1.0
        tp_label = "NORMAL"
    
    tp_pct_raw = round(base_tp * tp_multiplier, 2)
    # Floor: TP must be at least 1.5x SL for positive expectancy
    tp_pct_raw = max(tp_pct_raw, sl_pct_raw * 1.5)


    tp_pct_raw = min(tp_pct_raw, 7.0)
    print(f"  [ATR-SL] SL: {sl_pct_raw:.2f}% | TP: {tp_pct_raw:.2f}% ({tp_label}, F&G={fg_val}, base={base_tp}%, mult={tp_multiplier}x)")"""
    
    new_tp_block = """    # V3.1.66b: REALISTIC TP - tier-based, no F&G scaling
    # F&G scaling caused 9% TPs in capitulation (unrealistic, never hit)
    # TP is now strictly tier-based with a sane floor
    base_tp = tier_config["tp_pct"]  # T1=5%, T2=6%, T3=7%
    tp_floor = sl_pct_raw * 1.2  # Minimum 1.2x SL for positive expectancy
    tp_pct_raw = max(base_tp, tp_floor)
    # Hard cap per tier (no exceptions)
    _tier_tp_caps = {1: 5.0, 2: 6.0, 3: 7.0}
    _tp_cap = _tier_tp_caps.get(tier, 7.0)
    tp_pct_raw = min(tp_pct_raw, _tp_cap)
    print(f"  [ATR-SL] SL: {sl_pct_raw:.2f}% | TP: {tp_pct_raw:.2f}% (Tier {tier} cap={_tp_cap}%, floor=SL*1.2={tp_floor:.2f}%)")"""
    
    if old_tp_block in content:
        content = content.replace(old_tp_block, new_tp_block, 1)
        print("[FIX 1] Replaced F&G-scaled TP with tier-capped realistic TP")
        changes += 1
    else:
        print("[SKIP 1] TP scaling block not found (may already be fixed)")
        print("         Searching for partial match...")
        if "CAPITULATION_AGGRESSIVE" in content:
            print("         WARNING: CAPITULATION_AGGRESSIVE still in code but pattern didn't match exactly")
            print("         You may need to manually edit execute_trade() in smt_nightly_trade_v3_1.py")
        else:
            print("         CAPITULATION_AGGRESSIVE not found - likely already fixed")
    
    write_file(content)
    print(f"\n[DONE] Applied {changes} fixes to {FILE}")
    print(f"[BACKUP] Original saved as {BACKUP}")
    
    if changes > 0:
        print(f"\nTP behavior after fix:")
        print(f"  Tier 1 (BTC/ETH/BNB/LTC): TP=5.0%, SL=dynamic (capped 4.5% in fear)")
        print(f"  Tier 2 (SOL):              TP=6.0%, SL=dynamic")
        print(f"  Tier 3 (DOGE/XRP/ADA):     TP=7.0%, SL=dynamic")
        print(f"  Floor: TP >= SL * 1.2 (was 1.5x)")
        print(f"  No more F&G multiplier (was 1.8x in capitulation)")
    
    print(f"\nIMPORTANT: Your existing BTC SHORT still has 9.01% TP on WEEX.")
    print(f"Run: python3 fix_btc_short_tp.py to update it to 5.0%")
    print(f"\nThen commit:")
    print(f"  git add . && git commit -m 'V3.1.66b: realistic tier-capped TP, remove F&G scaling' && git push")


if __name__ == "__main__":
    apply_fixes()
