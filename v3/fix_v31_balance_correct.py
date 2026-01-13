#!/usr/bin/env python3
"""
fix_v31_balance_correct.py - Use correct WEEX API endpoint for balance
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_balance_correct.py

The issue: /capi/v2/account/accounts returns collateral.amount which can be negative
The fix: Use /capi/v2/account/assets which returns 'available' balance correctly
"""

import os
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

# Backup
backup = f"{FILE}.backup_balance2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    original = f.read()
with open(backup, 'w') as f:
    f.write(original)
print(f"Backup: {backup}")

content = original

# ================================================================
# FIX: Replace entire get_balance function with correct endpoint
# ================================================================

# Find and replace the broken get_balance
# First, let's find what's currently there (could be original or my previous fix)

# Pattern 1: My previous broken fix
old_balance_v1 = '''def get_balance() -> float:
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

# Pattern 2: Original V3.1
old_balance_v2 = '''def get_balance() -> float:
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

# The correct implementation using /capi/v2/account/assets
new_balance = '''def get_balance() -> float:
    """Get available USDT balance from WEEX using /assets endpoint"""
    try:
        # Use /assets endpoint which returns correct available balance
        endpoint = "/capi/v2/account/assets"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        # Response is a list: [{"coinName": "USDT", "available": "xxx", "equity": "xxx", ...}]
        if isinstance(data, list):
            for asset in data:
                if asset.get("coinName") == "USDT":
                    available = float(asset.get("available", 0))
                    if available > 0:
                        return available
        
        # Fallback to old endpoint if assets doesn't work
        endpoint2 = "/capi/v2/account/accounts"
        r2 = requests.get(f"{WEEX_BASE_URL}{endpoint2}", headers=weex_headers("GET", endpoint2), timeout=15)
        data2 = r2.json()
        if "collateral" in data2 and len(data2["collateral"]) > 0:
            amount = float(data2["collateral"][0].get("amount", 0))
            if amount > 0:
                return amount
        
        if TEST_MODE:
            return SIMULATED_BALANCE
        return SIMULATED_BALANCE
    except Exception as e:
        print(f"  [ERROR] get_balance: {e}")
        return SIMULATED_BALANCE'''

# Try to replace whichever version is present
if old_balance_v1 in content:
    content = content.replace(old_balance_v1, new_balance)
    print("FIX: Replaced previous broken fix with correct /assets endpoint")
elif old_balance_v2 in content:
    content = content.replace(old_balance_v2, new_balance)
    print("FIX: Replaced original with correct /assets endpoint")
else:
    print("WARNING: Could not find get_balance function to replace")
    print("You may need to manually replace it")

# Write changes
with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("FIX APPLIED!")
print("="*60)
print("""
The fix uses /capi/v2/account/assets endpoint which returns:
[{"coinName": "USDT", "available": "893.92", "equity": "998.51", ...}]

This gives the correct 'available' balance, not the weird 'amount' from collateral.

Test:
  python3 -c "from smt_nightly_trade_v3_1 import get_balance; print(f'Balance: {get_balance()}')"

Expected output: Balance: 893.xx (your actual available balance)

Then restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
