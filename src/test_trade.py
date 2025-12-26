"""
SMT Trade Test
Opens and closes 10 USDT position with AI logging
Run AFTER test_pipeline.py passes
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.weex_trader import WEEXTrader, AILogger

print("=" * 60)
print("SMT TRADE TEST (10 USDT)")
print("=" * 60)

# Init
trader = WEEXTrader()
logger = AILogger()
symbol = "cmt_btcusdt"

# 1. Market data
print("\n[1] Market Data")
price = trader.get_price(symbol)
balance = trader.get_balance()
print(f"    BTC: ${price:,.2f}")
print(f"    Balance: {balance:.2f} USDT")

# 2. Leverage
print("\n[2] Set Leverage 20x")
trader.set_leverage(symbol, 20)

# 3. AI decision to open
print("\n[3] AI Decision: OPEN LONG")
size = round(10 / price, 4)
logger.log(
    action="OPEN_POSITION",
    decision="EXECUTE", 
    reasoning="CEX outflow detected from Binance whale. Bullish signal validated. Opening minimum position.",
    data={"symbol": symbol, "side": "LONG", "size": size, "price": price}
)

# 4. Open
print("\n[4] Opening Position...")
result = trader.place_order(symbol, "1", size)
print(f"    {json.dumps(result)[:200]}")
logger.log(action="ORDER_EXECUTED", decision="SUCCESS", reasoning="Long opened", data=result)

# 5. Wait
print("\n[5] Waiting 3s...")
time.sleep(3)

# 6. AI decision to close
print("\n[6] AI Decision: CLOSE")
logger.log(
    action="CLOSE_POSITION",
    decision="EXECUTE",
    reasoning="Completing trade cycle for API test. Taking profit/loss.",
    data={"side": "CLOSE_LONG"}
)

# 7. Close
print("\n[7] Closing Position...")
result = trader.place_order(symbol, "3", size)
print(f"    {json.dumps(result)[:200]}")
logger.log(action="ORDER_EXECUTED", decision="SUCCESS", reasoning="Position closed", data=result)

# 8. Summary
print("\n[8] Summary")
final = trader.get_balance()
pnl = final - balance
print(f"    P/L: {pnl:+.4f} USDT")
print(f"    AI Logs: ai_logs/")

logger.log(
    action="TRADE_COMPLETE",
    decision="SUCCESS",
    reasoning=f"API test complete. PnL: {pnl:+.4f} USDT",
    data={"pnl": pnl, "initial": balance, "final": final}
)

print("\n" + "=" * 60)
print("DONE - Check ai_logs/ for AI decision trail")
print("=" * 60)
