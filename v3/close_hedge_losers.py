#!/usr/bin/env python3
"""Close the LOSING/SMALLER side of each hedged (dual) pair. Keep winners."""
import sys, time
sys.path.insert(0, '.')
from smt_nightly_trade_v3_1 import get_open_positions, place_order, round_size_to_step

positions = get_open_positions()
print(f"Total positions: {len(positions)}")

# Group by symbol
by_symbol = {}
for p in positions:
    sym = p['symbol']
    if sym not in by_symbol:
        by_symbol[sym] = {}
    by_symbol[sym][p['side']] = p

# Find hedged pairs
hedged = {sym: sides for sym, sides in by_symbol.items() if len(sides) == 2}

if not hedged:
    print("No hedged pairs found. Nothing to close.")
    sys.exit(0)

print(f"\nHedged pairs found: {len(hedged)}")
print("-" * 60)

to_close = []
for sym, sides in hedged.items():
    short_name = sym.replace('cmt_', '').upper()
    long_pos = sides.get('LONG', {})
    short_pos = sides.get('SHORT', {})
    
    long_pnl = float(long_pos.get('unrealized_pnl', 0))
    short_pnl = float(short_pos.get('unrealized_pnl', 0))
    long_margin = float(long_pos.get('margin', 0))
    short_margin = float(short_pos.get('margin', 0))
    
    print(f"\n{short_name}:")
    print(f"  LONG:  margin=${long_margin:.2f}  PnL=${long_pnl:.2f}  size={long_pos.get('size','?')}")
    print(f"  SHORT: margin=${short_margin:.2f}  PnL=${short_pnl:.2f}  size={short_pos.get('size','?')}")
    
    # Close the smaller margin side (less capital committed)
    # If similar margin, close the one with worse PnL
    if short_margin > long_margin * 2:
        # SHORT is much bigger position (like BNB SHORT +$131) - keep it, close LONG
        close_side = "LONG"
        close_pos = long_pos
    elif long_margin > short_margin * 2:
        close_side = "SHORT"
        close_pos = short_pos
    else:
        # Similar size - close worse PnL
        if long_pnl < short_pnl:
            close_side = "LONG"
            close_pos = long_pos
        else:
            close_side = "SHORT"
            close_pos = short_pos
    
    keep_side = "SHORT" if close_side == "LONG" else "LONG"
    keep_pos = sides[keep_side]
    print(f"  -> CLOSE {close_side} (PnL=${float(close_pos.get('unrealized_pnl',0)):.2f})")
    print(f"  -> KEEP  {keep_side} (PnL=${float(keep_pos.get('unrealized_pnl',0)):.2f})")
    to_close.append((sym, close_side, close_pos))

print("\n" + "=" * 60)
confirm = input(f"Close {len(to_close)} losing sides? (yes/no): ")
if confirm.lower() != 'yes':
    print("Cancelled.")
    sys.exit(0)

for sym, side, pos in to_close:
    short_name = sym.replace('cmt_', '').upper()
    size = float(pos.get('size', 0))
    close_size = round_size_to_step(size, sym)
    # type 3 = close long, type 4 = close short
    close_type = "3" if side == "LONG" else "4"
    
    print(f"\nClosing {short_name} {side} size={close_size}...")
    result = place_order(sym, close_type, close_size, tp_price=None, sl_price=None)
    oid = result.get('order_id', result.get('msg', 'unknown'))
    print(f"  Result: {oid}")
    time.sleep(1)

print("\nDone. Check positions:")
time.sleep(2)
remaining = get_open_positions()
for p in remaining:
    sn = p['symbol'].replace('cmt_', '').upper()
    print(f"  {sn}: {p['side']} PnL=${float(p.get('unrealized_pnl',0)):.2f}")
