"""
Pyramiding + Breakeven SL System
Adds to winners and protects profits
"""

def move_sl_to_breakeven(symbol: str, side: str, entry_price: float, 
                         current_price: float, profit_threshold: float = 2.0) -> bool:
    """
    Move stop loss to breakeven when position is in profit
    
    Args:
        symbol: Trading pair (e.g., 'cmt_btcusdt')
        side: 'LONG' or 'SHORT'
        entry_price: Original entry price
        current_price: Current market price
        profit_threshold: % profit before moving to breakeven (default 2%)
    
    Returns:
        True if SL moved successfully, False otherwise
    """
    import requests
    from smt_nightly_trade_v3_1 import WEEX_BASE_URL, generate_signature
    
    # Calculate current profit %
    if side == "LONG":
        profit_pct = ((current_price - entry_price) / entry_price) * 100
        breakeven_price = entry_price * 1.001  # +0.1% for fees
    else:  # SHORT
        profit_pct = ((entry_price - current_price) / entry_price) * 100
        breakeven_price = entry_price * 0.999  # -0.1% for fees
    
    # Only move if profitable enough
    if profit_pct < profit_threshold:
        return False
    
    # Cancel existing SL order
    try:
        # Get open orders
        timestamp = str(int(time.time() * 1000))
        method = "GET"
        path = f"/capi/v2/order/orders?symbol={symbol}"
        
        headers = {
            "X-WEEXC-APIKEY": os.getenv("WEEX_API_KEY"),
            "X-WEEXC-SIGN": generate_signature(timestamp, method, path, ""),
            "X-WEEXC-PASSPHRASE": os.getenv("WEEX_API_PASSPHRASE"),
            "X-WEEXC-TIMESTAMP": timestamp,
        }
        
        response = requests.get(f"{WEEX_BASE_URL}{path}", headers=headers, timeout=10)
        orders = response.json().get("data", [])
        
        # Cancel SL orders
        for order in orders:
            if order.get("orderType") == "5":  # Stop loss order
                cancel_url = f"{WEEX_BASE_URL}/capi/v2/order/cancel"
                cancel_body = {"orderId": order["orderId"], "symbol": symbol}
                
                timestamp = str(int(time.time() * 1000))
                method = "POST"
                path = "/capi/v2/order/cancel"
                body_str = json.dumps(cancel_body)
                
                headers["X-WEEXC-SIGN"] = generate_signature(timestamp, method, path, body_str)
                headers["X-WEEXC-TIMESTAMP"] = timestamp
                
                requests.post(cancel_url, json=cancel_body, headers=headers, timeout=10)
        
        # Place new SL at breakeven
        sl_body = {
            "symbol": symbol,
            "side": "3" if side == "LONG" else "4",  # Close long/short
            "orderType": "5",  # Stop loss
            "triggerPrice": str(round(breakeven_price, 2)),
            "size": str(get_position_size(symbol)),
        }
        
        timestamp = str(int(time.time() * 1000))
        method = "POST"
        path = "/capi/v2/order/placeOrder"
        body_str = json.dumps(sl_body)
        
        headers["X-WEEXC-SIGN"] = generate_signature(timestamp, method, path, body_str)
        headers["X-WEEXC-TIMESTAMP"] = timestamp
        
        response = requests.post(
            f"{WEEX_BASE_URL}{path}",
            json=sl_body,
            headers=headers,
            timeout=10
        )
        
        if response.json().get("code") == "0":
            print(f"  [BREAKEVEN] Moved SL to ${breakeven_price:.2f} (was at loss level)")
            return True
            
    except Exception as e:
        print(f"  [BREAKEVEN] Failed to move SL: {e}")
    
    return False


def should_pyramid(symbol: str, entry_price: float, current_price: float,
                   side: str, hours_open: float, current_size: float) -> dict:
    """
    Determine if position should be pyramided (add to winner)
    
    Returns:
        dict with 'should_add' (bool) and 'add_size' (float)
    """
    # Calculate profit %
    if side == "LONG":
        profit_pct = ((current_price - entry_price) / entry_price) * 100
    else:
        profit_pct = ((entry_price - current_price) / entry_price) * 100
    
    # Pyramiding rules
    if profit_pct >= 4.0 and hours_open >= 8:
        # +4% after 8h: Add 20% more
        return {"should_add": True, "add_pct": 0.20, "reason": "+4% after 8h"}
    
    elif profit_pct >= 2.0 and hours_open >= 4:
        # +2% after 4h: Add 30% more
        return {"should_add": True, "add_pct": 0.30, "reason": "+2% after 4h"}
    
    return {"should_add": False, "add_pct": 0, "reason": ""}


def execute_pyramid(symbol: str, side: str, add_pct: float, 
                    current_size: float, leverage: int) -> dict:
    """
    Add to winning position (pyramid)
    
    Args:
        symbol: Trading pair
        side: 'LONG' or 'SHORT'
        add_pct: % of current size to add (e.g., 0.30 = 30%)
        current_size: Current position size
        leverage: Same leverage as original position
    
    Returns:
        dict with success status and new order details
    """
    from smt_nightly_trade_v3_1 import execute_single_trade, get_price
    
    try:
        # Calculate add size
        add_size = current_size * add_pct
        current_price = get_price(symbol)
        
        # Use same leverage as original
        opportunity = {
            "pair": symbol.replace('cmt_', '').replace('usdt', '').upper(),
            "pair_info": {"symbol": symbol, "tier": 1},  # Will be adjusted
            "decision": {
                "signal": side,
                "confidence": 0.85,  # High confidence for pyramiding
                "reasoning": f"Pyramiding into winner (+{add_pct*100:.0f}%)"
            }
        }
        
        result = execute_single_trade(opportunity, leverage, add_size)
        
        if result.get("success"):
            print(f"  [PYRAMID] Added {add_pct*100:.0f}% to {symbol} {side}")
            print(f"  [PYRAMID] New size: {add_size:.4f} @ ${current_price:.2f}")
            return {"success": True, "order_id": result.get("order_id")}
        
    except Exception as e:
        print(f"  [PYRAMID] Failed: {e}")
    
    return {"success": False, "error": str(e)}


# Test the system
if __name__ == "__main__":
    print("PYRAMIDING + BREAKEVEN SYSTEM")
    print("="*60)
    
    # Test breakeven logic
    print("\nBreakeven SL Examples:")
    print("-"*60)
    
    # Example 1: LONG with 2.5% profit
    entry = 71000
    current = 72775  # +2.5%
    breakeven = entry * 1.001  # +0.1% for fees
    print(f"BTC LONG: Entry ${entry:,.0f} → Current ${current:,.0f}")
    print(f"  Profit: +2.5% → Move SL to ${breakeven:,.0f} (breakeven)")
    
    # Example 2: SHORT with 3% profit
    entry = 87000
    current = 84390  # +3%
    breakeven = entry * 0.999  # -0.1% for fees
    print(f"\nSOL SHORT: Entry ${entry:,.0f} → Current ${current:,.0f}")
    print(f"  Profit: +3% → Move SL to ${breakeven:,.0f} (breakeven)")
    
    # Test pyramiding logic
    print("\n\nPyramiding Examples:")
    print("-"*60)
    
    # Example: BTC LONG winning
    print("BTC LONG @ $71,000:")
    print(f"  After 4h at $72,420 (+2%): Add 30% more")
    print(f"  After 8h at $73,840 (+4%): Add 20% more")
    print(f"  Total position: 150% of original (1.5x size)")
    
    print("\n" + "="*60)
