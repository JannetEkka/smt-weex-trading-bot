"""
V3.1.84 Emergency Close Script
Closes ALL positions + cancels ALL orphan trigger/plan orders.
Uses the bot's own functions so auth/signing is handled.

3 passes:
  1. Cancel ALL orders on ALL symbols (triggers, plan orders, pending)
  2. Close ALL open positions
  3. Verify: re-check positions + sweep orders again
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually,
    cancel_all_orders_for_symbol, get_balance, TRADING_PAIRS
)

print("=" * 60)
print("V3.1.84 EMERGENCY CLOSE - Clean slate for restart")
print("=" * 60)

# ── PASS 1: Cancel ALL orders on ALL symbols FIRST ──
# This kills orphan TP/SL triggers BEFORE closing positions,
# so WEEX doesn't reject closes due to "open orders" blocking.
print("\n[PASS 1] Cancelling ALL orders on ALL symbols...")
total_cancelled = 0
for pair, info in TRADING_PAIRS.items():
    sym = info["symbol"]
    try:
        cleanup = cancel_all_orders_for_symbol(sym)
        n = len(cleanup.get("cancelled", []))
        if n > 0:
            total_cancelled += n
            print(f"  {pair}: cancelled {n} order(s)")
    except Exception as e:
        print(f"  {pair}: error cancelling: {e}")
    time.sleep(0.5)

print(f"  Total cancelled: {total_cancelled}")

# ── PASS 2: Close ALL open positions ──
print("\n[PASS 2] Closing ALL open positions...")
positions = get_open_positions()
print(f"  Found {len(positions)} open position(s)\n")

closed = 0
for pos in positions:
    sym = pos["symbol"]
    side = pos["side"]
    size = pos["size"]
    pnl = float(pos.get("unrealized_pnl", 0))
    entry = float(pos.get("entry_price", 0))
    sym_clean = sym.replace("cmt_", "").replace("usdt", "").upper()

    print(f"  Closing {sym_clean} {side} | size={size} | entry=${entry:.4f} | UPnL=${pnl:+.2f}")

    # Cancel any remaining orders for this specific symbol (belt + suspenders)
    try:
        cancel_all_orders_for_symbol(sym)
    except Exception:
        pass
    time.sleep(0.5)

    result = close_position_manually(sym, side, size)
    order_id = result.get("order_id")
    if not order_id and isinstance(result.get("data"), dict):
        order_id = result["data"].get("order_id")

    if order_id:
        print(f"    CLOSED: order_id={order_id}")
        closed += 1
    else:
        print(f"    RESULT: {result}")
    time.sleep(1)

print(f"\n  Closed {closed}/{len(positions)} positions")

# ── PASS 3: Verify clean state ──
print("\n[PASS 3] Verifying clean state...")
time.sleep(3)  # Wait for WEEX to settle

# Re-check positions
remaining = get_open_positions()
if remaining:
    print(f"  WARNING: {len(remaining)} position(s) still open!")
    for pos in remaining:
        sym_clean = pos["symbol"].replace("cmt_", "").replace("usdt", "").upper()
        print(f"    {sym_clean} {pos['side']} size={pos['size']} UPnL=${float(pos.get('unrealized_pnl',0)):+.2f}")
    print("  Retrying close...")
    for pos in remaining:
        try:
            cancel_all_orders_for_symbol(pos["symbol"])
            time.sleep(0.5)
            close_position_manually(pos["symbol"], pos["side"], pos["size"])
            time.sleep(1)
        except Exception as e:
            print(f"    Retry failed for {pos['symbol']}: {e}")
else:
    print("  All positions closed.")

# Final order sweep
orphans_left = 0
for pair, info in TRADING_PAIRS.items():
    sym = info["symbol"]
    try:
        cleanup = cancel_all_orders_for_symbol(sym)
        n = len(cleanup.get("cancelled", []))
        if n > 0:
            orphans_left += n
            print(f"  {pair}: cleaned {n} lingering order(s)")
    except Exception:
        pass

if orphans_left > 0:
    print(f"  Cleaned {orphans_left} lingering orders in final sweep")
else:
    print("  No orphan orders remaining.")

# ── FINAL STATUS ──
print("\n" + "=" * 60)
bal = get_balance()
final_pos = get_open_positions()
print(f"Balance: ${bal:.2f} USDT")
print(f"Open positions: {len(final_pos)}")
print(f"Status: {'CLEAN' if len(final_pos) == 0 else 'WARNING - positions still open'}")
print(f"Ready for V3.1.84 daemon restart")
print("=" * 60)
