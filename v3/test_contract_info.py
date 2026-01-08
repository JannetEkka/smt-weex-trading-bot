#!/usr/bin/env python3
"""
Test WEEX order placement with proper stepSize handling
"""

import requests

WEEX_BASE_URL = "https://api-contract.weex.com"

def get_contract_info(symbol: str):
    """Get contract info including stepSize from WEEX"""
    r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/contracts?symbol={symbol}", timeout=10)
    data = r.json()
    
    if isinstance(data, list) and len(data) > 0:
        info = data[0]
        return {
            "symbol": symbol,
            "size_increment": info.get("size_increment", "3"),
            "min_order_size": info.get("minOrderSize", "0.001"),
            "max_order_size": info.get("maxOrderSize", "100000"),
        }
    return None


def round_size_to_step(size: float, size_increment: str) -> float:
    """Round size to match stepSize requirement"""
    increment = int(size_increment)
    
    if increment >= 0:
        return round(size, increment)
    else:
        step = 10 ** abs(increment)
        return round(size / step) * step


# Test all trading pairs
pairs = [
    "cmt_ethusdt",
    "cmt_btcusdt", 
    "cmt_solusdt",
    "cmt_dogeusdt",
    "cmt_xrpusdt",
    "cmt_adausdt",
    "cmt_bnbusdt",
    "cmt_ltcusdt",
]

print("WEEX Contract Info for All Trading Pairs")
print("=" * 60)

for symbol in pairs:
    info = get_contract_info(symbol)
    if info:
        # Get price
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        price = float(r.json().get("last", 0))
        
        # Calculate size for $100
        raw_size = 100 / price if price > 0 else 0
        rounded_size = round_size_to_step(raw_size, info["size_increment"])
        
        print(f"\n{symbol}:")
        print(f"  Price: ${price:.4f}")
        print(f"  size_increment: {info['size_increment']} (decimals, negative = multiples of 10)")
        print(f"  min_order_size: {info['min_order_size']}")
        print(f"  For $100: raw={raw_size:.6f}, rounded={rounded_size}")
    else:
        print(f"\n{symbol}: ERROR getting contract info")

print("\n" + "=" * 60)
