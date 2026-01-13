#!/usr/bin/env python3
"""
fix_v31_market_logs.py - Include Gemini market analysis in AI logs
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_market_logs.py

Current: "Judge decision: LONG @ 77%. Votes: SENTIMENT=LONG(90%)..."
Want: "BTC showing bullish momentum with ETF inflows increasing..."
"""

import os
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    content = f.read()

backup = f"{FILE}.backup_mktlog_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(backup, 'w') as f:
    f.write(content)
print(f"Backup: {backup}")

# ================================================================
# FIX 1: Make SentimentPersona return market_context
# ================================================================
old_sentiment_return = '''            return {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
            }'''

new_sentiment_return = '''            return {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:600],  # Full Gemini analysis for AI logs
            }'''

if old_sentiment_return in content:
    content = content.replace(old_sentiment_return, new_sentiment_return)
    print("FIX 1: SentimentPersona returns market_context - APPLIED")
else:
    print("FIX 1: Pattern not found (may already have market_context)")

# ================================================================
# FIX 2: Make JudgePersona include market_context in reasoning
# ================================================================
old_judge_reasoning = '''"reasoning": f"Judge decision: {decision} @ {confidence:.0%}. Votes: {', '.join(vote_summary)}",'''

new_judge_reasoning = '''"reasoning": self._get_market_reasoning(persona_votes, decision, confidence, vote_summary),'''

if old_judge_reasoning in content:
    content = content.replace(old_judge_reasoning, new_judge_reasoning)
    print("FIX 2: JudgePersona reasoning updated - APPLIED")

# ================================================================
# FIX 3: Add helper method to JudgePersona class
# ================================================================
# Find the _wait_decision method and add new method before it

old_wait = '''    def _wait_decision(self, reason: str) -> Dict:'''

new_methods = '''    def _get_market_reasoning(self, persona_votes, decision, confidence, vote_summary):
        """Get market context from sentiment persona for AI logs"""
        # Find sentiment persona's market context
        market_ctx = ""
        sentiment_reason = ""
        for vote in persona_votes:
            if vote.get("persona") == "SENTIMENT":
                market_ctx = vote.get("market_context", "")[:400]
                sentiment_reason = vote.get("reasoning", "")
                break
        
        # Build reasoning like V3 did
        if market_ctx:
            return f"{sentiment_reason}. {market_ctx}"[:500]
        else:
            return f"Judge decision: {decision} @ {confidence:.0%}. Votes: {', '.join(vote_summary)}"
    
    def _wait_decision(self, reason: str) -> Dict:'''

if old_wait in content and '_get_market_reasoning' not in content:
    content = content.replace(old_wait, new_methods)
    print("FIX 3: Added _get_market_reasoning method - APPLIED")

with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*50)
print("MARKET LOGS FIX APPLIED!")
print("="*50)
print("""
Now AI logs will show actual market analysis like:
"BTC showing bullish momentum. Bitcoin ETF inflows reached 
$500M this week, institutional interest growing..."

Instead of:
"Judge decision: LONG @ 77%. Votes: SENTIMENT=LONG(90%)..."

Test:
  python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
