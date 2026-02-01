#!/usr/bin/env python3
"""
Patch: Fix tp_sl_hit to store PnL and symbol in close_data
Applies to: smt_daemon_v3_1.py
"""

import sys

TARGET_FILE = "smt_daemon_v3_1.py"

# Fix 1: quick_cleanup_check - add PnL when detecting tp_sl_hit
OLD_QUICK_CLEANUP = '''def quick_cleanup_check():
    """Quick check for closed positions"""
    
    position_closed = False
    
    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                position_closed = True'''

NEW_QUICK_CLEANUP = '''def quick_cleanup_check():
    """Quick check for closed positions"""
    
    position_closed = False
    
    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                cleanup = cancel_all_orders_for_symbol(symbol)
                
                # V3.1.25: Calculate PnL for tp_sl_hit closes
                trade = tracker.active.get(symbol, {})
                entry_price = trade.get("entry_price", 0)
                tp_price = trade.get("tp_price", 0)
                sl_price = trade.get("sl_price", 0)
                side = trade.get("side", "LONG")
                position_usdt = trade.get("position_usdt", 0)
                
                # Estimate PnL based on TP/SL (we don't know exact close price)
                # If profitable direction, assume TP hit; else SL hit
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
    if OLD_QUICK_CLEANUP not in content:
        print("[ERROR] Could not find quick_cleanup_check to patch.")
        print("        Function may have been modified.")
        sys.exit(1)
    
    # Apply patch
    new_content = content.replace(OLD_QUICK_CLEANUP, NEW_QUICK_CLEANUP)
    
    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("[OK] Patch applied successfully.")
    print("     - quick_cleanup_check now stores PnL and symbol")
    print("")
    print("Restart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
