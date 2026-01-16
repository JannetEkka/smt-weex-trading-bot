#!/bin/bash
# V3.1.11 - Dynamic Weights + Better Sentiment Prompt
# Fixes the bullish bias in bearish markets
# Run on VM: bash fix_v3_1_11.sh

cd ~/smt-weex-trading-bot/v3

# Stop daemon
pkill -f smt_daemon
sleep 2

# Backup
cp smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py.backup_v3_1_10

echo "Applying V3.1.11 - Dynamic Weights + Reality Check Prompt..."

python3 << 'PYTHON_SCRIPT'
import re

with open('smt_nightly_trade_v3_1.py', 'r') as f:
    content = f.read()

# ===========================================
# FIX 1: Update version
# ===========================================
content = content.replace(
    'SMT Nightly Trade V3.1.10',
    'SMT Nightly Trade V3.1.11'
)

# ===========================================
# FIX 2: Update Sentiment Prompt - "Reality Check"
# ===========================================
old_sentiment_prompt = '''            search_query = f"{pair} cryptocurrency price prediction sentiment today news"
            
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
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "main reason"}}"""'''

new_sentiment_prompt = '''            # V3.1.11: Reality Check prompt - focus on SHORT-TERM price action, not "moon" news
            search_query = f"{pair} cryptocurrency price action last 24 hours breaking support resistance selling pressure"
            
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
            
            # V3.1.11: Reality Check - ignore long-term hopium, focus on 4-24h trading window
            sentiment_prompt = f"""You are a SHORT-TERM crypto trader making a 4-24 hour trade decision for {pair}.

IGNORE: Long-term "moon" predictions, "institutional adoption", "ETF hopes", price targets for next year.
FOCUS ON: Last 24 hours price action, support/resistance breaks, volume on red vs green candles, liquidation data.

Market Context:
{market_context}

Based ONLY on short-term price action and momentum:
- If price is breaking DOWN through support or volume is spiking on RED candles = BEARISH
- If price is breaking UP through resistance or volume is spiking on GREEN candles = BULLISH  
- If choppy/sideways with no clear direction = NEUTRAL

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "short-term reason only"}}"""'''

if old_sentiment_prompt in content:
    content = content.replace(old_sentiment_prompt, new_sentiment_prompt)
    print("[OK] Updated sentiment prompt to Reality Check version")
else:
    print("[WARN] Could not find sentiment prompt - manual edit needed")

# ===========================================
# FIX 3: Dynamic Weights in JUDGE based on Regime
# ===========================================
old_weights = '''        for vote in persona_votes:
            persona = vote["persona"]
            signal = vote["signal"]
            confidence = vote["confidence"]
            
            weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}  # V3.1.7
            weight = weights.get(persona, 1.0)'''

new_weights = '''        # V3.1.11: Get regime FIRST for dynamic weight adjustment
        regime = self._get_market_regime()
        is_bearish = regime["regime"] == "BEARISH" or regime["change_24h"] < -0.5
        is_bullish = regime["regime"] == "BULLISH" or regime["change_24h"] > 1.0
        
        for vote in persona_votes:
            persona = vote["persona"]
            signal = vote["signal"]
            confidence = vote["confidence"]
            
            # V3.1.11: DYNAMIC WEIGHTS based on market regime
            # In bearish markets: trust FLOW and TECHNICAL more, SENTIMENT less
            # In bullish markets: normal weights
            if is_bearish:
                weights = {
                    "WHALE": 1.0,      # Was 2.0 - accumulation can be "catching falling knife"
                    "SENTIMENT": 0.5,  # Was 1.5 - news is always bullish hopium
                    "FLOW": 2.5,       # Was 1.0 - order flow is truth in panic
                    "TECHNICAL": 1.8   # Was 1.2 - trends matter in downtrends
                }
            elif is_bullish:
                weights = {
                    "WHALE": 2.0,      # Whales lead the way up
                    "SENTIMENT": 1.5,  # News drives FOMO
                    "FLOW": 1.0,       # Normal
                    "TECHNICAL": 1.2   # Normal
                }
            else:  # NEUTRAL
                weights = {
                    "WHALE": 1.5,      # Slightly reduced
                    "SENTIMENT": 1.0,  # Reduced - noise in chop
                    "FLOW": 1.5,       # Increased - flow matters in chop
                    "TECHNICAL": 1.5   # Increased - technicals guide in ranges
                }
            
            weight = weights.get(persona, 1.0)'''

if old_weights in content:
    content = content.replace(old_weights, new_weights)
    print("[OK] Updated JUDGE to use dynamic weights based on regime")
else:
    print("[WARN] Could not find weights section - manual edit needed")

