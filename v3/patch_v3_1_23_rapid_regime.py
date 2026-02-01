#!/usr/bin/env python3
"""
SMT V3.1.23 PATCH - RAPID REGIME DETECTION
==========================================
Run: python3 patch_v3_1_23_rapid_regime.py

This patches smt_nightly_trade_v3_1.py to fix slow regime switching.

WHAT IS HYSTERESIS?
-------------------
Hysteresis = "stickiness" to prevent flip-flopping.
Like a thermostat with a dead band - once ON, stays ON until temp drops
below a threshold (not just crosses the setpoint).

YOUR PROBLEM:
- Bot was locked to BULLISH at 17:30
- Opened LONGs (ADA, XRP, BNB) 
- Market was already turning BEARISH
- 30 min lock + 5 min cache = ~40 min delay to detect switch
- Result: -$450 in losses on wrong-side trades

THE FIX:
- Regime cache: 5 min -> 2 min
- Hysteresis lock: 30 min -> 10 min  
- Add MOMENTUM OVERRIDE: BTC 4h > 1.5% = instant regime switch
- Strong signals (score >= 2) = instant switch
"""

import os
import sys
import re
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

def patch_regime_cache(content):
    """Patch 1: Reduce regime cache from 5 min to 2 min"""
    old = 'cached = REGIME_CACHE.get("regime", 300)  # 5 min cache'
    new = 'cached = REGIME_CACHE.get("regime", 120)  # V3.1.23: 2 min cache for faster reaction'
    
    if old not in content:
        # Try without comment
        old = 'cached = REGIME_CACHE.get("regime", 300)'
        new = 'cached = REGIME_CACHE.get("regime", 120)  # V3.1.23: 2 min cache'
    
    if old in content:
        content = content.replace(old, new)
        print("[OK] Patch 1: Regime cache 5min -> 2min")
        return content, True
    else:
        print("[SKIP] Patch 1: Regime cache line not found (may already be patched)")
        return content, False

