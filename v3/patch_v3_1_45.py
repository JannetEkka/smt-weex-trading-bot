#!/usr/bin/env python3
"""
V3.1.45 PATCH SCRIPT - Run on VM to apply all changes
======================================================
Usage:
  cd ~/smt-weex-trading-bot/v3
  python3 patch_v3_1_45.py

This script:
1. Patches smt_nightly_trade_v3_1.py (whale persona + judge rules)
2. Patches smt_daemon_v3_1.py (PM rules + hedge threshold)
3. Verifies cryptoracle_client.py exists
4. Creates backups before editing
"""

import os
import sys
import shutil
import re

V3_DIR = os.path.dirname(os.path.abspath(__file__))

def backup(filepath):
    bak = filepath + ".bak_v3_1_44"
    if not os.path.exists(bak):
        shutil.copy2(filepath, bak)
        print(f"  Backed up: {bak}")
    else:
        print(f"  Backup already exists: {bak}")

def replace_in_file(filepath, old_text, new_text, label=""):
    with open(filepath, 'r') as f:
        content = f.read()
    
    if old_text not in content:
        # Try with flexible whitespace
        old_stripped = old_text.strip()
        if old_stripped not in content:
            print(f"  WARNING: Could not find text for [{label}]")
            print(f"  First 80 chars of search: {repr(old_text[:80])}")
            return False
        else:
            content = content.replace(old_stripped, new_text.strip(), 1)
    else:
        content = content.replace(old_text, new_text, 1)
    
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  OK: {label}")
    return True


