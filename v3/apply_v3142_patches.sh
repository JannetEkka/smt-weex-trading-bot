#!/bin/bash
# SMT V3.1.42 - EXTREME FEAR OVERRIDE PATCHES
# Run from: ~/smt-weex-trading-bot/v3/
# BACKUP FIRST, then apply patches

set -e

echo "=== SMT V3.1.42 PATCH: Extreme Fear Override ==="
echo ""

NIGHTLY="smt_nightly_trade_v3_1.py"
DAEMON="smt_daemon_v3_1.py"

# ============================================================
# STEP 0: BACKUP
# ============================================================
echo "[0/3] Creating backups..."
cp "$NIGHTLY" "${NIGHTLY}.bak_v3141"
cp "$DAEMON" "${DAEMON}.bak_v3141"
echo "  Backed up to ${NIGHTLY}.bak_v3141 and ${DAEMON}.bak_v3141"

# ============================================================
# PATCH 1A: Gemini Judge prompt - add Capitulation Rule
# File: smt_nightly_trade_v3_1.py (around line 1930)
# ============================================================
echo "[1/3] Patching Gemini Judge prompt (Capitulation Rule)..."

# Use python for multiline replacement (sed struggles with multiline)
python3 << 'PYEOF'
import re

with open("smt_nightly_trade_v3_1.py", "r") as f:
    content = f.read()

old = """6. In EXTREME FEAR (F&G < 20), shorting is dangerous - violent bounces are common. Favor LONG or WAIT. But also: LONGs in extreme fear are FRAGILE. The bounce may die fast. Set tighter TP (2-3%) and be ready to exit quickly."""

new = """6. In EXTREME FEAR (F&G < 20), shorting is dangerous - violent bounces are common. Favor LONG or WAIT. But also: LONGs in extreme fear are FRAGILE. The bounce may die fast. Set tighter TP (2-3%) and be ready to exit quickly.
6b. CAPITULATION RULE (F&G < 15): Market is in panic. Do NOT default to WAIT. If FLOW persona is BULLISH (taker buying), this is absorption - go LONG with 80%+ confidence and tight 2% TP. If FLOW is NEUTRAL and TECHNICAL shows RSI < 30, this is a mean reversion setup - go LONG at 75%+ confidence. Only WAIT if ALL personas are BEARISH. Extreme Fear + Flow Buying = Best entries of the cycle."""

if old in content:
    content = content.replace(old, new)
    with open("smt_nightly_trade_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 1A applied: Capitulation Rule added to Gemini Judge prompt")
else:
    print("  WARNING: Could not find target text for Patch 1A. Check manually.")
    print("  Looking for: '6. In EXTREME FEAR (F&G < 20)...'")
PYEOF

# ============================================================
# PATCH 1B: Add fear_greed + regime to Judge return dict
# File: smt_nightly_trade_v3_1.py (around line 2068)
# ============================================================
echo "[2/3] Patching Judge return dict (adding fear_greed, regime)..."

python3 << 'PYEOF'
with open("smt_nightly_trade_v3_1.py", "r") as f:
    content = f.read()

old = '''                "reasoning": f"Gemini Judge V3.1.39: {reasoning}. Votes: {', '.join(vote_summary)}",
                "persona_votes": persona_votes,
                "vote_breakdown": {
                    "long_score": 0,
                    "short_score": 0,
                    "neutral_score": 0,
                },
            }'''

new = '''                "reasoning": f"Gemini Judge V3.1.42: {reasoning}. Votes: {', '.join(vote_summary)}",
                "persona_votes": persona_votes,
                "vote_breakdown": {
                    "long_score": 0,
                    "short_score": 0,
                    "neutral_score": 0,
                },
                "fear_greed": regime.get("fear_greed", 50),
                "regime": regime.get("regime", "NEUTRAL"),
            }'''

if old in content:
    content = content.replace(old, new)
    with open("smt_nightly_trade_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 1B applied: fear_greed + regime added to return dict")
