#!/usr/bin/env python3
"""
V3.1.46 RECOVERY PATCH - LET WINNERS RUN
==========================================
Usage:
  cd ~/smt-weex-trading-bot/v3
  python3 patch_v3_1_46.py

Problem: Our biggest win is $55 but biggest loss is $299.
- Profit guards close winners at +0.5% to +1.3% (capturing $5-$30)
- But SL hits cost $50-$299
- We need wins of $100-$300 to match losses
- Current TP: 4-6% but positions never reach it because guards close early

Fix:
1. DISABLE profit_guard and breakeven_guard (let TP orders do their job)
2. DISABLE time_fade_guard (stop cutting winners at 2h)
3. WIDEN TP to 6-8% (need bigger wins to recover)
4. PM: only close positions losing > -2% or extreme risk scenarios
5. Keep SL unchanged (safety net stays)
"""

import os
import sys

V3_DIR = os.path.dirname(os.path.abspath(__file__))


def patch_daemon():
    """Disable profit guards in smt_daemon_v3_1.py"""
    filepath = os.path.join(V3_DIR, "smt_daemon_v3_1.py")
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found")
        return False
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    changes = 0
    
    # =========================================================
    # FIX 1: Disable ALL profit guards and breakeven guards
    # Wrap the entire profit guard section in a False check
    # =========================================================
    
    # Find the profit guard section start
    old_guard_intro = """                # V3.1.41 PROGRESSIVE PROFIT GUARD - learned from commit history
                # Old thresholds (2.5-3.5% peak) NEVER triggered. Positions peak at 0.5-1.5% and fade.
                # New: Multi-tier progressive system that actually captures profits."""
    
    new_guard_intro = """                # V3.1.46: PROFIT GUARDS DISABLED - Recovery mode
                # Problem: Guards close at +0.5-1.3% (capturing $5-30) but losses hit $50-299
                # Solution: Let TP orders do their job. Need +5-8% wins to recover.
                # Guards were cutting winners before they could become big wins.
                if False:  # V3.1.46: ALL profit guards disabled"""
    
    if old_guard_intro in content:
        content = content.replace(old_guard_intro, new_guard_intro, 1)
        print("  OK: Profit guards disabled (wrapped in if False)")
        changes += 1
    else:
        print("  WARNING: Could not find profit guard intro text")
        # Try a more targeted approach - just disable the should_exit assignments
        # by adding a master switch
    
    # =========================================================
    # FIX 2: Disable time_fade_guard
    # =========================================================
    
    old_time_fade = """                # V3.1.41: TIME-BASED TIGHTENING
                # After 2h in profit that peaked > 0.5%, if now giving back > 60% of peak, exit
                if not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:
                    if pnl_pct < peak_pnl_pct * 0.35:
                        should_exit = True
                        exit_reason = f"time_fade_guard ({hours_open:.1f}h, peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1"""
    
    new_time_fade = """                # V3.1.46: TIME-BASED TIGHTENING DISABLED - Let winners run
                # Was closing positions that peaked at 0.5-1% after 2h. These need time to hit 5%+ TP.
                # if not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:
                #     if pnl_pct < peak_pnl_pct * 0.35:
                #         should_exit = True
                #         exit_reason = f"time_fade_guard ..."
                pass  # V3.1.46: Disabled"""
    
    if old_time_fade in content:
        content = content.replace(old_time_fade, new_time_fade, 1)
        print("  OK: time_fade_guard disabled")
        changes += 1
    else:
        print("  WARNING: Could not find time_fade_guard text exactly")
        # Try partial match
        if "time_fade_guard" in content and 'hours_open >= 2.0 and peak_pnl_pct >= 0.5' in content:
            content = content.replace(
                'if not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:',
                'if False and not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:  # V3.1.46: DISABLED',
                1
            )
            print("  OK: time_fade_guard disabled (partial match)")
            changes += 1
    
    # =========================================================
    # FIX 3: Update PM prompt - restrict to risk-only closes
    # =========================================================
    
    # Replace Rule 2 (FADING MOMENTUM) in PM prompt
    old_rule2 = """RULE 2 - FADING MOMENTUM (CRITICAL - our #1 profit leak):
If a position peaked above +0.5% but current PnL has dropped to less than 40% of peak,
it is FADING. Close it to lock remaining profit before it goes to zero.
Example: peaked +1.2%, now +0.3% = gave back 75% of gains = CLOSE.
Example: peaked +0.8%, now +0.5% = gave back 37% = KEEP (still holding well)."""
    
    new_rule2 = """RULE 2 - LET WINNERS RUN (V3.1.46 RECOVERY MODE):
Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours.
Our biggest problem is NOT fading profits -- it is that our biggest win ($55) is
5x smaller than our biggest loss ($299). We need $100-300 wins to recover."""
    
    if old_rule2 in content:
        content = content.replace(old_rule2, new_rule2, 1)
        print("  OK: PM Rule 2 updated (let winners run)")
        changes += 1
    else:
        print("  SKIP: PM Rule 2 text not found exactly")
    
    # Replace Rule 3 (BREAKEVEN FADE)
    old_rule3 = """RULE 3 - BREAKEVEN FADE:
If a position peaked above +0.5% but has faded back to ~0% or negative, CLOSE immediately.
This was profitable and you let it die. Take the lesson, free the slot."""
    
    new_rule3 = """RULE 3 - BREAKEVEN PATIENCE (V3.1.46 RECOVERY MODE):
If a position has faded to breakeven, DO NOT CLOSE. The SL order is our safety net.
Crypto is volatile - a position at 0% can rally to +5% in the next hour.
Only the SL should close losing/breakeven positions. Do NOT close manually."""
    
    if old_rule3 in content:
        content = content.replace(old_rule3, new_rule3, 1)
        print("  OK: PM Rule 3 updated (breakeven patience)")
        changes += 1
    else:
        print("  SKIP: PM Rule 3 text not found exactly")
    
    # Replace Rule 6 (TIME-BASED PROFIT TIGHTENING)
    old_rule6 = """RULE 6 - TIME-BASED PROFIT TIGHTENING:
After 2+ hours held, if position peaked > 0.5% but current < 35% of peak, close it.
The move has exhausted. After 4+ hours held, if still under +0.3%, close it."""
    
    new_rule6 = """RULE 6 - TIME-BASED PATIENCE (V3.1.46 RECOVERY MODE):
Do NOT close positions just because they have been open 2-4 hours.
Our TP targets are 5-8%. These moves take TIME to develop (4-12 hours for alts, 12-48h for BTC).
Only close if: max_hold_hours exceeded AND position is negative. Positive positions get extra time."""
    
    if old_rule6 in content:
        content = content.replace(old_rule6, new_rule6, 1)
        print("  OK: PM Rule 6 updated (time patience)")
        changes += 1
    else:
        print("  SKIP: PM Rule 6 text not found exactly")
    
    # Replace the PM "be aggressive about locking profits" instruction
    old_aggressive = """Apply ALL 10 rules above. For each position, check every rule. Be aggressive about
locking profits -- our biggest problem is positions that peak at +1% and then fade
to 0% or negative. Better to close at +0.3% than ride to -1%."""
    
    new_aggressive = """Apply ALL 10 rules above. For each position, check every rule. Be PATIENT with winners.
Our biggest problem is NOT fading profits -- it is that we close winners too early.
Our biggest win is $55 but biggest loss is $299. We need $100+ wins to recover.
Do NOT close any position that is currently profitable. Let TP orders handle exits.
Only close positions that are: (a) losing more than -2%, or (b) past max hold time and negative,
or (c) creating extreme directional concentration risk (8+ same direction)."""
    
    if old_aggressive in content:
        content = content.replace(old_aggressive, new_aggressive, 1)
        print("  OK: PM aggressiveness replaced with patience")
        changes += 1
    else:
        print("  SKIP: PM aggressive text not found exactly")
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  DONE: smt_daemon_v3_1.py ({changes} changes)")
    return True