def patch_nightly_trade():
    """Patch smt_nightly_trade_v3_1.py: whale persona + judge rules"""
    filepath = os.path.join(V3_DIR, "smt_nightly_trade_v3_1.py")
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found!")
        return False
    
    backup(filepath)
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # =========================================================
    # PATCH 1: Replace WhalePersona class
    # =========================================================
    # Find the class definition and the analyze method up to _analyze_whale_flow
    
    old_whale_start = "class WhalePersona:"
    old_whale_end = "    def _analyze_whale_flow"
    
    if old_whale_start not in content:
        print("  ERROR: Cannot find 'class WhalePersona:' in nightly trade file")
        return False
    
    if old_whale_end not in content:
        print("  ERROR: Cannot find '_analyze_whale_flow' in nightly trade file")
        return False
    
    # Extract everything between class start and _analyze_whale_flow
    start_idx = content.index(old_whale_start)
    end_idx = content.index(old_whale_end)
    
    new_whale_class = '''class WhalePersona:
    """
    V3.1.45: Enhanced whale intelligence with Cryptoracle integration.
    
    BTC/ETH: Etherscan on-chain whale flow (primary) + Cryptoracle community sentiment (secondary)
    ALL OTHERS: Cryptoracle community sentiment analysis (no more "Skipped")
    
    Cryptoracle provides:
      - CO-A-02-03: Net sentiment direction (positive - negative ratio)
      - CO-S-01-01: Sentiment momentum Z-score (deviation from norm)
      - CO-S-01-05: Sentiment vs price dislocation (mean-reversion signal)
    """
    
    def __init__(self):
        self.name = "WHALE"
        self.weight = 2.0
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
        self._cryptoracle_data = None
        self._cryptoracle_fetched_at = 0
    
    def _get_cryptoracle_data(self) -> dict:
        """Fetch Cryptoracle data for all tokens (cached 10min)."""
        import time as _time
        now = _time.time()
        if self._cryptoracle_data and (now - self._cryptoracle_fetched_at) < 600:
            return self._cryptoracle_data
        
        try:
            from cryptoracle_client import get_all_trading_pair_sentiment
            data = get_all_trading_pair_sentiment()
            if data:
                self._cryptoracle_data = data
                self._cryptoracle_fetched_at = now
                return data
        except ImportError:
            print("  [WHALE] cryptoracle_client not found - using Etherscan only")
        except Exception as e:
            print(f"  [WHALE] Cryptoracle fetch error: {e}")
        
        return self._cryptoracle_data or {}
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        """Analyze whale/smart money activity for trading signal."""
        
        # Fetch Cryptoracle data for all pairs (one API call, cached)
        cr_data = self._get_cryptoracle_data()
        cr_signal = cr_data.get(pair.upper()) if cr_data else None
        
        if pair in ("ETH", "BTC"):
            return self._analyze_with_etherscan(pair, pair_info, cr_signal)
        else:
            return self._analyze_with_cryptoracle(pair, pair_info, cr_signal)
    
    def _analyze_with_etherscan(self, pair: str, pair_info: Dict, cr_signal: dict) -> Dict:
        """BTC/ETH: Etherscan whale flow (primary) + Cryptoracle (secondary)."""
        try:
            total_inflow = 0
            total_outflow = 0
            whale_signals = []
            whales_analyzed = 0
            
            for whale in TOP_WHALES[:5]:
                try:
                    flow = self._analyze_whale_flow(whale["address"], whale["label"])
                    if flow:
                        whales_analyzed += 1
                        total_inflow += flow["inflow"]
                        total_outflow += flow["outflow"]
                        
                        if flow["net"] > 100:
                            whale_signals.append(f"{whale['label']}: +{flow['net']:.0f} ETH")
                        elif flow["net"] < -100:
                            whale_signals.append(f"{whale['label']}: {flow['net']:.0f} ETH")
                    
                    time.sleep(0.25)
                except Exception as e:
                    print(f"  [WHALE] Error analyzing {whale['label']}: {e}")
                    continue
            
            if whales_analyzed == 0:
                if cr_signal:
                    return self._cryptoracle_to_vote(pair, cr_signal, "Etherscan unavailable, using Cryptoracle")
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.3,
                    "reasoning": "Could not fetch whale data from Etherscan",
                }
            
            net_flow = total_inflow - total_outflow
            
            if net_flow > 500:
                signal = "LONG"
                confidence = min(0.85, 0.5 + (net_flow / 5000))
                reasoning = f"Whale accumulation: +{net_flow:.0f} ETH net inflow"
            elif net_flow < -500:
                signal = "SHORT"
                confidence = min(0.85, 0.5 + (abs(net_flow) / 5000))
                reasoning = f"Whale distribution: {net_flow:.0f} ETH net outflow"
            elif net_flow > 100:
                signal = "LONG"
                confidence = 0.55
                reasoning = f"Mild whale accumulation: +{net_flow:.0f} ETH"
            elif net_flow < -100:
                signal = "SHORT"
                confidence = 0.55
                reasoning = f"Mild whale distribution: {net_flow:.0f} ETH"
            else:
                signal = "NEUTRAL"
                confidence = 0.4
                reasoning = f"Whale activity balanced: {net_flow:+.0f} ETH"
            
            if whale_signals:
                reasoning += f" | {'; '.join(whale_signals[:3])}"
            
            # V3.1.45: Cryptoracle boost/veto for BTC/ETH
            if cr_signal:
                cr_dir = cr_signal.get("signal", "NEUTRAL")
                cr_conf = cr_signal.get("confidence", 0.4)
                cr_net = cr_signal.get("net_sentiment", 0.5)
                cr_mom = cr_signal.get("sentiment_momentum", 0.0)
                
                if signal == cr_dir and cr_dir != "NEUTRAL":
                    boost = min(0.10, (cr_conf - 0.5) * 0.2)
                    confidence = min(0.85, confidence + boost)
                    reasoning += f" [CR confirms: sent={cr_net:.2f}, mom={cr_mom:.2f}]"
                elif signal != "NEUTRAL" and cr_dir != "NEUTRAL" and signal != cr_dir:
                    reasoning += f" [CR DIVERGES: community={cr_dir} sent={cr_net:.2f}]"
                    confidence = max(0.40, confidence - 0.05)
            
            return {
                "persona": self.name,
                "signal": signal,
                "confidence": confidence,
                "reasoning": reasoning,
                "data": {
                    "net_flow": net_flow,
                    "inflow": total_inflow,
                    "outflow": total_outflow,
                    "whales_analyzed": whales_analyzed,
                    "cryptoracle": cr_signal,
                },
            }
            
        except Exception as e:
            if cr_signal:
                return self._cryptoracle_to_vote(pair, cr_signal, f"Etherscan error ({e}), using Cryptoracle")
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"Whale analysis error: {str(e)}",
            }
    
    def _analyze_with_cryptoracle(self, pair: str, pair_info: Dict, cr_signal: dict) -> Dict:
        """Non-BTC/ETH pairs: Cryptoracle community sentiment as primary signal."""
        if not cr_signal:
            print(f"  [WHALE] No data for {pair} (Cryptoracle unavailable)")
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"No whale data for {pair}",
            }
        
        return self._cryptoracle_to_vote(pair, cr_signal, "")
    
    def _cryptoracle_to_vote(self, pair: str, cr: dict, prefix: str) -> Dict:
        """Convert Cryptoracle signal to a whale persona vote."""
        signal = cr.get("signal", "NEUTRAL")
        confidence = cr.get("confidence", 0.40)
        net_sent = cr.get("net_sentiment", 0.5)
        momentum = cr.get("sentiment_momentum", 0.0)
        price_gap = cr.get("sentiment_price_gap", 0.0)
        trend = cr.get("trend_1h", "FLAT")
        
        parts = []
        if prefix:
            parts.append(prefix)
        
        if net_sent > 0.65:
            parts.append(f"Strong bullish community sentiment ({net_sent:.2f})")
        elif net_sent > 0.55:
            parts.append(f"Mild bullish sentiment ({net_sent:.2f})")
        elif net_sent < 0.35:
            parts.append(f"Strong bearish community sentiment ({net_sent:.2f})")
        elif net_sent < 0.45:
            parts.append(f"Mild bearish sentiment ({net_sent:.2f})")
        else:
            parts.append(f"Neutral community sentiment ({net_sent:.2f})")
        
        if abs(momentum) > 1.0:
            direction = "bullish" if momentum > 0 else "bearish"
            parts.append(f"Sentiment momentum {direction} (z={momentum:.2f})")
        
        if abs(price_gap) > 2.0:
            gap_dir = "ahead of" if price_gap > 0 else "behind"
            parts.append(f"Sentiment {gap_dir} price (gap={price_gap:.2f})")
        
        if trend != "FLAT":
            parts.append(f"Trend: {trend}")
        
        reasoning = "; ".join(parts)
        
        return {
            "persona": self.name,
            "signal": signal,
            "confidence": confidence,
            "reasoning": reasoning,
            "data": {
                "source": "cryptoracle",
                "net_sentiment": net_sent,
                "sentiment_momentum": momentum,
                "sentiment_price_gap": price_gap,
                "trend": trend,
                "cryptoracle": cr,
            },
        }

'''
    
    content = content[:start_idx] + new_whale_class + content[end_idx:]
    print("  OK: WhalePersona class replaced with V3.1.45 (Cryptoracle integration)")
    
    # =========================================================
    # PATCH 2: Update has_whale_data flags
    # =========================================================
    # Now all pairs have whale data via Cryptoracle
    pairs_to_enable = [
        ('"BNB": {"symbol": "cmt_bnbusdt", "tier": 1, "has_whale_data": False}',
         '"BNB": {"symbol": "cmt_bnbusdt", "tier": 1, "has_whale_data": True}'),
        ('"LTC": {"symbol": "cmt_ltcusdt", "tier": 1, "has_whale_data": False}',
         '"LTC": {"symbol": "cmt_ltcusdt", "tier": 1, "has_whale_data": True}'),
        ('"SOL": {"symbol": "cmt_solusdt", "tier": 2, "has_whale_data": False}',
         '"SOL": {"symbol": "cmt_solusdt", "tier": 2, "has_whale_data": True}'),
        ('"DOGE": {"symbol": "cmt_dogeusdt", "tier": 3, "has_whale_data": False}',
         '"DOGE": {"symbol": "cmt_dogeusdt", "tier": 3, "has_whale_data": True}'),
        ('"XRP": {"symbol": "cmt_xrpusdt", "tier": 3, "has_whale_data": False}',
         '"XRP": {"symbol": "cmt_xrpusdt", "tier": 3, "has_whale_data": True}'),
        ('"ADA": {"symbol": "cmt_adausdt", "tier": 3, "has_whale_data": False}',
         '"ADA": {"symbol": "cmt_adausdt", "tier": 3, "has_whale_data": True}'),
    ]
    
    for old, new in pairs_to_enable:
        if old in content:
            content = content.replace(old, new, 1)
            pair_name = old.split('"')[1]
            print(f"  OK: {pair_name} has_whale_data -> True")
        else:
            # Try flexible match
            pair_name = old.split('"')[1]
            print(f"  SKIP: {pair_name} has_whale_data pattern not found (may already be True)")
    
    # =========================================================
    # PATCH 3: Update Judge prompt rules
    # =========================================================
    
    old_rules_start = "=== RULES (V3.1.41"
    # Also try other versions
    if old_rules_start not in content:
        for v in ["V3.1.42", "V3.1.43", "V3.1.44", "V3.1.39", "V3.1.40"]:
            candidate = f"=== RULES ({v}"
            if candidate in content:
                old_rules_start = candidate
                break
    
    if old_rules_start not in content:
        print("  WARNING: Could not find Judge rules header. Trying generic pattern...")
        # Try to find any rules header
        if "=== RULES (" in content:
            idx = content.index("=== RULES (")
            old_rules_start = content[idx:idx+20]
        else:
            print("  ERROR: Cannot find Judge rules section at all!")
            # Still write what we have
            with open(filepath, 'w') as f:
                f.write(content)
            return True
    
    # Find end of rules section (the line before the JSON response instruction)
    rules_start_idx = content.index(old_rules_start)
    
    # Find the end: look for "Respond with JSON ONLY"
    respond_marker = "Respond with JSON ONLY"
    if respond_marker in content[rules_start_idx:]:
        rules_end_idx = content.index(respond_marker, rules_start_idx)
    else:
        print("  WARNING: Cannot find end of rules section")
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    
    new_rules = """=== RULES (V3.1.45 - learned from 45+ iterations) ===
1. You MUST pick exactly ONE: LONG, SHORT, or WAIT.
2. If we already have BOTH a LONG and SHORT on this pair, return WAIT.
3. If we already have a LONG open and signal is LONG, return WAIT (already positioned).
4. If we already have a SHORT open and signal is SHORT, return WAIT.
5. If we have a losing position and a strong opposite signal (85%+), you CAN recommend the opposite direction (hedge).
6. In EXTREME FEAR (F&G < 20), shorting is dangerous - violent bounces are common. Favor LONG or WAIT. But also: LONGs in extreme fear are FRAGILE. The bounce may die fast. Set tighter TP (2-3%) and be ready to exit quickly.
6b. CAPITULATION RULE (F&G < 15): Market is in panic. Do NOT default to WAIT. If FLOW persona is BULLISH (taker buying), this is absorption - go LONG with 80%+ confidence and tight 2% TP. If FLOW is NEUTRAL and TECHNICAL shows RSI < 30, this is a mean reversion setup - go LONG at 75%+ confidence. Only WAIT if ALL personas are BEARISH. Extreme Fear + Flow Buying = Best entries of the cycle.
7. Negative funding rate = shorts paying longs = bullish. Positive funding + LONG = you pay every 8h, factor this into hold time.
8. Tier awareness: T1 (BTC/ETH/BNB/LTC) = slow, 15x leverage. T2 (SOL) = medium, 12x. T3 (DOGE/XRP/ADA) = fast, 10x.
9. DIRECTIONAL LIMIT: Max 6 positions in the same direction. We have 8 slots. Do NOT self-impose a lower limit.
10. CORRELATED PAIRS: If 4+ correlated LONGs open (BTC/ETH/SOL/DOGE/XRP), require 85%+ to add another.
11. TIME OF DAY: 00-06 UTC Asian session. Require 80%+ confidence (not 85%).
12. POST-REGIME SHIFT: If regime just changed, still trade if confidence >= 80%. Speed matters.
13. For TP/SL: SL 2-2.5%. TP should be 2-3x SL (4-6%). Max hold: T1=48h, T2=24h, T3=12h. Let winners RUN.
14. RECOVERY MODE: We are DOWN from starting balance. We CANNOT afford to WAIT on good setups. If 2+ personas agree on direction with 70%+ average confidence, TAKE THE TRADE. Playing safe = guaranteed last place.
15. SWAP MENTALITY: If we are full on slots but this signal is 80%+ conviction, say LONG or SHORT anyway. The daemon will handle swapping out the weakest position. Do NOT return WAIT just because slots are full.
16. WHALE DATA QUALITY: For BTC/ETH, WHALE uses on-chain Etherscan data (most reliable - actual wallet flows). For other pairs (SOL/DOGE/XRP/ADA/BNB/LTC), WHALE uses Cryptoracle community sentiment (social signals from Twitter/Telegram/Discord). Etherscan > Cryptoracle in reliability. If WHALE and FLOW disagree on altcoins, trust FLOW (order book data > social data).
17. SMART MONEY DIVERGENCE: If WHALE shows bullish but FLOW shows extreme selling, this is BULLISH ABSORPTION (whales/community buying the dip) - high conviction LONG. If WHALE shows bearish but FLOW shows buying, this is DISTRIBUTION INTO STRENGTH (smart money selling into FOMO) - caution on LONGs.
18. SENTIMENT MOMENTUM: If WHALE reports sentiment momentum z-score > 1.5, community is overheated (contrarian SHORT risk). If z-score < -1.5, community panic (contrarian LONG opportunity with F&G < 20). Combine with FLOW for highest conviction.

"""
    
    content = content[:rules_start_idx] + new_rules + content[rules_end_idx:]
    print("  OK: Judge rules updated to V3.1.45 (added rules 16-18)")
    
    # Write final
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  DONE: {filepath} patched successfully")
    return True


