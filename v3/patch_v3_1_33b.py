#!/usr/bin/env python3
"""V3.1.33b: Remove remaining LONG blockers"""

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    code = f.read()

old_block = '''            # 4. Block if 24h is negative
            if regime.get("btc_24h", 0) < 0:  # V3.1.24: Any negative blocks LONG
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            
            # 5. Block LONGs if OI shows short buildup
            if regime.get("oi_signal") == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG rejected by OI sensor - {regime.get('oi_reason', 'short buildup')[:60]}", persona_votes, vote_summary)
            
            # 6. Block new LONGs if existing LONGs bleeding
            if hasattr(self, '_open_positions') and self._open_positions:
                total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in self._open_positions if p.get('side') == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
                total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in self._open_positions if p.get('side') == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
                
                if total_long_loss > 15:
                    return self._wait_decision(f"BLOCKED: Existing LONGs losing ${total_long_loss:.1f}", persona_votes, vote_summary)
                if total_short_gain > 20 and total_long_loss > 8:
                    return self._wait_decision(f"BLOCKED: SHORTs +${total_short_gain:.1f} outperforming LONGs -${total_long_loss:.1f}", persona_votes, vote_summary)'''

new_block = '''            # 4-6. V3.1.33: Removed - confidence threshold handles risk
            # 85% in BEARISH, 80% in NEUTRAL is enough filtering
            pass'''

if old_block in code:
    code = code.replace(old_block, new_block)
    print("[OK] Removed filters 4, 5, 6 blocking LONGs")
else:
    print("[FAIL] Could not find block")

# Add SHORT in BULLISH filter
old_marker = '''        # V3.1.21: ABSORPTION - Cap extreme selling in BULLISH (symmetric with buying cap in BEARISH)'''
new_marker = '''        # V3.1.33: SHORT in BULLISH is counter-trend - need 85%
        if decision == "SHORT":
            if regime["regime"] == "BULLISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: SHORT in BULLISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)
        
        # V3.1.21: ABSORPTION - Cap extreme selling in BULLISH (symmetric with buying cap in BEARISH)'''

if old_marker in code:
    code = code.replace(old_marker, new_marker)
    print("[OK] Added SHORT counter-trend filter in BULLISH at 85%")
else:
    print("[WARN] Could not find ABSORPTION marker for SHORT filter")

with open(FILE, 'w') as f:
    f.write(code)

print("\n=== V3.1.33b PATCH DONE ===")