def patch_nightly_trade():
    """Widen TP targets in smt_nightly_trade_v3_1.py"""
    filepath = os.path.join(V3_DIR, "smt_nightly_trade_v3_1.py")
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found")
        return False
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    changes = 0
    
    # =========================================================
    # Widen TP targets
    # =========================================================
    
    # Tier 1: 6% -> 8%
    old_t1 = '"tp_pct": 6.0,           # V3.1.42: Recovery - let winners run to 6%'
    new_t1 = '"tp_pct": 8.0,           # V3.1.46: Recovery - need big wins to catch up'
    if old_t1 in content:
        content = content.replace(old_t1, new_t1, 1)
        print("  OK: Tier 1 TP: 6% -> 8%")
        changes += 1
    else:
        print("  SKIP: Tier 1 TP text not found")
    
    # Tier 2: 5% -> 7%
    old_t2 = '"tp_pct": 5.0,           # V3.1.42: Recovery - 5% TP'
    new_t2 = '"tp_pct": 7.0,           # V3.1.46: Recovery - need big wins'
    if old_t2 in content:
        content = content.replace(old_t2, new_t2, 1)
        print("  OK: Tier 2 TP: 5% -> 7%")
        changes += 1
    else:
        print("  SKIP: Tier 2 TP text not found")
    
    # Tier 3: 4% -> 6%
    old_t3 = '"tp_pct": 4.0,           # V3.1.42: Recovery - 4% TP'
    new_t3 = '"tp_pct": 6.0,           # V3.1.46: Recovery - need big wins'
    if old_t3 in content:
        content = content.replace(old_t3, new_t3, 1)
        print("  OK: Tier 3 TP: 4% -> 6%")
        changes += 1
    else:
        print("  SKIP: Tier 3 TP text not found")
    
    # =========================================================
    # Update Judge prompt rule 13 to reflect new TP targets
    # =========================================================
    old_r13 = "13. For TP/SL: SL 2-2.5%. TP should be 2-3x SL (4-6%). Max hold: T1=48h, T2=24h, T3=12h. Let winners RUN."
    new_r13 = "13. For TP/SL: SL 2-2.5%. TP should be 3-4x SL (6-8%). Max hold: T1=48h, T2=24h, T3=12h. Let winners RUN to full TP. Do NOT suggest tight TP like 1.5-2%. We need BIG wins."
    if old_r13 in content:
        content = content.replace(old_r13, new_r13, 1)
        print("  OK: Judge Rule 13 updated (TP 6-8%)")
        changes += 1
    else:
        print("  SKIP: Judge Rule 13 text not found")
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  DONE: smt_nightly_trade_v3_1.py ({changes} changes)")
    return True


def main():
    print("=" * 60)
    print("SMT V3.1.46 RECOVERY PATCH - LET WINNERS RUN")
    print("=" * 60)
    print()
    print("Changes:")
    print("  1. DISABLE all profit_guard / breakeven_guard exits")
    print("  2. DISABLE time_fade_guard exits")
    print("  3. Widen TP: T1=8%, T2=7%, T3=6%")
    print("  4. PM: only close losers > -2%, never close winners")
    print("  5. SL unchanged (safety net stays)")
    print()
    
    print("[1/2] Patching smt_daemon_v3_1.py...")
    patch_daemon()
    
    print()
    print("[2/2] Patching smt_nightly_trade_v3_1.py...")
    patch_nightly_trade()
    
    print()
    print("=" * 60)
    print("PATCH COMPLETE")
    print("=" * 60)
    print()
    print("Restart daemon:")
    print("  pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
    print("  tail -f daemon.log")
    print()
    print("Then commit:")
    print("  git add . && git commit -m 'V3.1.46: Recovery - disable profit guards, widen TP 6-8%' && git push")


if __name__ == "__main__":
    main()
