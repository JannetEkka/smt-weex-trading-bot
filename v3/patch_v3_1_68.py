#!/usr/bin/env python3
"""
V3.1.68: Fix Gemini rate limiting + per-cycle cooldown
=======================================================
Problems:
1. Sentiment persona gets empty responses because 8 Gemini calls fire too fast
2. Global cooldown blocks ALL trades after the first one in a cycle
3. TP could be tighter for faster profit capture

Fixes:
1. Add 3s delay between each pair's analysis (not just between Gemini calls)
2. Change cooldown to only apply BETWEEN cycles, not within same cycle
3. Tighten TP caps: T1=4%, T2=5%, T3=6% (was 5/6/7)

Run: python3 patch_v3_1_68.py
"""

import shutil
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
    bp = f"{path}.bak.{ts}"
    shutil.copy2(path, bp)
    print(f"  Backup: {bp}")

def patch_daemon():
    print("=" * 60)
    print("DAEMON PATCHES")
    print("=" * 60)
    backup(DAEMON_FILE)
    content = read_file(DAEMON_FILE)
    changes = 0

    # ================================================================
    # FIX 1: Change cooldown to not block within same signal cycle
    # The cooldown should prevent opening trades in CONSECUTIVE cycles,
    # not block the 2nd/3rd trade in the SAME analysis cycle.
    # Replace the cooldown check with one that uses cycle_start_time
    # ================================================================
    old_cooldown_check = """                # V3.1.65: GLOBAL TRADE COOLDOWN CHECK
                _now_cooldown = time.time()
                if _now_cooldown - _last_trade_opened_at < GLOBAL_TRADE_COOLDOWN:
                    _cd_remaining = GLOBAL_TRADE_COOLDOWN - (_now_cooldown - _last_trade_opened_at)
                    logger.info(f"GLOBAL COOLDOWN: {_cd_remaining:.0f}s remaining, skipping {opportunity['pair']}")
                    continue"""

    new_cooldown_check = """                # V3.1.68: INTER-CYCLE COOLDOWN (not intra-cycle)
                # Only block if the last trade was from a PREVIOUS cycle
                _now_cooldown = time.time()
                _cooldown_elapsed = _now_cooldown - _last_trade_opened_at
                if _cooldown_elapsed < GLOBAL_TRADE_COOLDOWN and _cooldown_elapsed > 120:
                    # More than 2min since last trade = different cycle, apply cooldown
                    _cd_remaining = GLOBAL_TRADE_COOLDOWN - _cooldown_elapsed
                    logger.info(f"GLOBAL COOLDOWN: {_cd_remaining:.0f}s remaining, skipping {opportunity['pair']}")
                    continue
                # Within same cycle (< 2min gap) = allow multiple trades"""

    if old_cooldown_check in content:
        content = content.replace(old_cooldown_check, new_cooldown_check, 1)
        print("[FIX 1] V3.1.68: Cooldown now only blocks between cycles, not within same cycle")
        changes += 1
    else:
        print("[SKIP 1] Cooldown check pattern not found")

    # ================================================================
    # FIX 2: Add delay between pair analyses to avoid Gemini rate limits
    # Find the loop that iterates over pairs and add a sleep
    # ================================================================
    # Look for the pattern where we iterate and call analyze
    old_pair_loop = """                time.sleep(1)  # Small delay between trades"""
    new_pair_loop = """                time.sleep(3)  # V3.1.68: 3s delay between trades (Gemini rate limit)"""

    if old_pair_loop in content:
        content = content.replace(old_pair_loop, new_pair_loop, 1)
        print("[FIX 2] V3.1.68: Increased inter-trade delay to 3s")
        changes += 1
    else:
        print("[SKIP 2] Inter-trade delay pattern not found")

    # ================================================================
    # FIX 3: Add delay between pair ANALYSES (the Gemini calls per pair)
    # Look for where each pair analysis ends and next begins
    # ================================================================
    # The analysis loop typically has a sleep after each pair analysis
    # Search for the pattern after AI LOG upload for each analysis
    old_analysis_sleep = """            time.sleep(1)  # Rate limit between analyses"""
    if old_analysis_sleep in content:
        content = content.replace(old_analysis_sleep, 
            """            time.sleep(4)  # V3.1.68: 4s between pair analyses (Gemini rate limit)""", 1)
        print("[FIX 3] V3.1.68: Added 4s delay between pair analyses")
        changes += 1
    else:
        # Try alternate pattern - look for where analysis results are logged
        # and add sleep after each pair
        if "time.sleep(2)  # Rate limit" in content:
            content = content.replace("time.sleep(2)  # Rate limit",
                "time.sleep(4)  # V3.1.68: 4s between analyses (Gemini rate limit)", 1)
            print("[FIX 3-ALT] V3.1.68: Increased analysis delay to 4s")
            changes += 1
        else:
            print("[SKIP 3] Analysis delay pattern not found - checking for insertion point...")
            # Find where we can add the delay
            # Look for the pattern after each pair's AI log upload in the analysis loop
            marker = '    [AI LOG OK] V3.1.9 Analysis'
            if marker not in content:
                # The upload happens with upload_ai_log_to_weex, find the analysis loop
                import re
                # Find "for pair, pair_info in" loop and add sleep after analysis
                pattern = r'(upload_ai_log_to_weex\(\s*stage=f"V3\.1\.9 Analysis.*?\))'
                # This is complex, let's try a simpler approach
                print("[INFO 3] Will need manual delay addition if Gemini rate limiting persists")

    # ================================================================
    # FIX 4: Update version banner
    # ================================================================
    old_banner = 'logger.info("SMT Daemon V3.1.67 - SNIPER MODE + RECOVERY: regime veto fix, realistic TP, aggressive slots")'
    new_banner = 'logger.info("SMT Daemon V3.1.68 - SNIPER MODE: inter-cycle cooldown, Gemini rate limit fix, tighter TP")'
    if old_banner in content:
        content = content.replace(old_banner, new_banner, 1)
        changes += 1
        print("[FIX 4] Updated version banner to V3.1.68")
    
    write_file(DAEMON_FILE, content)
    print(f"\n[DAEMON] Applied {changes} fixes")
    return changes


