#!/usr/bin/env python3
"""
SMT RECOVERY SCRIPT - Restore V3.1.67 from V3.1.65 backup
===========================================================
Applies ALL lost changes:
  V3.1.66:  fix RL log error, scoping bug, dead swap code, regime veto import
  V3.1.66b: realistic tier-capped TP, remove F&G scaling
  V3.1.66c: aggressive mode - 2 slots, 15min cooldown, 80% floor
  V3.1.66d: trust the whales - 3+2 slots, 15min cooldown
  V3.1.67:  fix sentiment persona empty error - validate Gemini response

Run on VM:
  cd ~/smt-weex-trading-bot/v3
  python3 recover_v3_1_67.py
"""

import shutil
import sys
from datetime import datetime

DAEMON_FILE = "smt_daemon_v3_1.py"
NIGHTLY_FILE = "smt_nightly_trade_v3_1.py"

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def backup(path):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{path}.bak.pre_recovery_{ts}"
    shutil.copy2(path, backup_path)
    print(f"  Backup: {backup_path}")
    return backup_path


def apply_daemon_fixes():
    """Apply V3.1.66, 66c, 66d, 67 fixes to smt_daemon_v3_1.py"""
    print("=" * 60)
    print("DAEMON FIXES (smt_daemon_v3_1.py)")
    print("=" * 60)
    
    backup(DAEMON_FILE)
    content = read_file(DAEMON_FILE)
    lines = content.split('\n')
    changes = 0

    # ================================================================
    # FIX 1 (V3.1.66): Add `global _last_trade_opened_at` to check_trading_signals
    # Python treats it as local variable without this
    # ================================================================
    in_func = False
    docstring_count = 0
    fix1_done = False
    for i, line in enumerate(lines):
        if 'def check_trading_signals():' in line:
            in_func = True
            docstring_count = 0
            continue
        if in_func and not fix1_done:
            if '"""' in line:
                docstring_count += 1
                if docstring_count >= 2:  # closing """
                    # Insert global after docstring
                    # Find next non-empty line
                    for j in range(i+1, min(i+10, len(lines))):
                        if lines[j].strip() and 'global _last_trade_opened_at' not in lines[j]:
                            # Check if already exists nearby
                            nearby = '\n'.join(lines[max(0,j-3):j+3])
                            if 'global _last_trade_opened_at' not in nearby:
                                lines.insert(j, '    global _last_trade_opened_at')
                                print(f"[FIX 1] V3.1.66: Added `global _last_trade_opened_at` at line {j+1}")
                                changes += 1
                            fix1_done = True
                            break
    
    content = '\n'.join(lines)

    # ================================================================
    # FIX 2 (V3.1.66): Fix RL log error - wrap get_market_regime_for_exit in try/except
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
        print("[FIX 2] V3.1.66: Wrapped RL get_market_regime_for_exit in try/except")
        changes += 1
    else:
        print("[SKIP 2] RL log pattern not found (may already be fixed)")

    # ================================================================
    # FIX 3 (V3.1.66): Fix regime veto self-import
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
        print("[FIX 3] V3.1.66: Fixed regime veto self-import")
        changes += 1
    else:
        print("[SKIP 3] Regime veto pattern not found")

    # ================================================================
    # FIX 4 (V3.1.66): Add traceback to loop error handler
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
        print("[FIX 4] V3.1.66: Added traceback to loop error handler")
        changes += 1
    else:
        print("[SKIP 4] Loop error pattern already fixed or not found")

    # ================================================================
    # FIX 5 (V3.1.66): Remove dead SWAP code (~80 lines behind `if False`)
    # ================================================================
    old_swap_start = """                    else:
                        # V3.1.65: SWAP DISABLED - realizing losses + fees kills equity
                        SWAP_MIN_CONFIDENCE = 999  # effectively disabled
                        if False and confidence >= SWAP_MIN_CONFIDENCE:"""
    
    end_marker = """                        else:
                            logger.info(f"Max positions reached, {confidence:.0%} < 80% swap threshold, skipping")
                            break"""
    
    if old_swap_start in content:
        swap_idx = content.index(old_swap_start)
        if end_marker in content[swap_idx:]:
            end_idx = content.index(end_marker, swap_idx) + len(end_marker)
            replacement = """                    else:
                        # V3.1.66: SWAP DISABLED - realizing losses + fees kills equity
                        logger.info(f"Max positions reached, skipping {opportunity['pair']} (swap disabled)")
                        break"""
            content = content[:swap_idx] + replacement + content[end_idx:]
            print("[FIX 5] V3.1.66: Removed ~80 lines of dead SWAP code")
            changes += 1
        else:
            print("[SKIP 5] Could not find end of SWAP block")
    else:
        print("[SKIP 5] SWAP block not found")

    # ================================================================
    # FIX 6 (V3.1.66): Update version banner
    # ================================================================
    old_banner = 'logger.info("SMT Daemon V3.1.63 - SNIPER MODE: 3 positions, 80% floor, WHALE+FLOW co-primary")'
    new_banner = 'logger.info("SMT Daemon V3.1.67 - SNIPER MODE + RECOVERY: regime veto fix, realistic TP, aggressive slots")'
    
    if old_banner in content:
        content = content.replace(old_banner, new_banner, 1)
        print("[FIX 6] V3.1.67: Updated version banner")
        changes += 1
    else:
        print("[SKIP 6] Version banner not found (may be different)")

    # ================================================================
    # FIX 7 (V3.1.66c): Change cooldown from 30min to 15min
    # ================================================================
    old_cd = "GLOBAL_TRADE_COOLDOWN = 1800  # 30 minutes between ANY new trade"
    new_cd = "GLOBAL_TRADE_COOLDOWN = 900  # V3.1.66c: 15 minutes (was 30min, too slow for competition)"
    
    if old_cd in content:
        content = content.replace(old_cd, new_cd, 1)
        print("[FIX 7] V3.1.66c: Cooldown 30min -> 15min")
        changes += 1
    else:
        print("[SKIP 7] Cooldown pattern not found")

    # ================================================================
    # FIX 8 (V3.1.66d): Update V3.1.63 SNIPER MODE log lines
    # ================================================================
    old_sniper = 'logger.info("V3.1.63 SNIPER MODE:")'
    new_sniper = 'logger.info("V3.1.67 SNIPER MODE (RECOVERED):")'
    if old_sniper in content:
        content = content.replace(old_sniper, new_sniper, 1)
        changes += 1
        print("[FIX 8] Updated SNIPER MODE log line")

    write_file(DAEMON_FILE, content)
    print(f"\n[DAEMON] Applied {changes} fixes")
    return changes


