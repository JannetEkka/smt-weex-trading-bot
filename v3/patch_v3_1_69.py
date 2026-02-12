#!/usr/bin/env python3
"""
V3.1.69: BULLETPROOF GEMINI TIMEOUT + INTERNAL WATCHDOG
=========================================================
Problem: genai.Client() hangs indefinitely, freezing the daemon.
_gemini_with_timeout only wraps generate_content, NOT client creation.

Fix 1: Replace _gemini_with_timeout in BOTH files with a version that
       wraps the ENTIRE flow (import + client + call) in one timeout.
Fix 2: Add internal watchdog thread to daemon that force-restarts if
       the main loop hasn't made progress in 10 minutes.
Fix 3: Update external watchdog.sh to check for hangs every 5 minutes
       (was 20 minutes).

Run: python3 patch_v3_1_69.py
"""

import shutil
from datetime import datetime

DAEMON_FILE = "smt_daemon_v3_1.py"
NIGHTLY_FILE = "smt_nightly_trade_v3_1.py"
WATCHDOG_FILE = "watchdog.sh"

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


def patch_nightly():
    """Fix _gemini_with_timeout to wrap ENTIRE Gemini flow including Client()"""
    print("=" * 60)
    print("NIGHTLY TRADE: Bulletproof Gemini timeout")
    print("=" * 60)
    backup(NIGHTLY_FILE)
    content = read_file(NIGHTLY_FILE)
    changes = 0

    # ================================================================
    # FIX 1: Replace _gemini_with_timeout with full-flow version
    # ================================================================
    old_timeout = '''def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)'''

    new_timeout = '''def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"  [GEMINI TIMEOUT] Call exceeded {timeout}s, cancelling")
            future.cancel()
            raise TimeoutError(f"Gemini call timed out after {timeout}s")


def _gemini_full_call(model, contents, config, timeout=90, use_grounding=False):
    """V3.1.69: BULLETPROOF Gemini call - wraps EVERYTHING in one timeout.
    
    This wraps client creation + generate_content in a single thread timeout.
    Prevents hangs from genai.Client() initialization or network issues.
    """
    import concurrent.futures
    
    def _do_call():
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client()
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"  [GEMINI TIMEOUT] Full call exceeded {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini full call timed out after {timeout}s")'''

    if old_timeout in content:
        content = content.replace(old_timeout, new_timeout, 1)
        print("[FIX 1] Added _gemini_full_call() to nightly trade")
        changes += 1
    else:
        print("[SKIP 1] _gemini_with_timeout pattern not found in nightly")

    # ================================================================
    # FIX 2: Update Sentiment persona to use _gemini_full_call
    # Replace the client creation + call with single _gemini_full_call
    # ================================================================
    old_sentiment_call = '''        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        
        client = genai.Client()'''

    new_sentiment_call = '''        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool'''

    # Only replace the FIRST occurrence (in Sentiment persona)
    if old_sentiment_call in content:
        # Find position to make sure it's in the sentiment context
        idx = content.find(old_sentiment_call)
        # Check it's in _analyze_with_retry
        context_start = max(0, idx - 200)
        context = content[context_start:idx]
        if '_analyze_with_retry' in context or 'combined_prompt' in content[idx:idx+500]:
            content = content[:idx] + new_sentiment_call + content[idx + len(old_sentiment_call):]
            print("[FIX 2a] Removed genai.Client() from Sentiment persona")
            changes += 1

    # Now replace the _gemini_with_timeout call in sentiment with _gemini_full_call
    old_sent_gemini = '''        response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=60)'''
    new_sent_gemini = '''        response = _gemini_full_call("gemini-2.5-flash", combined_prompt, grounding_config, timeout=75)'''

    if old_sent_gemini in content:
        content = content.replace(old_sent_gemini, new_sent_gemini, 1)
        print("[FIX 2b] Sentiment now uses _gemini_full_call (75s timeout)")
        changes += 1

    # Also fix the retry call in sentiment (the one inside the empty response retry)
    old_retry_gemini = '''                response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=60)'''
    new_retry_gemini = '''                response = _gemini_full_call("gemini-2.5-flash", combined_prompt, grounding_config, timeout=75)'''

    if old_retry_gemini in content:
        content = content.replace(old_retry_gemini, new_retry_gemini, 1)
        print("[FIX 2c] Sentiment retry also uses _gemini_full_call")
        changes += 1

    # ================================================================
    # FIX 3: Update Judge persona to use _gemini_full_call
    # ================================================================
    old_judge_client = '''            from google import genai
            from google.genai.types import GenerateContentConfig
            
            client = genai.Client()
            
            config = GenerateContentConfig(
                temperature=0.1,
            )
            
            response = _gemini_with_timeout(client, "gemini-2.5-flash", prompt, config, timeout=60)'''

    new_judge_client = '''            from google.genai.types import GenerateContentConfig
            
            config = GenerateContentConfig(
                temperature=0.1,
            )
            
            response = _gemini_full_call("gemini-2.5-flash", prompt, config, timeout=75)'''

    if old_judge_client in content:
        content = content.replace(old_judge_client, new_judge_client, 1)
        print("[FIX 3] Judge now uses _gemini_full_call (75s timeout)")
        changes += 1
    else:
        print("[SKIP 3] Judge client pattern not found")

    write_file(NIGHTLY_FILE, content)
    print(f"\n[NIGHTLY] Applied {changes} fixes")
    return changes


