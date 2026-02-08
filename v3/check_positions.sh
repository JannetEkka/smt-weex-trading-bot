#!/bin/bash
# Quick position checker for SMT

echo "============================================================"
echo "SMT POSITION STATUS"
echo "============================================================"

cd ~/smt-weex-trading-bot/v3

python3 << 'PYEOF'
from smt_nightly_trade_v3_1 import get_open_positions, get_balance
from datetime import datetime

# Current positions
balance = get_balance()
positions = get_open_positions()
total_upnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions)

print(f"\n💰 Balance: ${balance:,.2f}")
print(f"📊 Open Positions: {len(positions)}")
print(f"📈 Total UPnL: ${total_upnl:,.2f}")
print(f"💎 Equity: ${balance + total_upnl:,.2f}")

if positions:
    print(f"\n{'Symbol':<8} {'Side':<6} {'Entry':<12} {'Size':<12} {'UPnL':<12}")
    print("-" * 60)
    for pos in positions:
        symbol = pos['symbol'].replace('cmt_', '').replace('usdt', '').upper()
        side = pos['side']
        entry = pos['entry_price']
        size = pos['size']
        upnl = pos['unrealized_pnl']
        print(f"{symbol:<8} {side:<6} ${entry:<11,.2f} {size:<12,.2f} ${upnl:<11,.2f}")

print("\n" + "=" * 60)
PYEOF

# Trade history (last 10)
echo ""
echo "📜 RECENT TRADE HISTORY"
echo "============================================================"
python3 trade_history_all.py | tail -30

echo ""
echo "============================================================"
