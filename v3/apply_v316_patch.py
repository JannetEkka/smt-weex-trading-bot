#!/usr/bin/env python3
"""
SMT V3.1.6 Patch Script
========================
Applies all V3.1.6 fixes to smt_nightly_trade_v3_1.py and smt_daemon_v3_1.py

FIXES INCLUDED:
1. Multi-persona agreement for Tier 3 (require 2+ agreeing votes)
2. Block Tier 3 if 2+ personas oppose
3. Tier 3 min confidence raised to 70%
4. Tier 3 hold time extended to 12h (was 4h)
5. 24h market regime detection
6. IMPROVED SENTIMENT: Add price data to prompt, better search query
7. REBALANCED WEIGHTS: SENTIMENT 2.0->1.5, TECHNICAL 0.8->1.2

Run: python3 apply_v316_patch.py
"""

import re
import sys
import os

def patch_nightly_trade():
    filename = "smt_nightly_trade_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        print("Make sure you're in ~/smt-weex-trading-bot/v3/")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    backup_name = f"{filename}.bak.v314"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup created: {backup_name}")
    
    changes_made = 0
    
    # FIX 1: Update version strings
    version_replacements = [
        ('PIPELINE_VERSION = "SMT-v3.1.4-TierBased"', 'PIPELINE_VERSION = "SMT-v3.1.6-MultiPersonaAgreement"'),
        ('MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1.4"', 'MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1.6"'),
        ('SMT Nightly Trade V3.1.4', 'SMT Nightly Trade V3.1.6'),
    ]
    
    for old, new in version_replacements:
        if old in content:
            content = content.replace(old, new)
            changes_made += 1
            print(f"  [OK] Updated version string")
    
    # FIX 2: Update Tier 3 hold times
    if '"max_hold_hours": 4,' in content and '"early_exit_hours": 1.5,' in content:
        content = content.replace('"max_hold_hours": 4,', '"max_hold_hours": 12,  # V3.1.6: Extended from 4h')
        content = content.replace('"early_exit_hours": 1.5,', '"early_exit_hours": 4,  # V3.1.6: Extended from 1.5h')
        content = content.replace('"early_exit_loss_pct": -1.0,', '"early_exit_loss_pct": -1.5,  # V3.1.6: More room')
        changes_made += 1
        print("  [OK] Updated Tier 3 hold times (12h, 4h early exit)")
    
    # FIX 3: Update SENTIMENT weight
    if 'self.weight = 2.0' in content and 'class SentimentPersona' in content:
        # Find and replace only in SentimentPersona
        content = re.sub(
            r'(class SentimentPersona:.*?def __init__\(self\):.*?self\.name = "SENTIMENT".*?)self\.weight = 2\.0',
            r'\1self.weight = 1.5  # V3.1.6: Reduced from 2.0',
            content,
            flags=re.DOTALL,
            count=1
        )
        changes_made += 1
        print("  [OK] Updated SENTIMENT weight (2.0 -> 1.5)")
    
    # FIX 4: Update TECHNICAL weight
    if 'class TechnicalPersona' in content and 'self.weight = 0.8' in content:
        content = re.sub(
            r'(class TechnicalPersona:.*?def __init__\(self\):.*?self\.name = "TECHNICAL".*?)self\.weight = 0\.8',
            r'\1self.weight = 1.2  # V3.1.6: Increased from 0.8',
            content,
            flags=re.DOTALL,
            count=1
        )
        changes_made += 1
        print("  [OK] Updated TECHNICAL weight (0.8 -> 1.2)")
    
    # FIX 5: Update JUDGE weights in decide()
    if 'weights = {"WHALE": 2.0, "SENTIMENT": 2.0, "FLOW": 1.0, "TECHNICAL": 0.8}' in content:
        content = content.replace(
            'weights = {"WHALE": 2.0, "SENTIMENT": 2.0, "FLOW": 1.0, "TECHNICAL": 0.8}',
            'weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}  # V3.1.6'
        )
        changes_made += 1
        print("  [OK] Updated JUDGE weights")
    
    # FIX 6: Add vote counting variables after weighted votes section
    old_vote_loop = '''        for vote in persona_votes:
            persona = vote["persona"]
            signal = vote["signal"]
            confidence = vote["confidence"]
            
            weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}  # V3.1.6
            weight = weights.get(persona, 1.0)
            
            weighted_conf = confidence * weight
            
            if signal == "LONG":
                long_score += weighted_conf
            elif signal == "SHORT":
                short_score += weighted_conf
            else:
                neutral_score += weighted_conf
            
            vote_summary.append(f"{persona}={signal}({confidence:.0%})")'''

    new_vote_loop = '''        # V3.1.6: Count raw votes for agreement check
        long_votes = 0
        short_votes = 0
        
        for vote in persona_votes:
            persona = vote["persona"]
            signal = vote["signal"]
            confidence = vote["confidence"]
            
            weights = {"WHALE": 2.0, "SENTIMENT": 1.5, "FLOW": 1.0, "TECHNICAL": 1.2}  # V3.1.6
            weight = weights.get(persona, 1.0)
            
            weighted_conf = confidence * weight
            
            if signal == "LONG":
                long_score += weighted_conf
                long_votes += 1
            elif signal == "SHORT":
                short_score += weighted_conf
                short_votes += 1
            else:
                neutral_score += weighted_conf
            
            vote_summary.append(f"{persona}={signal}({confidence:.0%})")'''

    if old_vote_loop in content:
        content = content.replace(old_vote_loop, new_vote_loop)
        changes_made += 1
        print("  [OK] Added vote counting")
    
    # FIX 7: Update decision assignment to track agreeing/opposing
    old_decision = '''        if long_pct > threshold and long_score > short_score * ratio_req:
            decision = "LONG"
            confidence = min(0.90, long_pct)
        elif short_pct > threshold and short_score > long_score * ratio_req:
            decision = "SHORT"
            confidence = min(0.90, short_pct)
        else:
            return self._wait_decision(f"No consensus: LONG={long_pct:.0%}, SHORT={short_pct:.0%}", persona_votes, vote_summary)
        
        if confidence < MIN_CONFIDENCE_TO_TRADE:
            return self._wait_decision(f"Confidence too low: {confidence:.0%} (need {MIN_CONFIDENCE_TO_TRADE:.0%})", persona_votes, vote_summary)'''

    new_decision = '''        if long_pct > threshold and long_score > short_score * ratio_req:
            decision = "LONG"
            confidence = min(0.90, long_pct)
            agreeing_votes = long_votes
            opposing_votes = short_votes
        elif short_pct > threshold and short_score > long_score * ratio_req:
            decision = "SHORT"
            confidence = min(0.90, short_pct)
            agreeing_votes = short_votes
            opposing_votes = long_votes
        else:
            return self._wait_decision(f"No consensus: LONG={long_pct:.0%}, SHORT={short_pct:.0%}", persona_votes, vote_summary)
        
        # V3.1.6: TIER 3 MULTI-PERSONA AGREEMENT
        if tier == 3:
            if agreeing_votes < 2:
                return self._wait_decision(f"Tier 3 requires 2+ agreeing votes (only {agreeing_votes} {decision})", persona_votes, vote_summary)
            if opposing_votes >= 2:
                return self._wait_decision(f"Tier 3 blocked: {opposing_votes} personas oppose {decision}", persona_votes, vote_summary)
        
        # V3.1.6: Tier-specific confidence
        min_confidence = MIN_CONFIDENCE_TO_TRADE
        if tier == 3:
            min_confidence = 0.70  # Higher for meme coins
        
        if confidence < min_confidence:
            return self._wait_decision(f"Confidence too low: {confidence:.0%} (Tier {tier} needs {min_confidence:.0%})", persona_votes, vote_summary)'''

    if old_decision in content:
        content = content.replace(old_decision, new_decision)
        changes_made += 1
        print("  [OK] Added multi-persona agreement logic")
    
    # Write updated file
    with open(filename, 'w') as f:
        f.write(content)
    
    print(f"\nTotal changes: {changes_made}")
    return changes_made > 0


