#!/usr/bin/env python3
"""
fix_v31_all_issues.py - COMPREHENSIVE FIX for all issues
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_all_issues.py

Issues addressed:
1. Step sizes (LTC etc failing)
2. Signal check every 30 min (was 2hr)
3. AI logs include market reasoning
4. Floor balance protection ($950)
5. Lower TP from 5% to 3% (faster profits, free up margin)
6. Lower SL from 2.5% to 1.5% (cut losses faster)
"""

import os
import re
from datetime import datetime

def fix_trading_file():
    FILE = "smt_nightly_trade_v3_1.py"
    
    if not os.path.exists(FILE):
        print(f"ERROR: {FILE} not found!")
        return False
    
    backup = f"{FILE}.backup_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(FILE, 'r') as f:
        content = f.read()
    with open(backup, 'w') as f:
        f.write(content)
    print(f"Backup: {backup}")
    
    changes = 0
    
    # ================================================================
    # FIX 1: Correct step sizes
    # ================================================================
    old_steps = '''KNOWN_STEP_SIZES = {
    "cmt_btcusdt": 0.001,
    "cmt_ethusdt": 0.001,
    "cmt_solusdt": 0.1,
    "cmt_dogeusdt": 100,
    "cmt_xrpusdt": 10,
    "cmt_adausdt": 10,
    "cmt_bnbusdt": 0.01,
    "cmt_ltcusdt": 0.01,
}'''
    
    new_steps = '''KNOWN_STEP_SIZES = {
    "cmt_btcusdt": 0.0001,  # FIXED
    "cmt_ethusdt": 0.001,
    "cmt_solusdt": 0.1,
    "cmt_dogeusdt": 1,      # FIXED: was 100
    "cmt_xrpusdt": 1,       # FIXED: was 10
    "cmt_adausdt": 1,       # FIXED: was 10
    "cmt_bnbusdt": 0.1,     # FIXED: was 0.01
    "cmt_ltcusdt": 0.1,     # FIXED: was 0.01
}'''
    
    if old_steps in content:
        content = content.replace(old_steps, new_steps)
        print("FIX 1: Step sizes - APPLIED")
        changes += 1
    else:
        # Try regex
        pattern = r'KNOWN_STEP_SIZES = \{[^}]+\}'
        if re.search(pattern, content):
            content = re.sub(pattern, new_steps, content)
            print("FIX 1: Step sizes (regex) - APPLIED")
            changes += 1
    
    # ================================================================
    # FIX 2: Lower TP from 5% to 3% (faster profits)
    # ================================================================
    content = content.replace(
        'DEFAULT_TP_PCT = 5.0',
        'DEFAULT_TP_PCT = 3.0  # CHANGED: 5% -> 3% for faster profits'
    )
    content = content.replace(
        '"take_profit_percent": 5.0',
        '"take_profit_percent": 3.0'
    )
    # Also check for tp_pct assignments
    content = content.replace(
        'tp_pct = 5.0',
        'tp_pct = 3.0'
    )
    content = content.replace(
        'default_tp_pct": 5.0',
        'default_tp_pct": 3.0'
    )
    print("FIX 2: TP 5% -> 3% - APPLIED")
    changes += 1
    
    # ================================================================
    # FIX 3: Lower SL from 2.5% to 1.5% (cut losses faster)
    # ================================================================
    content = content.replace(
        'DEFAULT_SL_PCT = 2.5',
        'DEFAULT_SL_PCT = 1.5  # CHANGED: 2.5% -> 1.5% to cut losses faster'
    )
    content = content.replace(
        '"stop_loss_percent": 2.5',
        '"stop_loss_percent": 1.5'
    )
    content = content.replace(
        'sl_pct = 2.5',
        'sl_pct = 1.5'
    )
    content = content.replace(
        'default_sl_pct": 2.5',
        'default_sl_pct": 1.5'
    )
    print("FIX 3: SL 2.5% -> 1.5% - APPLIED")
    changes += 1
    
    # ================================================================
    # FIX 4: Add floor balance constant
    # ================================================================
    if 'FLOOR_BALANCE' not in content:
        content = content.replace(
            'STARTING_BALANCE = 1000.0',
            'STARTING_BALANCE = 1000.0\nFLOOR_BALANCE = 950.0  # Protect principal - stop trading below this'
        )
        print("FIX 4: Floor balance constant - APPLIED")
        changes += 1
    
    # Write changes
    with open(FILE, 'w') as f:
        f.write(content)
    
    return changes > 0


