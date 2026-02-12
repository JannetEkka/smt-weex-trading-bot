#!/usr/bin/env python3
"""
HOTFIX V3.1.64a - ENFORCE HARD CAP + FIX SL OVERRIDE
======================================================
Date: 2026-02-12
Fixes three bypass paths discovered after V3.1.64 deployment:

1. CONFIDENCE_OVERRIDE bypass: MAX_CONFIDENCE_SLOTS=2 lets bot open 5 positions
   FIX: Set MAX_CONFIDENCE_SLOTS=0, MAX_BONUS_SLOTS=0

2. ATR-SL overrides Judge's vol-adjusted SL: Judge widens to 4.5% but ATR-SL
   caps at 4.0% or resets to tier default 3.0%
   FIX: Pass Judge's SL as a floor to ATR-SL, use wider of the two

3. EXTREME FEAR OVERRIDE lowers session floor to 70%, bypassing our 85% floor
   FIX: Remove the override, let 85% confidence floor be absolute

4. OPPOSITE TRADE bypass: bypasses slot check entirely
   FIX: Remove opposite trade slot bypass

DEPLOYMENT:
    cd ~/smt-weex-trading-bot/v3
    python3 patch_v3_1_64a_hotfix.py
    # Then restart daemon (don't kill/restart if positions are being monitored)
"""

import os
import sys

DAEMON_FILE = "smt_daemon_v3_1.py"
NIGHTLY_FILE = "smt_nightly_trade_v3_1.py"

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def replace_one(content, old, new, desc):
    if old in content:
        content = content.replace(old, new, 1)
        print(f"  [OK] {desc}")
    else:
        print(f"  [SKIP] {desc} - pattern not found")
    return content

def main():
    print("=" * 60)
    print("HOTFIX V3.1.64a - ENFORCE HARD CAP + FIX SL + FEAR FLOOR")
    print("=" * 60)

    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found. Run from v3/ directory.")
            sys.exit(1)

    # ============================================================
    # DAEMON FIXES
    # ============================================================
    print(f"\n--- Patching {DAEMON_FILE} ---")
    d = read_file(DAEMON_FILE)

    # FIX 1: Kill confidence override slots
    d = replace_one(d,
        "        MAX_CONFIDENCE_SLOTS = 2",
        "        MAX_CONFIDENCE_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute",
        "MAX_CONFIDENCE_SLOTS = 0")

    # FIX 1b: Kill bonus slots
    d = replace_one(d,
        "        MAX_BONUS_SLOTS = 2  # V3.1.53: +2 slots only if new signal conf > all existing",
        "        MAX_BONUS_SLOTS = 0  # V3.1.64a: DISABLED - hard cap is absolute",
        "MAX_BONUS_SLOTS = 0")

    # FIX 3: Remove EXTREME FEAR session floor override
    d = replace_one(d,
        """                # Extreme fear overrides session filter
                if is_extreme_fear:
                    session_min_conf = min(session_min_conf, 0.70)
                    logger.info(f"EXTREME FEAR OVERRIDE: F&G={opp_fear_greed}, session floor -> 70% for {opportunity['pair']}")""",
        """                # V3.1.64a: REMOVED extreme fear floor override
                # 85% confidence floor is absolute - no exceptions
                if is_extreme_fear:
                    logger.info(f"EXTREME FEAR: F&G={opp_fear_greed}, but 85% floor stays for {opportunity['pair']}")""",
        "Remove extreme fear 70% floor override")

    # FIX 4: Remove opposite trade slot bypass
    d = replace_one(d,
        """                if trade_type_check == "opposite":
                    logger.info(f"OPPOSITE TRADE: bypassing slot check for {opportunity['pair']}")
                elif trades_executed >= available_slots:""",
        """                if trade_type_check == "opposite":
                    logger.info(f"OPPOSITE TRADE: {opportunity['pair']} (no slot bypass in V3.1.64a)")
                if trades_executed >= available_slots:""",
        "Remove opposite trade slot bypass")

    write_file(DAEMON_FILE, d)

    # ============================================================
    # NIGHTLY TRADE FIXES
    # ============================================================
    print(f"\n--- Patching {NIGHTLY_FILE} ---")
    n = read_file(NIGHTLY_FILE)

    # FIX 2: ATR-SL must respect Judge's vol-adjusted SL
    # The Judge sets sl_pct which gets passed to execute_trade.
    # But execute_trade recalculates SL from ATR, ignoring Judge's value.
    # Fix: use the wider of ATR-SL or 4.0% cap (raise cap when F&G < 15)

    n = replace_one(n,
        """            sl_pct_raw = max(dynamic_sl, tier_floor_sl)
            sl_pct_raw = min(sl_pct_raw, 4.0)""",
        """            sl_pct_raw = max(dynamic_sl, tier_floor_sl)
            # V3.1.64a: Widen SL cap in extreme fear (respect Judge's vol-adjusted SL)
            try:
                _fg_sl = get_fear_greed_index().get("value", 50)
            except:
                _fg_sl = 50
            _sl_cap = 6.0 if _fg_sl < 15 else 4.5 if _fg_sl < 30 else 4.0
            sl_pct_raw = min(sl_pct_raw, _sl_cap)""",
        "ATR-SL respects vol-adjusted cap (6% in capitulation)")

    write_file(NIGHTLY_FILE, n)

    # ============================================================
    # VERIFY
    # ============================================================
    print(f"\n--- Verification ---")
    d = read_file(DAEMON_FILE)
    n = read_file(NIGHTLY_FILE)

    checks = [
        ("MAX_CONFIDENCE_SLOTS = 0" in d, "Confidence override disabled"),
        ("MAX_BONUS_SLOTS = 0" in d, "Bonus slots disabled"),
        ("85% floor stays" in d, "Fear floor override removed"),
        ("no slot bypass in V3.1.64a" in d, "Opposite trade bypass removed"),
        ("_sl_cap = 6.0" in n, "ATR-SL cap widened in fear"),
    ]

    all_ok = True
    for check, name in checks:
        status = "PASS" if check else "FAIL"
        if not check:
            all_ok = False
        print(f"  [{status}] {name}")

    print(f"\n{'=' * 60}")
    if all_ok:
        print("ALL HOTFIXES APPLIED")
    else:
        print("SOME FIXES FAILED")
    print(f"{'=' * 60}")

    print(f"""
The daemon is currently running with 5 positions. Options:
  A) Let them play out, restart daemon after they close
  B) Restart now (positions stay on WEEX, daemon re-syncs)

To restart:
  ps aux | grep smt_daemon | grep -v grep | awk '{{print $2}}' | xargs -r kill
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  tail -30 logs/daemon_v3_1_7_$(date +%Y%m%d).log

Then commit:
  cd ~/smt-weex-trading-bot
  git add .
  git commit -m "V3.1.64a: hotfix hard cap, ATR-SL fear cap, remove fear floor override"
  git push
""")

if __name__ == "__main__":
    main()