def patch_daemon():
    """Patch smt_daemon_v3_1.py: PM rules + hedge threshold"""
    filepath = os.path.join(V3_DIR, "smt_daemon_v3_1.py")
    if not os.path.exists(filepath):
        print(f"ERROR: {filepath} not found!")
        return False
    
    backup(filepath)
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    changes = 0
    
    # =========================================================
    # FIX 1: Portfolio Manager Rule 1 - directional limit
    # =========================================================
    
    # 1a: Main Rule 1 text
    old1 = "Max 4 positions in the same direction normally. If 5+ LONGs or 5+ SHORTs, close the WEAKEST ones\n(lowest PnL% or most faded from peak) until we have max 4. All-same-direction = cascade\nliquidation risk in cross margin.\nEXCEPTION: If F&G < 15 (Capitulation), allow up to 5 LONGs. Violent bounces move ALL alts together,\nso being long across the board IS the correct play. Only enforce max 4 if F&G >= 15."
    
    new1 = "Max 5 positions in the same direction normally. If 6+ LONGs or 6+ SHORTs, close the WEAKEST ones\n(lowest PnL% or most faded from peak) until we have max 5. All-same-direction = cascade\nliquidation risk in cross margin.\nEXCEPTION: If F&G < 15 (Capitulation), allow up to 7 LONGs. Violent bounces move ALL alts together,\nso being long across the board IS the correct play. Only enforce max 5 if F&G >= 15."
    
    if old1 in content:
        content = content.replace(old1, new1, 1)
        print("  OK: PM Rule 1 limits updated (4->5 normal, 5->7 capitulation)")
        changes += 1
    else:
        # Try partial matches
        if "allow up to 5 LONGs" in content:
            content = content.replace("allow up to 5 LONGs", "allow up to 7 LONGs", 1)
            print("  OK: PM Rule 1 capitulation limit (5->7)")
            changes += 1
        if "until we have max 4" in content:
            content = content.replace("until we have max 4", "until we have max 5", 1)
            print("  OK: PM Rule 1 normal limit (4->5)")
            changes += 1
        if "Only enforce max 4 if F&G >= 15" in content:
            content = content.replace("Only enforce max 4 if F&G >= 15", "Only enforce max 5 if F&G >= 15", 1)
            changes += 1
        if "If 5+ LONGs or 5+ SHORTs" in content:
            content = content.replace("If 5+ LONGs or 5+ SHORTs", "If 6+ LONGs or 6+ SHORTs", 1)
            changes += 1
        if changes == 0:
            print("  WARNING: Could not find PM Rule 1 text to patch")
    
    # 1b: Rule 4c - concentration limit
    old4c = "c) There are 6+ LONGs open (raised from 5 during Capitulation)"
    new4c = "c) There are 8+ LONGs open (only during extreme over-concentration)"
    if old4c in content:
        content = content.replace(old4c, new4c, 1)
        print("  OK: PM Rule 4c updated (6->8)")
        changes += 1
    else:
        print("  SKIP: Rule 4c text not found (may differ)")
    
    # 1c: Rule 5 exception
    old5 = "allow up to 4 correlated altcoin LONGs alongside BTC"
    new5 = "allow up to 6 correlated altcoin LONGs alongside BTC"
    if old5 in content:
        content = content.replace(old5, new5, 1)
        print("  OK: PM Rule 5 correlated limit (4->6)")
        changes += 1
    else:
        print("  SKIP: Rule 5 correlated text not found")
    
    old5b = "Only enforce the strict 2-altcoin limit when F&G >= 15."
    new5b = "Only enforce the strict 3-altcoin limit when F&G >= 15."
    if old5b in content:
        content = content.replace(old5b, new5b, 1)
        changes += 1
    
    # =========================================================
    # FIX 2: Hedge threshold during capitulation
    # =========================================================
    old_hedge = "HEDGE_CONFIDENCE_THRESHOLD = 1.0  # Impossible to meet = no hedges"
    new_hedge = "HEDGE_CONFIDENCE_THRESHOLD = 0.95  # V3.1.45: Allow hedges at 95%+ even in capitulation"
    
    if old_hedge in content:
        content = content.replace(old_hedge, new_hedge, 1)
        print("  OK: Hedge threshold during capitulation (1.0 -> 0.95)")
        changes += 1
    else:
        print("  WARNING: Could not find hedge threshold line to patch")
    
    # Write
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  DONE: {filepath} patched ({changes} changes)")
    return True


