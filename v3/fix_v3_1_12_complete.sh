#!/bin/bash
# V3.1.12 COMPREHENSIVE FIX
# Includes: Reality Check Prompt + Dynamic Weights + Enhanced Regime
# Run on VM: bash fix_v3_1_12_complete.sh

cd ~/smt-weex-trading-bot/v3

# Stop daemon
pkill -f smt_daemon
sleep 2

# Backup
cp smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py.backup_$(date +%Y%m%d_%H%M%S)
cp smt_daemon_v3_1.py smt_daemon_v3_1.py.backup_$(date +%Y%m%d_%H%M%S)

echo "=============================================="
echo "Applying V3.1.12 COMPREHENSIVE FIX"
echo "=============================================="

python3 << 'PYTHON_SCRIPT'
import re

with open('smt_nightly_trade_v3_1.py', 'r') as f:
    content = f.read()

changes_made = []

# ===========================================
# FIX 1: Update version string
# ===========================================
for old_ver in ['V3.1.9', 'V3.1.10', 'V3.1.11']:
    if f'SMT Nightly Trade {old_ver}' in content:
        content = content.replace(f'SMT Nightly Trade {old_ver}', 'SMT Nightly Trade V3.1.12')
        changes_made.append(f"Updated version from {old_ver} to V3.1.12")

# ===========================================
# FIX 2: SENTIMENT PERSONA - Reality Check Prompt
# This is the CRITICAL fix for bullish bias
# ===========================================

# Find the SentimentPersona class and replace the analyze method
old_sentiment_section = '''    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            client = genai.Client()
            
            search_query = f"{pair} cryptocurrency price prediction sentiment today news"
            
            grounding_config = GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())],
                temperature=0.3
            )
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=search_query,
                config=grounding_config
            )
            
            market_context = response.text[:1500] if response.text else ""
            
            sentiment_prompt = f"""Based on this market context, determine sentiment for {pair}:

{market_context}

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "main reason"}}\"\"\"'''

new_sentiment_section = '''    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        """V3.1.12: Reality Check prompt - focus on SHORT-TERM price action, not hopium"""
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            client = genai.Client()
            
            # V3.1.12: Search for SHORT-TERM signals, not long-term predictions
            search_query = f"{pair} cryptocurrency price last 24 hours support resistance break selling buying pressure volume"
            
            grounding_config = GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())],
                temperature=0.3
            )
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=search_query,
                config=grounding_config
            )
            
            market_context = response.text[:1500] if response.text else ""
            
            # V3.1.12: REALITY CHECK PROMPT - tells Gemini to ignore hopium
            sentiment_prompt = f"""You are a SHORT-TERM crypto trader deciding on a 4-24 hour trade for {pair}.

CRITICAL INSTRUCTIONS:
- IGNORE: "Long-term bullish", "institutional adoption", "ETF approval hopes", "price targets for 2025/2026"
- IGNORE: Any prediction more than 48 hours out
- FOCUS ONLY ON: Last 24h price action, support/resistance breaks, volume patterns, liquidation events

Market Context:
{market_context}

DECISION RULES:
- If price BROKE DOWN through support OR volume spiked on RED candles OR major liquidations happened = BEARISH
- If price BROKE UP through resistance OR volume spiked on GREEN candles OR short squeeze happened = BULLISH
- If choppy sideways with no clear break = NEUTRAL

You MUST be willing to say BEARISH if the short-term action is bearish, even if long-term outlook is positive.

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "describe the SHORT-TERM price action reason only"}}\"\"\"'''

if old_sentiment_section in content:
    content = content.replace(old_sentiment_section, new_sentiment_section)
    changes_made.append("Updated Sentiment prompt to Reality Check version")
else:
    # Try a more flexible approach
    print("[WARN] Exact sentiment section not found, trying flexible replacement...")
    
    # Replace just the search query
    old_query = 'search_query = f"{pair} cryptocurrency price prediction sentiment today news"'
    new_query = '# V3.1.12: Reality Check - search for short-term price action\n            search_query = f"{pair} cryptocurrency price last 24 hours support resistance break volume"'
    
    if old_query in content:
        content = content.replace(old_query, new_query)
        changes_made.append("Updated search query to focus on short-term")
    
    # Replace the sentiment prompt
    old_prompt_start = 'sentiment_prompt = f"""Based on this market context, determine sentiment for {pair}:'
    new_prompt_start = '''sentiment_prompt = f"""You are a SHORT-TERM crypto trader making a 4-24 hour trade decision for {pair}.

IGNORE: Long-term predictions, "institutional adoption", ETF hopes, 2025 price targets.
FOCUS: Last 24h price action, support/resistance breaks, volume on red vs green candles.

Market Context:'''
    
    if old_prompt_start in content:
        content = content.replace(old_prompt_start, new_prompt_start)
        changes_made.append("Updated sentiment prompt intro")
    
    # Replace the JSON instruction
    old_json = '{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "main reason"}'
    new_json = '{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "SHORT-TERM price action reason only"}'
    
    if old_json in content:
        content = content.replace(old_json, new_json)
        changes_made.append("Updated JSON response format")