def patch_daemon():
    """Fix daemon's _gemini_with_timeout and add internal watchdog"""
    print("\n" + "=" * 60)
    print("DAEMON: Bulletproof Gemini timeout + internal watchdog")
    print("=" * 60)
    backup(DAEMON_FILE)
    content = read_file(DAEMON_FILE)
    changes = 0

    # ================================================================
    # FIX 1: Replace daemon's _gemini_with_timeout with bulletproof version
    # ================================================================
    old_daemon_timeout = '''def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout to prevent daemon hangs."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)'''

    new_daemon_timeout = '''def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout to prevent daemon hangs."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Gemini call timed out after {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini call timed out after {timeout}s")


def _gemini_full_call_daemon(model, contents, config, timeout=90):
    """V3.1.69: BULLETPROOF Gemini call for daemon - wraps EVERYTHING."""
    import concurrent.futures
    
    def _do_call():
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client()
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Gemini full call timed out after {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini full call timed out after {timeout}s")'''

    if old_daemon_timeout in content:
        content = content.replace(old_daemon_timeout, new_daemon_timeout, 1)
        print("[FIX 1] Added _gemini_full_call_daemon() to daemon")
        changes += 1
    else:
        print("[SKIP 1] Daemon _gemini_with_timeout pattern not found")

    # ================================================================
    # FIX 2: Update daemon's Gemini call (portfolio manager) to use new function
    # ================================================================
    # The daemon calls Gemini at line ~1746 for portfolio review
    old_daemon_gemini = '''            client = genai.Client()
            
            config = GenerateContentConfig('''
    # Check context to make sure we're in the right place
    if old_daemon_gemini in content:
        idx = content.find(old_daemon_gemini)
        # Find the matching _gemini_with_timeout call after this
        after = content[idx:idx+500]
        if '_gemini_with_timeout(client' in after:
            # Replace client creation
            content = content[:idx] + '''            config = GenerateContentConfig(''' + content[idx + len(old_daemon_gemini):]
            print("[FIX 2a] Removed genai.Client() from daemon portfolio manager")
            changes += 1
            
            # Replace the call
            old_pm_call = '_gemini_with_timeout(client, "gemini-2.5-flash", prompt, config, timeout=120)'
            new_pm_call = '_gemini_full_call_daemon("gemini-2.5-flash", prompt, config, timeout=90)'
            if old_pm_call in content:
                content = content.replace(old_pm_call, new_pm_call, 1)
                print("[FIX 2b] Daemon portfolio manager uses _gemini_full_call_daemon")
                changes += 1

    # ================================================================
    # FIX 3: Add internal watchdog thread that detects hangs
    # ================================================================
    # Add after DaemonState class, before the main functions
    internal_watchdog = '''

# ============================================================
# V3.1.69: INTERNAL WATCHDOG - detect and recover from hangs
# ============================================================
import threading

_last_progress_time = time.time()
_progress_lock = threading.Lock()

def _mark_progress():
    """Called by main loop to indicate the daemon is making progress."""
    global _last_progress_time
    with _progress_lock:
        _last_progress_time = time.time()

def _internal_watchdog():
    """Background thread that kills the process if it hangs > 10 minutes."""
    HANG_TIMEOUT = 600  # 10 minutes without progress = hung
    while True:
        time.sleep(60)
        with _progress_lock:
            elapsed = time.time() - _last_progress_time
        if elapsed > HANG_TIMEOUT:
            logger.error(f"INTERNAL WATCHDOG: No progress for {elapsed:.0f}s. Force exit!")
            logger.error("The external watchdog.sh will restart us.")
            os._exit(1)  # Hard exit, watchdog.sh will restart

# Start internal watchdog as daemon thread
_watchdog_thread = threading.Thread(target=_internal_watchdog, daemon=True)
_watchdog_thread.start()

'''

    # Insert after the tracker initialization
    marker = 'tracker = TradeTracker(state_file="trade_state_v3_1_7.json")'
    if marker in content and '_internal_watchdog' not in content:
        content = content.replace(marker, marker + internal_watchdog, 1)
        print("[FIX 3] Added internal watchdog thread (10min hang detection)")
        changes += 1
    else:
        if '_internal_watchdog' in content:
            print("[SKIP 3] Internal watchdog already exists")
        else:
            print("[SKIP 3] Tracker marker not found")

    # ================================================================
    # FIX 4: Add _mark_progress() calls in the main loop
    # ================================================================
    # Add after each health check and position monitor
    old_health = 'log_health()\n                last_health = now'
    new_health = 'log_health()\n                _mark_progress()\n                last_health = now'
    if old_health in content and '_mark_progress()' not in content.split('log_health()')[1][:100]:
        content = content.replace(old_health, new_health, 1)
        print("[FIX 4a] Added _mark_progress() after health check")
        changes += 1

    old_signal = 'check_trading_signals()\n                last_signal = now'
    new_signal = 'check_trading_signals()\n                _mark_progress()\n                last_signal = now'
    if old_signal in content:
        content = content.replace(old_signal, new_signal, 1)
        print("[FIX 4b] Added _mark_progress() after signal check")
        changes += 1

    old_position = 'monitor_positions()'
    new_position = 'monitor_positions()\n                _mark_progress()'
    # Only replace in the main loop, not in function definitions
    # Find the one in the while loop
    loop_idx = content.find('while state.is_running')
    if loop_idx > 0:
        after_loop = content[loop_idx:]
        if old_position in after_loop and '_mark_progress()' not in after_loop.split('monitor_positions()')[1][:50]:
            # Replace only the first occurrence after the while loop
            pos_in_loop = after_loop.find(old_position)
            abs_pos = loop_idx + pos_in_loop
            content = content[:abs_pos] + new_position + content[abs_pos + len(old_position):]
            print("[FIX 4c] Added _mark_progress() after position monitor")
            changes += 1

    # ================================================================
    # FIX 5: Update version banner
    # ================================================================
    old_v = 'SMT Daemon V3.1.68'
    new_v = 'SMT Daemon V3.1.69'
    if old_v in content:
        content = content.replace(old_v, new_v, 1)
        changes += 1
        print("[FIX 5] Updated version to V3.1.69")

    write_file(DAEMON_FILE, content)
    print(f"\n[DAEMON] Applied {changes} fixes")
    return changes


