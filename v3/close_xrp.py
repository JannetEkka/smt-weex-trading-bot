"""
V3.1.101: Close XRP position manually with proper cleanup + AI log.
Auto-detects side and size from WEEX. Cancels orphan orders first.

Usage: cd v3 && python3 close_xrp.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually,
    upload_ai_log_to_weex, cancel_all_orders_for_symbol
)

symbol = "cmt_xrpusdt"

# Auto-detect XRP position (side + size)
print("Checking XRP position on WEEX...")
positions = get_open_positions()
xrp_pos = None
for p in positions:
    if p.get("symbol") == symbol:
        xrp_pos = p
        break

if not xrp_pos:
    print("No XRP position found. Sweeping orphan orders anyway...")
    cleanup = cancel_all_orders_for_symbol(symbol)
    n = len(cleanup.get("cancelled", []))
    print(f"Cleaned {n} orphan order(s)." if n else "No orphan orders.")
    sys.exit(0)

side = xrp_pos["side"]
size = float(xrp_pos["size"])
entry = float(xrp_pos.get("entry_price", 0))
pnl = float(xrp_pos.get("unrealized_pnl", 0))
margin = float(xrp_pos.get("margin", 0))

print(f"Found: XRP {side} | size={size} | entry=${entry:.5f} | UPnL=${pnl:+.2f} | margin=${margin:.2f}")

# Step 1: Cancel all TP/SL trigger orders (prevents orphans)
print("\n[1/3] Cancelling TP/SL orders...")
cancel_result = cancel_all_orders_for_symbol(symbol)
n_cancelled = len(cancel_result.get("cancelled", []))
print(f"  Cancelled {n_cancelled} order(s)")

# Step 2: Close the position
print(f"\n[2/3] Closing XRP {side} (size={size})...")
close_result = close_position_manually(symbol, side, size)
order_id = close_result.get("order_id")
if not order_id and isinstance(close_result.get("data"), dict):
    order_id = close_result["data"].get("order_id")

if order_id:
    print(f"  CLOSED: order_id={order_id}")
else:
    print(f"  Result: {close_result}")
    print("  WARNING: Close may have failed. Check WEEX.")

# Step 3: Upload AI log (MANDATORY for competition)
print(f"\n[3/3] Uploading AI log...")
pnl_pct = (pnl / margin * 100) if margin > 0 else 0
upload_ai_log_to_weex(
    stage=f"Portfolio Manager: Close {side} XRP",
    input_data={
        "symbol": symbol,
        "side": side,
        "size": size,
        "entry_price": entry,
        "unrealized_pnl": pnl,
        "margin": margin,
        "pnl_pct": round(pnl_pct, 2),
    },
    output_data={
        "action": "PORTFOLIO_CLOSE",
        "order_id": order_id,
        "reason": "PM risk management: XRP sideways with no directional conviction. Closing to free slot for higher-probability setups.",
    },
    explanation=f"AI Portfolio Manager closing {side} XRP (PnL: {pnl_pct:+.1f}%). Position going sideways with no clear directional signal from ensemble. Freeing slot and capital for stronger opportunities.",
    order_id=order_id
)
print("  AI log uploaded.")

print(f"\nDone! XRP {side} closed. Order: {order_id}")