def patch_nightly():
    print("\n" + "=" * 60)
    print("NIGHTLY TRADE PATCHES")
    print("=" * 60)
    backup(NIGHTLY_FILE)
    content = read_file(NIGHTLY_FILE)
    changes = 0

    # ================================================================
    # FIX 1: Tighten TP caps (T1: 5->4, T2: 6->5, T3: 7->6)
    # At 18x leverage: 4% price = 72% ROE, 5% = 90% ROE, 6% = 108% ROE
    # These are still very aggressive targets
    # ================================================================
    old_tp_caps = """    _tier_tp_caps = {1: 5.0, 2: 6.0, 3: 7.0}"""
    new_tp_caps = """    _tier_tp_caps = {1: 4.0, 2: 5.0, 3: 6.0}  # V3.1.68: Tighter (was 5/6/7)"""

    if old_tp_caps in content:
        content = content.replace(old_tp_caps, new_tp_caps, 1)
        print("[FIX 1] V3.1.68: Tighter TP caps T1=4%, T2=5%, T3=6% (was 5/6/7)")
        changes += 1
    else:
        print("[SKIP 1] TP caps pattern not found")

    # ================================================================
    # FIX 2: Add explicit delay in _rate_limit_gemini if interval too short
    # Increase the minimum interval between Gemini calls
    # ================================================================
    old_interval = "_gemini_call_interval = 2.0  # seconds between calls"
    new_interval = "_gemini_call_interval = 4.0  # V3.1.68: 4s between Gemini calls (was 2s, caused empty responses)"

    if old_interval in content:
        content = content.replace(old_interval, new_interval, 1)
        print("[FIX 2] V3.1.68: Gemini call interval 2s -> 4s")
        changes += 1
    else:
        print("[SKIP 2] Gemini interval pattern not found")

    # ================================================================
    # FIX 3: Add retry on empty Gemini response in sentiment persona
    # Before falling back to neutral, retry once after a delay
    # ================================================================
    old_empty_check = """        # V3.1.67: Validate Gemini response is not empty
        if not response or not hasattr(response, 'text') or not response.text:
            print(f"  [SENTIMENT] Empty response from Gemini for {pair}")
            return self._fallback_result(pair, "Empty Gemini response")"""

    new_empty_check = """        # V3.1.68: Validate Gemini response - retry once on empty
        if not response or not hasattr(response, 'text') or not response.text:
            print(f"  [SENTIMENT] Empty response from Gemini for {pair}, retrying in 5s...")
            import time as _time
            _time.sleep(5)
            try:
                response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=60)
            except Exception:
                pass
            if not response or not hasattr(response, 'text') or not response.text:
                print(f"  [SENTIMENT] Still empty after retry for {pair}")
                return self._fallback_result(pair, "Empty Gemini response after retry")"""

    if old_empty_check in content:
        content = content.replace(old_empty_check, new_empty_check, 1)
        print("[FIX 3] V3.1.68: Sentiment persona retries once on empty Gemini response")
        changes += 1
    else:
        print("[SKIP 3] Empty check pattern not found")

    write_file(NIGHTLY_FILE, content)
    print(f"\n[NIGHTLY] Applied {changes} fixes")
    return changes


def verify():
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)
    
    daemon = read_file(DAEMON_FILE)
    nightly = read_file(NIGHTLY_FILE)
    
    checks = [
        ("Daemon: Inter-cycle cooldown", "_cooldown_elapsed > 120" in daemon),
        ("Nightly: Tighter TP caps", "4.0, 2: 5.0, 3: 6.0" in nightly),
        ("Nightly: 4s Gemini interval", "_gemini_call_interval = 4.0" in nightly),
        ("Nightly: Sentiment retry", "retrying in 5s" in nightly),
    ]
    
    all_pass = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}")
    
    import subprocess, sys
    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        result = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [PASS] {f} syntax OK")
        else:
            print(f"  [FAIL] {f}: {result.stderr}")
            all_pass = False
    
    return all_pass


def main():
    print("V3.1.68 PATCH: Gemini rate limit + inter-cycle cooldown + tighter TP\n")
    
    import os
    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found!")
            return
    
    d = patch_daemon()
    n = patch_nightly()
    total = d + n
    
    print(f"\nTotal: {total} fixes applied")
    
    ok = verify()
    if ok:
        print("\n[ALL CHECKS PASSED]")
    else:
        print("\n[SOME CHECKS FAILED]")
    
    print(f"\nNext steps:")
    print(f"  1. pkill -f smt_daemon")
    print(f"  2. nohup python3 smt_daemon_v3_1.py --force > daemon.log 2>&1 &")
    print(f"  3. tail -f daemon.log")
    print(f"  4. git add . && git commit -m 'V3.1.68: Gemini rate limit fix, inter-cycle cooldown, tighter TP 4/5/6%' && git push")


if __name__ == "__main__":
    main()
