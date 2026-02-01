#!/usr/bin/env python3
"""
Patch V3: Fix tp_sl_hit to store PnL and symbol in close_data
Applies to: smt_daemon_v3_1.py (current version)
"""

import sys

TARGET_FILE = "smt_daemon_v3_1.py"

OLD_BLOCK = '''                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                position_closed = True'''

NEW_BLOCK = '''                cleanup = cancel_all_orders_for_symbol(symbol)
                
                # V3.1.25: Calculate actual PnL for RL matching
                trade = tracker.active.get(symbol, {})
                entry_price = trade.get("entry_price", 0)
                side = trade.get("side", "LONG")
                position_usdt = trade.get("position_usdt", 0)
                pnl_usd = 0
                hit_tp = False
                
                if entry_price > 0 and position_usdt > 0:
                    try:
                        current_price = get_price(symbol)
                        if side == "LONG":
                            pnl_pct = ((current_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - current_price) / entry_price) * 100
                        pnl_usd = (pnl_pct / 100) * position_usdt
                        hit_tp = pnl_usd > 0
                    except:
                        pass
                
                tracker.close_trade(symbol, {
                    "reason": "tp_sl_hit",
                    "cleanup": cleanup,
                    "symbol": symbol,
                    "pnl": round(pnl_usd, 2),
                    "hit_tp": hit_tp,
                })
                state.trades_closed += 1
                position_closed = True'''


def main():
    try:
        with open(TARGET_FILE, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[ERROR] {TARGET_FILE} not found. Run from v3/ directory.")
        sys.exit(1)
    
    # Check if already patched
    if '"symbol": symbol,' in content and '"pnl": round(pnl_usd' in content:
        print("[OK] Already patched.")
        sys.exit(0)
    
    # Check if old block exists
    if OLD_BLOCK not in content:
        print("[ERROR] Could not find target block to patch.")
        sys.exit(1)
    
    # Apply patch
    new_content = content.replace(OLD_BLOCK, NEW_BLOCK)
    
    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("[OK] Patch applied successfully.")
    print("     - close_trade now stores symbol, pnl, hit_tp")
    print("")
    print("Restart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
