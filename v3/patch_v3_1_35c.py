#!/usr/bin/env python3
"""V3.1.35c: Remove absolute regime blocks - confidence thresholds handle this"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

old_block = '''        # V3.1.27: REGIME BLOCKING - Don't fight the trend
        if regime["regime"] == "BULLISH" and decision == "SHORT":
            print(f"  [JUDGE] BLOCKED: Don't SHORT in BULLISH regime (BTC: {regime.get('btc_24h', 0):+.1f}%)")
            return self._wait_decision(f"BLOCKED: Don't SHORT in BULLISH regime", persona_votes, vote_summary)
        
        if regime["regime"] == "BEARISH" and decision == "LONG":
            print(f"  [JUDGE] BLOCKED: Don't LONG in BEARISH regime (BTC: {regime.get('btc_24h', 0):+.1f}%)")
            return self._wait_decision(f"BLOCKED: Don't LONG in BEARISH regime", persona_votes, vote_summary)'''

new_block = '''        # V3.1.35c: Removed absolute regime blocks - V3.1.33/35 confidence thresholds handle this
        # BULLISH SHORT needs 85%, BEARISH LONG needs 85% (set above)
        # This allows high-conviction counter-trend trades'''

if old_block in code:
    code = code.replace(old_block, new_block)
    print("[OK] Removed absolute regime blocks (V3.1.27)")
else:
    print("[FAIL] Could not find regime blocking section")

with open(FILE, 'w') as f:
    f.write(code)

print("Done. Restart daemon.")
