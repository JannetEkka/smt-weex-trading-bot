#!/usr/bin/env python3
"""
Close BTC Position - Take Profit
================================
Closes current BTC long position at market price with AI log.
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

# WEEX API
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1"


def weex_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def weex_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": weex_sign(ts, method, path, body),
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE,
        "Content-Type": "application/json"
    }


def get_price(symbol: str) -> float:
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        return float(r.json().get("last", 0))
    except:
        return 0.0


def get_position(symbol: str) -> dict:
    """Get position details for a symbol"""
    try:
        endpoint = "/capi/v2/account/position/allPosition"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        if isinstance(data, list):
            for pos in data:
                if pos.get("symbol") == symbol and float(pos.get("size", 0)) > 0:
                    return {
                        "found": True,
                        "side": pos.get("side", "").upper(),
                        "size": float(pos.get("size", 0)),
                        "open_value": float(pos.get("open_value", 0)),
                        "entry_price": float(pos.get("open_value", 0)) / float(pos.get("size", 1)),
                        "unrealized_pnl": float(pos.get("unrealizePnl", 0)),
                        "margin": float(pos.get("marginSize", 0)),
                    }
        return {"found": False}
    except Exception as e:
        print(f"Error getting position: {e}")
        return {"found": False}


def cancel_plan_orders(symbol: str) -> dict:
    """Cancel TP/SL plan orders for symbol"""
    cancelled = []
    try:
        endpoint = f"/capi/v2/order/plan_orders?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        orders = r.json() if isinstance(r.json(), list) else []
        
        for order in orders:
            oid = order.get("order_id")
            if oid:
                cancel_endpoint = "/capi/v2/order/cancel_plan"
                body = json.dumps({"order_id": oid})
                requests.post(
                    f"{WEEX_BASE_URL}{cancel_endpoint}",
                    headers=weex_headers("POST", cancel_endpoint, body),
                    data=body,
                    timeout=15
                )
                cancelled.append(oid)
                print(f"  Cancelled plan order: {oid}")
    except Exception as e:
        print(f"  Error cancelling plan orders: {e}")
    
    return {"cancelled": cancelled}


def close_position(symbol: str, side: str, size: float) -> dict:
    """Close position at market"""
    # side "LONG" -> close type "3", side "SHORT" -> close type "4"
    close_type = "3" if side == "LONG" else "4"
    
    endpoint = "/capi/v2/order/placeOrder"
    order = {
        "symbol": symbol,
        "client_oid": f"smt_close_{int(time.time()*1000)}",
        "size": str(size),
        "type": close_type,
        "order_type": "0",
        "match_price": "1"  # Market order
    }
    
    body = json.dumps(order)
    r = requests.post(
        f"{WEEX_BASE_URL}{endpoint}",
        headers=weex_headers("POST", endpoint, body),
        data=body,
        timeout=15
    )
    return r.json()


def upload_ai_log(stage: str, input_data: dict, output_data: dict, explanation: str, order_id: int = None) -> dict:
    """Upload AI decision log to WEEX"""
    endpoint = "/capi/v2/order/uploadAiLog"
    
    payload = {
        "stage": stage,
        "model": MODEL_NAME,
        "input": input_data,
        "output": output_data,
        "explanation": explanation[:500]
    }
    
    if order_id:
        payload["orderId"] = int(order_id)
    
    body = json.dumps(payload)
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    symbol = "cmt_btcusdt"
    
    print("=" * 60)
    print("CLOSE BTC POSITION - TAKE PROFIT")
    print("=" * 60)
    
    # 1. Get current position
    print("\n[1] Getting BTC position...")
    position = get_position(symbol)
    
    if not position.get("found"):
        print("    No BTC position found!")
        return
    
    print(f"    Side: {position['side']}")
    print(f"    Size: {position['size']}")
    print(f"    Entry: ${position['entry_price']:,.2f}")
    print(f"    Unrealized PnL: ${position['unrealized_pnl']:+.2f}")
    
    # 2. Get current price
    print("\n[2] Getting current price...")
    current_price = get_price(symbol)
    print(f"    BTC/USDT: ${current_price:,.2f}")
    
    # Calculate PnL %
    if position['side'] == "LONG":
        pnl_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
    else:
        pnl_pct = ((position['entry_price'] - current_price) / position['entry_price']) * 100
    
    print(f"    PnL: {pnl_pct:+.2f}%")
    
    # 3. Cancel existing TP/SL orders
    print("\n[3] Cancelling existing TP/SL orders...")
    cancel_result = cancel_plan_orders(symbol)
    print(f"    Cancelled: {len(cancel_result['cancelled'])} orders")
    
    # 4. Close position at market
    print("\n[4] Closing position at market...")
    close_result = close_position(symbol, position['side'], position['size'])
    
    order_id = close_result.get("order_id")
    if order_id:
        print(f"    Order ID: {order_id}")
        print("    Position closed successfully!")
    else:
        print(f"    Close result: {close_result}")
        # Try to extract order_id from different response formats
        if isinstance(close_result, dict):
            order_id = close_result.get("orderId") or close_result.get("data", {}).get("order_id")
    
    # 5. Upload AI log
    print("\n[5] Uploading AI decision log...")
    
    ai_log_result = upload_ai_log(
        stage="V3.1 Manual Take Profit - BTCUSDT",
        input_data={
            "symbol": symbol,
            "position_side": position['side'],
            "position_size": position['size'],
            "entry_price": position['entry_price'],
            "current_price": current_price,
            "unrealized_pnl": position['unrealized_pnl'],
        },
        output_data={
            "action": "CLOSE_POSITION",
            "reason": "take_profit",
            "pnl_percent": round(pnl_pct, 2),
            "pnl_usdt": round(position['unrealized_pnl'], 2),
        },
        explanation=f"Manual take profit decision. Position was {position['side']} from ${position['entry_price']:,.2f}. "
                    f"Current price ${current_price:,.2f}. Locking in {pnl_pct:+.2f}% profit (${position['unrealized_pnl']:+.2f} USDT). "
                    f"Risk management: securing gains before potential reversal. Will re-evaluate for new entry.",
        order_id=int(order_id) if order_id and str(order_id).isdigit() else None
    )
    
    print(f"    AI Log uploaded: {ai_log_result}")
    
    # 6. Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Closed: {position['side']} {position['size']} BTC")
    print(f"  Entry: ${position['entry_price']:,.2f}")
    print(f"  Exit: ${current_price:,.2f}")
    print(f"  PnL: {pnl_pct:+.2f}% (${position['unrealized_pnl']:+.2f})")
    print("\n  Daemon will check for new signals on next cycle (30 min)")
    print("  Or restart daemon to trigger immediate signal check")


if __name__ == "__main__":
    main()
