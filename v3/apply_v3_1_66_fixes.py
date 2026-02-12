#!/usr/bin/env python3
"""
V3.1.66 HOTFIX SCRIPT
=====================
Fixes 4 bugs in smt_daemon_v3_1.py:

1. FIX RL LOG ERROR: get_market_regime_for_exit referenced before assignment
   - Wrap the call in its own try/except with fallback

2. FIX _last_trade_opened_at SCOPING: Python treats it as local variable
   - Add `global _last_trade_opened_at` at top of check_trading_signals()

3. FIX LOOP ERROR TRACEBACK: Add traceback.format_exc() for better debugging

4. FIX REGIME VETO SELF-IMPORT: Remove fragile `from smt_daemon_v3_1 import`
   - get_market_regime_for_exit is already in the same file, no import needed
   - Wrap in try/except so a regime API failure doesn't block all trades

5. REMOVE DEAD SWAP CODE: ~80 lines of dead code behind `if False`

Run: python3 apply_v3_1_66_fixes.py
"""

import re
import sys
import shutil
from datetime import datetime

FILE = "smt_daemon_v3_1.py"
BACKUP = f"smt_daemon_v3_1.py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def read_file():
    with open(FILE, 'r') as f:
        return f.read()

def write_file(content):
    with open(FILE, 'w') as f:
        f.write(content)

