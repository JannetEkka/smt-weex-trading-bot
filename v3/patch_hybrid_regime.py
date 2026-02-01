#!/usr/bin/env python3
"""
SMT Hybrid Regime Detection Patch V3.1.25
=========================================
Adds fast spike detection while keeping slow trend detection.

- Slow (4h/24h): For overall trend - avoids whipsaw
- Fast (1h): For sudden spikes only - catches reversals

Run: python3 patch_hybrid_regime.py
"""

import os

DAEMON_FILE = "smt_daemon_v3_1.py"

# New hybrid regime function
NEW_REGIME_FUNCTION = '''
def get_market_regime_for_exit():
    """
    V3.1.25: HYBRID regime detection
    - Slow (4h/24h candles) for trend
    - Fast (1h candles) for spike detection
    
    SPIKE_UP: BTC pumped >1.5% in 1h - danger for SHORTs
    SPIKE_DOWN: BTC dumped >1.5% in 1h - danger for LONGs
    """
    try:
        # === SLOW TREND (existing logic) ===
        url_4h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url_4h, timeout=10)
        data_4h = r.json()
        
        change_24h = 0
        change_4h = 0
        
        if isinstance(data_4h, list) and len(data_4h) >= 7:
            closes = [float(c[4]) for c in data_4h]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
        
        # === FAST SPIKE DETECTION (new) ===
        url_1h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=1h&limit=2"
        r = requests.get(url_1h, timeout=10)
        data_1h = r.json()
        
        change_1h = 0
        if isinstance(data_1h, list) and len(data_1h) >= 2:
            closes_1h = [float(c[4]) for c in data_1h]
            change_1h = ((closes_1h[0] - closes_1h[1]) / closes_1h[1]) * 100
        
        # === REGIME DECISION ===
        
        # V3.1.25: SPIKE detection takes priority (fast override)
        # This catches sudden pumps/dumps that slow detection misses
        if change_1h > 1.5:
            return {
                "regime": "SPIKE_UP",
                "change_24h": change_24h,
                "change_4h": change_4h,
                "change_1h": change_1h,
                "spike": True,
            }
        elif change_1h < -1.5:
            return {
                "regime": "SPIKE_DOWN",
                "change_24h": change_24h,
                "change_4h": change_4h,
                "change_1h": change_1h,
                "spike": True,
            }
        
        # No spike - use slow trend detection
        if change_24h < -1.0 or change_4h < -1.0:
            regime = "BEARISH"
        elif change_24h > 1.5 or change_4h > 1.0:
            regime = "BULLISH"
        else:
            regime = "NEUTRAL"
        
        return {
            "regime": regime,
            "change_24h": change_24h,
            "change_4h": change_4h,
            "change_1h": change_1h,
            "spike": False,
        }
        
    except Exception as e:
        logger.error(f"[REGIME] API error: {e}")
    
    return {"regime": "UNKNOWN", "change_24h": 0, "change_4h": 0, "change_1h": 0, "spike": False}

'''

# Updated regime exit check with spike handling
NEW_REGIME_EXIT_LOGIC = '''
            # V3.1.25: SPIKE detection - fast exit on sudden moves
            if regime.get("spike"):
                spike_type = regime["regime"]
                change_1h = regime.get("change_1h", 0)
                
                # SPIKE_UP = danger for SHORTs
                if spike_type == "SPIKE_UP" and side == "SHORT" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_UP: BTC +{change_1h:.1f}% in 1h, SHORT losing ${abs(pnl):.1f}"
                
                # SPIKE_DOWN = danger for LONGs
                elif spike_type == "SPIKE_DOWN" and side == "LONG" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_DOWN: BTC {change_1h:.1f}% in 1h, LONG losing ${abs(pnl):.1f}"
            
            # V3.1.23: Standard regime exits (slower trend-based)
            elif regime["regime"] == "BEARISH" and side == "LONG" and pnl < -15:
'''


