#!/usr/bin/env python3
"""
Fix V3.1.46 - properly disable profit guards by commenting out the code.
"""
import os, sys

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smt_daemon_v3_1.py")
with open(filepath, 'r') as f:
    content = f.read()

# Replace the entire broken section (from the broken if False blocks through the tier guards)
old_block = """                if False:  # V3.1.46: ALL profit guards disabled
                    pass
                if False:  # V3.1.46: profit guard block disabled
                    # TIER 1: Move SL to breakeven at +0.8%, guard at peak-50% above +1.2%
                # TIER 2: Move SL to breakeven at +0.6%, guard at peak-50% above +1.0%
                # TIER 3: Move SL to breakeven at +0.5%, guard at peak-50% above +0.8%
                
                fade_pct = peak_pnl_pct - pnl_pct  # How much we gave back
                
                if tier == 3:
                    # Tier 3 (DOGE, XRP, ADA): They reverse FAST - tight guards
                    if peak_pnl_pct >= 0.8 * confidence_multiplier and pnl_pct < peak_pnl_pct * (0.40 * confidence_multiplier):
                        should_exit = True
                        exit_reason = f"T3_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%, gave back 60%+)"
                        state.early_exits += 1
                    elif peak_pnl_pct >= 0.5 * confidence_multiplier and pnl_pct <= 0.05:
                        should_exit = True
                        exit_reason = f"T3_breakeven_guard (peak: +{peak_pnl_pct:.1f}%, faded to breakeven)"
                        state.early_exits += 1
                elif tier == 2:
                    # Tier 2 (SOL): Medium volatility
                    if peak_pnl_pct >= 1.0 * confidence_multiplier and pnl_pct < peak_pnl_pct * (0.45 * confidence_multiplier):
                        should_exit = True
                        exit_reason = f"T2_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%, gave back 55%+)"
                        state.early_exits += 1
                    elif peak_pnl_pct >= 0.6 * confidence_multiplier and pnl_pct <= 0.05:
                        should_exit = True
                        exit_reason = f"T2_breakeven_guard (peak: +{peak_pnl_pct:.1f}%, faded to breakeven)"
                        state.early_exits += 1
                else:
                    # Tier 1 (BTC, ETH, BNB, LTC): Slower movers but still guard
                    if peak_pnl_pct >= 1.2 * confidence_multiplier and pnl_pct < peak_pnl_pct * (0.50 * confidence_multiplier):
                        should_exit = True
                        exit_reason = f"T1_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%, gave back 50%+)"
                        state.early_exits += 1
                    elif peak_pnl_pct >= 0.8 * confidence_multiplier and pnl_pct <= 0.05:
                        should_exit = True
                        exit_reason = f"T1_breakeven_guard (peak: +{peak_pnl_pct:.1f}%, faded to breakeven)"
                        state.early_exits += 1"""

new_block = """                # V3.1.46: ALL PROFIT GUARDS DISABLED - Recovery mode
                # We need wins of $100-300, not $15-50. Let TP orders handle exits.
                # fade_pct = peak_pnl_pct - pnl_pct
                # if tier == 3: T3_profit_guard ... DISABLED
                # elif tier == 2: T2_profit_guard ... DISABLED
                # else: T1_profit_guard ... DISABLED"""

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print("OK: All profit guards properly commented out")
else:
    print("ERROR: Could not find the exact block.")
    print("Showing lines 1138-1185 for debug:")
    lines = content.split('\n')
    for i in range(1137, min(1185, len(lines))):
        print(f"  {i+1}: {lines[i]}")
    sys.exit(1)

print("Restart: pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
