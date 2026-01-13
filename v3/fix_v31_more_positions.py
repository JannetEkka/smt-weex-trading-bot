#!/usr/bin/env python3
"""
fix_v31_more_positions.py - Allow more positions like competitors
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_more_positions.py
"""

import os
from datetime import datetime

# Fix trading file
FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

backup = f"{FILE}.backup_pos_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    content = f.read()
with open(backup, 'w') as f:
    f.write(content)
print(f"Backup: {backup}")

# ================================================================
# FIX 1: Increase MAX_OPEN_POSITIONS from 5 to 8
# ================================================================
content = content.replace(
    'MAX_OPEN_POSITIONS = 5',
    'MAX_OPEN_POSITIONS = 8  # Increased from 5 to compete'
)
print("FIX 1: MAX_OPEN_POSITIONS 5 -> 8")

# ================================================================
# FIX 2: Reduce position size to allow more positions
# From 7% to 5% per position
# ================================================================
content = content.replace(
    'MAX_SINGLE_POSITION_PCT = 0.12',
    'MAX_SINGLE_POSITION_PCT = 0.08'
)
content = content.replace(
    'MAX_SINGLE_POSITION_PCT = 0.07',
    'MAX_SINGLE_POSITION_PCT = 0.06'
)
content = content.replace(
    'MIN_SINGLE_POSITION_PCT = 0.05',
    'MIN_SINGLE_POSITION_PCT = 0.04'
)
content = content.replace(
    'MIN_SINGLE_POSITION_PCT = 0.04',
    'MIN_SINGLE_POSITION_PCT = 0.03'
)
print("FIX 2: Position sizes reduced (more positions, smaller each)")

with open(FILE, 'w') as f:
    f.write(content)

# Also fix daemon
DAEMON = "smt_daemon_v3_1.py"
if os.path.exists(DAEMON):
    with open(DAEMON, 'r') as f:
        dcontent = f.read()
    
    dcontent = dcontent.replace(
        'MAX_OPEN_POSITIONS = 5',
        'MAX_OPEN_POSITIONS = 8'
    )
    
    with open(DAEMON, 'w') as f:
        f.write(dcontent)
    print("FIX 3: Daemon MAX_OPEN_POSITIONS 5 -> 8")

print("\n" + "="*50)
print("DONE!")
print("="*50)
print("""
Changes:
- MAX_OPEN_POSITIONS: 5 -> 8
- Position sizes: smaller to fit more

Your current state:
- Balance: $628 available
- Positions: 5 (using ~$400 margin)
- Can now open 3 more smaller positions

Test:
  python3 -c "from smt_nightly_trade_v3_1 import MAX_OPEN_POSITIONS; print(f'Max positions: {MAX_OPEN_POSITIONS}')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
