#!/usr/bin/env python3
"""V3.1.33: Selective trading - 80% min, counter-trend at 85%"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

# ============================================================
# CHANGE 1: Replace the LONG block - allow at 85% in BEARISH
# ============================================================
old_long_block = '''        # V3.1.20 PREDATOR: STRICT LONG FILTERS
        if decision == "LONG":
            # 1. V3.1.24: Block LONGs in BEARISH or NEUTRAL regime
            if regime["regime"] in ("BEARISH", "NEUTRAL"):
                return self._wait_decision(f"BLOCKED: LONG in {regime['regime']} regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            
            # 2. V3.1.20: POSITIVE MOMENTUM REQUIREMENT
            # In NEUTRAL, require BTC > +0.2% (actually climbing, not just "flat")
            if regime["regime"] == "NEUTRAL" and regime.get("btc_24h", 0) < 0.2:
                return self._wait_decision(f"BLOCKED: LONG in NEUTRAL needs BTC > +0.2% (current: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            
            # 3. V3.1.20: WHALE VETO
            # If WHALE persona voted SHORT, don't go LONG regardless of other signals'''

new_long_block = '''        # V3.1.33: SELECTIVE TRADING - counter-trend needs 85%
        if decision == "LONG":
            # 1. V3.1.33: In BEARISH, LONG is counter-trend - need 85%+
            if regime["regime"] == "BEARISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: LONG in BEARISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)
            
            # 2. V3.1.33: In NEUTRAL, need 80%+
            if regime["regime"] == "NEUTRAL" and confidence < 0.80:
                return self._wait_decision(f"BLOCKED: LONG in NEUTRAL needs 80%+ (have {confidence:.0%})", persona_votes, vote_summary)
            
            # 3. V3.1.20: WHALE VETO (keep - whales know best)
            # If WHALE persona voted SHORT, don't go LONG regardless of other signals'''

if old_long_block in code:
    code = code.replace(old_long_block, new_long_block)
    print("[OK] CHANGE 1: LONG allowed in BEARISH at 85%+, NEUTRAL at 80%+")
else:
    print("[FAIL] CHANGE 1: Could not find LONG block")

# ============================================================
# CHANGE 2: Remove over-restrictive LONG filters
# ============================================================
old_btc_drop = '''            # 4. V3.1.20: BTC DROPPING VETO
            # If BTC is actively dropping in last 24h, don't open new LONGs
            if regime.get("btc_24h", 0) < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            
            # 5. Block LONGs if OI shows short buildup
            if regime.get("oi_signal") == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG rejected by OI sensor - {regime.get('oi_reason', 'short buildup')[:60]}", persona_votes, vote_summary)
            
            # 6. Block new LONGs if existing LONGs bleeding
            if hasattr(self, '_open_positions') and self._open_positions:
                total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in self._open_positions if p.get('side') == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
                total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in self._open_positions if p.get('side') == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
                
                if total_long_loss > 15:
                    return self._wait_decision(f"BLOCKED: Existing LONGs losing ${total_long_loss:.1f}", persona_votes, vote_summary)'''

new_btc_drop = '''            # 4-6. V3.1.33: Removed BTC drop veto, OI block, bleeding LONGs filter
            # Confidence threshold (85% in BEARISH) handles risk already
            pass'''

if old_btc_drop in code:
    code = code.replace(old_btc_drop, new_btc_drop)
    print("[OK] CHANGE 2: Removed BTC drop veto, OI block, bleeding filter")
else:
    print("[FAIL] CHANGE 2: Could not find BTC dropping block")

# ============================================================
# CHANGE 3: Add SHORT filter in BULLISH (counter-trend needs 85%)
# Find where SHORTs are processed after the LONG block
# ============================================================
# We need to add a SHORT filter similar to the LONG one
# Look for where SHORT threshold is lowered in BEARISH
old_short_section = '            print(f"  [JUDGE] BEARISH/WEAK regime: Lowered SHORT threshold to 50%")'
new_short_section = '            print(f"  [JUDGE] V3.1.33: BEARISH regime, SHORT at 80%+ min")'

count = code.count(old_short_section)
if count > 0:
    code = code.replace(old_short_section, new_short_section)
    print(f"[OK] CHANGE 3a: SHORT log updated ({count} instances)")

# ============================================================
# CHANGE 4: Global MIN_CONFIDENCE raised to 80%
# ============================================================
old_min_conf = 'MIN_CONFIDENCE_TO_TRADE = 0.75'
new_min_conf = 'MIN_CONFIDENCE_TO_TRADE = 0.80  # V3.1.33: Selective - only high conviction trades'

if old_min_conf in code:
    code = code.replace(old_min_conf, new_min_conf)
    print("[OK] CHANGE 4: MIN_CONFIDENCE_TO_TRADE 75% -> 80%")
else:
    print("[WARN] CHANGE 4: MIN_CONFIDENCE_TO_TRADE not found at 0.75")

# ============================================================
# CHANGE 5: Add BULLISH SHORT filter
# Find the section where SHORT decisions are processed
# Add counter-trend filter for SHORTs in BULLISH
# ============================================================
# Look for where SHORT threshold is set in BEARISH regime
old_bearish_short = '''min_confidence = 0.50  # BEARISH: Lower bar for shorts'''
new_bearish_short = '''min_confidence = 0.80  # V3.1.33: 80% min for all trades'''
if old_bearish_short in code:
    code = code.replace(old_bearish_short, new_bearish_short)
    print("[OK] CHANGE 5a: BEARISH SHORT threshold 50% -> 80%")

# Add BULLISH SHORT counter-trend block
# Find where decision == "SHORT" filtering happens
# We'll add it right after the LONG block by finding the end marker
if '            # 4-6. V3.1.33:' in code:
    # Find the SHORT decision handling area - add counter-trend filter
    # Look for where SHORT is evaluated
    short_filter = '''
        # V3.1.33: SHORT in BULLISH is counter-trend - need 85%
        if decision == "SHORT":
            if regime["regime"] == "BULLISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: SHORT in BULLISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)
'''
    # Insert after the LONG block's pass statement
    old_pass = '''            # 4-6. V3.1.33: Removed BTC drop veto, OI block, bleeding LONGs filter
            # Confidence threshold (85% in BEARISH) handles risk already
            pass'''
    new_pass = '''            # 4-6. V3.1.33: Removed BTC drop veto, OI block, bleeding LONGs filter
            # Confidence threshold (85% in BEARISH) handles risk already
            pass
        
        # V3.1.33: SHORT in BULLISH is counter-trend - need 85%
        if decision == "SHORT":
            if regime["regime"] == "BULLISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: SHORT in BULLISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)'''
    
    if old_pass in code:
        code = code.replace(old_pass, new_pass)
        print("[OK] CHANGE 5b: Added SHORT counter-trend filter in BULLISH at 85%")
    else:
        print("[FAIL] CHANGE 5b: Could not insert SHORT BULLISH filter")

# ============================================================
# WRITE
# ============================================================
with open(FILE, 'w') as f:
    f.write(code)

print("\n=== V3.1.33 PATCH COMPLETE ===")
print("Rules:")
print("  - ALL trades: 80% minimum confidence")
print("  - LONG in BEARISH: 85% minimum (counter-trend)")
print("  - SHORT in BULLISH: 85% minimum (counter-trend)")
print("  - LONG in NEUTRAL: 80% minimum")
print("  - With-trend trades: 80% minimum")
print("  - Removed: BTC drop veto, OI block, bleeding LONGs filter")
print("  - Kept: Whale veto on LONGs")
print("\nRestart: pkill -f smt_daemon; sleep 2; nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
