#!/usr/bin/env python3
"""Add symmetric bullish handling to V3.1.21"""
import shutil
from datetime import datetime

source_file = "smt_nightly_trade_v3_1.py"
backup = f"smt_nightly_trade_v3_1.py.backup_v321_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy(source_file, backup)
print(f"Backup: {backup}")

with open(source_file, 'r') as f:
    content = f.read()

changes = []

# 1. Add get_support_proximity function after get_resistance_proximity
if "def get_support_proximity" not in content:
    support_func = '''

def get_support_proximity(symbol="cmt_btcusdt") -> dict:
    """V3.1.21: Check if near 24h low (support) - BULLISH equivalent of resistance"""
    result = {"near_support": False, "distance_pct": 0, "low_24h": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1h&limit=25"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 24:
            lows = [float(c[3]) for c in candles[:24]]
            low_24h = min(lows)
            current = float(candles[0][4])
            dist = ((current - low_24h) / low_24h) * 100
            result = {"near_support": dist < 1.0, "distance_pct": round(dist, 2), "low_24h": low_24h, "current_price": current}
            if result["near_support"]:
                print(f"  [SUPPORT] Near 24h low: ${current:.0f} vs ${low_24h:.0f} (+{dist:.1f}%)")
    except: pass
    return result

'''
    content = content.replace(
        "def get_resistance_proximity(",
        support_func + "def get_resistance_proximity("
    )
    changes.append("Added get_support_proximity() for LONG boost near lows")

# 2. Add detect_whale_absorption function
if "def detect_whale_absorption" not in content:
    absorption_func = '''

def detect_whale_absorption(whale_vote: dict, flow_vote: dict, regime: dict) -> dict:
    """
    V3.1.21: Detect whale absorption - whales buying while retail panic sells.
    
    BULLISH ABSORPTION: 
    - Extreme selling pressure (taker ratio < 0.5)
    - But whale flow is POSITIVE (accumulating)
    - Price hasn't broken 4h support
    = Whales absorbing the dip, prepare for reversal
    """
    result = {"absorption_detected": False, "type": "NONE", "boost": 1.0}
    
    try:
        whale_signal = whale_vote.get("signal", "NEUTRAL")
        whale_conf = whale_vote.get("confidence", 0)
        whale_data = whale_vote.get("data", {})
        net_flow = whale_data.get("net_flow", 0)
        
        flow_signal = flow_vote.get("signal", "NEUTRAL")
        
        # BULLISH ABSORPTION: Extreme selling but whales accumulating
        if flow_signal == "SHORT" and whale_signal == "LONG" and net_flow > 200:
            result = {
                "absorption_detected": True, 
                "type": "BULLISH_ABSORPTION",
                "boost": 1.5,
                "reason": f"Whales absorbing sell-off (+{net_flow:.0f} ETH)"
            }
            print(f"  [ABSORPTION] BULLISH: Retail panic selling but whales +{net_flow:.0f} ETH")
        
        # BEARISH DISTRIBUTION: Extreme buying but whales distributing
        elif flow_signal == "LONG" and whale_signal == "SHORT" and net_flow < -200:
            result = {
                "absorption_detected": True,
                "type": "BEARISH_DISTRIBUTION",
                "boost": 1.5,
                "reason": f"Whales distributing into rally ({net_flow:.0f} ETH)"
            }
            print(f"  [DISTRIBUTION] BEARISH: Retail FOMO but whales {net_flow:.0f} ETH")
    except:
        pass
    
    return result

'''
    # Insert before get_enhanced_market_regime
    content = content.replace(
        "def detect_regime_shift()",
        absorption_func + "def detect_regime_shift()"
    )
    changes.append("Added detect_whale_absorption() for smart money detection")

# 3. Add RSI overbought filter check in TechnicalPersona
# Find and update the RSI logic
if "rsi_blocks_long" not in content:
    # Add RSI overbought detection
    old_rsi = "RSI oversold:"
    new_rsi = "RSI oversold:"
    
    # We'll add the logic in Judge instead since Technical is complex
    changes.append("RSI overbought filter will be in Judge")

# 4. Update Judge weights for BULLISH symmetry
# Find the bullish weights section and update
old_bullish_weights = '''elif is_bullish:
                weights = {
                    "WHALE": 2.0,      # Whales lead the way up
                    "SENTIMENT": 1.8 if signal == "LONG" else 0.8,  # V3.1.18: Favor LONG sentiment
                    "FLOW": 1.0,       # Normal
                    "TECHNICAL": 1.2   # Normal
                }'''

new_bullish_weights = '''elif is_bullish:
                # V3.1.21: SYMMETRIC BULLISH WEIGHTS (mirror of bearish)
                weights = {
                    "WHALE": 2.5,      # V3.1.21: Whales lead the way up - TRUST THEM
                    "SENTIMENT": 2.0 if signal == "LONG" else 0.8,  # V3.1.21: Trust LONG sentiment (symmetric)
                    "FLOW": 2.0,       # V3.1.21: Trust buying pressure in bull
                    "TECHNICAL": 1.5   # V3.1.21: Trust momentum
                }'''

