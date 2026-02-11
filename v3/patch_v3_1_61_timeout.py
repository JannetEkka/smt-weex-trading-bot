#!/usr/bin/env python3
"""V3.1.61: Add 120s timeout to ALL Gemini API calls to prevent daemon hangs"""
import re

changes = 0

# ============================================================
# FIX 1: smt_daemon_v3_1.py - Portfolio Manager Gemini call
# ============================================================
with open("smt_daemon_v3_1.py", "r") as f:
    daemon = f.read()

# Add timeout helper function after imports section
helper = '''
# ============================================================
# V3.1.61: GEMINI TIMEOUT WRAPPER
# ============================================================

def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout to prevent daemon hangs."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)

'''

# Insert after the DaemonState class (before first function)
marker = "class DaemonState:"
if "_gemini_with_timeout" not in daemon:
    # Find a good insertion point - right before the class
    idx = daemon.find(marker)
    if idx > 0:
        daemon = daemon[:idx] + helper + daemon[idx:]
        changes += 1
        print("FIX 1a: Added _gemini_with_timeout helper to daemon")
    else:
        print("WARN: Could not find DaemonState marker")

# Replace the bare generate_content call in gemini_portfolio_review
old_daemon_call = """        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config
        )"""

new_daemon_call = """        response = _gemini_with_timeout(client, "gemini-2.5-flash", prompt, config, timeout=120)"""

if old_daemon_call in daemon:
    daemon = daemon.replace(old_daemon_call, new_daemon_call, 1)
    changes += 1
    print("FIX 1b: Wrapped daemon PM Gemini call with 120s timeout")
else:
    print("WARN: Daemon Gemini call pattern not found (may already be patched)")

# Update version string
daemon = daemon.replace(
    'SMT Daemon V3.1.55 - OPPOSITE SIDE + WHALE EXITS + SYNC FIX',
    'SMT Daemon V3.1.61 - GEMINI TIMEOUT + OPPOSITE SIDE + WHALE EXITS'
)
changes += 1

with open("smt_daemon_v3_1.py", "w") as f:
    f.write(daemon)

# ============================================================
# FIX 2: smt_nightly_trade_v3_1.py - Analysis + Judge calls
# ============================================================
with open("smt_nightly_trade_v3_1.py", "r") as f:
    nightly = f.read()

# Add timeout helper to nightly too
nightly_helper = '''
# V3.1.61: Gemini timeout wrapper
def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)

'''

# Insert after the hot-reload marker or near top
if "_gemini_with_timeout" not in nightly:
    # Find a good spot - after the imports/constants
    hot_reload = "Hot-reload enabled"
    idx = nightly.find(hot_reload)
    if idx > 0:
        # Find end of that line
        eol = nightly.find("\n", idx)
        nightly = nightly[:eol+1] + nightly_helper + nightly[eol+1:]
        changes += 1
        print("FIX 2a: Added _gemini_with_timeout helper to nightly")
    else:
        print("WARN: Could not find hot-reload marker in nightly")

# Fix call site 1: _analyze_with_retry (grounded search call ~line 1513)
old_nightly_call1 = """        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=combined_prompt,
            config=grounding_config
        )"""

new_nightly_call1 = """        response = _gemini_with_timeout(client, "gemini-2.5-flash", combined_prompt, grounding_config, timeout=120)"""

if old_nightly_call1 in nightly:
    nightly = nightly.replace(old_nightly_call1, new_nightly_call1, 1)
    changes += 1
    print("FIX 2b: Wrapped nightly analysis Gemini call with 120s timeout")
else:
    print("WARN: Nightly analysis call pattern not found")

# Fix call site 2: Judge Gemini call (~line 2095)
old_nightly_call2 = """            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config
            )"""

new_nightly_call2 = """            response = _gemini_with_timeout(client, "gemini-2.5-flash", prompt, config, timeout=120)"""

if old_nightly_call2 in nightly:
    nightly = nightly.replace(old_nightly_call2, new_nightly_call2, 1)
    changes += 1
    print("FIX 2c: Wrapped nightly Judge Gemini call with 120s timeout")
else:
    print("WARN: Nightly Judge call pattern not found")

with open("smt_nightly_trade_v3_1.py", "w") as f:
    f.write(nightly)

print(f"\nTotal changes: {changes}")
print("=" * 50)

# Syntax check
import py_compile
try:
    py_compile.compile("smt_daemon_v3_1.py", doraise=True)
    py_compile.compile("smt_nightly_trade_v3_1.py", doraise=True)
    print("SYNTAX OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")

print("""
Restart:
  pkill -9 -f smt_daemon_v3_1
  sleep 2
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  sleep 10
  tail -20 logs/daemon_v3_1_7_$(date +%Y%m%d).log

Then commit:
  git add -A && git commit -m "V3.1.61: 120s timeout on all Gemini API calls - prevents daemon hang" && git push
""")