def verify_cryptoracle_client():
    """Check that cryptoracle_client.py exists in v3 directory."""
    filepath = os.path.join(V3_DIR, "cryptoracle_client.py")
    if os.path.exists(filepath):
        print(f"  OK: {filepath} exists")
        return True
    else:
        print(f"  ERROR: {filepath} NOT FOUND!")
        print(f"  You need to copy cryptoracle_client.py to {V3_DIR}/ first!")
        return False


def main():
    print("=" * 60)
    print("SMT V3.1.45 PATCH")
    print("Cryptoracle Integration + PM Fix + Hedge Fix")
    print("=" * 60)
    print()
    
    # Check we're in the right directory
    nightly = os.path.join(V3_DIR, "smt_nightly_trade_v3_1.py")
    daemon = os.path.join(V3_DIR, "smt_daemon_v3_1.py")
    
    if not os.path.exists(nightly):
        print(f"ERROR: Cannot find {nightly}")
        print("Make sure you run this from ~/smt-weex-trading-bot/v3/")
        sys.exit(1)
    
    if not os.path.exists(daemon):
        print(f"ERROR: Cannot find {daemon}")
        sys.exit(1)
    
    print("[1/3] Checking cryptoracle_client.py...")
    if not verify_cryptoracle_client():
        print("\nABORTED: Copy cryptoracle_client.py first, then re-run this script.")
        sys.exit(1)
    
    print()
    print("[2/3] Patching smt_nightly_trade_v3_1.py...")
    if not patch_nightly_trade():
        print("WARNING: nightly trade patch had issues")
    
    print()
    print("[3/3] Patching smt_daemon_v3_1.py...")
    if not patch_daemon():
        print("WARNING: daemon patch had issues")
    
    print()
    print("=" * 60)
    print("PATCH COMPLETE")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Test Cryptoracle: python3 cryptoracle_client.py")
    print("  2. Restart daemon:   pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
    print("  3. Watch logs:       tail -f daemon.log")
    print("  4. Commit:           git add . && git commit -m 'V3.1.45: Cryptoracle + PM fix + hedge fix' && git push")
    print()
    print("Backups saved as *.bak_v3_1_44")


if __name__ == "__main__":
    main()