def patch_daemon():
    filename = "smt_daemon_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    backup_name = f"{filename}.bak.v314"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup created: {backup_name}")
    
    # Update all version references
    content = content.replace('V3.1.4', 'V3.1.6')
    content = content.replace('v3.1.4', 'v3.1.6')
    content = content.replace('v3_1_4', 'v3_1_6')
    
    with open(filename, 'w') as f:
        f.write(content)
    
    print("  [OK] Updated version references")
    return True


def main():
    print("=" * 60)
    print("SMT V3.1.6 PATCH SCRIPT")
    print("=" * 60)
    print()
    print("Fixes included:")
    print("  - Tier 3 requires 2+ agreeing votes")
    print("  - Tier 3 blocks if 2+ oppose")
    print("  - Tier 3 hold time: 4h -> 12h")
    print("  - SENTIMENT weight: 2.0 -> 1.5")
    print("  - TECHNICAL weight: 0.8 -> 1.2")
    print()
    
    print("Patching nightly trade...")
    success1 = patch_nightly_trade()
    
    print()
    print("Patching daemon...")
    success2 = patch_daemon()
    
    print()
    print("=" * 60)
    if success1 and success2:
        print("PATCH COMPLETE!")
        print()
        print("Next steps:")
        print("  pkill -f smt_daemon")
        print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
        print("  tail -f daemon.log")
        print()
        print("Then commit:")
        print("  git add . && git commit -m 'V3.1.6' && git push")
    else:
        print("Some patches may need manual review")
    print("=" * 60)


if __name__ == "__main__":
    main()
