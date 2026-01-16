#!/usr/bin/env python3
"""
SMT V3.1.6 - Regime Exit NOW
============================
This script runs ONCE to close positions fighting the trend.

Run: python3 regime_exit_now.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smt_nightly_trade_v3_1 import (
    get_open_positions, close_position_manually, 
    get_price, upload_ai_log_to_weex, get_balance,
    WEEX_BASE_URL
)
import requests

def get_market_regime():
    """Get current market regime"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            if change_24h < -2 or change_4h < -1:
                return {"regime": "BEARISH", "change_24h": change_24h, "change_4h": change_4h}
            elif change_24h > 2 or change_4h > 1:
                return {"regime": "BULLISH", "change_24h": change_24h, "change_4h": change_4h}
            else:
                return {"regime": "NEUTRAL", "change_24h": change_24h, "change_4h": change_4h}
    except Exception as e:
        print(f"Error getting regime: {e}")
    
    return {"regime": "NEUTRAL", "change_24h": 0, "change_4h": 0}


def main():
    print("=" * 60)
    print("SMT V3.1.6 - AI REGIME EXIT")
    print("=" * 60)
    
    balance = get_balance()
    print(f"Balance: ${balance:.2f}")
    
    regime = get_market_regime()
    print(f"Market Regime: {regime['regime']}")
    print(f"BTC 4h: {regime['change_4h']:+.2f}%")
    print(f"BTC 24h: {regime['change_24h']:+.2f}%")
    print()
    
    positions = get_open_positions()
    print(f"Open positions: {len(positions)}")
    print()
    
    # Analyze all positions
    to_close = []
    to_keep = []
    
    for pos in positions:
        symbol = pos['symbol']
        side = pos['side']
        pnl = float(pos.get('unrealized_pnl', 0))
        size = float(pos['size'])
        entry = float(pos.get('entry_price', 0))
        
        symbol_clean = symbol.replace('cmt_', '').upper()
        
        # Decision logic
        should_close = False
        reason = ""
        
        # In BEARISH regime, close losing LONGs
        if regime['regime'] == "BEARISH" and side == "LONG" and pnl < -5:
            should_close = True
            reason = f"LONG losing ${abs(pnl):.2f} in BEARISH market"
        
        # In BULLISH regime, close losing SHORTs
        elif regime['regime'] == "BULLISH" and side == "SHORT" and pnl < -5:
            should_close = True
            reason = f"SHORT losing ${abs(pnl):.2f} in BULLISH market"
        
        status = "CLOSE" if should_close else "KEEP"
        print(f"  {symbol_clean} {side}: ${pnl:+.2f} -> {status}")
        
        if should_close:
            to_close.append({
                "symbol": symbol,
                "side": side,
                "size": size,
                "pnl": pnl,
                "reason": reason
            })
        else:
            to_keep.append({"symbol": symbol_clean, "side": side, "pnl": pnl})
    
    print()
    print(f"AI Decision: Close {len(to_close)} positions fighting the trend")
    print()
    
    if not to_close:
        print("Nothing to close - all positions aligned with regime or profitable")
        return
    
    # Sort by worst PnL
    to_close.sort(key=lambda x: x['pnl'])
    
    # Execute closes
    total_loss = 0
    for pos in to_close:
        symbol = pos['symbol']
        side = pos['side']
        size = pos['size']
        pnl = pos['pnl']
        reason = pos['reason']
        
        symbol_clean = symbol.replace('cmt_', '').upper()
        
        print(f"[AI EXIT] Closing {side} {symbol_clean} (${pnl:+.2f})...")
        
        # Execute close order
        result = close_position_manually(symbol, side, size)
        order_id = result.get('order_id')
        
        # Upload AI log
        upload_ai_log_to_weex(
            stage=f"V3.1.6 AI Regime Exit: {side} {symbol_clean}",
            input_data={
                "symbol": symbol,
                "side": side,
                "size": size,
                "unrealized_pnl": pnl,
                "market_regime": regime['regime'],
                "btc_24h_change": regime['change_24h'],
                "btc_4h_change": regime['change_4h'],
            },
            output_data={
                "action": "CLOSE",
                "ai_decision": "REGIME_EXIT",
                "order_id": order_id,
            },
            explanation=f"AI Regime-Aware Exit: {reason}. Market regime is {regime['regime']} (BTC 24h: {regime['change_24h']:+.1f}%, 4h: {regime['change_4h']:+.1f}%). Position was fighting the trend. Closing to free margin for regime-aligned opportunities.",
            order_id=order_id
        )
        
        total_loss += pnl
        print(f"  Order ID: {order_id}")
        print()
    
    print("=" * 60)
    print(f"Closed {len(to_close)} positions")
    print(f"Total realized loss: ${total_loss:.2f}")
    print(f"Freed margin for new trades")
    print("=" * 60)
    print()
    print("Now restart daemon to find new opportunities:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
