"""
PM close script — closes ETH and BTC positions, logs as Portfolio Manager.
Run once, then delete.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from smt_nightly_trade_v3_1 import (
    get_open_positions,
    close_position_manually,
    upload_ai_log_to_weex,
    cancel_all_orders_for_symbol,
)

TARGET_SYMBOLS = {
    "cmt_ethusdt": "ETH",
    "cmt_btcusdt": "BTC",
}

def pm_close(symbol: str, label: str, side: str, size: float):
    print(f"\n[PM CLOSE] {label} {side} size={size}")

    # Cancel orphan orders first (same as close_position_manually)
    try:
        cleanup = cancel_all_orders_for_symbol(symbol)
        if cleanup.get("cancelled"):
            print(f"  Cancelled {len(cleanup['cancelled'])} orphan orders")
    except Exception as e:
        print(f"  Warning: orphan cleanup failed: {e}")

    # Close the position
    result = close_position_manually(symbol, side, size)
    order_id = result.get("data", {}).get("orderId") or result.get("orderId")
    print(f"  Close result: {result}")

    # Upload PM log
    stage = f"Portfolio Manager: Close {side} {label}"
    explanation = (
        f"Portfolio Manager initiated close of {side} {label} position. "
        f"Reason: Rebalancing portfolio to active pairs (LTC/XRP/SOL/ADA). "
        f"BTC and ETH removed from TRADING_PAIRS in V3.2.14 — freeing slots for active pairs."
    )
    input_data = {
        "symbol": symbol,
        "side": side,
        "size": size,
        "action": "pm_rebalance",
    }
    output_data = {
        "decision": "CLOSE",
        "reason": "pair_removed_v3214",
        "order_id": order_id,
    }
    log_result = upload_ai_log_to_weex(
        stage=stage,
        input_data=input_data,
        output_data=output_data,
        explanation=explanation,
        order_id=order_id,
    )
    print(f"  AI log: {log_result.get('code')} — {stage}")
    return result

def main():
    positions = get_open_positions()
    print(f"Open positions found: {len(positions)}")

    closed = 0
    for pos in positions:
        sym = pos["symbol"]
        if sym not in TARGET_SYMBOLS:
            continue
        label = TARGET_SYMBOLS[sym]
        side = pos["side"]   # "LONG" or "SHORT"
        size = pos["size"]
        pm_close(sym, label, side, size)
        closed += 1

    if closed == 0:
        print("\nNo ETH/BTC positions found — nothing to close.")
    else:
        print(f"\nDone. Closed {closed} position(s). Check daemon logs for orphan cleanup confirmation.")

if __name__ == "__main__":
    main()