# ===========================================
# FIX 4: Remove duplicate regime call (we moved it earlier)
# ===========================================
# The regime is now fetched at the start of decide(), so we need to reuse it
old_regime_call = '''        # V3.1.8: STRICTER MARKET TREND FILTER - Don't fight the trend!
        # Now applies to ALL pairs including BTC
        regime = self._get_market_regime()'''

new_regime_call = '''        # V3.1.8: STRICTER MARKET TREND FILTER - Don't fight the trend!
        # Now applies to ALL pairs including BTC
        # V3.1.11: regime already fetched above for dynamic weights'''

if old_regime_call in content:
    content = content.replace(old_regime_call, new_regime_call)
    print("[OK] Removed duplicate regime call")

# ===========================================
# FIX 5: Add logging for weight adjustment
# ===========================================
old_vote_summary = '''            vote_summary.append(f"{persona}={signal}({confidence:.0%})")'''

new_vote_summary = '''            vote_summary.append(f"{persona}={signal}({confidence:.0%})x{weight:.1f}")'''

if old_vote_summary in content:
    content = content.replace(old_vote_summary, new_vote_summary)
    print("[OK] Updated vote summary to show weights")

# ===========================================
# FIX 6: Improve TECHNICAL persona to be more SHORT-friendly
# ===========================================
old_technical_short = '''            # Momentum
            if momentum > 3:
                signals.append(("LONG", 0.5, f"Strong momentum: +{momentum:.1f}%"))
            elif momentum < -3:
                signals.append(("SHORT", 0.5, f"Weak momentum: {momentum:.1f}%"))'''

new_technical_short = '''            # Momentum - V3.1.11: More sensitive to downward momentum
            if momentum > 3:
                signals.append(("LONG", 0.5, f"Strong momentum: +{momentum:.1f}%"))
            elif momentum < -2:  # Was -3, now -2 for earlier SHORT signals
                signals.append(("SHORT", 0.6, f"Weak momentum: {momentum:.1f}%"))  # Was 0.5, now 0.6'''

if old_technical_short in content:
    content = content.replace(old_technical_short, new_technical_short)
    print("[OK] Made TECHNICAL more sensitive to downward momentum")

# ===========================================
# FIX 7: Add price vs SMA check to TECHNICAL
# ===========================================
old_technical_sma = '''            # SMA trend
            if sma_20 > sma_50 and price > sma_20:
                signals.append(("LONG", 0.4, "Bullish SMA alignment"))
            elif sma_20 < sma_50 and price < sma_20:
                signals.append(("SHORT", 0.4, "Bearish SMA alignment"))'''

new_technical_sma = '''            # SMA trend - V3.1.11: Stronger signals for bearish alignment
            if sma_20 > sma_50 and price > sma_20:
                signals.append(("LONG", 0.4, "Bullish SMA alignment"))
            elif sma_20 < sma_50 and price < sma_20:
                signals.append(("SHORT", 0.5, "Bearish SMA alignment"))  # Was 0.4
            # V3.1.11: Price below both SMAs = strong bearish
            elif price < sma_20 and price < sma_50:
                signals.append(("SHORT", 0.5, f"Price below both SMAs"))'''

if old_technical_sma in content:
    content = content.replace(old_technical_sma, new_technical_sma)
    print("[OK] Enhanced TECHNICAL with stronger bearish SMA signals")

with open('smt_nightly_trade_v3_1.py', 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("V3.1.11 CHANGES APPLIED:")
print("="*60)
print("1. Sentiment prompt: Reality Check (ignore hopium, focus on price action)")
print("2. Dynamic weights based on regime:")
print("   BEARISH: WHALE 1.0, SENTIMENT 0.5, FLOW 2.5, TECHNICAL 1.8")
print("   BULLISH: WHALE 2.0, SENTIMENT 1.5, FLOW 1.0, TECHNICAL 1.2")
print("   NEUTRAL: WHALE 1.5, SENTIMENT 1.0, FLOW 1.5, TECHNICAL 1.5")
print("3. TECHNICAL: More sensitive to downward momentum (-2% vs -3%)")
print("4. TECHNICAL: Stronger SHORT signal when price < both SMAs")
print("5. Vote summary shows weights for debugging")
print("="*60)

PYTHON_SCRIPT

# Also update daemon version string
sed -i 's/SMT Daemon V3.1.10/SMT Daemon V3.1.11/g' smt_daemon_v3_1.py

echo ""
echo "Restarting daemon..."
nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
sleep 3

echo ""
echo "Checking daemon..."
ps aux | grep smt_daemon | grep -v grep

echo ""
echo "Recent log:"
tail -30 daemon.log

echo ""
echo "DONE! V3.1.11 is running."
echo "Commit with: git add -A && git commit -m 'V3.1.11: Dynamic weights + Reality Check prompt' && git push"