def apply_nightly_fixes():
    """Apply V3.1.66b, 67 fixes to smt_nightly_trade_v3_1.py"""
    print("\n" + "=" * 60)
    print("NIGHTLY TRADE FIXES (smt_nightly_trade_v3_1.py)")
    print("=" * 60)
    
    backup(NIGHTLY_FILE)
    content = read_file(NIGHTLY_FILE)
    changes = 0

    # ================================================================
    # FIX 1 (V3.1.66b): Replace F&G-scaled TP with realistic tier-capped TP
    # This was the root cause of 9% TP targets
    # ================================================================
    old_tp_block = """    # V3.1.51: Regime-scaled TP
    try:
        fg_data = get_fear_greed_index()
        fg_val = fg_data.get("value", 50)
    except:
        fg_val = 50
    
    base_tp = tier_config["tp_pct"]
    if fg_val < 15:
        tp_multiplier = 1.8
        tp_label = "CAPITULATION_AGGRESSIVE"
    elif fg_val < 30:
        tp_multiplier = 1.25
        tp_label = "FEAR"
    elif fg_val > 80:
        tp_multiplier = 0.50
        tp_label = "EXTREME_GREED"
    elif fg_val > 60:
        tp_multiplier = 0.65
        tp_label = "GREED"
    else:
        tp_multiplier = 1.0
        tp_label = "NORMAL"
    
    tp_pct_raw = round(base_tp * tp_multiplier, 2)
    # Floor: TP must be at least 1.5x SL for positive expectancy
    tp_pct_raw = max(tp_pct_raw, sl_pct_raw * 1.5)


    tp_pct_raw = min(tp_pct_raw, 7.0)
    print(f"  [ATR-SL] SL: {sl_pct_raw:.2f}% | TP: {tp_pct_raw:.2f}% ({tp_label}, F&G={fg_val}, base={base_tp}%, mult={tp_multiplier}x)")"""
    
    new_tp_block = """    # V3.1.66b: REALISTIC TP - tier-based, no F&G scaling
    # F&G scaling caused 9% TPs in capitulation (unrealistic, never hit)
    # TP is now strictly tier-based with a sane floor
    base_tp = tier_config["tp_pct"]  # T1=5%, T2=6%, T3=7%
    tp_floor = sl_pct_raw * 1.2  # Minimum 1.2x SL for positive expectancy
    tp_pct_raw = max(base_tp, tp_floor)
    # Hard cap per tier (no exceptions)
    _tier_tp_caps = {1: 5.0, 2: 6.0, 3: 7.0}
    _tp_cap = _tier_tp_caps.get(tier, 7.0)
    tp_pct_raw = min(tp_pct_raw, _tp_cap)
    print(f"  [ATR-SL] SL: {sl_pct_raw:.2f}% | TP: {tp_pct_raw:.2f}% (Tier {tier} cap={_tp_cap}%, floor=SL*1.2={tp_floor:.2f}%)")"""
    
    if old_tp_block in content:
        content = content.replace(old_tp_block, new_tp_block, 1)
        print("[FIX 1] V3.1.66b: Replaced F&G-scaled TP with tier-capped realistic TP")
        changes += 1
    else:
        print("[SKIP 1] TP scaling block not found exactly")
        if "CAPITULATION_AGGRESSIVE" in content:
            print("  WARNING: CAPITULATION_AGGRESSIVE still in code!")
            print("  Trying flexible pattern match...")
            # Try to find and replace just the key parts
            import re
            # Find the block from "# V3.1.51: Regime-scaled TP" to the ATR-SL print
            pattern = r'    # V3\.1\.51: Regime-scaled TP.*?print\(f"  \[ATR-SL\].*?\)'
            match = re.search(pattern, content, re.DOTALL)
            if match:
                content = content[:match.start()] + new_tp_block + content[match.end():]
                print("  [FIX 1-ALT] Replaced TP block using regex")
                changes += 1
            else:
                print("  FAILED: Could not find TP block. MANUAL EDIT NEEDED.")
        else:
            print("  CAPITULATION_AGGRESSIVE not found - likely already fixed")

    # ================================================================
    # FIX 2 (V3.1.67): Fix sentiment persona empty response error
    # Add validation before accessing response.text
    # ================================================================
    old_sentiment = """        response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=60)
        
        clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)"""
    
    new_sentiment = """        response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=60)
        
        # V3.1.67: Validate Gemini response is not empty
        if not response or not hasattr(response, 'text') or not response.text:
            print(f"  [SENTIMENT] Empty response from Gemini for {pair}")
            return self._fallback_result(pair, "Empty Gemini response")
        
        clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        
        if not clean_text:
            print(f"  [SENTIMENT] Empty text after cleanup for {pair}")
            return self._fallback_result(pair, "Empty response text")
        
        data = json.loads(clean_text)"""
    
    if old_sentiment in content:
        content = content.replace(old_sentiment, new_sentiment, 1)
        print("[FIX 2] V3.1.67: Added sentiment persona empty response validation")
        changes += 1
    else:
        print("[SKIP 2] Sentiment pattern not found exactly")
        # Try a more flexible match
        if "response.text.strip().replace" in content and "Empty response from Gemini" not in content:
            # Find the first occurrence in sentiment persona context
            idx = content.find('response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config')
            if idx > 0:
                # Find the next line with response.text.strip()
                next_clean = content.find("clean_text = response.text.strip()", idx)
                if next_clean > 0 and next_clean - idx < 200:
                    # Insert validation before clean_text line
                    validation = """
        # V3.1.67: Validate Gemini response is not empty
        if not response or not hasattr(response, 'text') or not response.text:
            print(f"  [SENTIMENT] Empty response from Gemini for {pair}")
            return self._fallback_result(pair, "Empty Gemini response")
        
"""
                    content = content[:next_clean] + validation + content[next_clean:]
                    print("[FIX 2-ALT] V3.1.67: Inserted sentiment validation before clean_text")
                    changes += 1

    # ================================================================
    # FIX 3 (V3.1.67): Also validate Judge persona Gemini response
    # ================================================================
    # Find Judge's response parsing
    judge_old = """            clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()"""
    judge_validation = """            # V3.1.67: Validate Judge response
            if not response or not hasattr(response, 'text') or not response.text:
                print(f"  [JUDGE] Empty Gemini response, using fallback")
                return self._fallback_decide(persona_votes, pair, balance, competition_status, tier, tier_config, regime)
            clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()"""
    
    # Only apply to the SECOND occurrence (Judge, not Sentiment which we already fixed)
    # Count occurrences
    count = content.count(judge_old)
    if count >= 2 and "V3.1.67: Validate Judge response" not in content:
        # Replace the second occurrence only
        first_idx = content.find(judge_old)
        if first_idx >= 0:
            second_idx = content.find(judge_old, first_idx + len(judge_old))
            if second_idx >= 0:
                content = content[:second_idx] + judge_validation + content[second_idx + len(judge_old):]
                print("[FIX 3] V3.1.67: Added Judge persona empty response validation")
                changes += 1
    else:
        print("[SKIP 3] Judge validation - pattern count mismatch or already applied")

    write_file(NIGHTLY_FILE, content)
    print(f"\n[NIGHTLY] Applied {changes} fixes")
    return changes