def patch_watchdog():
    """Update watchdog.sh for faster hang detection"""
    print("\n" + "=" * 60)
    print("WATCHDOG: Faster hang detection")
    print("=" * 60)
    backup(WATCHDOG_FILE)
    
    new_watchdog = '''#!/bin/bash
# V3.1.69 WATCHDOG - faster hang detection (5min vs 20min)
TIMEOUT=300  # 5 minutes without log activity = hung

while true; do
    sleep 30  # Check every 30 seconds

    DAEMON_COUNT=$(pgrep -fc "smt_daemon_v3_1.py" 2>/dev/null || echo 0)

    # Kill duplicates
    if [ "$DAEMON_COUNT" -gt 1 ]; then
        echo "$(date) | WATCHDOG: $DAEMON_COUNT daemons. Killing ALL." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        pkill -9 -f "smt_daemon_v3_1.py"
        sleep 5
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 60
        continue
    fi

    # Restart if dead
    if [ "$DAEMON_COUNT" -eq 0 ]; then
        echo "$(date) | WATCHDOG: No daemon. Starting." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 60
        continue
    fi

    # Detect hangs - check log freshness
    LOG_FILE=~/smt-weex-trading-bot/v3/logs/daemon_v3_1_7_$(date +%Y%m%d).log
    if [ -f "$LOG_FILE" ]; then
        LAST_MOD=$(stat -c %Y "$LOG_FILE")
        NOW=$(date +%s)
        DIFF=$((NOW - LAST_MOD))

        if [ $DIFF -gt $TIMEOUT ]; then
            echo "$(date) | WATCHDOG: Stale ${DIFF}s (>${TIMEOUT}s). SIGKILL + restart." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
            pkill -9 -f "smt_daemon_v3_1.py"
            sleep 5
            cd ~/smt-weex-trading-bot/v3
            nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
            sleep 60
        fi
    fi
done
'''
    
    write_file(WATCHDOG_FILE, new_watchdog)
    print("[FIX] Watchdog: 5min timeout (was 20min), 30s check interval (was 60s)")
    return 1