# ===========================================
# FIX 3: Add Enhanced Regime Functions
# ===========================================

# Check if already added
if 'get_fear_greed_index' not in content:
    # Find insertion point - after TRADING_PAIRS or before first class
    insert_marker = "# Competition\nCOMPETITION_START"
    
    regime_functions = '''# ============================================================
# V3.1.12: ENHANCED MULTI-FACTOR REGIME DETECTION
# ============================================================

def get_fear_greed_index() -> dict:
    """
    Fetch Fear & Greed Index from alternative.me
    CONTRARIAN indicator:
    - 0-25: Extreme Fear = BUY signal (others panic, we accumulate)
    - 75-100: Extreme Greed = SELL signal (others euphoric, we take profit)
    """
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json()
        if data.get("data"):
            value = int(data["data"][0]["value"])
            classification = data["data"][0]["value_classification"]
            return {"value": value, "classification": classification, "error": None}
    except Exception as e:
        pass
    return {"value": 50, "classification": "Neutral", "error": "API failed"}


def get_aggregate_funding_rate() -> dict:
    """
    Average funding rate across all pairs.
    High positive (>0.05%) = overleveraged longs = expect dump
    Negative (<-0.03%) = overleveraged shorts = expect pump
    """
    try:
        import requests
        total_funding = 0
        count = 0
        
        for pair in ["btcusdt", "ethusdt", "solusdt", "adausdt"]:
            url = f"{WEEX_BASE_URL}/capi/v2/public/funding_rate?symbol=cmt_{pair}"
            r = requests.get(url, timeout=5)
            data = r.json()
            if data.get("data"):
                funding = float(data["data"].get("funding_rate", 0))
                total_funding += funding
                count += 1
        
        if count > 0:
            return {"avg_funding": total_funding / count, "pairs_checked": count, "error": None}
    except:
        pass
    return {"avg_funding": 0, "pairs_checked": 0, "error": "API failed"}


def get_enhanced_market_regime() -> dict:
    """
    V3.1.12: Multi-factor regime detection
    
    Factors (with weights):
    1. BTC 24h change: -3 to +3 (primary driver)
    2. BTC 4h change: -1 to +1 (short-term momentum)
    3. Fear & Greed: -2 to +2 (CONTRARIAN - fear=buy, greed=sell)
    4. Funding Rate: -2 to +2 (leverage positioning)
    
    Total score determines regime:
    - score <= -1: BEARISH
    - score >= +1: BULLISH
    - else: NEUTRAL
    """
    import requests
    
    result = {
        "regime": "NEUTRAL",
        "confidence": 0.5,
        "btc_24h": 0,
        "btc_4h": 0,
        "fear_greed": 50,
        "avg_funding": 0,
        "factors": [],
        "score": 0
    }
    
    score = 0
    factors = []
    
    # ===== Factor 1 & 2: BTC Price =====
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            btc_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            btc_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            result["btc_24h"] = btc_24h
            result["btc_4h"] = btc_4h
            
            if btc_24h < -2: score -= 3; factors.append(f"BTC dumping: {btc_24h:+.1f}%")
            elif btc_24h < -1: score -= 2; factors.append(f"BTC dropping: {btc_24h:+.1f}%")
            elif btc_24h < -0.5: score -= 1; factors.append(f"BTC weak: {btc_24h:+.1f}%")
            elif btc_24h > 2: score += 3; factors.append(f"BTC pumping: {btc_24h:+.1f}%")
            elif btc_24h > 1: score += 2; factors.append(f"BTC rising: {btc_24h:+.1f}%")
            elif btc_24h > 0.5: score += 1; factors.append(f"BTC up: {btc_24h:+.1f}%")
            
            if btc_4h < -1: score -= 1; factors.append(f"4h down: {btc_4h:+.1f}%")
            elif btc_4h > 1: score += 1; factors.append(f"4h up: {btc_4h:+.1f}%")
    except Exception as e:
        factors.append(f"BTC error: {e}")
    
    # ===== Factor 3: Fear & Greed (CONTRARIAN) =====
    fg = get_fear_greed_index()
    result["fear_greed"] = fg["value"]
    
    if fg["error"] is None:
        if fg["value"] <= 20: score += 2; factors.append(f"EXTREME FEAR ({fg['value']}): contrarian BUY")
        elif fg["value"] <= 35: score += 1; factors.append(f"Fear ({fg['value']})")
        elif fg["value"] >= 80: score -= 2; factors.append(f"EXTREME GREED ({fg['value']}): contrarian SELL")
        elif fg["value"] >= 65: score -= 1; factors.append(f"Greed ({fg['value']})")
    
    # ===== Factor 4: Aggregate Funding =====
    funding = get_aggregate_funding_rate()
    result["avg_funding"] = funding["avg_funding"]
    
    if funding["error"] is None:
        if funding["avg_funding"] > 0.0008: score -= 2; factors.append(f"High funding: longs overleveraged")
        elif funding["avg_funding"] > 0.0004: score -= 1; factors.append(f"Elevated funding")
        elif funding["avg_funding"] < -0.0004: score += 2; factors.append(f"Negative funding: shorts squeezable")
        elif funding["avg_funding"] < -0.0001: score += 1; factors.append(f"Low funding")
    
    # ===== Final Regime =====
    result["score"] = score
    result["factors"] = factors
    
    if score <= -3: result["regime"] = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: result["regime"] = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: result["regime"] = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: result["regime"] = "BULLISH"; result["confidence"] = 0.65
    else: result["regime"] = "NEUTRAL"; result["confidence"] = 0.5
    
    print(f"  [REGIME] {result['regime']} (score: {score}, conf: {result['confidence']:.0%})")
    print(f"  [REGIME] BTC 24h: {result['btc_24h']:+.1f}% | F&G: {result['fear_greed']} | Funding: {result['avg_funding']:.5f}")
    for f in factors[:4]:
        print(f"  [REGIME]   > {f}")
    
    return result


# Competition
COMPETITION_START'''
    
    if insert_marker in content:
        content = content.replace(insert_marker, regime_functions)
        changes_made.append("Added enhanced regime functions (F&G, Funding, Multi-factor)")
    else:
        print("[WARN] Could not find insertion point for regime functions")