else:
    print("  WARNING: Could not find target text for Patch 1B.")
    print("  The version string might differ. Checking for V3.1.39...")
    # Try a more flexible match
    import re
    pattern = r'("reasoning": f"Gemini Judge V3\.1\.\d+:.*?vote_summary\)}".*?"neutral_score": 0,\s*},\s*})'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        old_block = match.group(0)
        new_block = old_block.rstrip('}').rstrip() + ''',
                "fear_greed": regime.get("fear_greed", 50),
                "regime": regime.get("regime", "NEUTRAL"),
            }'''
        content = content.replace(old_block, new_block)
        with open("smt_nightly_trade_v3_1.py", "w") as f:
            f.write(content)
        print("  PATCH 1B applied via flexible match")
    else:
        print("  FAILED: Could not patch return dict. Apply manually.")
PYEOF

# ============================================================
# PATCH 2: Asian Session Filter with Extreme Fear Override
# File: smt_daemon_v3_1.py (around line 734)
# ============================================================
echo "[3/3] Patching Asian Session Filter (Extreme Fear override)..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

old = """                # V3.1.41: ASIAN SESSION FILTER (00-06 UTC) - hard code, don't trust LLM
                import datetime as _dt_module
                utc_hour = _dt_module.datetime.now(_dt_module.timezone.utc).hour
                opp_confidence = opportunity["decision"]["confidence"]
                if 0 <= utc_hour < 6 and opp_confidence < 0.85:
                    logger.warning(f"ASIAN SESSION FILTER: {utc_hour}:00 UTC, confidence {opp_confidence:.0%} < 85%, skipping {opportunity['pair']}")
                    continue"""

new = """                # V3.1.42: ASIAN SESSION FILTER (00-06 UTC) with EXTREME FEAR override
                import datetime as _dt_module
                utc_hour = _dt_module.datetime.now(_dt_module.timezone.utc).hour
                opp_confidence = opportunity["decision"]["confidence"]
                # V3.1.42: Pull fear_greed from decision dict (added in Judge return)
                opp_fear_greed = opportunity["decision"].get("fear_greed", 50)
                is_extreme_fear = opp_fear_greed < 20
                if 0 <= utc_hour < 6 and opp_confidence < 0.85 and not is_extreme_fear:
                    logger.warning(f"ASIAN SESSION FILTER: {utc_hour}:00 UTC, confidence {opp_confidence:.0%} < 85%, F&G={opp_fear_greed}, skipping {opportunity['pair']}")
                    continue
                elif 0 <= utc_hour < 6 and is_extreme_fear:
                    logger.info(f"EXTREME FEAR OVERRIDE: F&G={opp_fear_greed} < 20, bypassing Asian filter for {opportunity['pair']} ({opp_confidence:.0%})")"""

if old in content:
    content = content.replace(old, new)
    with open("smt_daemon_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 2 applied: Asian Session Filter now bypassed in Extreme Fear")
else:
    print("  WARNING: Could not find target text for Patch 2.")
    print("  Check if V3.1.41 Asian filter text matches exactly.")
PYEOF

# ============================================================
# VERIFY
# ============================================================
echo ""
echo "=== VERIFICATION ==="
echo ""
echo "Patch 1A (Capitulation Rule in prompt):"
grep -n "6b. CAPITULATION RULE" "$NIGHTLY" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 1B (fear_greed in return dict):"
grep -n '"fear_greed": regime.get' "$NIGHTLY" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2 (Extreme Fear override):"
grep -n "EXTREME FEAR OVERRIDE" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "=== DONE ==="
echo ""
echo "Next steps:"
echo "  1. pkill -f smt_daemon"
echo "  2. nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &"
echo "  3. tail -f daemon.log  (watch for EXTREME FEAR OVERRIDE messages)"
echo "  4. git add . && git commit -m 'V3.1.42: Extreme Fear override - bypass Asian filter, Capitulation Rule in Judge' && git push"
