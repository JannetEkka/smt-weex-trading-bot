from smt_nightly_trade_v3_1 import get_open_positions, close_position_manually

print("🚨 EMERGENCY: Closing all positions...")
print("="*60)

positions = get_open_positions()

for pos in positions:
    symbol = pos['symbol']
    side = pos['side']
    size = pos['size']
    upnl = pos['unrealized_pnl']
    
    symbol_clean = symbol.replace('cmt_', '').replace('usdt', '').upper()
    
    print(f"\nClosing {symbol_clean} {side}...")
    print(f"  Size: {size} | UPnL: ${upnl:.2f}")
    
    result = close_position_manually(symbol, side, size)
    
    if result.get('success'):
        print(f"  ✅ Closed! Order: {result.get('order_id')}")
    else:
        print(f"  ❌ Failed: {result.get('error')}")

print("\n" + "="*60)
print("All positions closed. Check balance:")

from smt_nightly_trade_v3_1 import get_balance
print(f"Final Balance: ${get_balance():.2f}")