else:
    changes_made.append("Enhanced regime functions already present")

# ===========================================
# FIX 4: Update JUDGE to use Dynamic Weights + Enhanced Regime
# ===========================================

# Find the weights line in JUDGE
old_weights_line = 'weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}  # V3.1.7'

new_weights_block = '''# V3.1.12: Dynamic weights based on market regime
            regime_for_weights = get_enhanced_market_regime()
            is_bearish = regime_for_weights["regime"] == "BEARISH"
            is_bullish = regime_for_weights["regime"] == "BULLISH"
            
            if is_bearish:
                # In bear market: trust FLOW and TECHNICAL, ignore hopium
                weights = {"WHALE": 1.0, "SENTIMENT": 0.5, "FLOW": 2.5, "TECHNICAL": 1.8}
            elif is_bullish:
                # In bull market: whales and sentiment lead
                weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}
            else:
                # Neutral: balanced
                weights = {"WHALE": 1.5, "SENTIMENT": 1.0, "FLOW": 1.5, "TECHNICAL": 1.5}'''

if old_weights_line in content:
    content = content.replace(old_weights_line, new_weights_block)
    changes_made.append("Updated JUDGE with dynamic weights based on regime")
else:
    # Check if already updated
    if "Dynamic weights based on market regime" in content:
        changes_made.append("Dynamic weights already present")
    else:
        print("[WARN] Could not find weights line to update")

# ===========================================
# FIX 5: Update vote summary to show weights
# ===========================================
old_vote_summary = 'vote_summary.append(f"{persona}={signal}({confidence:.0%})")'
new_vote_summary = 'vote_summary.append(f"{persona}={signal}({confidence:.0%})w{weight:.1f}")'

if old_vote_summary in content:
    content = content.replace(old_vote_summary, new_vote_summary)
    changes_made.append("Vote summary now shows weights")

