#!/usr/bin/env python3
"""
fix_v31_market_logs_v2.py - Fix the broken method + include BOTH judge summary AND market analysis
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_market_logs_v2.py
"""

import os
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

with open(FILE, 'r') as f:
    content = f.read()

backup = f"{FILE}.backup_mktlog2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(backup, 'w') as f:
    f.write(content)
print(f"Backup: {backup}")

# ================================================================
# FIX 1: Remove the broken method call and use simple approach
# ================================================================

# Replace the broken line with a simple approach that includes BOTH
broken_line = '"reasoning": self._get_market_reasoning(persona_votes, decision, confidence, vote_summary),'

simple_reasoning = '''"reasoning": f"Judge decision: {decision} @ {confidence:.0%}. Votes: {', '.join(vote_summary)}",
            "persona_votes": persona_votes,  # Include votes so daemon can extract market_context'''

if broken_line in content:
    content = content.replace(broken_line, simple_reasoning)
    print("FIX 1: Removed broken method call")

# Also remove the broken method if it was partially added
if '_get_market_reasoning' in content and 'def _get_market_reasoning' in content:
    # Remove the method
    import re
    content = re.sub(r'    def _get_market_reasoning\(self.*?(?=    def _wait_decision)', '', content, flags=re.DOTALL)
    print("FIX 1b: Removed broken method definition")

# ================================================================
# FIX 2: Update daemon to include BOTH in explanation
# ================================================================
DAEMON = "smt_daemon_v3_1.py"

with open(DAEMON, 'r') as f:
    dcontent = f.read()

dbackup = f"{DAEMON}.backup_mktlog2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(dbackup, 'w') as f:
    f.write(dcontent)
print(f"Daemon backup: {dbackup}")

# Find the upload_ai_log_to_weex call and fix the explanation
# We want BOTH: Judge summary + Market context

old_upload = '''                upload_ai_log_to_weex(
                    stage=f"V3.1 Analysis - {pair}",
                    input_data={
                        "pair": pair,
                        "balance": balance,
                        "personas": ["WHALE", "SENTIMENT", "FLOW", "TECHNICAL"],
                    },
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence", 0),
                        "votes": decision.get("vote_breakdown", {}),
                    },
                    explanation=decision.get("market_context", decision.get("reasoning", ""))[:500]
                )'''

new_upload = '''                # Build explanation with BOTH judge summary AND market context
                judge_summary = decision.get("reasoning", "")
                market_ctx = ""
                for vote in decision.get("persona_votes", []):
                    if vote.get("persona") == "SENTIMENT" and vote.get("market_context"):
                        market_ctx = vote.get("market_context", "")[:300]
                        break
                
                full_explanation = f"{judge_summary} | Market: {market_ctx}" if market_ctx else judge_summary
                
                upload_ai_log_to_weex(
                    stage=f"V3.1 Analysis - {pair}",
                    input_data={
                        "pair": pair,
                        "balance": balance,
                        "personas": ["WHALE", "SENTIMENT", "FLOW", "TECHNICAL"],
                    },
                    output_data={
                        "decision": decision.get("decision"),
                        "confidence": decision.get("confidence", 0),
                        "votes": decision.get("vote_breakdown", {}),
                    },
                    explanation=full_explanation[:500]
                )'''

if old_upload in dcontent:
    dcontent = dcontent.replace(old_upload, new_upload)
    print("FIX 2: Daemon explanation includes BOTH - APPLIED")
else:
    # Try simpler fix
    old_simple = 'explanation=decision.get("market_context", decision.get("reasoning", ""))[:500]'
    new_simple = '''explanation=f"{decision.get('reasoning', '')} | {next((v.get('market_context', '')[:200] for v in decision.get('persona_votes', []) if v.get('persona') == 'SENTIMENT'), '')}"[:500]'''
    
    if old_simple in dcontent:
        dcontent = dcontent.replace(old_simple, new_simple)
        print("FIX 2: Daemon explanation (simple) - APPLIED")
    else:
        print("FIX 2: Could not find upload pattern - trying another approach")
        # Just ensure we have the right explanation line
        dcontent = dcontent.replace(
            'explanation=decision.get("reasoning", "")[:500]',
            '''explanation=f"{decision.get('reasoning', '')} | Market: {next((v.get('market_context', '')[:250] for v in decision.get('persona_votes', []) if v.get('persona') == 'SENTIMENT' and v.get('market_context')), '')}"[:500]'''
        )
        print("FIX 2: Updated explanation line")

with open(FILE, 'w') as f:
    f.write(content)

with open(DAEMON, 'w') as f:
    f.write(dcontent)

print("\n" + "="*50)
print("FIX APPLIED!")
print("="*50)
print("""
AI logs will now show BOTH:
"Judge decision: LONG @ 77%. Votes: SENTIMENT=LONG(90%)... | Market: Bitcoin ETF inflows increased..."

Test:
  python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"
  python3 -c "import smt_daemon_v3_1; print('OK')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
""")
