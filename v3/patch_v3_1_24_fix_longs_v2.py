#!/usr/bin/env python3
"""
SMT V3.1.24 PATCH - STOP LOSING LONGS (CORRECTED)
==================================================
Run: python3 patch_v3_1_24_fix_longs_v2.py

Based on actual code structure found via grep.
"""

import os
import sys
import shutil
from datetime import datetime

TARGET_FILE = "smt_nightly_trade_v3_1.py"
BACKUP_SUFFIX = f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def patch_version(content):
    """Patch 1: Version header"""
    for old in ['V3.1.23 - RAPID REGIME DETECTION', 'V3.1.21 - BEAR HUNTER']:
        if old in content:
            content = content.replace(old, 'V3.1.24 - STOP LOSING LONGS')
            print("[OK] Patch 1: Version -> V3.1.24")
            return content, True
    if 'V3.1.24' in content[:600]:
        print("[SKIP] Patch 1: Already V3.1.24")
        return content, False
    print("[SKIP] Patch 1: Version not found")
    return content, False

def patch_long_block_threshold(content):
    """Patch 2: Block LONGs when BTC < 0 (was < -0.5)"""
    old = 'if regime.get("btc_24h", 0) < -0.5:'
    new = 'if regime.get("btc_24h", 0) < 0:  # V3.1.24: Any negative blocks LONG'
    
    # Only replace in the LONG blocking context (around line 2021)
    if old in content:
        # Find and replace only the second occurrence (first is regime scoring)
        first_idx = content.find(old)
        second_idx = content.find(old, first_idx + 1)
        if second_idx > 0:
            content = content[:second_idx] + new + content[second_idx + len(old):]
            print("[OK] Patch 2: LONG block BTC < -0.5% -> < 0%")
            return content, True
    print("[SKIP] Patch 2: Pattern not found or already patched")
    return content, False

def patch_bullish_weights(content):
    """Patch 3: Reduce FLOW weight in BULLISH, increase SENTIMENT"""
    old_weights = '''            if is_bullish:
                weights = {
                    "WHALE": 2.5,      # V3.1.21: Whales lead the way up - TRUST THEM
                    "SENTIMENT": 2.0 if signal == "LONG" else 0.8,  # V3.1.21: Trust LONG sentiment (symmetric)
                    "FLOW": 2.0,       # V3.1.21: Trust buying pressure in bull
                    "TECHNICAL": 1.5   # V3.1.21: Trust momentum
                }'''
    
    new_weights = '''            if is_bullish:
                weights = {
                    "WHALE": 2.5,      # Whales lead the way
                    "SENTIMENT": 2.0,  # V3.1.24: Trust sentiment equally for LONG/SHORT
                    "FLOW": 1.2,       # V3.1.24: REDUCED from 2.0 - taker spikes often fake
                    "TECHNICAL": 1.5   # Trust momentum
                }'''
    
    if old_weights in content:
        content = content.replace(old_weights, new_weights)
        print("[OK] Patch 3: BULLISH weights - FLOW 2.0->1.2")
        return content, True
    elif 'FLOW": 1.2' in content:
        print("[SKIP] Patch 3: Already patched")
        return content, False
    print("[WARN] Patch 3: BULLISH weights not found")
    return content, False

def patch_neutral_weights(content):
    """Patch 4: Reduce FLOW weight in NEUTRAL"""
    old_neutral = '''            else:  # NEUTRAL
                weights = {
                    "WHALE": 1.5,      # Slightly reduced
                    "SENTIMENT": 1.0,  # Reduced - noise in chop
                    "FLOW": 1.5,       # Increased - flow matters in chop
                    "TECHNICAL": 1.5   # Increased - technicals guide in ranges
                }'''
    
    new_neutral = '''            else:  # NEUTRAL
                weights = {
                    "WHALE": 1.5,      # Slightly reduced
                    "SENTIMENT": 1.2,  # V3.1.24: Increased - sentiment has been right
                    "FLOW": 1.0,       # V3.1.24: REDUCED from 1.5 - don't trust in chop
                    "TECHNICAL": 1.5   # Technicals guide in ranges
                }'''
    
    if old_neutral in content:
        content = content.replace(old_neutral, new_neutral)
        print("[OK] Patch 4: NEUTRAL weights - FLOW 1.5->1.0, SENTIMENT 1.0->1.2")
        return content, True
    print("[SKIP] Patch 4: NEUTRAL weights not found")
    return content, False

