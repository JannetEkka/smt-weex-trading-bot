"""
V3.1.83 Emergency Close Script
Closes all positions + cancels ALL orphan trigger orders.
Uses the bot's own functions so auth/signing is handled.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually,
    cancel_all_orders_for_symbol, get_balance, TRADING_PAIRS
)

print("=" * 60)
print("V3.1.83 EMERGENCY CLOSE - PM requested close all")
print("=" * 60)

# Step 1: Close all positions (close_position_manually now cancels triggers first)
positions = get_open_positions()
print(f"Found {len(positions)} open positions\n")

for pos in positions:
    sym = pos["symbol"]
    side = pos["side"]
    size = pos["size"]
    pnl = pos.get("unrealized_pnl", 0)
    entry = pos.get("entry_price", 0)
    sym_clean = sym.replace("cmt_", "").replace("usdt", "").upper()

    print(f"Closing {sym_clean} {side} | size={size} | entry=${entry:.4f} | UPnL=${pnl:+.2f}")
    result = close_position_manually(sym, side, size)
    order_id = result.get("order_id")
    if not order_id and isinstance(result.get("data"), dict):
        order_id = result["data"].get("order_id")

    if order_id:
        print(f"  CLOSED: order_id={order_id}")
    else:
        print(f"  RESULT: {result}")
    time.sleep(1)

# Step 2: Sweep ALL trading symbols for any remaining orphan triggers
print("\nSweeping ALL symbols for orphan triggers...")
total_cleaned = 0
for pair, info in TRADING_PAIRS.items():
    sym = info["symbol"]
    try:
        cleanup = cancel_all_orders_for_symbol(sym)
        n = len(cleanup.get("cancelled", []))
        if n > 0:
            total_cleaned += n
            print(f"  {pair}: cancelled {n} orphan trigger(s)")
    except Exception as e:
        print(f"  {pair}: error: {e}")

print(f"\nTotal orphan triggers cleaned: {total_cleaned}")
print("=" * 60)
bal = get_balance()
print(f"Final Balance: ${bal:.2f}")
print("Reason: PM manual close - fresh start for V3.1.83")
print("=" * 60)