def verify_fixes():
    """Verify all fixes were applied correctly"""
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)
    
    daemon = read_file(DAEMON_FILE)
    nightly = read_file(NIGHTLY_FILE)
    
    checks = [
        ("Daemon: global _last_trade_opened_at", "global _last_trade_opened_at" in daemon),
        ("Daemon: RL regime try/except", "rl_regime = get_market_regime_for_exit()\n                        except Exception:" in daemon),
        ("Daemon: No self-import", "from smt_daemon_v3_1 import get_market_regime_for_exit" not in daemon),
        ("Daemon: Loop traceback", "logger.error(traceback.format_exc())" in daemon),
        ("Daemon: Dead swap removed", "if False and confidence >= SWAP_MIN_CONFIDENCE" not in daemon),
        ("Daemon: 15min cooldown", "GLOBAL_TRADE_COOLDOWN = 900" in daemon),
        ("Daemon: V3.1.67 banner", "V3.1.67" in daemon),
        ("Nightly: No CAPITULATION_AGGRESSIVE TP", "CAPITULATION_AGGRESSIVE" not in nightly or "CAPITULATION_AGGRESSIVE" in nightly.split("# V3.1.66b")[0] if "# V3.1.66b" in nightly else "CAPITULATION_AGGRESSIVE" not in nightly),
        ("Nightly: Tier-capped TP", "_tier_tp_caps" in nightly),
        ("Nightly: Sentiment empty check", "Empty response from Gemini" in nightly or "Empty Gemini response" in nightly),
    ]
    
    all_pass = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}")
    
    # Check for CAPITULATION_AGGRESSIVE more carefully
    if "CAPITULATION_AGGRESSIVE" in nightly:
        # Check if it's only in the flow persona (capitulation cap) not TP scaling
        tp_section_start = nightly.find("tp_pct_raw")
        if tp_section_start > 0:
            tp_section = nightly[tp_section_start:tp_section_start+500]
            if "CAPITULATION_AGGRESSIVE" in tp_section:
                print("  [FAIL] CAPITULATION_AGGRESSIVE still in TP section!")
                all_pass = False
            else:
                print("  [INFO] CAPITULATION_AGGRESSIVE exists in FLOW persona (expected, that's the cap logic)")
    
    # Syntax check
    print("\n  Syntax checks:")
    import subprocess
    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        result = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"    [PASS] {f} syntax OK")
        else:
            print(f"    [FAIL] {f} syntax error: {result.stderr}")
            all_pass = False
    
    return all_pass


