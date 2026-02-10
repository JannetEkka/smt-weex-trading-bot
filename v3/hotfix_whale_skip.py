#!/usr/bin/env python3
"""
V3.1.45b HOTFIX - Remove orchestrator whale skip
=================================================
The WhalePersona class was patched but the CALLING code still skips
whale analysis for non-BTC/ETH pairs. This fixes it.

Usage:
  cd ~/smt-weex-trading-bot/v3
  python3 hotfix_whale_skip.py
"""

import os
import sys

filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smt_nightly_trade_v3_1.py")

if not os.path.exists(filepath):
    print(f"ERROR: {filepath} not found")
    sys.exit(1)

with open(filepath, 'r') as f:
    content = f.read()

# The old orchestrator code that skips WHALE for non-BTC/ETH
old_block = '''        # 1. Whale Persona (ONLY for ETH/BTC)
        if pair in ("ETH", "BTC"):
            print(f"  [WHALE] Analyzing...")
            whale_vote = self.whale.analyze(pair, pair_info)
            votes.append(whale_vote)
            print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")
        else:
            print(f"  [WHALE] Skipped (no whale data for {pair})")'''

# New code: always call whale.analyze() -- the class itself handles routing
new_block = '''        # 1. Whale Persona (V3.1.45: ALL pairs via Cryptoracle, BTC/ETH also use Etherscan)
        print(f"  [WHALE] Analyzing...")
        whale_vote = self.whale.analyze(pair, pair_info)
        votes.append(whale_vote)
        print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")'''

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print("OK: Removed orchestrator whale skip. WHALE now runs for ALL pairs.")
else:
    print("WARNING: Could not find exact old block. Trying flexible match...")
    
    # Try matching just the key conditional
    if 'if pair in ("ETH", "BTC"):' in content and '[WHALE] Skipped (no whale data for' in content:
        # Find the section
        idx = content.index('# 1. Whale Persona')
        # Find the end (start of # 2. Sentiment)
        end_marker = '# 2. Sentiment Persona'
        end_idx = content.index(end_marker, idx)
        
        # Extract what's between
        old_section = content[idx:end_idx]
        
        new_section = '''# 1. Whale Persona (V3.1.45: ALL pairs via Cryptoracle, BTC/ETH also use Etherscan)
        print(f"  [WHALE] Analyzing...")
        whale_vote = self.whale.analyze(pair, pair_info)
        votes.append(whale_vote)
        print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")
        
        '''
        
        content = content[:idx] + new_section + content[end_idx:]
        with open(filepath, 'w') as f:
            f.write(content)
        print("OK: Removed orchestrator whale skip (flexible match).")
    else:
        print("ERROR: Cannot find the whale skip block at all. Manual edit needed.")
        print("Look for 'if pair in (\"ETH\", \"BTC\"):' near line 2347")
        sys.exit(1)

print("Done. Restart daemon to apply.")
print("  pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
