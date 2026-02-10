#!/usr/bin/env python3
"""
V3.1.47 STOP THE BLEEDING
===========================
Usage:
  cd ~/smt-weex-trading-bot/v3
  python3 patch_v3_1_47.py

The PM is STILL closing positions at a loss despite V3.1.46.
Losses while sleeping: -$267 all from PM + hard stop.

Three killers identified:
1. PM Rule 4a: "peaked +0.5%, faded to 30% of peak" -> closed BTC -$6, LTC -$6, BTC -$41
2. PM Rule 6/9: "held 3-4h and negative, stale slot" -> closed SOL -$46, XRP -$32, BNB -$70, ETH -$16
3. Code HARD STOP: "LONG losing $50 in NEUTRAL" -> closed DOGE -$52

Fix:
1. Rewrite PM Rule 4 - NEVER close LONGs during extreme fear unless losing > SL%
2. Rewrite PM Rule 9 - Remove "slot efficiency" (it closes losing positions for "fresh" ones)
3. Raise hard_stop_threshold from 5% to 15% of margin (effectively disabled, SL handles it)
"""

import os, sys

V3_DIR = os.path.dirname(os.path.abspath(__file__))

def patch_daemon():
    filepath = os.path.join(V3_DIR, "smt_daemon_v3_1.py")
    with open(filepath, 'r') as f:
        content = f.read()
    
    changes = 0
    
    # =========================================================
    # FIX 1: PM Rule 4 - stop closing faded positions in extreme fear
    # =========================================================
    old_rule4 = """RULE 4 - F&G CONTRADICTION CHECK:
If F&G < 20 (extreme fear) but regime says BULLISH, the bounce may be fragile.
HOWEVER: extreme fear is ALSO when contrarian LONGs make the most money (violent bounces).
So do NOT close LONGs just because F&G is low. Only close LONGs in extreme fear IF:
  a) Position has peaked above +0.5% and faded below 30% of peak (Rule 2, but with 30% not 40% threshold), OR
  b) Position has been held > 4h (not 2h) and is flat or negative, OR
  c) There are 8+ LONGs open (only during extreme over-concentration)
Do NOT close a recently opened LONG (< 2h) that was entered during Extreme Fear (F&G < 20).
These are CAPITULATION REVERSAL entries - they need time to develop. The first 1-2 hours
are often choppy before the real bounce kicks in.
If F&G > 80 (extreme greed) and all positions are LONG, close the weakest 1-2."""

    new_rule4 = """RULE 4 - F&G CONTRADICTION CHECK (V3.1.47 RECOVERY):
If F&G < 20 (extreme fear), DO NOT CLOSE ANY POSITIONS unless they hit their SL order on WEEX.
Extreme fear creates the best entries. Every position we close at a loss during extreme fear
has historically bounced back within hours. Our SL orders are on the exchange - trust them.
The ONLY exception: If F&G > 80 (extreme greed) and all positions are LONG, close the weakest 1.
During extreme fear: NO closes. Period. Let SL handle risk."""

    if old_rule4 in content:
        content = content.replace(old_rule4, new_rule4, 1)
        print("  OK: PM Rule 4 rewritten (no closes during extreme fear)")
        changes += 1
    else:
        print("  WARNING: PM Rule 4 text not found exactly")
    
    # =========================================================
    # FIX 2: PM Rule 9 - remove slot efficiency (it kills losers for "fresh" trades)
    # =========================================================
    old_rule9 = """RULE 9 - SLOT EFFICIENCY:
Each slot costs opportunity. A position using $700 margin earning $5 is 0.7% return.
If that slot could be used for a fresh regime-aligned trade, close the stale one.
Prioritize closing positions that have been flat (< +/-0.3%) for > 1 hour."""

    new_rule9 = """RULE 9 - SLOT PATIENCE (V3.1.47 RECOVERY):
Do NOT close positions to free slots. Our SL orders protect against catastrophic loss.
A position at -0.3% after 1 hour can be at +3% after 4 hours. Crypto is volatile.
The ONLY reason to free a slot is if we have 8 positions AND a 90%+ conviction signal waiting.
Never close a position just because it is flat or slightly negative."""

    if old_rule9 in content:
        content = content.replace(old_rule9, new_rule9, 1)
        print("  OK: PM Rule 9 rewritten (no slot efficiency kills)")
        changes += 1
    else:
        print("  WARNING: PM Rule 9 text not found exactly")

    # =========================================================
    # FIX 3: Raise hard_stop_threshold so it effectively never fires
    # The SL order on WEEX at 2-2.5% handles this already
    # =========================================================
    old_hard = "            hard_stop_threshold = -(margin * 0.05)      # -5% of margin"
    new_hard = "            hard_stop_threshold = -(margin * 0.25)      # V3.1.47: Raised to 25% - let SL on WEEX handle it"

    if old_hard in content:
        content = content.replace(old_hard, new_hard, 1)
        print("  OK: Hard stop threshold raised (5% -> 25% of margin)")
        changes += 1
    else:
        print("  WARNING: hard_stop_threshold line not found")

    # =========================================================
    # FIX 4: Also raise regime_fight_threshold
    # Currently at 2% of margin - this closed positions too early
    # =========================================================
    old_regime = "            regime_fight_threshold = -(margin * 0.02)   # -2% of margin"
    new_regime = "            regime_fight_threshold = -(margin * 0.15)   # V3.1.47: Raised to 15% - trust SL"

    if old_regime in content:
        content = content.replace(old_regime, new_regime, 1)
        print("  OK: Regime fight threshold raised (2% -> 15% of margin)")
        changes += 1
    else:
        print("  WARNING: regime_fight_threshold line not found")

    # =========================================================
    # FIX 5: Strengthen the PM final instruction
    # =========================================================
    old_final = """Do NOT close any position that is currently profitable. Let TP orders handle exits.
Only close positions that are: (a) losing more than -2%, or (b) past max hold time and negative,
or (c) creating extreme directional concentration risk (8+ same direction)."""

    new_final = """CRITICAL V3.1.47 RULE: Do NOT close ANY position at a loss. We have SL orders on WEEX.
Every time the PM closes a losing position, we lock in a loss AND pay fees AND lose the bounce.
Our data shows: -$267 lost in 8 hours from PM closing losers that would have recovered.
The ONLY acceptable closes are:
(a) Position past max_hold_hours AND losing more than -3% = stale loser, SL probably broken
(b) 8+ positions in same direction creating liquidation cascade risk
(c) Winning positions that hit max_hold_hours (take the profit)
NEVER close: positions under 6 hours old, positions losing less than -3%, positions during F&G < 20."""

    if old_final in content:
        content = content.replace(old_final, new_final, 1)
        print("  OK: PM final instruction hardened")
        changes += 1
    else:
        print("  SKIP: PM final instruction text not found")

    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  DONE: smt_daemon_v3_1.py ({changes} changes)")
    return True


def main():
    print("=" * 60)
    print("SMT V3.1.47 - STOP THE BLEEDING")
    print("=" * 60)
    
    print()
    print("Patching smt_daemon_v3_1.py...")
    patch_daemon()
    
    print()
    print("Restart daemon:")
    print("  pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
    print("  tail -f daemon.log")
    print()
    print("Commit:")
    print("  git add . && git commit -m 'V3.1.47: Stop PM bleeding - no loss closes' && git push")


if __name__ == "__main__":
    main()
