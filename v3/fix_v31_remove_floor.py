#!/usr/bin/env python3
"""
fix_v31_remove_floor.py - Remove the $950 floor that's blocking trades
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_remove_floor.py

The floor was a bad idea because:
- Available balance = Total equity - Margin in positions
- With 5 positions open, available is $624 even though equity is ~$1000
- So the floor blocks ALL new trades!
"""

import os

FILE = "smt_daemon_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

with open(FILE, 'r') as f:
    content = f.read()

# Remove the floor balance check
floor_check = '''
        # Floor balance protection - protect principal
        if balance < 950.0:
            logger.warning(f"Balance ${balance:.2f} below $950 floor. No new trades.")
            return'''

if floor_check in content:
    content = content.replace(floor_check, '')
    print("Removed floor balance check")
else:
    # Try alternate pattern
    content = content.replace(
        '''        # Floor balance protection
        if balance < 950.0:
            logger.warning(f"Balance ${balance:.2f} below $950 floor. No new trades.")
            return''',
        ''
    )
    print("Removed floor balance check (alt)")

with open(FILE, 'w') as f:
    f.write(content)

print("\nDone! Floor check removed.")
print("""
Restart daemon:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  tail -f logs/daemon_v3_1_*.log
""")