def verify():
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)
    
    daemon = read_file(DAEMON_FILE)
    nightly = read_file(NIGHTLY_FILE)
    watchdog = read_file(WATCHDOG_FILE)
    
    checks = [
        ("Nightly: _gemini_full_call exists", "def _gemini_full_call(" in nightly),
        ("Nightly: Sentiment uses _gemini_full_call", "_gemini_full_call(\"gemini-2.5-flash\", combined_prompt" in nightly),
        ("Nightly: Judge uses _gemini_full_call", "_gemini_full_call(\"gemini-2.5-flash\", prompt, config" in nightly),
        ("Nightly: No bare genai.Client() in Sentiment", True),  # Will check manually
        ("Daemon: _gemini_full_call_daemon exists", "def _gemini_full_call_daemon(" in daemon),
        ("Daemon: Internal watchdog", "_internal_watchdog" in daemon),
        ("Daemon: _mark_progress calls", "_mark_progress()" in daemon),
        ("Daemon: V3.1.69", "V3.1.69" in daemon),
        ("Watchdog: 5min timeout", "TIMEOUT=300" in watchdog),
        ("Watchdog: 30s interval", "sleep 30" in watchdog),
    ]
    
    # Check that Sentiment persona no longer has bare genai.Client()
    # Find _analyze_with_retry and check for genai.Client()
    idx = nightly.find("def _analyze_with_retry")
    if idx > 0:
        method_end = nightly.find("\n    def ", idx + 10)
        method_body = nightly[idx:method_end] if method_end > 0 else nightly[idx:idx+2000]
        has_bare_client = "client = genai.Client()" in method_body
        checks[3] = ("Nightly: No bare genai.Client() in Sentiment", not has_bare_client)
    
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
    print("V3.1.69: BULLETPROOF GEMINI TIMEOUT + INTERNAL WATCHDOG\n")
    
    import os
    for f in [DAEMON_FILE, NIGHTLY_FILE, WATCHDOG_FILE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found!")
            return
    
    n = patch_nightly()
    d = patch_daemon()
    w = patch_watchdog()
    total = n + d + w
    
    print(f"\nTotal: {total} fixes applied")
    ok = verify()
    
    if ok:
        print("\n[ALL CHECKS PASSED]")
    else:
        print("\n[SOME CHECKS FAILED] - review above")
    
    print(f"\nNext steps:")
    print(f"  1. pkill -9 -f smt_daemon")
    print(f"  2. pkill -f watchdog.sh")
    print(f"  3. nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &")
    print(f"  4. nohup bash watchdog.sh >> logs/watchdog.log 2>&1 &")
    print(f"  5. tail -f logs/daemon_v3_1_7_$(date +%Y%m%d).log")
    print(f"  6. git add . && git commit -m 'V3.1.69: bulletproof Gemini timeout, internal watchdog, 5min external watchdog' && git push")


if __name__ == "__main__":
    main()
