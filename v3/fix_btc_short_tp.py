#!/usr/bin/env python3
"""
Fix BTC SHORT TP on WEEX
=========================
Current: TP at $61,806.90 (9.01% from entry $67,924.20)
Target:  TP at ~$64,527.99 (5.0% from entry $67,924.20)

This cancels existing plan orders and places new ones with corrected TP.
SL stays the same ($69,957.30 = ~3.0%).
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import requests

WEEX_BASE_URL = "https://api-contract.weex.com"
API_KEY = os.environ.get("WEEX_API_KEY", "weex_cda1971e60e00a1f6ce7393c1fa2cf86")
API_SECRET = os.environ.get("WEEX_API_SECRET", "15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c")
PASSPHRASE = os.environ.get("WEEX_API_PASSPHRASE", "weex8282888")

SYMBOL = "cmt_btcusdt"
ENTRY_PRICE = 67924.20
NEW_TP_PCT = 5.0  # Tier 1 realistic TP
# TP for SHORT = entry * (1 - tp_pct/100)
NEW_TP_PRICE = round(ENTRY_PRICE * (1 - NEW_TP_PCT / 100), 1)
EXISTING_SL_PRICE = 69957.30  # Keep SL the same

def weex_headers(method, path, body=''):
    timestamp = str(int(time.time()))
    message = timestamp + method.upper() + path + body
    sig = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(sig).decode()
    return {
        'ACCESS-KEY': API_KEY,
        'ACCESS-SIGN': signature,
        'ACCESS-TIMESTAMP': timestamp,
        'ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json',
        'locale': 'en-US',
    }

def get_plan_orders():
    """Get existing plan (TP/SL) orders for BTC"""
    path = f"/capi/v2/order/plan/currentPlan?symbol={SYMBOL}&pageSize=50"
    r = requests.get(f"{WEEX_BASE_URL}{path}", headers=weex_headers('GET', path), timeout=10)
    data = r.json()
    if data.get("code") == "00000":
        return data.get("data", [])
    print(f"Error getting plan orders: {data}")
    return []

def cancel_plan_order(order_id):
    """Cancel a specific plan order"""
    path = "/capi/v2/order/plan/cancelPlan"
    body = json.dumps({"orderId": str(order_id), "symbol": SYMBOL})
    r = requests.post(f"{WEEX_BASE_URL}{path}", headers=weex_headers('POST', path, body), data=body, timeout=10)
    data = r.json()
    return data

def place_plan_order(trigger_price, size, order_type, client_oid_prefix):
    """Place a plan order (TP or SL)"""
    path = "/capi/v2/order/plan/placePlan"
    body = json.dumps({
        'symbol': SYMBOL,
        'client_oid': f'{client_oid_prefix}_{int(time.time()*1000)}',
        'size': str(size),
        'type': str(order_type),  # 3=close long, 4=close short
        'match_type': '1',  # market
        'execute_price': '0',  # market price
        'trigger_price': str(trigger_price)
    })
    r = requests.post(f"{WEEX_BASE_URL}{path}", headers=weex_headers('POST', path, body), data=body, timeout=10)
    data = r.json()
    return data

def get_position():
    """Get current BTC position"""
    path = "/capi/v2/account/position/allPosition"
    r = requests.get(f"{WEEX_BASE_URL}{path}", headers=weex_headers('GET', path), timeout=10)
    data = r.json()
    if data.get("code") == "00000":
        positions = data.get("data", [])
        for p in positions:
            if p.get("symbol") == SYMBOL and p.get("side", "").upper() == "SHORT":
                return p
    return None

def main():
    print("=" * 60)
    print("FIX BTC SHORT TP")
    print("=" * 60)
    
    # 1. Verify position exists
    pos = get_position()
    if not pos:
        print("ERROR: No BTC SHORT position found")
        return
    
    size = float(pos.get("size", 0))
    entry = float(pos.get("entry_price", 0))
    pnl = float(pos.get("unrealized_pnl", 0))
    print(f"Position: SHORT {size} BTC")
    print(f"Entry: ${entry}")
    print(f"UPnL: ${pnl:+.2f}")
    print(f"New TP: ${NEW_TP_PRICE} ({NEW_TP_PCT}% from entry)")
    print(f"SL stays: ${EXISTING_SL_PRICE}")
    
    # 2. Get existing plan orders
    print(f"\nFetching plan orders...")
    orders = get_plan_orders()
    
    tp_orders = []
    sl_orders = []
    for o in orders:
        trigger = float(o.get("trigger_price", 0))
        otype = o.get("type", "")
        oid = o.get("orderId", "")
        # type 4 = close short. Trigger below entry = TP, above entry = SL
        if otype == "4":
            if trigger < entry:
                tp_orders.append(o)
                print(f"  Found TP order: ${trigger} (id: {oid})")
            else:
                sl_orders.append(o)
                print(f"  Found SL order: ${trigger} (id: {oid})")
    
    if not tp_orders:
        print("WARNING: No TP order found to replace")
    
    # 3. Cancel old TP orders
    for tp_o in tp_orders:
        oid = tp_o.get("orderId")
        print(f"\nCancelling old TP order {oid}...")
        result = cancel_plan_order(oid)
        print(f"  Result: {result.get('code')} - {result.get('msg', 'ok')}")
        time.sleep(0.5)
    
    # 4. Place new TP order with corrected price
    print(f"\nPlacing new TP at ${NEW_TP_PRICE}...")
    tp_result = place_plan_order(
        trigger_price=round(NEW_TP_PRICE, 1),
        size=size,
        order_type=4,  # close short
        client_oid_prefix="smt_tp_fix"
    )
    print(f"  Result: {tp_result.get('code')} - {tp_result.get('msg', 'ok')}")
    if tp_result.get("code") == "00000":
        print(f"\nSUCCESS: BTC SHORT TP updated to ${NEW_TP_PRICE} ({NEW_TP_PCT}%)")
    else:
        print(f"\nFAILED: {tp_result}")
    
    print(f"\nDone. SL unchanged at ${EXISTING_SL_PRICE}")


if __name__ == "__main__":
    confirm = input(f"Update BTC SHORT TP from ~$61,807 to ${NEW_TP_PRICE}? (yes/no): ")
    if confirm.lower() == "yes":
        main()
    else:
        print("Aborted.")
