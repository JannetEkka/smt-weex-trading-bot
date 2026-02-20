"""Quick close BNB position + upload AI log to WEEX."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually,
    cancel_all_orders_for_symbol, upload_ai_log_to_weex,
    get_balance, get_recent_close_order_id
)

SYMBOL = "cmt_bnbusdt"
PAIR = "BNB"

print("=" * 50)
print(f"Closing {PAIR} position + uploading AI log")
print("=" * 50)

# 1. Get current positions
positions = get_open_positions()
bnb_pos = None
for pos in positions:
    if pos["symbol"] == SYMBOL:
        bnb_pos = pos
        break

if not bnb_pos:
    print(f"No {PAIR} position found!")
    print(f"Open positions: {[p['symbol'] for p in positions]}")
    sys.exit(1)

side = bnb_pos["side"]
size = bnb_pos["size"]
entry = float(bnb_pos.get("entry_price", 0))
pnl = float(bnb_pos.get("unrealized_pnl", 0))
print(f"Found: {PAIR} {side} | size={size} | entry=${entry:.4f} | UPnL=${pnl:+.2f}")

# 2. Cancel orphan orders first
print(f"\nCancelling orders for {PAIR}...")
try:
    cleanup = cancel_all_orders_for_symbol(SYMBOL)
    n = len(cleanup.get("cancelled", []))
    print(f"  Cancelled {n} order(s)")
except Exception as e:
    print(f"  Cancel error (continuing): {e}")
time.sleep(1)

# 3. Close position
print(f"\nClosing {PAIR} {side}...")
result = close_position_manually(SYMBOL, side, size)
order_id = result.get("order_id")
if not order_id and isinstance(result.get("data"), dict):
    order_id = result["data"].get("order_id")

if order_id:
    print(f"  CLOSED: order_id={order_id}")
else:
    print(f"  Result: {result}")
    # Try to get order_id from history
    time.sleep(2)
    print("  Trying to get close order_id from WEEX history...")
    order_id = get_recent_close_order_id(SYMBOL)
    if order_id:
        print(f"  Found from history: order_id={order_id}")
    else:
        print("  WARNING: Could not get order_id")

# 4. Upload AI log
if order_id:
    print(f"\nUploading AI log with order_id={order_id}...")
    close_type = "Close LONG" if side == "long" else "Close SHORT"
    stage = f"Portfolio Manager: {close_type} {PAIR}"
    explanation = (
        f"Manual close of {PAIR} {side.upper()} position during daemon maintenance. "
        f"Entry: ${entry:.4f}, UPnL at close: ${pnl:+.2f}. "
        f"Position closed to deploy V3.2.61 update (12H price action context for Judge)."
    )
    log_result = upload_ai_log_to_weex(
        stage=stage,
        input_data={"action": "manual_close", "pair": PAIR, "reason": "daemon_update"},
        output_data={"closed": True, "entry_price": entry, "unrealized_pnl": pnl},
        explanation=explanation,
        order_id=int(order_id)
    )
    print(f"  AI log result: {json.dumps(log_result) if isinstance(log_result, dict) else log_result}")
else:
    print("\nWARNING: No order_id — cannot upload AI log!")

# 5. Verify
time.sleep(2)
remaining = get_open_positions()
bnb_remaining = [p for p in remaining if p["symbol"] == SYMBOL]
if not bnb_remaining:
    print(f"\n{PAIR} position CLOSED successfully.")
else:
    print(f"\nWARNING: {PAIR} position still open!")

bal = get_balance()
print(f"Balance: ${bal:.2f} USDT")
print("=" * 50)