def patch_hysteresis_function(content):
    """Patch 2: Replace entire apply_regime_hysteresis function"""
    
    # Pattern to find the old function
    old_func_pattern = r'def apply_regime_hysteresis\(score: int, raw_regime: str\) -> str:.*?(?=\n\nSENTIMENT_CACHE_TTL|\ndef [a-z_]+\()'
    
    new_func = '''def apply_regime_hysteresis(score: int, raw_regime: str, btc_4h_change: float = 0) -> str:
    """
    V3.1.23: RAPID REGIME DETECTION
    
    Changes:
    1. Lock reduced from 30 min to 10 min
    2. MOMENTUM OVERRIDE: If BTC 4h change > 1.5%, IMMEDIATE switch (bypass hysteresis)
    3. Strong signals (score >= 2 or <= -2) switch immediately
    """
    current = REGIME_STATE.get("current_regime", "NEUTRAL")
    history = REGIME_STATE.get("regime_score_history", [])
    history.append(score)
    if len(history) > 3: history = history[-3:]
    REGIME_STATE["regime_score_history"] = history
    now = time.time()
    
    # V3.1.23: MOMENTUM OVERRIDE - bypass hysteresis for strong 4h moves
    if btc_4h_change < -1.5 and current != "BEARISH":
        REGIME_STATE["current_regime"] = "BEARISH"
        REGIME_STATE["regime_locked_until"] = now + 600  # 10 min lock
        print(f"  [HYSTERESIS] MOMENTUM OVERRIDE: -> BEARISH (4h: {btc_4h_change:+.1f}%)")
        return "BEARISH"
    
    if btc_4h_change > 1.5 and current != "BULLISH":
        REGIME_STATE["current_regime"] = "BULLISH"
        REGIME_STATE["regime_locked_until"] = now + 600  # 10 min lock
        print(f"  [HYSTERESIS] MOMENTUM OVERRIDE: -> BULLISH (4h: {btc_4h_change:+.1f}%)")
        return "BULLISH"
    
    # V3.1.23: Reduced lock from 30 min to 10 min
    if REGIME_STATE.get("regime_locked_until", 0) > now:
        remaining = (REGIME_STATE["regime_locked_until"] - now) / 60
        print(f"  [HYSTERESIS] Locked to {current} for {remaining:.0f}m")
        return current
    
    # V3.1.23: Strong signals (score >= 2 or <= -2) switch immediately
    if score <= -2 and current != "BEARISH":
        REGIME_STATE["current_regime"] = "BEARISH"
        REGIME_STATE["regime_locked_until"] = now + 600
        print(f"  [HYSTERESIS] STRONG BEARISH (score: {score}) -> BEARISH")
        return "BEARISH"
    
    if score >= 2 and current != "BULLISH":
        REGIME_STATE["current_regime"] = "BULLISH"
        REGIME_STATE["regime_locked_until"] = now + 600
        print(f"  [HYSTERESIS] STRONG BULLISH (score: {score}) -> BULLISH")
        return "BULLISH"
    
    # Normal hysteresis for weaker signals
    if len(history) >= 2:
        avg = sum(history[-2:]) / 2
        if current == "BEARISH" and avg >= 1:
            new_r = "NEUTRAL" if avg < 2 else "BULLISH"
            REGIME_STATE["current_regime"] = new_r
            REGIME_STATE["regime_locked_until"] = now + 600  # V3.1.23: 10 min
            print(f"  [HYSTERESIS] BEARISH -> {new_r}")
            return new_r
        if current == "BULLISH" and avg <= -1:
            new_r = "NEUTRAL" if avg > -2 else "BEARISH"
            REGIME_STATE["current_regime"] = new_r
            REGIME_STATE["regime_locked_until"] = now + 600  # V3.1.23: 10 min
            print(f"  [HYSTERESIS] BULLISH -> {new_r}")
            return new_r
        if current == "NEUTRAL":
            if all(s <= -1 for s in history[-2:]):
                REGIME_STATE["current_regime"] = "BEARISH"
                REGIME_STATE["regime_locked_until"] = now + 600
                return "BEARISH"
            if all(s >= 1 for s in history[-2:]):
                REGIME_STATE["current_regime"] = "BULLISH"
                REGIME_STATE["regime_locked_until"] = now + 600
                return "BULLISH"
        return current
    REGIME_STATE["current_regime"] = raw_regime
    return raw_regime


'''
    
    match = re.search(old_func_pattern, content, re.DOTALL)
    if match:
        content = content[:match.start()] + new_func + content[match.end():]
        print("[OK] Patch 2: Hysteresis function replaced with rapid version")
        return content, True
    else:
        print("[SKIP] Patch 2: Hysteresis function not found (may already be patched)")
        return content, False

def patch_final_regime_assignment(content):
    """Patch 3: Update final regime assignment to use hysteresis with momentum"""
    
    old_block = '''    # ===== Final Regime =====
    result["score"] = score
    result["factors"] = factors
    
    if score <= -3: result["regime"] = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: result["regime"] = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: result["regime"] = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: result["regime"] = "BULLISH"; result["confidence"] = 0.65
    else: result["regime"] = "NEUTRAL"; result["confidence"] = 0.5
    
    print(f"  [REGIME] {result[\\'regime\\']} (score: {score}, conf: {result[\\'confidence\\']:.0%})")'''
    
    new_block = '''    # ===== Final Regime =====
    result["score"] = score
    result["factors"] = factors
    
    # V3.1.23: Determine raw regime from score
    if score <= -3: raw_regime = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: raw_regime = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: raw_regime = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: raw_regime = "BULLISH"; result["confidence"] = 0.65
    else: raw_regime = "NEUTRAL"; result["confidence"] = 0.5
    
    # V3.1.23: Apply hysteresis with momentum override (pass btc_4h for fast switching)
    result["regime"] = apply_regime_hysteresis(score, raw_regime, result.get("btc_4h", 0))
    
    print(f"  [REGIME] {result['regime']} (score: {score}, conf: {result['confidence']:.0%})")'''
    
    # Try to find and replace with actual content
    # The issue is the f-string quotes - let's use a regex approach
    pattern = r'(    # ===== Final Regime =====\n    result\["score"\] = score\n    result\["factors"\] = factors\n\n    )if score <= -3: result\["regime"\] = "BEARISH".*?print\(f"  \[REGIME\] \{result\[\'regime\'\]\}'
    
    # Simpler approach - find the marker and replace block
    marker = '# ===== Final Regime ====='
    if marker in content:
        # Find start of block
        start_idx = content.find(marker)
        # Find the print statement that ends this block
        search_area = content[start_idx:start_idx+1500]
        
        # Find where the regime assignment ends (before the BTC 24h print)
        end_marker = 'print(f"  [REGIME] BTC 24h:'
        end_offset = search_area.find(end_marker)
        
        if end_offset > 0:
            # Extract and replace just the regime assignment part
            old_section = content[start_idx:start_idx+end_offset]
            
            new_section = '''# ===== Final Regime =====
    result["score"] = score
    result["factors"] = factors
    
    # V3.1.23: Determine raw regime from score
    if score <= -3: raw_regime = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: raw_regime = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: raw_regime = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: raw_regime = "BULLISH"; result["confidence"] = 0.65
    else: raw_regime = "NEUTRAL"; result["confidence"] = 0.5
    
    # V3.1.23: Apply hysteresis with momentum override (pass btc_4h for fast switching)
    result["regime"] = apply_regime_hysteresis(score, raw_regime, result.get("btc_4h", 0))
    
    print(f"  [REGIME] {{result['regime']}} (score: {{score}}, conf: {{result['confidence']:.0%}})")
    '''
            
            # Check if already patched
            if 'raw_regime = "BEARISH"' in old_section:
                print("[SKIP] Patch 3: Final regime assignment already patched")
                return content, False
            
            content = content[:start_idx] + new_section + content[start_idx+end_offset:]
            print("[OK] Patch 3: Final regime assignment updated with hysteresis call")
            return content, True
    
    print("[SKIP] Patch 3: Final regime block not found")
    return content, False