def main():
    print("=" * 60)
    print("SMT Hybrid Regime Detection Patch V3.1.25")
    print("=" * 60)
    
    if not os.path.exists(DAEMON_FILE):
        print(f"ERROR: {DAEMON_FILE} not found!")
        return False
    
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    # Check if already patched
    if "SPIKE_UP" in content:
        print("Already patched with hybrid regime!")
        return True
    
    print("\n[1/3] Updating regime detection function...")
    
    # Find and replace the old regime function
    old_func_start = 'def get_market_regime_for_exit():'
    old_func_marker = '"""Get BTC 24h trend for regime-based exits'
    
    if old_func_start in content and old_func_marker in content:
        # Find the full function (from def to next def or class)
        start_idx = content.find(old_func_start)
        
        # Find where function ends (next function definition)
        next_def = content.find('\ndef ', start_idx + 10)
        if next_def == -1:
            next_def = content.find('\nclass ', start_idx + 10)
        
        if next_def != -1:
            old_func = content[start_idx:next_def]
            content = content.replace(old_func, NEW_REGIME_FUNCTION)
            print("   OK: Replaced regime detection function")
        else:
            print("   WARN: Could not find function end")
    else:
        print("   WARN: Could not find old regime function")
    
    print("\n[2/3] Adding spike exit logic...")
    
    # Find the regime exit logic and add spike handling before it
    old_exit_marker = '# V3.1.23: Simplified regime exit logic'
    
    if old_exit_marker in content:
        # Add spike logic before the existing regime logic
        new_logic = '''# V3.1.25: SPIKE detection - fast exit on sudden moves
            if regime.get("spike"):
                spike_type = regime["regime"]
                change_1h = regime.get("change_1h", 0)
                
                # SPIKE_UP = danger for SHORTs
                if spike_type == "SPIKE_UP" and side == "SHORT" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_UP: BTC +{change_1h:.1f}% in 1h, SHORT losing ${abs(pnl):.1f}"
                
                # SPIKE_DOWN = danger for LONGs
                elif spike_type == "SPIKE_DOWN" and side == "LONG" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_DOWN: BTC {change_1h:.1f}% in 1h, LONG losing ${abs(pnl):.1f}"
            
            # V3.1.23: Simplified regime exit logic'''
        
        content = content.replace(old_exit_marker, new_logic)
        print("   OK: Added spike exit logic")
    else:
        print("   WARN: Could not find exit logic marker")
        # Try alternate approach
        alt_marker = '# LONG losing in BEARISH market'
        if alt_marker in content:
            new_logic = '''# V3.1.25: SPIKE detection - fast exit on sudden moves
            if regime.get("spike"):
                spike_type = regime["regime"]
                change_1h = regime.get("change_1h", 0)
                
                if spike_type == "SPIKE_UP" and side == "SHORT" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_UP: BTC +{change_1h:.1f}% in 1h, SHORT losing ${abs(pnl):.1f}"
                
                elif spike_type == "SPIKE_DOWN" and side == "LONG" and pnl < -10:
                    should_close = True
                    reason = f"SPIKE_DOWN: BTC {change_1h:.1f}% in 1h, LONG losing ${abs(pnl):.1f}"
            
            # LONG losing in BEARISH market'''
            content = content.replace(alt_marker, new_logic)
            print("   OK: Added spike exit logic (alternate)")
    
    print("\n[3/3] Updating log message...")
    
    # Update the regime log to show 1h change
    old_log = 'logger.info(f"[REGIME] Market: {regime[\'regime\']} | 24h: {regime[\'change_24h\']:+.1f}% | 4h: {regime[\'change_4h\']:+.1f}%")'
    new_log = 'logger.info(f"[REGIME] Market: {regime[\'regime\']} | 1h: {regime.get(\'change_1h\', 0):+.1f}% | 4h: {regime[\'change_4h\']:+.1f}% | 24h: {regime[\'change_24h\']:+.1f}%{\" SPIKE!\" if regime.get(\'spike\') else \"\"}")'
    
    if old_log in content:
        content = content.replace(old_log, new_log)
        print("   OK: Updated log format")
    else:
        print("   WARN: Could not find log format to update")
    
    # Update version in header
    content = content.replace(
        "V3.1.24 - REGIME-ALIGNED + RL DATA COLLECTION",
        "V3.1.25 - HYBRID REGIME + RL DATA COLLECTION"
    )
    content = content.replace(
        "V3.1.23 - REGIME-ALIGNED TRADING",
        "V3.1.25 - HYBRID REGIME DETECTION"
    )
    
    # Write back
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("\n" + "=" * 60)
    print("PATCH COMPLETE!")
    print("=" * 60)
    print("""
V3.1.25 Hybrid Regime Detection:

  SLOW (4h/24h candles):
    - BEARISH: 24h < -1% OR 4h < -1%
    - BULLISH: 24h > +1.5% OR 4h > +1%
    - NEUTRAL: Everything else
    
  FAST (1h candles) - OVERRIDES slow:
    - SPIKE_UP: 1h > +1.5% --> Exit SHORTs losing >$10
    - SPIKE_DOWN: 1h < -1.5% --> Exit LONGs losing >$10

This catches sudden pumps/dumps while avoiding whipsaw!

Next steps:
  pkill -f smt_daemon
  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
  tail -f daemon.log | grep REGIME

Commit:
  git add -A && git commit -m "V3.1.25: Hybrid regime - fast spike detection" && git push
""")
    
    return True


if __name__ == "__main__":
    main()
