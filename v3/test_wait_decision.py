#!/usr/bin/env python3
"""
Test to verify _wait_decision returns persona_votes
Run this on VM to check if the fix is deployed
"""

from smt_nightly_trade_v3_1 import JudgePersona

# Create test votes
test_votes = [
    {"persona": "WHALE", "signal": "NEUTRAL", "confidence": 0.4, "reasoning": "Balanced whale activity"},
    {"persona": "SENTIMENT", "signal": "LONG", "confidence": 0.9, "reasoning": "Bullish market sentiment"},
    {"persona": "FLOW", "signal": "SHORT", "confidence": 0.75, "reasoning": "Strong ask depth"},
    {"persona": "TECHNICAL", "signal": "SHORT", "confidence": 0.7, "reasoning": "RSI overbought"},
]

judge = JudgePersona()

# Simulate a WAIT decision scenario
print("Testing _wait_decision with persona_votes...")
print("=" * 50)

# Build vote_summary like the real code does
vote_summary = []
for vote in test_votes:
    vote_summary.append(f"{vote['persona']}={vote['signal']}({vote['confidence']:.0%})")

print(f"Vote summary: {vote_summary}")

# Call _wait_decision
result = judge._wait_decision(
    reason="Confidence too low: 62% (need 65%)",
    persona_votes=test_votes,
    vote_summary=vote_summary
)

print(f"\nResult:")
print(f"  decision: {result.get('decision')}")
print(f"  reasoning: {result.get('reasoning')}")
print(f"  persona_votes count: {len(result.get('persona_votes', []))}")

if result.get('persona_votes'):
    print(f"\n  persona_votes content:")
    for v in result.get('persona_votes', []):
        print(f"    - {v.get('persona')}: {v.get('signal')} ({v.get('confidence'):.0%})")
    print("\n[SUCCESS] _wait_decision returns persona_votes!")
else:
    print("\n[FAILED] persona_votes is EMPTY - OLD FILE DEPLOYED!")
    print("You need to update smt_nightly_trade_v3_1.py on VM")
