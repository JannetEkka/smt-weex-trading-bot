"""
V3.1.58 - Simplified Judge Prompt
Removes contradictory rules. Trusts code guardrails. Lets Gemini focus on signal quality.
"""

import re

DAEMON_FILE = "smt_nightly_trade_v3_1.py"

# The old prompt starts at "=== RULES" and ends before the JSON response format
OLD_RULES = '''=== RULES (V3.1.45 - learned from 45+ iterations) ===
1. You MUST pick exactly ONE: LONG, SHORT, or WAIT.
2. If we already have BOTH a LONG and SHORT on this pair, return WAIT.
3. If we already have a LONG open and signal is LONG, return WAIT (already positioned).
4. If we already have a SHORT open and signal is SHORT, return WAIT.
5. If we have a losing position and a strong opposite signal (85%+), you CAN recommend the opposite direction (hedge).
6. In EXTREME FEAR (F&G < 20), be cautious with SHORTs near round-number support levels. But do NOT assume fear = buy. If FLOW + SENTIMENT + TECHNICAL all agree on SHORT, the sell-off is real -- SHORT is allowed. Fear can always get worse.
6b. CAPITULATION RULE (F&G < 15): Contrarian LONGs are ONLY valid when FLOW confirms actual buying (taker ratio > 1.5). If FLOW is NEUTRAL or BEARISH, extreme fear does NOT mean buy -- it means the sell-off is continuing. If 3+ personas agree on SHORT with 65%+ average confidence, SHORT is allowed even at F&G < 15. Do not fight confirmed downtrends because fear is high. The best SHORT entries happen when everyone is too scared to short.
7. Negative funding rate = shorts paying longs = bullish. Positive funding + LONG = you pay every 8h, factor this into hold time.
8. Tier awareness: T1 (BTC/ETH/BNB/LTC) = slow, 15x leverage. T2 (SOL) = medium, 12x. T3 (DOGE/XRP/ADA) = fast, 10x.
9. DIRECTIONAL LIMIT: Max 6 positions in the same direction. We have 8 slots. Do NOT self-impose a lower limit.
10. CORRELATED PAIRS: If 4+ correlated LONGs open (BTC/ETH/SOL/DOGE/XRP), require 85%+ to add another.
11. TIME OF DAY: 00-06 UTC Asian session. Require 80%+ confidence (not 85%).
12. POST-REGIME SHIFT: If regime just changed, still trade if confidence >= 80%. Speed matters.
13. For TP/SL: SL 2-2.5%. TP 6-8% (set by tier config, NOT by you). Max hold: T1=72h, T2=48h, T3=24h. Let winners RUN to full TP. We need $200-400 wins to recover. TP/SL are handled by tier config -- focus your decision on DIRECTION only.
14. RECOVERY MODE: We are DOWN from starting balance. We CANNOT afford to WAIT on good setups. If 2+ personas agree on direction with 70%+ average confidence, TAKE THE TRADE. Playing safe = guaranteed last place.
15. SWAP MENTALITY: If we are full on slots, STILL say LONG or SHORT if the signal is strong (70%+). The daemon handles slot management. NEVER return WAIT just because slots are full. Your job is SIGNAL QUALITY, not position management.
16. WHALE DATA QUALITY: For BTC/ETH, WHALE uses on-chain Etherscan data (most reliable - actual wallet flows). For other pairs (SOL/DOGE/XRP/ADA/BNB/LTC), WHALE uses Cryptoracle community sentiment (social signals from Twitter/Telegram/Discord). Etherscan > Cryptoracle in reliability. If WHALE and FLOW disagree on altcoins, trust FLOW (order book data > social data).
17. SMART MONEY DIVERGENCE: If WHALE shows bullish but FLOW shows extreme selling, this is BULLISH ABSORPTION (whales/community buying the dip) - high conviction LONG. If WHALE shows bearish but FLOW shows buying, this is DISTRIBUTION INTO STRENGTH (smart money selling into FOMO) - caution on LONGs.
18. SENTIMENT MOMENTUM: If WHALE reports sentiment momentum z-score > 1.5, community is overheated (contrarian SHORT risk). If z-score < -1.5, community panic (contrarian LONG opportunity with F&G < 20). Combine with FLOW for highest conviction.'''

NEW_RULES = '''=== DECISION GUIDELINES (V3.1.58) ===

YOUR ONLY JOB: Decide LONG, SHORT, or WAIT based on signal quality. Position limits, TP/SL, and slot management are handled by code -- ignore them entirely.

SIGNAL RELIABILITY (most to least trustworthy):
  1. FLOW (order book taker ratio) -- actual money moving. Most reliable.
  2. WHALE (on-chain for BTC/ETH, Cryptoracle sentiment for alts) -- smart money / community intelligence.
  3. SENTIMENT (web search price action) -- short-term momentum context.
  4. TECHNICAL (RSI/SMA/momentum) -- lagging but useful for confirmation.

HOW TO DECIDE:
- If FLOW + WHALE agree on direction: trade it. This is your highest-conviction signal.
- If FLOW contradicts WHALE on altcoins: trust FLOW (real orders > social chatter).
- If WHALE shows buying but FLOW shows heavy selling: this is ABSORPTION -- bullish.
- If WHALE shows selling but FLOW shows buying: this is DISTRIBUTION -- bearish.
- WAIT only when signals genuinely conflict with no clear majority, or confidence is truly low.

FEAR & GREED:
- Extreme fear does NOT automatically mean buy. If FLOW confirms selling, the dump is real.
- Extreme greed does NOT automatically mean sell. If FLOW confirms buying, the rally is real.
- Contrarian trades need FLOW confirmation. Without it, go with the trend.

FUNDING RATE: Negative = shorts paying longs (bullish lean). Positive = longs paying shorts (bearish lean). Not a trade signal alone, but tips the balance when other signals are close.

IMPORTANT: Say LONG or SHORT if you see a good setup. Do not say WAIT to be "safe." We are in a competition and need to take quality trades. Only WAIT when there is genuinely no edge.'''

def patch():
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    if "V3.1.58" in content:
        print("Already patched to V3.1.58")
        return
    
    if OLD_RULES not in content:
        print("ERROR: Could not find old rules block. Manual patch needed.")
        print("Searching for partial match...")
        if "V3.1.45 - learned from 45" in content:
            print("Found V3.1.45 marker. Trying flexible replacement...")
            # Find the rules section by markers
            start_marker = "=== RULES (V3.1.45"
            end_marker = "Respond with JSON ONLY"
            
            start_idx = content.find(start_marker)
            end_idx = content.find(end_marker)
            
            if start_idx == -1 or end_idx == -1:
                print(f"FAILED: start={start_idx}, end={end_idx}")
                return
            
            content = content[:start_idx] + NEW_RULES + "\n\n" + content[end_idx:]
            print("Flexible replacement succeeded!")
        else:
            print("No V3.1.45 marker found either. Aborting.")
            return
    else:
        content = content.replace(OLD_RULES, NEW_RULES)
        print("Exact replacement succeeded!")
    
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("V3.1.58 Judge prompt simplified.")
    print("Old: 18 rigid rules with contradictions")
    print("New: Signal hierarchy + guidelines, lets Gemini reason freely")

if __name__ == "__main__":
    patch()