def patch_version_header(content):
    """Patch 4: Update version in header"""
    old = 'SMT Nightly Trade V3.1.21 - BEAR HUNTER + BULL SYMMETRY MODE'
    new = 'SMT Nightly Trade V3.1.23 - RAPID REGIME DETECTION'
    
    if old in content:
        content = content.replace(old, new)
        print("[OK] Patch 4: Version header updated to V3.1.23")
        return content, True
    elif 'V3.1.23' in content[:500]:
        print("[SKIP] Patch 4: Already at V3.1.23")
        return content, False
    else:
        print("[SKIP] Patch 4: Version header not found")
        return content, False

def main():
    print("=" * 60)
    print("SMT V3.1.23 PATCH - RAPID REGIME DETECTION")
    print("=" * 60)
    print()
    
    # Check file exists
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] {TARGET_FILE} not found in current directory")
        print(f"        Current dir: {os.getcwd()}")
        print(f"        Run this from: ~/smt-weex-trading-bot")
        sys.exit(1)
    
    # Create backup
    backup_file = TARGET_FILE + BACKUP_SUFFIX
    shutil.copy(TARGET_FILE, backup_file)
    print(f"[BACKUP] Created: {backup_file}")
    print()
    
    # Read content
    content = read_file(TARGET_FILE)
    original_len = len(content)
    
    patches_applied = 0
    
    # Apply patches
    content, applied = patch_regime_cache(content)
    if applied: patches_applied += 1
    
    content, applied = patch_hysteresis_function(content)
    if applied: patches_applied += 1
    
    content, applied = patch_final_regime_assignment(content)
    if applied: patches_applied += 1
    
    content, applied = patch_version_header(content)
    if applied: patches_applied += 1
    
    print()
    print("-" * 60)
    
    if patches_applied > 0:
        write_file(TARGET_FILE, content)
        print(f"[DONE] Applied {patches_applied} patches")
        print(f"       File size: {original_len} -> {len(content)} bytes")
        print()
        print("NEXT STEPS:")
        print("  1. Restart daemon: pkill -f smt_daemon && python3 smt_daemon_v3_1.py &")
        print("  2. Monitor logs: tail -f nohup.out | grep -E 'HYSTERESIS|MOMENTUM'")
        print("  3. If working, commit: git add . && git commit -m 'V3.1.23 rapid regime' && git push")
    else:
        print("[INFO] No patches needed - file may already be patched")
        # Remove unnecessary backup
        os.remove(backup_file)
        print(f"[CLEANUP] Removed backup (no changes)")
    
    print()

if __name__ == "__main__":
    main()
