#!/usr/bin/env python3
"""
Patch: Fix RL logging 'regime' undefined error
Applies to: smt_daemon_v3_1.py
"""

import sys

TARGET_FILE = "smt_daemon_v3_1.py"

OLD_BLOCK = '''                # V3.1.23: Log decision for RL training
                if RL_ENABLED and rl_collector:
                    try:
                        persona_dict = {}
                        for v in decision.get("persona_votes", []):
                            persona_dict[v.get("persona", "?")] = {
                                "signal": v.get("signal", "WAIT"),
                                "confidence": v.get("confidence", 0.5)
                            }
                        
                        rl_collector.log_decision(
                            symbol=symbol,
                            action=signal,
                            confidence=confidence,
                            persona_votes=persona_dict,
                            market_state={
                                "btc_24h": regime.get("change_24h", 0) if regime else 0,
                                "btc_4h": regime.get("change_4h", 0) if regime else 0,
                                "regime": regime.get("regime", "NEUTRAL") if regime else "NEUTRAL",
                            },
                            portfolio_state={
                                "num_positions": len(open_positions),
                                "long_exposure": sum(1 for p in open_positions if p.get("side") == "LONG") / 8,
                                "short_exposure": sum(1 for p in open_positions if p.get("side") == "SHORT") / 8,
                                "upnl_pct": sum(float(p.get("unrealized_pnl", 0)) for p in open_positions) / max(balance, 1) * 100,
                            },
                            tier=tier,
                        )
                    except Exception as e:
                        logger.warning(f"RL log error: {e}")'''

NEW_BLOCK = '''                # V3.1.24: Log decision for RL training
                if RL_ENABLED and rl_collector:
                    try:
                        # Get regime data for RL logging
                        rl_regime = get_market_regime_for_exit()
                        
                        persona_dict = {}
                        for v in decision.get("persona_votes", []):
                            persona_dict[v.get("persona", "?")] = {
                                "signal": v.get("signal", "WAIT"),
                                "confidence": v.get("confidence", 0.5)
                            }
                        
                        rl_collector.log_decision(
                            symbol=symbol,
                            action=signal,
                            confidence=confidence,
                            persona_votes=persona_dict,
                            market_state={
                                "btc_24h": rl_regime.get("change_24h", 0),
                                "btc_4h": rl_regime.get("change_4h", 0),
                                "regime": rl_regime.get("regime", "NEUTRAL"),
                            },
                            portfolio_state={
                                "num_positions": len(open_positions),
                                "long_exposure": sum(1 for p in open_positions if p.get("side") == "LONG") / 8,
                                "short_exposure": sum(1 for p in open_positions if p.get("side") == "SHORT") / 8,
                                "upnl_pct": sum(float(p.get("unrealized_pnl", 0)) for p in open_positions) / max(balance, 1) * 100,
                            },
                            tier=tier,
                        )
                    except Exception as e:
                        logger.warning(f"RL log error: {e}")'''


def main():
    # Read file
    try:
        with open(TARGET_FILE, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[ERROR] {TARGET_FILE} not found. Run from v3/ directory.")
        sys.exit(1)
    
    # Check if already patched
    if "rl_regime = get_market_regime_for_exit()" in content:
        print("[OK] Already patched.")
        sys.exit(0)
    
    # Check if old block exists
    if OLD_BLOCK not in content:
        print("[ERROR] Could not find target block to patch.")
        print("        File may have been modified.")
        sys.exit(1)
    
    # Apply patch
    new_content = content.replace(OLD_BLOCK, NEW_BLOCK)
    
    # Write back
    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("[OK] Patch applied successfully.")
    print("     - Added: rl_regime = get_market_regime_for_exit()")
    print("     - Fixed: regime -> rl_regime references")
    print("")
    print("Restart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
