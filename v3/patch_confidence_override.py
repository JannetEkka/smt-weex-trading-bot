#!/usr/bin/env python3
"""
SMT Daemon Patch - V3.1.26 (Confidence Override Slots)
======================================================
High confidence signals (85%+) can open positions beyond the 3-slot limit.

- BASE_SLOTS = 3 (normal)
- CONFIDENCE_OVERRIDE_THRESHOLD = 85%
- MAX_CONFIDENCE_SLOTS = 2 (extra slots for 85%+ signals)

So max positions = 5 if you have multiple 85%+ signals.

Usage:
    cd ~/smt-weex-trading-bot/v3
    python3 patch_confidence_override.py
"""

import os
import sys

DAEMON_FILE = "smt_daemon_v3_1.py"

PATCHES = [
    # Patch 1: Update version
    {
        "name": "Version update to V3.1.26",
        "old": 'SMT Trading Daemon V3.1.25',
        "new": 'SMT Trading Daemon V3.1.26'
    },
    
    # Patch 1b: Also try V3.1.24 if V3.1.25 not found
    {
        "name": "Version update (from V3.1.24)",
        "old": 'SMT Trading Daemon V3.1.24',
        "new": 'SMT Trading Daemon V3.1.26'
    },
    
    # Patch 2: Add confidence override constants after BASE_SLOTS
    {
        "name": "Add confidence override constants",
        "old": '''        BASE_SLOTS = 3  # V3.1.22 CAPITAL PROTECTION
        MAX_BONUS_SLOTS = 2  # Can earn up to 2 extra slots from risk-free positions
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots''',
        "new": '''        BASE_SLOTS = 3  # V3.1.22 CAPITAL PROTECTION
        MAX_BONUS_SLOTS = 2  # Can earn up to 2 extra slots from risk-free positions
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.26: High confidence override
        CONFIDENCE_OVERRIDE_THRESHOLD = 0.85  # 85%+ signals can exceed normal limits
        MAX_CONFIDENCE_SLOTS = 2  # Up to 2 extra slots for high conviction trades'''
    },
    
    # Patch 3: Modify trade execution to allow confidence override
    {
        "name": "Add confidence override in trade execution",
        "old": '''# Execute ALL qualifying trades (up to available slots)
        if trade_opportunities:
            # Sort by confidence (highest first)
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            
            # V3.1.19: Use smart slot calculation (same as above)
            current_positions = len(open_positions)
            available_slots = effective_max_positions - current_positions
            
            trades_executed = 0
            
            for opportunity in trade_opportunities:
                # Check if we still have slots
                if trades_executed >= available_slots:
                    logger.info(f"Max positions reached, skipping remaining opportunities")
                    break''',
        "new": '''# Execute ALL qualifying trades (up to available slots)
        if trade_opportunities:
            # Sort by confidence (highest first)
            trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
            
            # V3.1.19: Use smart slot calculation (same as above)
            current_positions = len(open_positions)
            available_slots = effective_max_positions - current_positions
            
            # V3.1.26: Track confidence override slots used
            confidence_slots_used = 0
            
            trades_executed = 0
            
            for opportunity in trade_opportunities:
                confidence = opportunity["decision"]["confidence"]
                
                # Check if we still have slots
                if trades_executed >= available_slots:
                    # V3.1.26: High confidence override - can use extra slots
                    if confidence >= CONFIDENCE_OVERRIDE_THRESHOLD and confidence_slots_used < MAX_CONFIDENCE_SLOTS:
                        logger.info(f"CONFIDENCE OVERRIDE: {confidence:.0%} >= 85% - using conviction slot {confidence_slots_used + 1}/{MAX_CONFIDENCE_SLOTS}")
                        confidence_slots_used += 1
                    else:
                        logger.info(f"Max positions reached, skipping remaining opportunities")
                        break'''
    },
]


def main():
    print("=" * 60)
    print("SMT Daemon Patch - V3.1.26 (Confidence Override Slots)")
    print("=" * 60)
    print("85%+ confidence signals can open beyond 3-slot limit")
    print("=" * 60)
    
    if not os.path.exists(DAEMON_FILE):
        print(f"ERROR: {DAEMON_FILE} not found")
        print("Run from ~/smt-weex-trading-bot/v3/")
        sys.exit(1)
    
    # Read file
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    # Backup
    backup = DAEMON_FILE + ".bak_v3125"
    with open(backup, "w") as f:
        f.write(content)
    print(f"Backup: {backup}")
    
    # Apply patches
    applied = 0
    for p in PATCHES:
        if p["old"] in content:
            content = content.replace(p["old"], p["new"], 1)
            print(f"[OK] {p['name']}")
            applied += 1
        else:
            print(f"[SKIP] {p['name']}")
    
    # Save
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    # Verify syntax
    print("\nVerifying syntax...")
    if os.system(f"python3 -m py_compile {DAEMON_FILE}") == 0:
        print("Syntax OK")
    else:
        print("SYNTAX ERROR - restoring backup")
        os.system(f"cp {backup} {DAEMON_FILE}")
        sys.exit(1)
    
    print(f"\nDone: {applied} patches applied")
    print("\n" + "=" * 60)
    print("NEW BEHAVIOR:")
    print("  - Normal signals (70-84%): max 3 positions")
    print("  - High conviction (85%+): can open up to 5 positions")
    print("  - Sorted by confidence, so best signals get slots first")
    print("=" * 60)
    print("\nRestart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
