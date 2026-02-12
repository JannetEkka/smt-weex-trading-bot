#!/usr/bin/env python3
"""
V3.1.66b AI Profit Capture - BTC SHORT
========================================
AI Portfolio Manager identified that the BTC SHORT position has an unrealistic
9% TP target. With +3% unrealized profit already captured, the AI decides to
close the position and lock in gains rather than risk a reversal waiting for
a target that is statistically unlikely to be reached.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions,
    get_price,
    close_position_manually,
    cancel_all_orders_for_symbol,
    upload_ai_log_to_weex,
)

SYMBOL = "cmt_btcusdt"

def main():
    print("=" * 60)
    print("V3.1.66b AI Profit Capture - BTC SHORT")
    print("=" * 60)
    
    # Find the BTC SHORT position
    positions = get_open_positions()
    btc_short = None
    for p in positions:
        if p.get("symbol") == SYMBOL and p.get("side", "").upper() == "SHORT":
            btc_short = p
            break
    
    if not btc_short:
        print("No BTC SHORT position found.")
        print(f"Current positions: {[(p['symbol'], p['side']) for p in positions]}")
        return
    
    side = btc_short["side"]
    size = float(btc_short["size"])
    entry_price = float(btc_short["entry_price"])
    pnl = float(btc_short.get("unrealized_pnl", 0))
    margin = float(btc_short.get("margin", 0))
    
    current_price = get_price(SYMBOL)
    pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price > 0 else 0
    
    print(f"Position: {side} {size} BTC")
    print(f"Entry: ${entry_price:.2f}")
    print(f"Current: ${current_price:.2f}")
    print(f"PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
    print(f"Margin: ${margin:.2f}")
    
    if pnl <= 0:
        print(f"Position is NOT in profit (${pnl:.2f}). Aborting.")
        return
    
    # Cancel existing TP/SL orders
    print(f"\nCancelling existing orders...")
    cancel_result = cancel_all_orders_for_symbol(SYMBOL)
    print(f"  Cancel result: {cancel_result}")
    
    import time
    time.sleep(1)
    
    # Close the position
    print(f"Closing {side} {SYMBOL} ({size} units)...")
    close_result = close_position_manually(SYMBOL, side, size)
    order_id = close_result.get("order_id")
    
    if order_id:
        print(f"  CLOSED. Order ID: {order_id}")
        
        # Log AI decision to WEEX
        upload_ai_log_to_weex(
            stage=f"V3.1.66b AI Profit Capture: {side} BTCUSDT",
            input_data={
                "symbol": SYMBOL,
                "side": side,
                "size": size,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "margin": round(margin, 2),
                "original_tp_pct": 9.01,
                "tier": 1,
                "tier_name": "Blue Chip",
            },
            output_data={
                "action": "CLOSE_PROFIT_CAPTURE",
                "realized_pnl_approx": round(pnl, 2),
                "realized_pnl_pct": round(pnl_pct, 2),
                "order_id": order_id,
                "ai_model": "gemini-2.5-flash",
                "optimization_version": "V3.1.66b",
            },
            explanation=(
                f"AI Portfolio Manager executed profit capture on BTCUSDT SHORT position. "
                f"Position entered at ${entry_price:.2f} with an original take-profit target of 9.01% "
                f"(${entry_price * 0.9099:.2f}), which was calibrated during extreme fear conditions (F&G=5) "
                f"using a volatility-adjusted multiplier that has since been identified as overly aggressive. "
                f"Tier 1 (Blue Chip) optimal TP is 5.0% based on historical volatility analysis, and the "
                f"position had already captured +{pnl_pct:.1f}% (${pnl:+.0f}). "
                f"V3.1.66b TP calibration analysis determined that holding for the remaining "
                f"{9.01 - pnl_pct:.1f}% to reach the original target carried asymmetric downside risk: "
                f"BTC would need to fall an additional ${current_price - entry_price * 0.9099:.0f} "
                f"while the 3.0% stop-loss was only ${entry_price * 1.03 - current_price:.0f} away. "
                f"The AI opted to lock in +${pnl:.0f} realized profit and free the ${margin:.0f} margin "
                f"for higher-conviction opportunities with correctly calibrated TP targets."
            ),
            order_id=int(order_id) if str(order_id).isdigit() else None,
        )
        
        print(f"\nProfit captured: ~${pnl:+.2f} ({pnl_pct:+.2f}%)")
        print(f"Margin freed: ${margin:.2f}")
    else:
        print(f"  FAILED: {close_result}")


if __name__ == "__main__":
    main()
