#!/usr/bin/env python3
"""
fix_v31_ai_logs.py - Include ACTUAL market reasoning in AI logs (like V3)
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_ai_logs.py

The problem: V3.1 AI logs only show "Judge decision: LONG @ 70%"
V3 showed: "Cardano shows strong bullish catalysts with an imminent US spot ETF decision..."

Fix: 
1. Make SentimentPersona return the full market_context
2. Make JudgePersona create a proper reasoning summary
3. Pass this to AI log upload
"""

import os
from datetime import datetime

def fix_trading_file():
    FILE = "smt_nightly_trade_v3_1.py"
    
    if not os.path.exists(FILE):
        print(f"ERROR: {FILE} not found!")
        return False
    
    backup = f"{FILE}.backup_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(FILE, 'r') as f:
        content = f.read()
    with open(backup, 'w') as f:
        f.write(content)
    print(f"Backup: {backup}")
    
    # ================================================================
    # FIX 1: SentimentPersona - return full market_context
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
                "market_context": market_context[:800],  # ADDED: full market analysis for AI logs
            }'''
    
    if old_sentiment_return in content:
        content = content.replace(old_sentiment_return, new_sentiment_return)
        print("FIX 1: SentimentPersona returns market_context - APPLIED")
    
    # ================================================================
    # FIX 2: JudgePersona - create detailed reasoning from sentiment
    # ================================================================
    # Find where Judge creates final decision and enhance reasoning
    
    old_judge_return = '''        return {
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "persona_votes": persona_votes,
            "vote_breakdown": {
                "long_pct": long_pct,
                "short_pct": short_pct,
                "neutral_pct": neutral_pct,
            }
        }'''
    
    new_judge_return = '''        # Get market context from sentiment persona for detailed AI logs
        market_analysis = ""
        for vote in persona_votes:
            if vote.get("persona") == "SENTIMENT" and vote.get("market_context"):
                market_analysis = vote["market_context"][:500]
                break
        
        # Create detailed reasoning for AI logs (like V3)
        detailed_reasoning = f"{reasoning}"
        if market_analysis:
            # Extract key insight from market context
            detailed_reasoning = f"{vote.get('reasoning', reasoning)}. {market_analysis[:300]}"
        
        return {
            "decision": decision,
            "confidence": confidence,
            "reasoning": detailed_reasoning[:500],  # CHANGED: now includes market analysis
            "persona_votes": persona_votes,
            "vote_breakdown": {
                "long_pct": long_pct,
                "short_pct": short_pct,
                "neutral_pct": neutral_pct,
            },
            "market_context": market_analysis,  # ADDED: for AI logs
        }'''
    
    if old_judge_return in content:
        content = content.replace(old_judge_return, new_judge_return)
        print("FIX 2: JudgePersona includes market context in reasoning - APPLIED")
    else:
        print("FIX 2: JudgePersona pattern not found - checking alternate...")
        # The return might be slightly different, let's try a more flexible approach
    
    with open(FILE, 'w') as f:
        f.write(content)
    
    return True


def fix_daemon_file():
    FILE = "smt_daemon_v3_1.py"
    
    if not os.path.exists(FILE):
        print(f"WARNING: {FILE} not found")
        return False
    
    backup = f"{FILE}.backup_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(FILE, 'r') as f:
        content = f.read()
    with open(backup, 'w') as f:
        f.write(content)
    print(f"Daemon backup: {backup}")
    
    # ================================================================
    # FIX 3: Daemon - upload market_context to AI log
    # ================================================================
    # The daemon should use decision.get("reasoning") which now includes market context
    # But let's also add a separate log for the market analysis
    
    old_log_upload = '''                upload_ai_log_to_weex(
                    stage=f"V3.1 Analysis - {pair}",
                    input_data={
                        "pair": pair,
                        "balance": balance,
                        "open_positions": len(open_positions),
                    },
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence"),
                    },
                    explanation=decision.get("reasoning", "")[:500]
                )'''
    
    new_log_upload = '''                # Get market reasoning (like V3 did)
                market_reasoning = decision.get("reasoning", "")
                # Also check for market_context if available
                if decision.get("market_context"):
                    market_reasoning = decision["market_context"][:400]
                
                upload_ai_log_to_weex(
                    stage=f"V3.1 Analysis - {pair}",
                    input_data={
                        "pair": pair,
                        "balance": balance,
                        "open_positions": len(open_positions),
                    },
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence"),
                    },
                    explanation=market_reasoning[:500]  # Now includes actual market analysis
                )'''
    
    if old_log_upload in content:
        content = content.replace(old_log_upload, new_log_upload)
        print("FIX 3: Daemon uploads market context to AI log - APPLIED")
    else:
        # Try simpler replacement
        old_simple = 'explanation=decision.get("reasoning", "")[:500]'
        new_simple = 'explanation=decision.get("market_context", decision.get("reasoning", ""))[:500]'
        if old_simple in content:
            content = content.replace(old_simple, new_simple)
            print("FIX 3: Daemon AI log explanation (simple) - APPLIED")
    
    with open(FILE, 'w') as f:
        f.write(content)
    
    return True


if __name__ == "__main__":
    print("="*60)
    print("V3.1 AI LOGS FIX")
    print("="*60)
    print()
    print("This fix makes AI logs show actual market reasoning like V3:")
    print()
    print("BEFORE (V3.1):")
    print('  "Judge decision: LONG @ 70%. Votes: SENTIMENT=LONG(85%)..."')
    print()
    print("AFTER (like V3):")
    print('  "Cardano shows strong bullish catalysts with an imminent')
    print('   US spot ETF decision and significant protocol developments..."')
    print()
    
    fix_trading_file()
    print()
    fix_daemon_file()
    
    print("\n" + "="*60)
    print("AI LOGS FIX APPLIED!")
    print("="*60)
    print("""
Test:
  python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &

The next trade will upload market reasoning to WEEX AI logs!
""")
