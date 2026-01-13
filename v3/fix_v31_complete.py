#!/usr/bin/env python3
"""
fix_v31_complete.py - Fix ALL V3.1 issues
==========================================
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_complete.py

Fixes:
1. Position sizing (margin * leverage)
2. Too many NEUTRAL responses (loosen consensus from 55% to 45%)
3. Lower MIN_CONFIDENCE_TO_TRADE from 60% to 55%
"""

import os
import re
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

# Create backup
backup = f"{FILE}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    original = f.read()
with open(backup, 'w') as f:
    f.write(original)
print(f"Backup: {backup}")

content = original

# ================================================================
# FIX 1: Position sizing - multiply by leverage
# ================================================================
old_sizing = "raw_size = position_usdt / current_price"
new_sizing = """# FIXED: position_usdt is MARGIN, multiply by leverage for notional
    notional_usdt = position_usdt * MAX_LEVERAGE
    raw_size = notional_usdt / current_price"""

if old_sizing in content:
    content = content.replace(old_sizing, new_sizing)
    print("FIX 1: Position sizing - APPLIED")
else:
    print("FIX 1: Position sizing - already fixed or not found")

# ================================================================
# FIX 2: Loosen consensus requirement (55% -> 45%)
# ================================================================
# Change: if long_pct > 0.55 and long_score > short_score * 1.3:
# To: if long_pct > 0.45 and long_score > short_score * 1.2:

old_consensus_long = "if long_pct > 0.55 and long_score > short_score * 1.3:"
new_consensus_long = "if long_pct > 0.45 and long_score > short_score * 1.2:  # FIXED: lowered threshold"

old_consensus_short = "elif short_pct > 0.55 and short_score > long_score * 1.3:"
new_consensus_short = "elif short_pct > 0.45 and short_score > long_score * 1.2:  # FIXED: lowered threshold"

if old_consensus_long in content:
    content = content.replace(old_consensus_long, new_consensus_long)
    print("FIX 2a: Long consensus threshold - APPLIED (55%->45%)")
else:
    print("FIX 2a: Long consensus - already fixed or not found")

if old_consensus_short in content:
    content = content.replace(old_consensus_short, new_consensus_short)
    print("FIX 2b: Short consensus threshold - APPLIED (55%->45%)")
else:
    print("FIX 2b: Short consensus - already fixed or not found")

# ================================================================
# FIX 3: Lower MIN_CONFIDENCE_TO_TRADE (60% -> 55%)
# ================================================================
old_min_conf = "MIN_CONFIDENCE_TO_TRADE = 0.60"
new_min_conf = "MIN_CONFIDENCE_TO_TRADE = 0.55  # FIXED: lowered from 0.60"

if old_min_conf in content:
    content = content.replace(old_min_conf, new_min_conf)
    print("FIX 3: MIN_CONFIDENCE_TO_TRADE - APPLIED (60%->55%)")
else:
    print("FIX 3: MIN_CONFIDENCE_TO_TRADE - already fixed or not found")

# ================================================================
# FIX 4: Cap confidence at 0.90 instead of 0.85 (allow higher confidence trades)
# ================================================================
old_cap = "confidence = min(0.85, long_pct)"
new_cap = "confidence = min(0.90, long_pct)  # FIXED: allow higher confidence"

if old_cap in content:
    content = content.replace(old_cap, new_cap)
    # Also fix the short version
    content = content.replace("confidence = min(0.85, short_pct)", 
                              "confidence = min(0.90, short_pct)  # FIXED: allow higher confidence")
    print("FIX 4: Confidence cap - APPLIED (0.85->0.90)")
else:
    print("FIX 4: Confidence cap - already fixed or not found")

# ================================================================
# Write fixed file
# ================================================================
with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("VERIFICATION")
print("="*60)

# Verify fixes
with open(FILE, 'r') as f:
    final = f.read()

checks = [
    ("Position sizing", "notional_usdt = position_usdt * MAX_LEVERAGE"),
    ("Consensus threshold", "long_pct > 0.45"),
    ("MIN_CONFIDENCE", "MIN_CONFIDENCE_TO_TRADE = 0.55"),
]

all_ok = True
for name, pattern in checks:
    if pattern in final:
        print(f"  {name}: OK")
    else:
        print(f"  {name}: FAILED - check manually!")
        all_ok = False

print("\n" + "="*60)
if all_ok:
    print("ALL FIXES APPLIED SUCCESSFULLY!")
else:
    print("SOME FIXES MAY HAVE FAILED - check file manually")
print("="*60)

print("""
NEXT STEPS:
1. Stop daemon: pkill -f smt_daemon_v3_1.py
2. Close old tiny positions (see close_old_positions.py)
3. Test: python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"
4. Start: nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
5. Monitor: tail -f logs/daemon_v3_1_*.log
6. Commit: git add -A && git commit -m "Fix V3.1 sizing + consensus" && git push
""")
