#!/usr/bin/env python3
"""
fix_v31.py - Fix V3.1 position sizing bug
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31.py
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

# FIX 1: Position sizing bug
# The bug: raw_size = position_usdt / current_price
# This treats position_usdt as NOTIONAL, but it's actually MARGIN
# Fix: raw_size = (position_usdt * MAX_LEVERAGE) / current_price

old_code = "raw_size = position_usdt / current_price"
new_code = """# FIXED: position_usdt is MARGIN, multiply by leverage for notional position
    notional_usdt = position_usdt * MAX_LEVERAGE
    raw_size = notional_usdt / current_price"""

if old_code in original:
    fixed = original.replace(old_code, new_code)
    print("FIX 1: Position sizing - APPLIED")
else:
    print("FIX 1: Position sizing - already fixed or pattern not found")
    fixed = original

# Write fixed file
with open(FILE, 'w') as f:
    f.write(fixed)

print("\n" + "="*50)
print("VERIFICATION")
print("="*50)

# Verify
with open(FILE, 'r') as f:
    content = f.read()

if "notional_usdt = position_usdt * MAX_LEVERAGE" in content:
    print("Position sizing: FIXED")
else:
    print("Position sizing: NOT FIXED - check manually!")

print("\n" + "="*50)
print("NEXT STEPS")
print("="*50)
print("1. Stop daemon: pkill -f smt_daemon_v3_1.py")
print("2. Test imports: python3 -c \"from smt_nightly_trade_v3_1 import *; print('OK')\"")
print("3. Start daemon: python3 smt_daemon_v3_1.py &")
print("4. Check logs: tail -f logs/daemon_v3_1_*.log")
