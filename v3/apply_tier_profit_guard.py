#!/usr/bin/env python3
"""
Tier-Aware Profit Guard Patch for smt_daemon_v3_1.py
Tighter trailing stops for volatile Tier 3 assets.
"""
import os

target_file = "smt_daemon_v3_1.py"

if not os.path.exists(target_file):
    print(f"Error: {target_file} not found.")
    exit(1)

with open(target_file, 'r') as f:
    content = f.read()

# Old single threshold (too loose for Tier 3)
old_code = '''                # V3.1.20 PREDATOR: Trailing protection locks minimum profit
                # Only exit if peak was +3.5% or more AND now below +1.5%
                # This guarantees profit while allowing trades to breathe
                if peak_pnl_pct >= 3.5 and pnl_pct < 1.5:
                    should_exit = True
                    exit_reason = f"trailing_protection (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                    state.early_exits += 1'''

# New tier-aware thresholds
new_code = '''                # V3.1.22 TIER-AWARE PROFIT GUARD
                # Tier 3 (DOGE, XRP, ADA): Tighter - they reverse fast
                # Tier 1/2 (BTC, ETH, SOL): More room to breathe
                if tier == 3:
                    # Tier 3: Exit if peak >= 2% and drops to 0.5%
                    if peak_pnl_pct >= 2.0 and pnl_pct < 0.5:
                        should_exit = True
                        exit_reason = f"T3_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1
                elif tier == 2:
                    # Tier 2: Exit if peak >= 2.5% and drops to 1.0%
                    if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:
                        should_exit = True
                        exit_reason = f"T2_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1
                else:
                    # Tier 1: Exit if peak >= 3.0% and drops to 1.5%
                    if peak_pnl_pct >= 3.0 and pnl_pct < 1.5:
                        should_exit = True
                        exit_reason = f"T1_profit_guard (peak: +{peak_pnl_pct:.1f}%, now: {pnl_pct:.1f}%)"
                        state.early_exits += 1'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(target_file, 'w') as f:
        f.write(content)
    print("OK: Tier-aware profit guard applied")
    print("")
    print("New thresholds:")
    print("  Tier 1 (BTC,ETH,BNB,LTC): peak >= 3.0%, exit at < 1.5%")
    print("  Tier 2 (SOL):             peak >= 2.5%, exit at < 1.0%")
    print("  Tier 3 (DOGE,XRP,ADA):    peak >= 2.0%, exit at < 0.5%")
else:
    print("ERROR: Could not find the old profit guard code.")
    print("The file may have different formatting or already be patched.")
    print("")
    print("Manual fix: Find line 673 and replace the single threshold with tier-aware logic.")
