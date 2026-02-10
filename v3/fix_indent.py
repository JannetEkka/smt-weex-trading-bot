#!/usr/bin/env python3
"""Fix indentation error from V3.1.46 patch"""
import os, sys

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smt_daemon_v3_1.py")
with open(filepath, 'r') as f:
    content = f.read()

old = """                if False:  # V3.1.46: ALL profit guards disabled
                
                # TIER 1: Move SL to breakeven at +0.8%, guard at peak-50% above +1.2%"""

new = """                if False:  # V3.1.46: ALL profit guards disabled
                    pass
                if False:  # V3.1.46: profit guard block disabled
                    # TIER 1: Move SL to breakeven at +0.8%, guard at peak-50% above +1.2%"""

if old in content:
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print("OK: Fixed indentation error")
else:
    print("ERROR: Could not find the broken text. Trying alternative fix...")
    # Alternative: just replace the if False line with a pass-through
    alt_old = "                if False:  # V3.1.46: ALL profit guards disabled"
    alt_new = "                if False:  # V3.1.46: ALL profit guards disabled\n                    pass  # V3.1.46 placeholder"
    if alt_old in content:
        content = content.replace(alt_old, alt_new, 1)
        with open(filepath, 'w') as f:
            f.write(content)
        print("OK: Fixed with pass placeholder")
    else:
        print("ERROR: Cannot find the line at all")
        sys.exit(1)

print("Restart: pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