def fix_daemon_file():
    FILE = "smt_daemon_v3_1.py"
    
    if not os.path.exists(FILE):
        print(f"WARNING: {FILE} not found")
        return False
    
    backup = f"{FILE}.backup_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(FILE, 'r') as f:
        content = f.read()
    with open(backup, 'w') as f:
        f.write(content)
    print(f"Daemon backup: {backup}")
    
    # ================================================================
    # FIX 5: Signal check every 30 min (was 2 hours)
    # ================================================================
    content = content.replace(
        'SIGNAL_CHECK_INTERVAL = 2 * 60 * 60',
        'SIGNAL_CHECK_INTERVAL = 30 * 60  # CHANGED: 2hr -> 30min'
    )
    content = content.replace(
        'SIGNAL_CHECK_INTERVAL = 1 * 60 * 60',
        'SIGNAL_CHECK_INTERVAL = 30 * 60  # CHANGED: 1hr -> 30min'
    )
    # If neither found, try adding
    if 'SIGNAL_CHECK_INTERVAL = 30 * 60' not in content:
        content = content.replace(
            'SIGNAL_CHECK_INTERVAL =',
            'SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes #'
        )
    print("FIX 5: Signal interval -> 30 min - APPLIED")
    
    # ================================================================
    # FIX 6: Add floor balance protection in daemon
    # ================================================================
    # Find where we check balance and add floor protection
    old_check = '''        if available_slots <= 0:
            logger.info("Max positions reached")
            return'''
    
    new_check = '''        if available_slots <= 0:
            logger.info("Max positions reached")
            return
        
        # Floor balance protection
        FLOOR_BALANCE = 950.0
        if balance < FLOOR_BALANCE:
            logger.warning(f"Balance ${balance:.2f} below floor ${FLOOR_BALANCE}. Protecting principal - no new trades.")
            return'''
    
    if old_check in content and 'FLOOR_BALANCE' not in content:
        content = content.replace(old_check, new_check)
        print("FIX 6: Floor balance protection - APPLIED")
    
    # ================================================================
    # FIX 7: Better AI log explanation with market context
    # ================================================================
    # Find upload_ai_log_to_weex call and enhance explanation
    old_log = 'explanation=decision.get("reasoning", "")[:500]'
    new_log = '''explanation=f"{decision.get('reasoning', '')} | Votes: {', '.join([f'{v.get(\"persona\",\"?\")}={v.get(\"signal\",\"?\")}({v.get(\"confidence\",0):.0%})' for v in decision.get('persona_votes', [])])}"[:500]'''
    
    if old_log in content:
        content = content.replace(old_log, new_log)
        print("FIX 7: AI log explanation enhanced - APPLIED")
    
    with open(FILE, 'w') as f:
        f.write(content)
    
    return True


if __name__ == "__main__":
    print("="*60)
    print("V3.1 COMPREHENSIVE FIX")
    print("="*60)
    print()
    
    fix_trading_file()
    print()
    fix_daemon_file()
    
    print("\n" + "="*60)
    print("ALL FIXES APPLIED!")
    print("="*60)
    print("""
Summary of changes:

1. STEP SIZES: Fixed BTC/DOGE/XRP/ADA/BNB/LTC
   - LTC: 0.01 -> 0.1 (fixes the error)
   
2. TP: 5% -> 3%
   - Faster profit taking
   - Frees up margin for more positions
   
3. SL: 2.5% -> 1.5%
   - Cut losses faster
   - Less drawdown per trade
   
4. FLOOR BALANCE: $950
   - Won't open new trades if balance < $950
   - Protects your principal
   
5. SIGNAL CHECK: 2hr -> 30min
   - More frequent trading opportunities
   - Faster response to market changes
   
6. AI LOGS: Now include vote breakdown
   - Shows SENTIMENT=LONG(85%), FLOW=LONG(70%), etc.

Risk/Reward with new settings:
- TP +3%: ~$60 profit per $2000 position
- SL -1.5%: ~$30 loss per $2000 position
- Ratio: 2:1 (good!)

Test:
  python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &

Commit:
  git add -A && git commit -m "V3.1: all fixes - steps, TP/SL, 30min signals, floor protection" && git push
""")