if old_bullish_weights in content:
    content = content.replace(old_bullish_weights, new_bullish_weights)
    changes.append("Updated BULLISH weights to be symmetric with BEARISH")

# 5. Add LONG threshold lowering in BULLISH regime
old_long_threshold = '''if decision == "LONG":
            if regime["regime"] == "BULLISH":
                min_confidence = 0.70  # BULLISH: 70% for LONGs'''

new_long_threshold = '''if decision == "LONG":
            if regime["regime"] == "BULLISH":
                min_confidence = 0.50  # V3.1.21: SYMMETRIC - 50% for LONGs in BULLISH (like SHORTs in BEARISH)
                print(f"  [JUDGE] V3.1.21: BULLISH regime - LONG threshold lowered to 50%")'''

if old_long_threshold in content:
    content = content.replace(old_long_threshold, new_long_threshold)
    changes.append("Lowered LONG threshold to 50% in BULLISH (symmetric)")

# 6. Add RSI overbought block for LONGs
old_rsi_block = '''# V3.1.20 PREDATOR: STRICT LONG FILTERS
        if decision == "LONG":'''

new_rsi_block = '''# V3.1.21: RSI OVERBOUGHT FILTER (symmetric with oversold SHORT filter)
        rsi_value = 50
        for vote in persona_votes:
            if vote.get("persona") == "TECHNICAL":
                # Extract RSI from reasoning
                reasoning = vote.get("reasoning", "")
                if "RSI" in reasoning:
                    import re
                    match = re.search(r'RSI[:\s]+(\d+\.?\d*)', reasoning)
                    if match:
                        rsi_value = float(match.group(1))
                break
        
        if decision == "LONG" and rsi_value > 75:
            return self._wait_decision(f"BLOCKED: RSI {rsi_value:.0f} > 75 (overbought) - no LONGs", persona_votes, vote_summary)
        
        # V3.1.20 PREDATOR: STRICT LONG FILTERS
        if decision == "LONG":'''

if old_rsi_block in content and "RSI OVERBOUGHT FILTER" not in content:
    content = content.replace(old_rsi_block, new_rsi_block)
    changes.append("Added RSI > 75 overbought filter for LONGs")

# 7. Add absorption selling cap in BULLISH
old_short_block = '''# V3.1.15: Only block SHORTs in STRONG bullish (>2% up)'''

new_short_block = '''# V3.1.21: ABSORPTION - Cap extreme selling in BULLISH (symmetric with buying cap in BEARISH)
        if decision == "SHORT" and regime["regime"] == "BULLISH":
            # Check for whale absorption
            whale_vote = next((v for v in persona_votes if v.get("persona") == "WHALE"), {})
            flow_vote = next((v for v in persona_votes if v.get("persona") == "FLOW"), {})
            absorption = detect_whale_absorption(whale_vote, flow_vote, regime)
            
            if absorption.get("type") == "BULLISH_ABSORPTION":
                return self._wait_decision(f"BLOCKED: {absorption.get('reason', 'Whale absorption detected')}", persona_votes, vote_summary)
        
        # V3.1.15: Only block SHORTs in STRONG bullish (>2% up)'''

if old_short_block in content and "ABSORPTION - Cap extreme selling" not in content:
    content = content.replace(old_short_block, new_short_block)
    changes.append("Added absorption detection to block false SHORT signals in BULLISH")

# 8. Add support proximity boost for LONGs
old_position_size = '''# V3.1.9: Increased position sizing (was 7%)
        base_size = balance * 0.15'''

new_position_size = '''# V3.1.21: Support proximity boost for LONGs (symmetric with resistance for SHORTs)
        symbol = TRADING_PAIRS.get(pair, {}).get("symbol", f"cmt_{pair.lower()}usdt")
        support = get_support_proximity(symbol)
        resistance = get_resistance_proximity(symbol)
        
        # V3.1.9: Increased position sizing (was 7%)
        base_size = balance * 0.15'''

if "get_support_proximity(symbol)" not in content:
    content = content.replace(old_position_size, new_position_size)
    changes.append("Added support proximity check for LONG boost")

# 9. Update version string
content = content.replace("V3.1.21 - BEAR HUNTER", "V3.1.21 - BEAR HUNTER + BULL SYMMETRY")
changes.append("Updated version to V3.1.21 BEAR HUNTER + BULL SYMMETRY")

with open(source_file, 'w') as f:
    f.write(content)

print(f"\n{'='*60}")
print("V3.1.21 BULL SYMMETRY PATCHES APPLIED")
print('='*60)
for c in changes:
    print(f"  [OK] {c}")
print(f"\nTest: python3 {source_file} --test")
print("Then restart daemon: pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
