#!/usr/bin/env python3
"""V3.1.35: Fix Judge weights - trust Flow, nerf stale Sentiment, fix LONG thresholds"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

# ============================================================
# CHANGE 1: Fix BULLISH weights - Flow should lead, not Sentiment
# ============================================================
old_bullish_weights = '''            elif is_bullish:
                # V3.1.21: SYMMETRIC BULLISH WEIGHTS (mirror of bearish)
                weights = {
                    "WHALE": 2.5,      # V3.1.21: Whales lead the way up - TRUST THEM
                    "SENTIMENT": 2.0 if signal == "LONG" else 0.8,  # V3.1.21: Trust LONG sentiment (symmetric)
                    "FLOW": 1.2,       # V3.1.24: REDUCED from 2.0 - taker spikes fake
                    "TECHNICAL": 1.5   # V3.1.21: Trust momentum
                }'''

new_bullish_weights = '''            elif is_bullish:
                # V3.1.35: FLOW LEADS in BULLISH - it's real-time, sentiment is old news
                weights = {
                    "WHALE": 2.0,      # Whales important but not dominant
                    "SENTIMENT": 0.8 if signal == "SHORT" else 1.5,  # V3.1.35: Nerf SHORT sentiment (backward-looking)
                    "FLOW": 2.5,       # V3.1.35: FLOW IS KING - real-time orderbook/taker data
                    "TECHNICAL": 1.2   # Technicals lag in fast moves
                }'''

if old_bullish_weights in code:
    code = code.replace(old_bullish_weights, new_bullish_weights)
    print("[OK] CHANGE 1: BULLISH weights - Flow 1.2->2.5, Sentiment SHORT nerfed")
else:
    print("[FAIL] CHANGE 1: Could not find BULLISH weights")

# ============================================================
# CHANGE 2: Fix BEARISH weights - nerf LONG sentiment, boost Flow
# ============================================================
old_bearish_weights = '''                weights = {
                    "WHALE": 0.8,      # Reduce whale (accumulation noise)
                    "SENTIMENT": 1.8 if signal == "SHORT" else 1.0,  # V3.1.18: Favor SHORT sentiment
                    "FLOW": 1.8,       # Trust flow
                    "TECHNICAL": 1.8   # Trust technicals
                }'''

new_bearish_weights = '''                weights = {
                    "WHALE": 1.0,      # V3.1.35: Whale accumulation can signal bottoms
                    "SENTIMENT": 1.0 if signal == "SHORT" else 0.8,  # V3.1.35: Reduced - sentiment lags
                    "FLOW": 2.5,       # V3.1.35: FLOW IS KING - catches reversals first
                    "TECHNICAL": 1.5   # Trust technicals
                }'''

if old_bearish_weights in code:
    code = code.replace(old_bearish_weights, new_bearish_weights)
    print("[OK] CHANGE 2: BEARISH weights - Flow 1.8->2.5, Sentiment nerfed")
else:
    print("[FAIL] CHANGE 2: Could not find BEARISH weights")

# ============================================================
# CHANGE 3: Fix NEUTRAL weights
# ============================================================
old_neutral_weights = '''            else:  # NEUTRAL
                weights = {
                    "WHALE": 1.5,      # Slightly reduced
                    "SENTIMENT": 1.2,  # V3.1.24: Increased - sentiment has been right
                    "FLOW": 1.0,       # V3.1.24: REDUCED from 1.5 - don't trust in chop
                    "TECHNICAL": 1.5   # Technicals guide in ranges
                }'''

new_neutral_weights = '''            else:  # NEUTRAL
                # V3.1.35: Balanced but Flow still leads
                weights = {
                    "WHALE": 1.5,
                    "SENTIMENT": 1.0,  # V3.1.35: Reduced - backward-looking
                    "FLOW": 2.0,       # V3.1.35: Real-time data leads
                    "TECHNICAL": 1.5
                }'''

if old_neutral_weights in code:
    code = code.replace(old_neutral_weights, new_neutral_weights)
    print("[OK] CHANGE 3: NEUTRAL weights - Flow 1.0->2.0")
else:
    print("[FAIL] CHANGE 3: Could not find NEUTRAL weights")

# ============================================================
# CHANGE 4: Fix LONG confidence thresholds (the real killer)
# ============================================================
old_long_thresholds = '''        if decision == "LONG":
            # V3.1.24: Require at least 2 personas agreeing on LONG
            if long_votes < 2:
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)
            
            if regime["regime"] == "BULLISH":
                min_confidence = 0.70  # V3.1.24: Raised from 50% - stop bad LONGs
                print(f"  [JUDGE] V3.1.21: BULLISH regime - LONG threshold lowered to 50%")
                print(f"  [JUDGE] V3.1.20: BULLISH regime - LONG needs 70%")
            elif regime["regime"] == "NEUTRAL":
                min_confidence = 0.85  # NEUTRAL: 85% for LONGs (very strict)
                print(f"  [JUDGE] V3.1.20: NEUTRAL regime - LONG needs 85%")
            else:  # BEARISH
                min_confidence = 1.0  # Impossible - will be blocked anyway'''

new_long_thresholds = '''        if decision == "LONG":
            # V3.1.35: Require at least 2 personas agreeing on LONG
            if long_votes < 2:
                return self._wait_decision(f"BLOCKED: LONG needs 2+ personas (only {long_votes})", persona_votes, vote_summary)
            
            if regime["regime"] == "BULLISH":
                min_confidence = 0.60  # V3.1.35: BULLISH = go with trend
                print(f"  [JUDGE] V3.1.35: BULLISH regime - LONG needs 60%")
            elif regime["regime"] == "NEUTRAL":
                min_confidence = 0.80  # V3.1.35: NEUTRAL needs more conviction
                print(f"  [JUDGE] V3.1.35: NEUTRAL regime - LONG needs 80%")
            else:  # BEARISH
                min_confidence = 0.85  # V3.1.35: Counter-trend, high conviction only
                print(f"  [JUDGE] V3.1.35: BEARISH regime - LONG needs 85%")'''

if old_long_thresholds in code:
    code = code.replace(old_long_thresholds, new_long_thresholds)
    print("[OK] CHANGE 4: LONG thresholds - BULLISH 70->60%, BEARISH impossible->85%")
else:
    print("[FAIL] CHANGE 4: Could not find LONG thresholds")

# ============================================================
# CHANGE 5: Fix SHORT thresholds symmetrically
# Find where SHORT min_confidence is set
# ============================================================
# The current code doesn't have explicit SHORT thresholds after LONG block
# The BEARISH lowered threshold was changed to 80% in V3.1.33
# Add proper SHORT thresholds
old_short_conf = '''        if tier == 3:
            min_confidence = max(min_confidence, 0.65)  # Tier 3 floor'''

new_short_conf = '''        # V3.1.35: Symmetric SHORT thresholds
        if decision == "SHORT":
            if regime["regime"] == "BEARISH":
                min_confidence = max(min_confidence, 0.60)  # With trend
                print(f"  [JUDGE] V3.1.35: BEARISH regime - SHORT needs 60%")
            elif regime["regime"] == "NEUTRAL":
                min_confidence = max(min_confidence, 0.80)
                print(f"  [JUDGE] V3.1.35: NEUTRAL regime - SHORT needs 80%")
            else:  # BULLISH
                min_confidence = max(min_confidence, 0.85)  # Counter-trend
                print(f"  [JUDGE] V3.1.35: BULLISH regime - SHORT needs 85%")
        
        if tier == 3:
            min_confidence = max(min_confidence, 0.65)  # Tier 3 floor'''

if old_short_conf in code:
    code = code.replace(old_short_conf, new_short_conf)
    print("[OK] CHANGE 5: Added symmetric SHORT thresholds")
else:
    print("[FAIL] CHANGE 5: Could not find tier 3 floor")

with open(FILE, 'w') as f:
    f.write(code)

print("\n=== V3.1.35 JUDGE FIX COMPLETE ===")
print("Weight changes (all regimes):")
print("  FLOW: Now 2.0-2.5x across all regimes (was 1.0-1.8)")
print("  SENTIMENT: Nerfed to 0.8-1.0 for counter-trend votes (was 1.2-2.0)")
print("  WHALE: Balanced at 1.0-2.0")
print("Threshold changes:")
print("  LONG in BULLISH: 70% -> 60% (with trend)")
print("  LONG in BEARISH: impossible -> 85% (counter-trend)")
print("  SHORT in BEARISH: 60% (with trend)")
print("  SHORT in BULLISH: 85% (counter-trend)")
