#!/usr/bin/env python3
"""V3.1.35b: Fix 2-persona LONG requirement when whale is skipped"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

old = '''            # V3.1.35: Require at least 2 personas agreeing on LONG
            if long_votes < 2:
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)'''

new = '''            # V3.1.35b: Require 2 personas for LONG, but 1 is OK if whale skipped AND Flow is extreme
            whale_voted = any(v.get("persona") == "WHALE" and v.get("signal") != "SKIP" for v in persona_votes)
            flow_vote = next((v for v in persona_votes if v.get("persona") == "FLOW"), {})
            flow_extreme = flow_vote.get("confidence", 0) >= 0.85
            
            if long_votes < 2 and not (long_votes >= 1 and not whale_voted and flow_extreme):
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)'''

if old in code:
    code = code.replace(old, new)
    print("[OK] Fixed: Allow 1-persona LONG when whale skipped + Flow extreme")
else:
    print("[FAIL] Could not find 2-persona block")

with open(FILE, 'w') as f:
    f.write(code)

print("Done. Restart daemon.")
