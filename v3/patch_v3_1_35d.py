#!/usr/bin/env python3
"""V3.1.35d: Fix T3 blocking + strong bullish block"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

changes = 0

# CHANGE 1: T3 blocking - allow if Flow is extreme
old_t3 = '''        # V3.1.16: Relaxed Tier 3 requirements (was blocking too many trades)
        if tier == 3:
            # Only block if there's strong opposition (2+ at high confidence)
            if opposing_votes >= 2 and agreeing_votes < 2:
                return self._wait_decision(f"Tier 3 blocked: {opposing_votes} personas oppose {decision}", persona_votes, vote_summary)'''

new_t3 = '''        # V3.1.35d: Relaxed T3 - allow if Flow is extreme even with opposition
        if tier == 3:
            flow_v = next((v for v in persona_votes if v.get("persona") == "FLOW"), {})
            flow_is_extreme = flow_v.get("confidence", 0) >= 0.85 and flow_v.get("signal") == decision
            if opposing_votes >= 2 and agreeing_votes < 2 and not flow_is_extreme:
                return self._wait_decision(f"Tier 3 blocked: {opposing_votes} personas oppose {decision}", persona_votes, vote_summary)'''

if old_t3 in code:
    code = code.replace(old_t3, new_t3)
    changes += 1
    print("[OK] CHANGE 1: T3 allows extreme Flow override")
else:
    print("[FAIL] CHANGE 1")

# CHANGE 2: Remove STRONG BULLISH absolute SHORT block
old_strong = '''            return self._wait_decision(f"BLOCKED: SHORT in STRONG BULLISH regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)'''

new_strong = '''            print(f"  [JUDGE] WARNING: SHORT in STRONG BULLISH regime - high risk")
            # V3.1.35d: Don't block, just warn. 85% threshold handles this.'''

if old_strong in code:
    code = code.replace(old_strong, new_strong)
    changes += 1
    print("[OK] CHANGE 2: Strong BULLISH SHORT block -> warning only")
else:
    print("[FAIL] CHANGE 2")

# CHANGE 3: Remove absorption block (line 2066)
old_absorption = '''                return self._wait_decision(f"BLOCKED: {absorption.get('reason', 'Whale absorption detected')}", persona_votes, vote_summary)'''
new_absorption = '''                print(f"  [JUDGE] WARNING: {absorption.get('reason', 'Whale absorption detected')}")
                # V3.1.35d: Warn only, don't block'''

if old_absorption in code:
    code = code.replace(old_absorption, new_absorption)
    changes += 1
    print("[OK] CHANGE 3: Absorption block -> warning only")
else:
    print("[WARN] CHANGE 3: absorption block not found")

with open(FILE, 'w') as f:
    f.write(code)

print(f"\n=== V3.1.35d: {changes} changes applied ===")
