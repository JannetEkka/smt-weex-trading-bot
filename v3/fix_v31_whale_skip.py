#!/usr/bin/env python3
"""
fix_v31_whale_skip.py - Only use WHALE persona for ETH/BTC
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 fix_v31_whale_skip.py

Problem: WhalePersona runs for ALL pairs but returns NEUTRAL(0%) for non-ETH,
which dilutes the vote even though SENTIMENT might be 85% LONG.

Fix: Only add whale vote for ETH/BTC pairs. For other pairs, skip entirely.
"""

import os
from datetime import datetime

FILE = "smt_nightly_trade_v3_1.py"

if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found!")
    exit(1)

# Backup
backup = f"{FILE}.backup_whale_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
with open(FILE, 'r') as f:
    original = f.read()
with open(backup, 'w') as f:
    f.write(original)
print(f"Backup: {backup}")

content = original

# ================================================================
# FIX: Only add whale vote for ETH/BTC
# ================================================================

old_whale_section = '''        # 1. Whale Persona (for ETH/BTC)
        print(f"  [WHALE] Analyzing...")
        whale_vote = self.whale.analyze(pair, pair_info)
        votes.append(whale_vote)
        print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")'''

new_whale_section = '''        # 1. Whale Persona (ONLY for ETH/BTC - our unique edge)
        if pair in ("ETH", "BTC"):
            print(f"  [WHALE] Analyzing...")
            whale_vote = self.whale.analyze(pair, pair_info)
            votes.append(whale_vote)
            print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")
        else:
            print(f"  [WHALE] Skipped (no whale data for {pair})")'''

if old_whale_section in content:
    content = content.replace(old_whale_section, new_whale_section)
    print("FIX 1: Whale skip for non-ETH/BTC - APPLIED")
else:
    print("FIX 1: Whale section not found - checking alternate pattern")
    # Try simpler pattern
    if "votes.append(whale_vote)" in content and "# 1. Whale Persona" in content:
        print("  Found whale section, applying manual fix...")

# ================================================================
# FIX 2: Update persona weights for better balance
# SENTIMENT should lead for Tier 2 pairs
# ================================================================

old_weights = 'weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.5, "TECHNICAL": 1.0}'
new_weights = 'weights = {"WHALE": 2.0, "SENTIMENT": 2.0, "FLOW": 1.0, "TECHNICAL": 0.8}  # FIXED: SENTIMENT equal to WHALE'

if old_weights in content:
    content = content.replace(old_weights, new_weights)
    print("FIX 2: Persona weights - APPLIED")
else:
    # Check if already modified
    if '"SENTIMENT": 2.5' in content:
        # Previous fix applied, update it
        content = content.replace(
            'weights = {"WHALE": 1.5, "SENTIMENT": 2.5, "FLOW": 1.5, "TECHNICAL": 1.0}  # FIXED: SENTIMENT dominates',
            'weights = {"WHALE": 2.0, "SENTIMENT": 2.0, "FLOW": 1.0, "TECHNICAL": 0.8}  # FIXED: balanced'
        )
        print("FIX 2: Updated previous weight fix")

# ================================================================
# FIX 3: Lower thresholds for 3-persona votes (when no WHALE)
# ================================================================

# For Tier 2 pairs with only 3 personas, we need lower thresholds
old_consensus_check = '''        long_pct = long_score / total
        short_pct = short_score / total
        
        # Need clear consensus'''

new_consensus_check = '''        long_pct = long_score / total
        short_pct = short_score / total
        
        # Adjust threshold based on number of votes (3 or 4 personas)
        num_votes = len(persona_votes)
        threshold = 0.35 if num_votes <= 3 else 0.40  # Lower for 3-persona votes
        ratio_req = 1.1 if num_votes <= 3 else 1.15
        
        # Need clear consensus'''

if old_consensus_check in content:
    content = content.replace(old_consensus_check, new_consensus_check)
    print("FIX 3: Dynamic threshold - APPLIED")

# Also update the if statements to use threshold variable
content = content.replace(
    "if long_pct > 0.45 and long_score > short_score * 1.2:",
    "if long_pct > threshold and long_score > short_score * ratio_req:"
)
content = content.replace(
    "if long_pct > 0.40 and long_score > short_score * 1.1:",
    "if long_pct > threshold and long_score > short_score * ratio_req:"
)
content = content.replace(
    "elif short_pct > 0.45 and short_score > long_score * 1.2:",
    "elif short_pct > threshold and short_score > long_score * ratio_req:"
)
content = content.replace(
    "elif short_pct > 0.40 and short_score > long_score * 1.1:",
    "elif short_pct > threshold and short_score > long_score * ratio_req:"
)

# Write changes
with open(FILE, 'w') as f:
    f.write(content)

print("\n" + "="*60)
print("FIXES APPLIED!")
print("="*60)
print("""
Changes:
1. WHALE persona only runs for ETH/BTC (skipped for other pairs)
2. Balanced weights: WHALE=2.0, SENTIMENT=2.0, FLOW=1.0, TECH=0.8
3. Dynamic thresholds: 35% for 3 personas, 40% for 4 personas

For DOGE/SOL/XRP/etc (3 personas):
  - SENTIMENT(2.0) + FLOW(1.0) + TECHNICAL(0.8) = 3.8 total weight
  - If SENTIMENT=LONG(85%), FLOW=LONG(70%), TECH=NEUTRAL(40%):
    - LONG score = 0.85*2.0 + 0.70*1.0 = 2.4
    - NEUTRAL score = 0.40*0.8 = 0.32
    - LONG% = 2.4/2.72 = 88% -> TRADES!

For ETH/BTC (4 personas):
  - WHALE(2.0) + SENTIMENT(2.0) + FLOW(1.0) + TECHNICAL(0.8) = 5.8 total
  - WHALE signal carries more weight

Test:
  python3 -c "from smt_nightly_trade_v3_1 import *; print('OK')"

Restart:
  pkill -f smt_daemon_v3_1.py
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &

Commit:
  git add -A && git commit -m "Fix V3.1: skip WHALE for non-ETH pairs, dynamic thresholds" && git push
""")