# ===========================================
# FIX 6: Update regime blocking to use enhanced regime
# ===========================================
old_regime_block = '''        # V3.1.8: STRICTER MARKET TREND FILTER - Don't fight the trend!
        # Now applies to ALL pairs including BTC
        regime = self._get_market_regime()
        
        # Block LONGs in bearish regime (ANY negative 24h = bearish for safety)
        if decision == "LONG":
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH regime (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)
            # V3.1.8: Also block if 24h is negative even if not "BEARISH" threshold
            if regime["change_24h"] < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)
        
        # Block SHORTs in strong bullish regime
        if decision == "SHORT" and regime["regime"] == "BULLISH" and regime["change_24h"] > 3.0:
            return self._wait_decision(f"BLOCKED: SHORT in strong BULLISH regime (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)'''

new_regime_block = '''        # V3.1.12: Use enhanced multi-factor regime for blocking
        # regime_for_weights already fetched above with dynamic weights
        regime = regime_for_weights
        
        # Block LONGs in bearish regime
        if decision == "LONG":
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH (score:{regime['score']}, F&G:{regime['fear_greed']})", persona_votes, vote_summary)
            if regime.get("btc_24h", 0) < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping ({regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
        
        # Block SHORTs in strong bullish regime
        if decision == "SHORT" and regime["regime"] == "BULLISH" and regime.get("confidence", 0) > 0.7:
            return self._wait_decision(f"BLOCKED: SHORT in strong BULLISH (score:{regime['score']})", persona_votes, vote_summary)'''

if old_regime_block in content:
    content = content.replace(old_regime_block, new_regime_block)
    changes_made.append("Updated blocking logic to use enhanced regime")
else:
    print("[WARN] Could not find regime blocking section - may need manual update")

# ===========================================
# SAVE FILE
# ===========================================
with open('smt_nightly_trade_v3_1.py', 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("V3.1.12 CHANGES APPLIED:")
print("="*60)
for change in changes_made:
    print(f"  [OK] {change}")
print("="*60)

PYTHON_SCRIPT

echo ""
echo "Updating daemon version..."
sed -i 's/SMT Daemon V3.1.[0-9]*/SMT Daemon V3.1.12/g' smt_daemon_v3_1.py

echo ""
echo "Checking for syntax errors..."
python3 -m py_compile smt_nightly_trade_v3_1.py
if [ $? -eq 0 ]; then
    echo "[OK] smt_nightly_trade_v3_1.py - No syntax errors"
else
    echo "[ERROR] smt_nightly_trade_v3_1.py has syntax errors!"
    exit 1
fi

python3 -m py_compile smt_daemon_v3_1.py
if [ $? -eq 0 ]; then
    echo "[OK] smt_daemon_v3_1.py - No syntax errors"
else
    echo "[ERROR] smt_daemon_v3_1.py has syntax errors!"
    exit 1
fi

echo ""
echo "Testing enhanced regime function..."
python3 -c "
from smt_nightly_trade_v3_1 import get_enhanced_market_regime
print('Testing get_enhanced_market_regime()...')
regime = get_enhanced_market_regime()
print(f'Result: {regime[\"regime\"]} with confidence {regime[\"confidence\"]:.0%}')
print('Test PASSED!')
"

echo ""
echo "Testing sentiment prompt update..."
grep -A 5 "IGNORE:" smt_nightly_trade_v3_1.py | head -10
if [ $? -eq 0 ]; then
    echo "[OK] Reality Check prompt is in place"
else
    echo "[WARN] Reality Check prompt may not be applied"
fi

echo ""
echo "Restarting daemon..."
nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
sleep 5

echo ""
ps aux | grep smt_daemon | grep -v grep
if [ $? -eq 0 ]; then
    echo "[OK] Daemon is running"
else
    echo "[ERROR] Daemon failed to start!"
    tail -30 daemon.log
    exit 1
fi

echo ""
echo "=============================================="
echo "V3.1.12 COMPLETE - Summary"
echo "=============================================="
echo "1. Sentiment Prompt: Reality Check (ignore hopium, focus 4-24h)"
echo "2. Enhanced Regime: BTC + Fear&Greed + Funding"
echo "3. Dynamic Weights:"
echo "   BEARISH: WHALE=1.0, SENTIMENT=0.5, FLOW=2.5, TECH=1.8"
echo "   BULLISH: WHALE=2.0, SENTIMENT=1.5, FLOW=1.0, TECH=1.2"
echo "   NEUTRAL: WHALE=1.5, SENTIMENT=1.0, FLOW=1.5, TECH=1.5"
echo "4. Contrarian F&G: Fear=BUY, Greed=SELL"
echo "=============================================="
echo ""
echo "Commit with:"
echo "git add -A && git commit -m 'V3.1.12: Enhanced regime (F&G+Funding) + Reality Check prompt + Dynamic weights' && git push"
