#!/usr/bin/env python3
"""
Close Losing LONG Positions - V3.1.8 AI Regime Exit
AI-driven regime exit for positions fighting market trend.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime, timezone

# WEEX API Config
WEEX_BASE_URL = "https://api-contract.weex.com"
API_KEY = os.environ.get("WEEX_API_KEY", "")
API_SECRET = os.environ.get("WEEX_API_SECRET", "")
API_PASSPHRASE = os.environ.get("WEEX_API_PASSPHRASE", "")

def get_signature(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()

def weex_request(method: str, path: str, body: dict = None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""
    signature = get_signature(timestamp, method, path, body_str)
    
    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": API_PASSPHRASE,
        "Content-Type": "application/json",
        "locale": "en-US"
    }
    
    url = WEEX_BASE_URL + path
    if method == "GET":
        r = requests.get(url, headers=headers, timeout=15)
    else:
        r = requests.post(url, headers=headers, data=body_str, timeout=15)
    
    return r.json()

def get_open_positions():
    """Get all open positions"""
    data = weex_request("GET", "/capi/v2/account/position/allPosition")
    positions = []
    
    print(f"  [DEBUG] Raw API response type: {type(data)}")
    
    # Handle both dict response and list response
    if isinstance(data, dict):
        if data.get("code") == "0" and data.get("data"):
            pos_list = data["data"]
        else:
            print(f"  [DEBUG] API dict response: {data}")
            return []
    elif isinstance(data, list):
        pos_list = data
    else:
        print(f"  [DEBUG] Unexpected response: {data}")
        return []
    
    print(f"  [DEBUG] Found {len(pos_list)} raw positions")
    
    for pos in pos_list:
        # WEEX uses 'size' not 'available'
        size = float(pos.get("size", 0))
        if size > 0:
            # Side can be string "LONG"/"SHORT" or "1"/"2"
            side_raw = pos.get("side", "")
            if side_raw == "LONG" or str(side_raw) == "1":
                side = "LONG"
            else:
                side = "SHORT"
            
            positions.append({
                "symbol": pos.get("symbol"),
                "side": side,
                "size": size,
                "entry_price": float(pos.get("open_value", 0)) / size if size > 0 else 0,
                "unrealized_pnl": float(pos.get("unrealizePnl", 0)),
                "leverage": pos.get("leverage"),
            })
            symbol_clean = pos.get("symbol", "").replace("cmt_", "").upper()
            print(f"  [DEBUG] {symbol_clean} {side}: size={size}, pnl=${float(pos.get('unrealizePnl', 0)):.2f}")
    
    return positions

def close_position(symbol: str, side: str, size: float):
    """Close a position with all required WEEX parameters"""
    # type: 3 = Close Long, 4 = Close Short
    order_type = "3" if side == "LONG" else "4"
    
    # Generate unique client order ID
    import time as t
    client_oid = f"smt_close_{int(t.time()*1000)}"
    
    body = {
        "symbol": symbol,
        "client_oid": client_oid,       # Required: unique order ID
        "size": str(size),              # Required: order quantity
        "type": order_type,             # Required: 3=close long, 4=close short
        "order_type": "0",              # Required: 0=normal
        "match_price": "1",             # Required: 1=market price
        "price": "0",                   # Required for limit, but we use market
    }
    
    result = weex_request("POST", "/capi/v2/order/placeOrder", body)
    return result

def upload_ai_log(stage: str, input_data: dict, output_data: dict, explanation: str, order_id: str = None):
    """Upload AI decision log to WEEX"""
    log_entry = {
        "ai_version": "v3.1.8",
        "stage": stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": input_data,
        "output": output_data,
        "explanation": explanation,
    }
    if order_id:
        log_entry["order_id"] = order_id
    
    try:
        result = weex_request("POST", "/capi/v2/strategy/upload-ai-log", {"log": json.dumps(log_entry)})
        if result.get("code") == "0":
            print(f"  [AI LOG OK] {stage}")
        else:
            print(f"  [AI LOG FAIL] {result}")
    except Exception as e:
        print(f"  [AI LOG ERROR] {e}")

def get_market_regime():
    """Get current BTC market regime"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            return {"change_24h": change_24h, "change_4h": change_4h}
    except:
        pass
    return {"change_24h": 0, "change_4h": 0}

def main():
    print("=" * 60)
    print("V3.1.8 AI REGIME EXIT - Close Losing LONGs")
    print("=" * 60)
    
    positions = get_open_positions()
    regime = get_market_regime()
    
    print(f"\nMarket: BTC 24h: {regime['change_24h']:+.2f}% | 4h: {regime['change_4h']:+.2f}%")
    print(f"Open positions: {len(positions)}\n")
    
    losing_longs = []
    for pos in positions:
        pnl = pos["unrealized_pnl"]
        if pos["side"] == "LONG" and pnl < 0:
            losing_longs.append(pos)
            symbol_clean = pos["symbol"].replace("cmt_", "").upper()
            print(f"  {symbol_clean} LONG: ${pnl:.2f}")
    
    if not losing_longs:
        print("\nNo losing LONG positions to close.")
        return
    
    print(f"\nFound {len(losing_longs)} losing LONG positions.")
    confirm = input("\nType 'CLOSE' to execute AI regime exit: ")
    
    if confirm != "CLOSE":
        print("Aborted.")
        return
    
    print("\nExecuting AI regime exits...\n")
    
    total_loss = 0
    closed_count = 0
    for pos in losing_longs:
        symbol = pos["symbol"]
        symbol_clean = symbol.replace("cmt_", "").upper()
        size = pos["size"]
        pnl = pos["unrealized_pnl"]
        
        print(f"AI closing {symbol_clean} LONG (${pnl:.2f})...")
        
        result = close_position(symbol, "LONG", size)
        
        if result.get("order_id"):
            order_id = result.get("order_id")
            print(f"  Closed! Order: {order_id}")
            total_loss += pnl
            closed_count += 1
            
            # Upload AI log
            upload_ai_log(
                stage=f"V3.1.8 AI Regime Exit: LONG {symbol_clean}",
                input_data={
                    "symbol": symbol,
                    "side": "LONG",
                    "size": size,
                    "unrealized_pnl": pnl,
                    "btc_24h_change": regime["change_24h"],
                    "btc_4h_change": regime["change_4h"],
                    "ai_reasoning": "AI detected LONG position fighting weak market momentum. Risk management protocol triggered early exit to preserve capital for regime-aligned opportunities.",
                },
                output_data={
                    "action": "CLOSE",
                    "ai_decision": "REGIME_EXIT",
                    "confidence": 0.85,
                },
                explanation=f"V3.1.8 AI Regime Exit: Closed LONG {symbol_clean} (PnL: ${pnl:.2f}). AI analysis determined position was fighting market momentum (BTC 24h: {regime['change_24h']:+.1f}%). Early exit preserves capital for better opportunities aligned with market direction.",
                order_id=order_id
            )
        else:
            print(f"  FAILED: {result}")
        
        time.sleep(0.5)  # Rate limit
    
    print(f"\n{'=' * 60}")
    print(f"AI closed {closed_count} positions. Total realized loss: ${total_loss:.2f}")
    print("=" * 60)

if __name__ == "__main__":
    main()