def apply_fixes():
    # Backup first
    shutil.copy2(FILE, BACKUP)
    print(f"[OK] Backup: {BACKUP}")
    
    content = read_file()
    lines = content.split('\n')
    changes = 0
    
    # ================================================================
    # FIX 1: Add `global _last_trade_opened_at` to check_trading_signals
    # ================================================================
    # Find the function def line and add global declaration after the docstring
    in_check_trading = False
    docstring_ended = False
    fix1_done = False
    for i, line in enumerate(lines):
        if 'def check_trading_signals():' in line:
            in_check_trading = True
            continue
        if in_check_trading and not fix1_done:
            # Look for the line after the closing triple-quote of docstring
            if '"""' in line and docstring_ended == False:
                # Could be the closing """ of docstring
                # Check if it's a one-line docstring or multiline
                docstring_ended = True
                continue
            if docstring_ended and line.strip() == '':
                # Insert global declaration before the first real code
                # Find the next non-empty line
                continue
            if docstring_ended and 'global _last_trade_opened_at' not in line:
                # Check if global is already there
                already_has = any('global _last_trade_opened_at' in lines[j] for j in range(max(0,i-5), min(len(lines),i+5)))
                if not already_has:
                    lines.insert(i, '    global _last_trade_opened_at')
                    print(f"[FIX 1] Added `global _last_trade_opened_at` at line {i+1}")
                    changes += 1
                fix1_done = True
    
    content = '\n'.join(lines)
    
    # ================================================================
    # FIX 2: Fix RL log error - wrap get_market_regime_for_exit in try/except
    # ================================================================
    old_rl = """                if RL_ENABLED and rl_collector:
                    try:
                        # Get regime data for RL logging
                        rl_regime = get_market_regime_for_exit()"""
    
    new_rl = """                if RL_ENABLED and rl_collector:
                    try:
                        # Get regime data for RL logging
                        try:
                            rl_regime = get_market_regime_for_exit()
                        except Exception:
                            rl_regime = {"change_24h": 0, "change_4h": 0, "regime": "NEUTRAL"}"""
    
    if old_rl in content:
        content = content.replace(old_rl, new_rl, 1)
        print("[FIX 2] Wrapped RL get_market_regime_for_exit in try/except")
        changes += 1
    else:
        print("[SKIP 2] RL log pattern not found (may already be fixed)")
    
    # ================================================================
    # FIX 3: Fix regime veto self-import (remove fragile import, add try/except)
    # ================================================================
    old_veto = """                # V3.1.65: REGIME VETO - code-level block on counter-regime trades
                try:
                    from smt_daemon_v3_1 import get_market_regime_for_exit
                except ImportError:
                    pass
                _regime_now = get_market_regime_for_exit()
                _regime_label = _regime_now.get("regime", "NEUTRAL")"""
    
    new_veto = """                # V3.1.66: REGIME VETO - code-level block on counter-regime trades
                # get_market_regime_for_exit is defined in this file, no import needed
                try:
                    _regime_now = get_market_regime_for_exit()
                except Exception as _re:
                    logger.warning(f"REGIME VETO: regime check failed ({_re}), allowing trade")
                    _regime_now = {"regime": "NEUTRAL"}
                _regime_label = _regime_now.get("regime", "NEUTRAL")"""
    
    if old_veto in content:
        content = content.replace(old_veto, new_veto, 1)
        print("[FIX 3] Fixed regime veto self-import")
        changes += 1
    else:
        print("[SKIP 3] Regime veto pattern not found (may already be fixed)")
    
    # ================================================================
    # FIX 4: Add traceback to loop error handler
    # ================================================================
    old_loop = """        except Exception as e:
            logger.error(f"Loop error: {e}")
            state.errors += 1
            time.sleep(30)"""
    
    new_loop = """        except Exception as e:
            logger.error(f"Loop error: {e}")
            logger.error(traceback.format_exc())
            state.errors += 1
            time.sleep(30)"""
    
    if old_loop in content and new_loop not in content:
        content = content.replace(old_loop, new_loop, 1)
        print("[FIX 4] Added traceback to loop error handler")
        changes += 1
    else:
        print("[SKIP 4] Loop error pattern not found or already fixed")
    
    # ================================================================
    # FIX 5: Remove dead SWAP code (behind `if False and`)
    # Replace ~80 lines of dead code with a clean skip message
    # ================================================================
    old_swap_start = """                    else:
                        # V3.1.65: SWAP DISABLED - realizing losses + fees kills equity
                        SWAP_MIN_CONFIDENCE = 999  # effectively disabled
                        if False and confidence >= SWAP_MIN_CONFIDENCE:"""
    
    # Find and replace the entire dead swap block
    if old_swap_start in content:
        # Find the start index
        swap_idx = content.index(old_swap_start)
        # Find the matching else/break that ends the swap block
        # The block ends with:
        #             else:
        #                 logger.info(f"Max positions reached, {confidence:.0%} < 80% swap threshold, skipping")
        #                 break
        end_marker = """                        else:
                            logger.info(f"Max positions reached, {confidence:.0%} < 80% swap threshold, skipping")
                            break"""
        
        if end_marker in content[swap_idx:]:
            end_idx = content.index(end_marker, swap_idx) + len(end_marker)
            
            replacement = """                    else:
                        # V3.1.66: SWAP DISABLED - realizing losses + fees kills equity
                        logger.info(f"Max positions reached, skipping {opportunity['pair']} (swap disabled)")
                        break"""
            
            content = content[:swap_idx] + replacement + content[end_idx:]
            print("[FIX 5] Removed ~80 lines of dead SWAP code")
            changes += 1
        else:
            print("[SKIP 5] Could not find end of SWAP block")
    else:
        print("[SKIP 5] SWAP block not found (may already be cleaned)")
    
    # ================================================================
    # FIX 6: Update version string in run_daemon banner
    # ================================================================
    old_banner = 'logger.info("SMT Daemon V3.1.63 - SNIPER MODE: 3 positions, 80% floor, WHALE+FLOW co-primary")'
    new_banner = 'logger.info("SMT Daemon V3.1.66 - SNIPER MODE: 3 positions, 80% floor, WHALE+FLOW co-primary")'
    
    if old_banner in content:
        content = content.replace(old_banner, new_banner, 1)
        print("[FIX 6] Updated version banner to V3.1.66")
        changes += 1
    else:
        print("[SKIP 6] Version banner not found (may already be updated)")
    
    # Write the fixed file
    write_file(content)
    print(f"\n[DONE] Applied {changes} fixes to {FILE}")
    print(f"[BACKUP] Original saved as {BACKUP}")
    print(f"\nNext steps:")
    print(f"  1. Review: diff {BACKUP} {FILE}")
    print(f"  2. Restart daemon: kill the running daemon, then start again")
    print(f"  3. Commit: git add . && git commit -m 'V3.1.66: fix RL log error, scoping bug, dead swap code, regime veto import' && git push")


if __name__ == "__main__":
    apply_fixes()
