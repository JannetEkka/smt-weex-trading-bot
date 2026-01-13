#!/usr/bin/env python3
"""
close_old_positions.py - Close all old tiny positions
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 close_old_positions.py
"""

import sys
sys.path.insert(0, '.')

from smt_nightly_trade_v3_1 import (
    get_open_positions, get_balance, 
    close_position_manually, cancel_all_orders_for_symbol
)

print("="*60)
print("CLOSING ALL OLD POSITIONS")
print("="*60)

balance = get_balance()
positions = get_open_positions()

print(f"\nBalance: ${balance:.2f}")
print(f"Open positions: {len(positions)}")

if not positions:
    print("\nNo positions to close!")
    exit(0)

print("\nCurrent positions:")
total_pnl = 0
for p in positions:
    pnl = p.get('unrealized_pnl', 0)
    total_pnl += pnl
    margin = p.get('margin', 0)
    symbol_short = p['symbol'].replace('cmt_', '').upper()
    print(f"  {symbol_short}: {p['side']} size={p['size']} margin=${margin:.2f} pnl=${pnl:.2f}")

print(f"\nTotal unrealized PnL: ${total_pnl:.2f}")

# Ask for confirmation
print("\n" + "="*60)
response = input("Close ALL these positions? (yes/no): ")

if response.lower() != 'yes':
    print("Cancelled.")
    exit(0)

print("\nClosing positions...")
for p in positions:
    symbol = p['symbol']
    symbol_short = symbol.replace('cmt_', '').upper()
    
    print(f"\n  Closing {symbol_short}...")
    
    # Close position
    result = close_position_manually(symbol, p['side'], p['size'])
    print(f"    Close result: {result.get('order_id', result.get('msg', 'unknown'))}")
    
    # Cancel any pending orders
    cleanup = cancel_all_orders_for_symbol(symbol)
    cancelled = len(cleanup.get('cancelled', []))
    if cancelled > 0:
        print(f"    Cancelled {cancelled} pending orders")

print("\n" + "="*60)
print("DONE! All positions closed.")
print("="*60)

# Check final state
import time
time.sleep(2)
final_balance = get_balance()
final_positions = get_open_positions()
print(f"\nFinal balance: ${final_balance:.2f}")
print(f"Remaining positions: {len(final_positions)}")
print(f"\nV3.1 can now trade with proper sizing!")
