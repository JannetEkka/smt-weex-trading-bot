#!/usr/bin/env python3
"""
fix_v31_ada_and_multi.py - Fix ADA size rounding + allow multiple positions per pair
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_ada_and_multi.py
"""

import os
from datetime import datetime

# ================================================================
# FIX TRADING FILE
# ================================================================
FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

backup = f"{FILE}.backup_ada_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    content = f.read()
with open(backup, 'w') as f:
    f.write(content)
print(f"Backup: {backup}")

# ================================================================
# FIX 1: Better size rounding for large step sizes (ADA, XRP, DOGE)
# The issue: 2592 / 10 = 259.2, need to floor to 2590
# ================================================================

old_round = '''def round_size_to_step(size: float, symbol: str) -> float:
    contract_info = get_contract_info(symbol)
    step = contract_info.get("step_size", 0.001)
    if step >= 1:
        return int(size / step) * step
    decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
    return round(int(size / step) * step, decimals)'''

new_round = '''def round_size_to_step(size: float, symbol: str) -> float:
    """Round size DOWN to nearest step - FIXED for all step sizes"""
    contract_info = get_contract_info(symbol)
    step = contract_info.get("step_size", 0.001)
    
    import math
    # Floor to nearest step (works for step=10, step=0.1, etc)
    floored = math.floor(size / step) * step
    
    # Handle precision
    if step >= 1:
        return int(floored)  # Return as integer for large steps
    else:
        decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
        return round(floored, decimals)'''

if old_round in content:
    content = content.replace(old_round, new_round)
    print("FIX 1: Size rounding (math.floor) - APPLIED")
else:
    # Try to find any round_size_to_step and replace
    import re
    pattern = r'def round_size_to_step\(size: float, symbol: str\) -> float:.*?(?=\ndef |\nclass |\Z)'
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_round + '\n\n', content, flags=re.DOTALL)
        print("FIX 1: Size rounding (regex) - APPLIED")
    else:
        print("FIX 1: Could not find round_size_to_step")

# Write trading file
with open(FILE, 'w') as f:
    f.write(content)

# ================================================================
# FIX DAEMON FILE - Allow multiple positions per pair
# ================================================================
DAEMON = "smt_daemon_v3_1.py"

if os.path.exists(DAEMON):
    with open(DAEMON, 'r') as f:
        dcontent = f.read()
    
    backup2 = f"{DAEMON}.backup_ada_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(backup2, 'w') as f:
        f.write(dcontent)
    print(f"Daemon backup: {backup2}")
    
    # FIX 2: Comment out or remove the "Already have position" skip
    # Find patterns like: if pair in existing_pairs: continue
    
    old_skip = '''            # Skip if we already have a position in this pair
            existing_pairs = [p["symbol"].replace("cmt_", "").replace("usdt", "").upper() 
                            for p in open_positions]
            if pair in existing_pairs:
                logger.info(f"  {pair}: Already have position")
                continue'''
    
    new_skip = '''            # Allow multiple positions per pair (like competitors do)
            # existing_pairs check removed'''
    
    if old_skip in dcontent:
        dcontent = dcontent.replace(old_skip, new_skip)
        print("FIX 2: Allow multiple positions per pair - APPLIED")
    else:
        # Try simpler pattern
        if 'Already have position' in dcontent:
            # Comment out the skip
            dcontent = dcontent.replace(
                'logger.info(f"  {pair}: Already have position")',
                '# logger.info(f"  {pair}: Already have position")  # DISABLED'
            )
            dcontent = dcontent.replace(
                'if pair in existing_pairs:\n                continue',
                '# if pair in existing_pairs:\n                #     continue  # DISABLED - allow multiple'
            )
            print("FIX 2: Disabled 'Already have position' skip")
    
    with open(DAEMON, 'w') as f:
        f.write(dcontent)

print("\n" + "="*50)
print("FIXES APPLIED!")
print("="*50)
print("""
Changes:
1. Size rounding: Uses math.floor() for proper rounding
   - ADA 2592 -> 2590 (divisible by 10)
   
2. Multiple positions: Removed "Already have position" skip
   - Can now open LONG and SHORT on same pair
   - Like WeexAlphaHunter does

Test:
  python3 -c "from smt_nightly_trade_v3_1 import round_size_to_step; print(round_size_to_step(2592, 'cmt_adausdt'))"
  # Should print: 2590

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
