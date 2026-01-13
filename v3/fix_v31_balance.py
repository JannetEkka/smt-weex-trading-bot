#!/usr/bin/env python3
"""
fix_v31_balance.py - EMERGENCY FIX for negative balance bug
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_balance.py
"""

import os
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

# Backup
backup = f"{FILE}.backup_balance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    original = f.read()
with open(backup, 'w') as f:
    f.write(original)
print(f"Backup: {backup}")

content = original

# ================================================================
# FIX 1: Fix get_balance to handle negative/weird values
# ================================================================

old_get_balance = '''def get_balance() -> float:
    try:
        endpoint = "/capi/v2/account/accounts"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        balance = 0.0
        if "collateral" in data and len(data["collateral"]) > 0:
            balance = float(data["collateral"][0].get("amount", 0))
        if balance == 0 or TEST_MODE:
            return SIMULATED_BALANCE
        return balance
    except:
        return SIMULATED_BALANCE'''

new_get_balance = '''def get_balance() -> float:
    """Get available balance from WEEX - FIXED to handle edge cases"""
    try:
        endpoint = "/capi/v2/account/accounts"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        balance = 0.0
        
        # Try multiple fields to find the right balance
        if "collateral" in data and len(data["collateral"]) > 0:
            col = data["collateral"][0]
            # Try 'available' first, then 'amount'
            balance = float(col.get("available", col.get("amount", 0)))
        
        # SAFETY: Balance must be positive and reasonable
        if balance <= 0:
            print(f"  [WARNING] Invalid balance: {balance}, using fallback")
            # Try equity or other fields
            if isinstance(data, dict):
                balance = float(data.get("equity", data.get("available", SIMULATED_BALANCE)))
        
        # Final sanity check
        if balance <= 0 or balance > 100000:
            print(f"  [WARNING] Balance out of range: {balance}, using simulated")
            return SIMULATED_BALANCE
            
        if TEST_MODE:
            return SIMULATED_BALANCE
            
        return balance
    except Exception as e:
        print(f"  [ERROR] get_balance failed: {e}")
        return SIMULATED_BALANCE'''

if old_get_balance in content:
    content = content.replace(old_get_balance, new_get_balance)
    print("FIX 1: get_balance - APPLIED")
else:
    print("FIX 1: get_balance pattern not found - MANUAL FIX NEEDED")

# ================================================================
# FIX 2: Add safety check in execute_trade for negative sizes
# ================================================================

old_size_calc = '''    raw_size = notional_usdt / current_price
    size = round_size_to_step(raw_size, symbol)'''

new_size_calc = '''    raw_size = notional_usdt / current_price
    
    # SAFETY: Size must be positive
    if raw_size <= 0:
        print(f"  [ERROR] Invalid raw_size: {raw_size} (notional={notional_usdt}, price={current_price})")
        return {"executed": False, "reason": f"Invalid size calculation: {raw_size}"}
    
    size = round_size_to_step(raw_size, symbol)
    
    # Double check after rounding
    if size <= 0:
        print(f"  [ERROR] Size rounded to 0 or negative: {size}")
        return {"executed": False, "reason": f"Size too small after rounding: {size}"}'''

if old_size_calc in content:
    content = content.replace(old_size_calc, new_size_calc)
    print("FIX 2: Size safety check - APPLIED")
else:
    print("FIX 2: Size calc pattern not found")

# Write changes
with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("EMERGENCY FIX APPLIED!")
print("="*60)
print("""
Changes:
1. get_balance() now tries 'available' field first
2. Added safety checks for negative/invalid balance
3. Added safety check for negative size in execute_trade

Test:
  python3 -c "from smt_nightly_trade_v3_1 import get_balance; print(f'Balance: {get_balance()}')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
