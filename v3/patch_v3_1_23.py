#!/usr/bin/env python3
"""
SMT Daemon V3.1.23 Patch Script
Fixes the HARD STOP that was killing regime-aligned SHORTs

Run on VM:
    python3 patch_v3_1_23.py
"""

import re

DAEMON_FILE = "smt_daemon_v3_1.py"

# Old broken code to find
OLD_CODE = '''            # V3.1.14: Calculate portfolio context
            total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in positions if p['side'] == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
            total_long_gain = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'LONG' and float(p.get('unrealized_pnl', 0)) > 0)
            total_short_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in positions if p['side'] == 'SHORT' and float(p.get('unrealized_pnl', 0)) < 0)
            total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
            shorts_winning = total_short_gain > 30 and total_long_loss > 20  # V3.1.20: Raised from 20/15
            longs_winning = total_long_gain > 30 and total_short_loss > 20  # V3.1.20: Raised from 20/15
            
            # V3.1.20 PREDATOR: Only exit on SEVERE losses, let SL do its job
            # LONG losing in BEARISH market - raised from $5 to $15
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -15:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market - raised from $5 to $15
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -15:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.20 PREDATOR: Removed NEUTRAL weak market exit - trust the SL
            
            # V3.1.20 PREDATOR: HARD STOP raised to $30 - let SL work
            elif side == "LONG" and pnl < -30:
                should_close = True
                reason = f"HARD STOP: LONG losing ${abs(pnl):.1f}"
            
            elif side == "SHORT" and pnl < -30:
                should_close = True
                reason = f"HARD STOP: SHORT losing ${abs(pnl):.1f}"
            
            # V3.1.20 PREDATOR: Raised from $6 to $12 when opposite winning
            elif side == "LONG" and pnl < -12 and shorts_winning:
                should_close = True
                reason = f"LONG -${abs(pnl):.1f} while SHORTs winning"
            
            elif side == "SHORT" and pnl < -12 and longs_winning:
                should_close = True
                reason = f"SHORT -${abs(pnl):.1f} while LONGs winning"'''

# New fixed code
NEW_CODE = '''            # V3.1.23: Simplified regime exit logic - only exit positions FIGHTING the regime
            # Trust the 2% SL on WEEX for normal stops
            
            # LONG losing in BEARISH market
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -15:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -15:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.23 FIX: HARD STOP only for positions FIGHTING regime (raised to $50)
            # LONG in BEARISH/NEUTRAL losing badly = cut it
            elif side == "LONG" and pnl < -50 and regime["regime"] in ("BEARISH", "NEUTRAL"):
                should_close = True
                reason = f"HARD STOP: LONG losing ${abs(pnl):.1f} in {regime['regime']} market"
            
            # SHORT in BULLISH losing badly = cut it (but NOT in BEARISH/NEUTRAL!)
            elif side == "SHORT" and pnl < -50 and regime["regime"] == "BULLISH":
                should_close = True
                reason = f"HARD STOP: SHORT losing ${abs(pnl):.1f} in BULLISH market"
            
            # V3.1.23: No more unconditional HARD STOP or "opposite winning" exit
            # Trust the 2% SL on WEEX to do its job'''

# Update header
OLD_HEADER = '''#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1.22 - CAPITAL PROTECTION
=========================
No partial closes. Higher conviction trades only.

V3.1.20 Changes (PREDATOR MODE):'''

NEW_HEADER = '''#!/usr/bin/env python3
"""
SMT Trading Daemon V3.1.23 - REGIME-ALIGNED TRADING
=========================
CRITICAL FIX: HARD STOP was killing regime-aligned trades.

V3.1.23 Changes (REGIME FIX):
- REMOVED unconditional $30 HARD STOP that killed SHORTs in BEARISH markets
- HARD STOP now ONLY fires for positions FIGHTING the regime
- SHORT in BEARISH/NEUTRAL = let it run, trust the 2% SL
- LONG in BEARISH/NEUTRAL losing >$50 = cut it
- SHORT in BULLISH losing >$50 = cut it
- Removed "opposite winning" exit logic - too aggressive

V3.1.20 Changes (PREDATOR MODE):'''


def main():
    print("=" * 60)
    print("SMT Daemon V3.1.23 Patch")
    print("Fixes HARD STOP killing regime-aligned SHORTs")
    print("=" * 60)
    
    # Read file
    try:
        with open(DAEMON_FILE, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {DAEMON_FILE} not found!")
        print("Make sure you're in ~/smt-weex-trading-bot directory")
        return False
    
    # Check if already patched
    if "V3.1.23" in content:
        print("Already patched to V3.1.23!")
        return True
    
    # Apply header patch
    if OLD_HEADER in content:
        content = content.replace(OLD_HEADER, NEW_HEADER)
        print("[OK] Header updated to V3.1.23")
    else:
        print("[WARN] Header not found exactly, trying to continue...")
    
    # Apply main code patch
    if OLD_CODE in content:
        content = content.replace(OLD_CODE, NEW_CODE)
        print("[OK] Regime exit logic fixed")
    else:
        print("[ERROR] Could not find the code to patch!")
        print("The daemon may have been modified. Manual patch needed.")
        return False
    
    # Write back
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("\n" + "=" * 60)
    print("PATCH APPLIED SUCCESSFULLY!")
    print("=" * 60)
    print("\nChanges made:")
    print("1. Removed unconditional $30 HARD STOP")
    print("2. SHORTs in BEARISH/NEUTRAL now run freely (trust 2% SL)")
    print("3. HARD STOP only for positions FIGHTING regime")
    print("\nNext steps:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
    print("  tail -f daemon.log")
    print("\nThen commit:")
    print("  git add -A && git commit -m 'V3.1.23: Fix HARD STOP' && git push")
    
    return True


if __name__ == "__main__":
    main()
