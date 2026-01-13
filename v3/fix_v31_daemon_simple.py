#!/usr/bin/env python3
"""
fix_v31_daemon_simple.py - Fix the daemon without breaking syntax
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_daemon_simple.py
"""

import os
from datetime import datetime

FILE = "smt_daemon_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

# Backup
backup = f"{FILE}.backup_simple_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    content = f.read()
with open(backup, 'w') as f:
    f.write(content)
print(f"Backup: {backup}")

# ================================================================
# FIX 1: Signal check every 30 min (was 2 hours)
# ================================================================
content = content.replace(
    'SIGNAL_CHECK_INTERVAL = 2 * 60 * 60',
    'SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes'
)
content = content.replace(
    'SIGNAL_CHECK_INTERVAL = 1 * 60 * 60',
    'SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes'
)
print("FIX 1: Signal interval -> 30 min")

# ================================================================
# FIX 2: Floor balance protection
# ================================================================
old_slots = '''        if available_slots <= 0:
            logger.info("Max positions reached")
            return'''

new_slots = '''        if available_slots <= 0:
            logger.info("Max positions reached")
            return
        
        # Floor balance protection - protect principal
        if balance < 950.0:
            logger.warning(f"Balance ${balance:.2f} below $950 floor. No new trades.")
            return'''

if old_slots in content:
    content = content.replace(old_slots, new_slots)
    print("FIX 2: Floor balance protection")

# ================================================================
# FIX 3: Better AI log - use market_context if available
# This is the fix that broke before - doing it properly now
# ================================================================
old_explanation = 'explanation=decision.get("reasoning", "")[:500]'
new_explanation = 'explanation=decision.get("market_context", decision.get("reasoning", ""))[:500]'

if old_explanation in content:
    content = content.replace(old_explanation, new_explanation)
    print("FIX 3: AI log uses market_context")

# Remove the broken f-string if it exists
broken_line = '''explanation=f"{decision.get('reasoning', '')} | Votes: {', '.join([f'{v.get("persona","?")}={v.get("signal","?")}({v.get("confidence",0):.0%})' for v in decision.get('persona_votes', [])])}"[:500]'''
if broken_line in content:
    content = content.replace(broken_line, new_explanation)
    print("FIX 3b: Removed broken f-string")

# Write
with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*50)
print("FIXES APPLIED!")
print("="*50)
print("""
Test:
  python3 -c "import smt_daemon_v3_1; print('OK')"

Start:
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &

Monitor:
  tail -f logs/daemon_v3_1_*.log
""")
