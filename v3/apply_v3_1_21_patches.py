#!/usr/bin/env python3
"""SMT V3.1.21 Auto-Patcher - Applies Bear Hunter upgrades"""
import os, re, shutil
from datetime import datetime

def apply_patches():
    source_file = "smt_nightly_trade_v3_1.py"
    if not os.path.exists(source_file):
        print(f"ERROR: {source_file} not found")
        return False
    
    backup = f"smt_nightly_trade_v3_1.py.backup_v320_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy(source_file, backup)
    print(f"Backup: {backup}")
    
    with open(source_file, 'r') as f:
        content = f.read()
    
    changes = []
    
    # Patch 1: Add deque import
    if "from collections import deque" not in content:
        content = content.replace(
            "from typing import Dict, List, Optional, Tuple",
            "from typing import Dict, List, Optional, Tuple\nfrom collections import deque"
        )
        changes.append("Added deque import")
    
    # Patch 2: Add hot-reload import
    if "HOT_RELOAD_ENABLED" not in content:
        hot_reload_import = '''
# V3.1.21: Hot-reload settings
try:
    from hot_reload import get_confidence_threshold, should_pause, should_emergency_exit, is_direction_enabled, get_tp_sl_multipliers
    HOT_RELOAD_ENABLED = True
    print("  [V3.1.21] Hot-reload enabled")
except ImportError:
    HOT_RELOAD_ENABLED = False
'''
        content = content.replace(
            "from collections import deque",
            "from collections import deque" + hot_reload_import
        )
        changes.append("Added hot-reload import")
    
    # Patch 3: Add whale flow history after CHAIN_ID
    if "WHALE_FLOW_HISTORY" not in content:
        whale_history = '''

# V3.1.21: Whale flow history for divergence detection
WHALE_FLOW_HISTORY = deque(maxlen=6)
WHALE_FLOW_HISTORY_FILE = "whale_flow_history.json"

def load_whale_flow_history():
    global WHALE_FLOW_HISTORY
    try:
        if os.path.exists(WHALE_FLOW_HISTORY_FILE):
            with open(WHALE_FLOW_HISTORY_FILE, 'r') as f:
                WHALE_FLOW_HISTORY = deque(json.load(f), maxlen=6)
                print(f"  [WHALE] Loaded {len(WHALE_FLOW_HISTORY)} flow samples")
    except: pass

def save_whale_flow_history():
    try:
        with open(WHALE_FLOW_HISTORY_FILE, 'w') as f:
            json.dump(list(WHALE_FLOW_HISTORY), f)
    except: pass

load_whale_flow_history()
'''
        content = content.replace("CHAIN_ID = 1", "CHAIN_ID = 1" + whale_history)
        changes.append("Added whale flow history")
    
    # Patch 4: Add regime shift detection before get_enhanced_market_regime
    if "def detect_regime_shift" not in content:
        regime_shift = '''
def detect_regime_shift() -> dict:
    """V3.1.21: Detect 4h trend flips for early entry"""
    result = {"shift_detected": False, "shift_type": "NONE", "confidence_adjustment": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=3"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 3:
            curr = float(candles[0][4])
            prev = float(candles[1][4])
            prev2 = float(candles[2][4])
            prev_4h = ((prev - prev2) / prev2) * 100
            curr_4h = ((curr - prev) / prev) * 100
            if prev_4h > 0.5 and curr_4h < -0.3:
                result = {"shift_detected": True, "shift_type": "BEARISH_SHIFT", "confidence_adjustment": -15}
                print(f"  [REGIME SHIFT] BEARISH: +{prev_4h:.1f}% -> {curr_4h:.1f}%")
            elif prev_4h < -0.5 and curr_4h > 0.3:
                result = {"shift_detected": True, "shift_type": "BULLISH_SHIFT", "confidence_adjustment": -10}
                print(f"  [REGIME SHIFT] BULLISH: {prev_4h:.1f}% -> +{curr_4h:.1f}%")
    except Exception as e:
        print(f"  [REGIME SHIFT] Error: {e}")
    return result

def get_resistance_proximity(symbol="cmt_btcusdt") -> dict:
    """V3.1.21: Check if near 24h high"""
    result = {"near_resistance": False, "distance_pct": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1h&limit=25"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 24:
            high_24h = max(float(c[2]) for c in candles[:24])
            current = float(candles[0][4])
            dist = ((current - high_24h) / high_24h) * 100
            result = {"near_resistance": dist > -1.0, "distance_pct": round(dist, 2), "high_24h": high_24h}
            if result["near_resistance"]:
                print(f"  [RESISTANCE] Near 24h high: ${current:.0f} vs ${high_24h:.0f}")
    except: pass
    return result

'''
        content = content.replace(
            "def get_enhanced_market_regime()",
            regime_shift + "\ndef get_enhanced_market_regime()"
        )
        changes.append("Added regime shift detection")
    
    # Patch 5: Update header
    content = content.replace("V3.1.20 - PREDATOR MODE", "V3.1.21 - BEAR HUNTER MODE")
    changes.append("Updated version to V3.1.21")
    
    with open(source_file, 'w') as f:
        f.write(content)
    
    print(f"\n{'='*50}")
    print("V3.1.21 PATCHES APPLIED")
    print('='*50)
    for c in changes:
        print(f"  [OK] {c}")
    print(f"\nTest: python3 {source_file} --test")
    return True

if __name__ == "__main__":
    print("SMT V3.1.21 Auto-Patcher")
    print("="*50)
    apply_patches()
