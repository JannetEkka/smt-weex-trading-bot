"""
V3.1.58c - Wire prediction market into WHALE persona for BTC
PM data gets included in WHALE's reasoning, which Judge then sees.
"""

DAEMON_FILE = "smt_nightly_trade_v3_1.py"

def patch():
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    if "fetch_prediction_market" in content:
        print("PM already integrated. Skipping.")
        return
    
    # 1. Add PM import in the _get_cryptoracle_data area
    # Find the existing cryptoracle import line
    old_import = 'from cryptoracle_client import get_all_trading_pair_sentiment'
    new_import = 'from cryptoracle_client import get_all_trading_pair_sentiment, fetch_prediction_market'
    
    if old_import not in content:
        print(f"ERROR: Could not find cryptoracle import line")
        return
    
    content = content.replace(old_import, new_import)
    
    # 2. In _analyze_with_etherscan for BTC, add PM data to reasoning
    # Find the section where we build the Etherscan result for BTC/ETH
    # We'll add PM data right before the final return in _analyze_with_etherscan
    
    old_etherscan_return = '''            return {
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
            }'''
    
    new_etherscan_return = '''            # V3.1.58: Add prediction market data for BTC
            pm_data = None
            if pair == "BTC":
                try:
                    pm_data = fetch_prediction_market()
                    if pm_data:
                        pm_val = pm_data["pm_sentiment"]
                        pm_sig = pm_data["pm_signal"]
                        pm_str = pm_data["pm_strength"]
                        reasoning += f" [PM: {pm_sig} {pm_str} ({pm_val:+.3f})]"
                        # PM confirms whale signal = boost confidence
                        if pm_sig == signal and signal != "NEUTRAL" and pm_str == "STRONG":
                            confidence = min(0.90, confidence + 0.08)
                        elif pm_sig == signal and signal != "NEUTRAL":
                            confidence = min(0.85, confidence + 0.04)
                        # PM contradicts = slight reduction
                        elif pm_sig != "NEUTRAL" and signal != "NEUTRAL" and pm_sig != signal:
                            confidence = max(0.40, confidence - 0.05)
                except Exception as e:
                    print(f"  [WHALE] PM fetch error: {e}")
            
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
                    "prediction_market": pm_data,
                },
            }'''
    
    if old_etherscan_return not in content:
        print("ERROR: Could not find etherscan return block. Trying flexible match...")
        # Try without exact whitespace
        if '"whales_analyzed": whales_analyzed' in content and '"cryptoracle": cr_signal' in content:
            print("Found partial markers, but exact block doesn't match. Manual integration needed.")
        return
    
    content = content.replace(old_etherscan_return, new_etherscan_return)
    
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("V3.1.58c: Prediction market wired into WHALE persona for BTC.")
    print("- PM confirms WHALE direction = confidence boost (+4-8%)")
    print("- PM contradicts = slight reduction (-5%)")
    print("- Judge sees PM data in WHALE reasoning string")

if __name__ == "__main__":
    patch()
