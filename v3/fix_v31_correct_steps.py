#!/usr/bin/env python3
"""
fix_v31_correct_steps.py - Set CORRECT step sizes based on WEEX error messages
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_correct_steps.py

WEEX error messages tell us the actual step sizes:
- ADA: stepSize '10' (error said 2592 invalid, needs multiple of 10)
- LTC: stepSize '0.1' (error said 13.11 invalid)
"""

import os

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    content = f.read()

# The WRONG step sizes we have now
wrong_steps = '''KNOWN_STEP_SIZES = {
    "cmt_btcusdt": 0.0001,  # FIXED
    "cmt_ethusdt": 0.001,
    "cmt_solusdt": 0.1,
    "cmt_dogeusdt": 1,      # FIXED: was 100
    "cmt_xrpusdt": 1,       # FIXED: was 10
    "cmt_adausdt": 1,       # FIXED: was 10
    "cmt_bnbusdt": 0.1,     # FIXED: was 0.01
    "cmt_ltcusdt": 0.1,     # FIXED: was 0.01
}'''

# The CORRECT step sizes based on WEEX errors
correct_steps = '''KNOWN_STEP_SIZES = {
    "cmt_btcusdt": 0.0001,
    "cmt_ethusdt": 0.01,    # WEEX wants 0.01
    "cmt_solusdt": 0.1,
    "cmt_dogeusdt": 100,    # WEEX error confirmed
    "cmt_xrpusdt": 10,      # WEEX error confirmed
    "cmt_adausdt": 10,      # WEEX error: stepSize '10'
    "cmt_bnbusdt": 0.1,
    "cmt_ltcusdt": 0.1,     # WEEX error: stepSize '0.1'
}'''

if wrong_steps in content:
    content = content.replace(wrong_steps, correct_steps)
    print("Fixed step sizes to match WEEX requirements")
else:
    # Try to find any KNOWN_STEP_SIZES block and replace
    import re
    pattern = r'KNOWN_STEP_SIZES = \{[^}]+\}'
    if re.search(pattern, content):
        content = re.sub(pattern, correct_steps, content)
        print("Fixed step sizes (regex)")
    else:
        print("ERROR: Could not find KNOWN_STEP_SIZES")

with open(FILE, 'w') as f:
    f.write(content)

print("""
Correct step sizes (from WEEX errors):
  BTC:  0.0001
  ETH:  0.01
  SOL:  0.1
  DOGE: 100
  XRP:  10
  ADA:  10
  BNB:  0.1
  LTC:  0.1

Test:
  python3 -c "from smt_nightly_trade_v3_1 import round_size_to_step; print('ADA:', round_size_to_step(2592, 'cmt_adausdt'))"
  # Should print: ADA: 2590

Restart daemon:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
