"""
V3.2.65 Emergency Close Script
Closes ALL positions + cancels ALL orphan trigger/plan orders.
Uploads AI logs with order_id for EVERY close (MANDATORY for competition).

3 passes:
  1. Cancel ALL orders on ALL symbols (triggers, plan orders, pending)
  2. Close ALL open positions + upload AI log with order_id
  3. Verify: re-check positions + sweep orders again
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually,
    cancel_all_orders_for_symbol, get_balance, get_price,
    get_account_equity, TRADING_PAIRS, upload_ai_log_to_weex,
    get_recent_close_order_id, get_tier_for_symbol, get_tier_config,
)

print("=" * 60)
print("V3.2.65 EMERGENCY CLOSE - AI logs included")
print("=" * 60)

# ── PASS 1: Cancel ALL orders on ALL symbols FIRST ──
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

# ── PASS 2: Close ALL open positions + AI log ──
print("\n[PASS 2] Closing ALL open positions (with AI logs)...")
positions = get_open_positions()
print(f"  Found {len(positions)} open position(s)\n")

closed = 0
for pos in positions:
    sym = pos["symbol"]
    side = pos["side"]
    size = pos["size"]
    pnl = float(pos.get("unrealized_pnl", 0))
    entry = float(pos.get("entry_price", 0))
    current = get_price(sym)
    sym_clean = sym.replace("cmt_", "").replace("usdt", "").upper()
    tier = get_tier_for_symbol(sym)
    tier_config = get_tier_config(tier)

    # Calculate PnL %
    if entry > 0 and current > 0:
        if side == "LONG":
            pnl_pct = ((current - entry) / entry) * 100
        else:
            pnl_pct = ((entry - current) / entry) * 100
    else:
        pnl_pct = 0.0

    print(f"  Closing {sym_clean} {side} | size={size} | entry=${entry:.4f} | current=${current:.4f} | UPnL=${pnl:+.2f} ({pnl_pct:+.2f}%)")

    # Cancel any remaining orders for this specific symbol
    try:
        cancel_all_orders_for_symbol(sym)
    except Exception:
        pass
    time.sleep(0.5)

    # Close position
    result = close_position_manually(sym, side, size)
    order_id = result.get("order_id")
    if not order_id and isinstance(result.get("data"), dict):
        order_id = result["data"].get("order_id")
    if not order_id:
        order_id = result.get("orderId")
        if not order_id and isinstance(result.get("data"), dict):
            order_id = result["data"].get("orderId")

    # Fallback: query recent close order
    if not order_id:
        time.sleep(1.5)
        order_id = get_recent_close_order_id(sym)

    if order_id:
        print(f"    CLOSED: order_id={order_id}")
        closed += 1

        # Upload AI log with order_id — MANDATORY for competition
        try:
            upload_ai_log_to_weex(
                stage=f"Portfolio Manager: Close {side} {sym_clean}",
                input_data={
                    "symbol": sym,
                    "side": side,
                    "entry_price": entry,
                    "current_price": current,
                    "tier": tier,
                },
                output_data={
                    "action": "EMERGENCY_CLOSE",
                    "pnl_usd": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": "V3.2.65 TP ceiling fix — closing positions opened with incorrect 0.8-1.5% TP ceilings",
                },
                explanation=f"Emergency close {side} {sym_clean}. Entry ${entry:.4f}, exit ${current:.4f}. PnL: ${pnl:.2f} ({pnl_pct:+.2f}%). Positions opened with wrong TP ceilings (0.8-1.5% instead of 0.3%), closing to re-enter with correct parameters.",
                order_id=int(order_id) if str(order_id).isdigit() else None,
            )
            print(f"    AI LOG: uploaded with order_id={order_id}")
        except Exception as e:
            print(f"    AI LOG ERROR: {e}")
    else:
        print(f"    WARNING: no order_id — RESULT: {result}")
        # Still upload AI log without order_id (better than nothing)
        try:
            upload_ai_log_to_weex(
                stage=f"Portfolio Manager: Close {side} {sym_clean}",
                input_data={"symbol": sym, "side": side, "entry_price": entry},
                output_data={"action": "EMERGENCY_CLOSE", "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)},
                explanation=f"Emergency close {side} {sym_clean}. PnL: ${pnl:.2f} ({pnl_pct:+.2f}%). Order ID not available.",
            )
        except Exception:
            pass
    time.sleep(1)

print(f"\n  Closed {closed}/{len(positions)} positions")

# ── PASS 3: Verify clean state ──
print("\n[PASS 3] Verifying clean state...")
time.sleep(3)

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
            r = close_position_manually(pos["symbol"], pos["side"], pos["size"])
            _oid = r.get("order_id") or (r.get("data", {}) or {}).get("order_id")
            if not _oid:
                time.sleep(1.5)
                _oid = get_recent_close_order_id(pos["symbol"])
            sym_clean = pos["symbol"].replace("cmt_", "").replace("usdt", "").upper()
            if _oid:
                upload_ai_log_to_weex(
                    stage=f"Portfolio Manager: Close {pos['side']} {sym_clean}",
                    input_data={"symbol": pos["symbol"], "side": pos["side"]},
                    output_data={"action": "EMERGENCY_CLOSE_RETRY"},
                    explanation=f"Retry close {pos['side']} {sym_clean}.",
                    order_id=int(_oid) if str(_oid).isdigit() else None,
                )
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
print("Ready for daemon restart with 0.3% TP ceiling")
print("=" * 60)