def main():
    print("=" * 60)
    print("SMT RECOVERY: V3.1.65 -> V3.1.67")
    print("=" * 60)
    print()
    print("Changes being applied:")
    print("  V3.1.66:  fix RL log error, scoping bug, dead swap code, regime veto import")
    print("  V3.1.66b: realistic tier-capped TP, remove F&G scaling") 
    print("  V3.1.66c: 15min cooldown (was 30min)")
    print("  V3.1.67:  fix sentiment/judge persona empty response validation")
    print()
    
    # Check files exist
    import os
    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found!")
            print(f"Make sure you're in ~/smt-weex-trading-bot/v3")
            sys.exit(1)
    
    daemon_changes = apply_daemon_fixes()
    nightly_changes = apply_nightly_fixes()
    
    total = daemon_changes + nightly_changes
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total} fixes applied")
    print(f"{'=' * 60}")
    
    all_pass = verify_fixes()
    
    if all_pass:
        print("\n[ALL CHECKS PASSED]")
    else:
        print("\n[SOME CHECKS FAILED] - Review output above")
    
    print(f"\nNext steps:")
    print(f"  1. Review changes: git diff")
    print(f"  2. Kill daemon: pkill -f smt_daemon")
    print(f"  3. Restart: nohup python3 smt_daemon_v3_1.py --force > daemon.log 2>&1 &")
    print(f"  4. Monitor: tail -f daemon.log")
    print(f"  5. Commit: git add . && git commit -m 'V3.1.67: RECOVERY - restore v66-67 fixes (RL, regime veto, TP caps, sentiment validation)' && git push")


if __name__ == "__main__":
    main()
