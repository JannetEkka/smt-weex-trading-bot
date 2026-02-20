#!/usr/bin/env python3
"""
PM Close All Positions — Portfolio Manager rebalance close.
Closes LTC, XRP, SOL positions with proper AI log uploads (PM stage, with order_id).
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, cancel_all_orders_for_symbol,
    close_position_manually, upload_ai_log_to_weex,
    get_recent_close_order_id
)

TARGETS = ["cmt_ltcusdt", "cmt_xrpusdt", "cmt_solusdt"]

def main():
    positions = get_open_positions()
    if not positions:
        print("No open positions found.")
        return

    for pos in positions:
        sym = pos["symbol"]
        if sym not in TARGETS:
            print(f"  Skipping {sym} (not in target list)")
            continue

        side = pos["side"]          # "LONG" / "SHORT"
        size = pos["size"]
        entry = pos["entry_price"]
        upnl = pos["unrealized_pnl"]
        pair = sym.replace("cmt_", "").replace("usdt", "").upper()

        print(f"\n=== Closing {pair} {side} | size={size} entry=${entry} UPnL=${upnl:.2f} ===")

        # 1. Cancel all TP/SL triggers
        cleanup = cancel_all_orders_for_symbol(sym)
        if cleanup.get("cancelled"):
            print(f"  Cancelled {len(cleanup['cancelled'])} trigger orders")
            time.sleep(1)

        # 2. Close position
        result = close_position_manually(sym, side, size)
        order_id = result.get("data", {}).get("orderId") if isinstance(result.get("data"), dict) else None
        print(f"  Close result: {result}")

        # 3. If order_id not in place_order response, fetch from fills
        if not order_id:
            time.sleep(2)
            order_id = get_recent_close_order_id(sym)
            print(f"  Fetched close order_id from fills: {order_id}")

        # 4. Upload AI log — PM stage
        stage = f"Portfolio Manager: Close {side} {pair}"
        explanation = (
            f"Portfolio Manager rebalance: closing {pair} {side} position. "
            f"Entry ${entry}, UPnL ${upnl:.2f}. "
            f"Rebalancing portfolio after V3.2.60 strategy update — "
            f"reducing exposure to free capital for higher-conviction setups. "
            f"Risk management: clearing underwater positions to preserve equity."
        )
        input_data = {
            "pair": pair,
            "side": side,
            "entry_price": entry,
            "unrealized_pnl": round(upnl, 2),
            "reason": "PM rebalance — V3.2.60 strategy update",
        }
        output_data = {
            "action": f"CLOSE_{side}",
            "pair": pair,
            "size": size,
        }

        log_result = upload_ai_log_to_weex(stage, input_data, output_data, explanation, order_id=order_id)
        print(f"  AI log: {log_result.get('code', 'unknown')}")
        time.sleep(1)

    print("\n=== Done. All target positions closed. ===")

if __name__ == "__main__":
    main()