def patch_bullish_long_confidence(content):
    """Patch 5: Raise BULLISH LONG threshold from 50% to 70%"""
    old = 'min_confidence = 0.50  # V3.1.21: SYMMETRIC - 50% for LONGs in BULLISH (like SHORTs in BEARISH)'
    new = 'min_confidence = 0.70  # V3.1.24: Raised from 50% - stop bad LONGs'
    
    if old in content:
        content = content.replace(old, new)
        print("[OK] Patch 5: BULLISH LONG confidence 50% -> 70%")
        return content, True
    elif 'min_confidence = 0.70  # V3.1.24' in content:
        print("[SKIP] Patch 5: Already patched")
        return content, False
    print("[SKIP] Patch 5: Pattern not found")
    return content, False

def patch_block_long_neutral(content):
    """Patch 6: Block LONGs in NEUTRAL regime too"""
    old = '''            # 1. Block LONGs in BEARISH regime
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)'''
    
    new = '''            # 1. V3.1.24: Block LONGs in BEARISH or NEUTRAL regime
            if regime["regime"] in ("BEARISH", "NEUTRAL"):
                return self._wait_decision(f"BLOCKED: LONG in {regime['regime']} regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)'''
    
    if old in content:
        content = content.replace(old, new)
        print("[OK] Patch 6: Block LONGs in NEUTRAL too")
        return content, True
    elif 'BEARISH or NEUTRAL' in content:
        print("[SKIP] Patch 6: Already patched")
        return content, False
    print("[SKIP] Patch 6: Pattern not found")
    return content, False

def patch_long_votes_requirement(content):
    """Patch 7: Require 2+ personas for LONG"""
    # Insert check right after "if decision == LONG:"
    old = '''        if decision == "LONG":
            # V3.1.24: Require at least 2 personas'''
    
    # Check if already patched
    if 'LONG needs 2+ personas' in content:
        print("[SKIP] Patch 7: Already patched")
        return content, False
    
    # Find where to insert
    marker = '''        if decision == "LONG":
            if regime["regime"]'''
    
    new_insert = '''        if decision == "LONG":
            # V3.1.24: Require at least 2 personas agreeing on LONG
            if long_votes < 2:
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)
            
            if regime["regime"]'''
    
    if marker in content:
        content = content.replace(marker, new_insert)
        print("[OK] Patch 7: LONG requires 2+ personas")
        return content, True
    
    # Try alternate marker
    marker2 = '''        if decision == "LONG":
            # V3.1.24: Block LONGs'''
    
    if marker2 in content:
        new_insert2 = '''        if decision == "LONG":
            # V3.1.24: Require at least 2 personas agreeing on LONG
            if long_votes < 2:
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)
            
            # V3.1.24: Block LONGs'''
        content = content.replace(marker2, new_insert2)
        print("[OK] Patch 7: LONG requires 2+ personas (alt)")
        return content, True
    
    print("[WARN] Patch 7: Could not find insertion point")
    return content, False

def main():
    print("=" * 60)
    print("SMT V3.1.24 PATCH - STOP LOSING LONGS (v2)")
    print("=" * 60)
    print()
    
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] {TARGET_FILE} not found")
        sys.exit(1)
    
    backup_file = TARGET_FILE + BACKUP_SUFFIX
    shutil.copy(TARGET_FILE, backup_file)
    print(f"[BACKUP] {backup_file}")
    print()
    
    content = read_file(TARGET_FILE)
    patches_applied = 0
    
    content, ok = patch_version(content)
    if ok: patches_applied += 1
    
    content, ok = patch_long_block_threshold(content)
    if ok: patches_applied += 1
    
    content, ok = patch_bullish_weights(content)
    if ok: patches_applied += 1
    
    content, ok = patch_neutral_weights(content)
    if ok: patches_applied += 1
    
    content, ok = patch_bullish_long_confidence(content)
    if ok: patches_applied += 1
    
    content, ok = patch_block_long_neutral(content)
    if ok: patches_applied += 1
    
    content, ok = patch_long_votes_requirement(content)
    if ok: patches_applied += 1
    
    print()
    print("-" * 60)
    
    if patches_applied > 0:
        write_file(TARGET_FILE, content)
        print(f"[DONE] Applied {patches_applied}/7 patches")
        print()
        print("WHAT CHANGED:")
        print("  - LONGs blocked when BTC 24h < 0% (was -0.5%)")
        print("  - LONGs blocked in NEUTRAL regime (was only BEARISH)")
        print("  - FLOW weight reduced: 2.0->1.2 (BULLISH), 1.5->1.0 (NEUTRAL)")
        print("  - SENTIMENT weight increased in NEUTRAL: 1.0->1.2")
        print("  - BULLISH LONG threshold: 50% -> 70%")
        print("  - LONGs require 2+ personas agreeing")
        print()
        print("SHORTs UNCHANGED - still work as before")
        print()
        print("RESTART:")
        print("  pkill -9 -f smt_daemon")
        print("  nohup python3 smt_daemon_v3_1.py > nohup.out 2>&1 &")
    else:
        print("[INFO] No patches applied")
        os.remove(backup_file)

if __name__ == "__main__":
    main()
