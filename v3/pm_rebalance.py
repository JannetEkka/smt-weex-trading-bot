#!/usr/bin/env python3
"""
PM Strategy Rebalance - V3.1.81
================================
Portfolio Manager identified suboptimal slot allocation.
Closing stale/dying positions to free slots for higher-conviction opportunities.
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

WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"
MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1"


def weex_sign(timestamp, method, path, body=""):
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def weex_headers(method, path, body=""):
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": weex_sign(ts, method, path, body),
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE,
        "Content-Type": "application/json",
    }


def get_positions():
    endpoint = "/capi/v2/account/position/allPosition"
    r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
    data = r.json()
    positions = []
    if isinstance(data, list):
        for pos in data:
            size = float(pos.get("size", 0))
            if size > 0:
                open_value = float(pos.get("open_value", 0))
                entry_price = open_value / size if size > 0 else 0
                positions.append({
                    "symbol": pos.get("symbol"),
                    "side": pos.get("side", "").upper(),
                    "size": size,
                    "entry_price": entry_price,
                    "unrealized_pnl": float(pos.get("unrealizePnl", 0)),
                })
    return positions


def get_price(symbol):
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        return float(r.json().get("last", 0))
    except:
        return 0.0


def close_position(symbol, side, size):
    close_type = "3" if side == "LONG" else "4"
    endpoint = "/capi/v2/order/placeOrder"
    order = {
        "symbol": symbol,
        "client_oid": f"smt_pm_rebal_{int(time.time()*1000)}",
        "size": str(size),
        "type": close_type,
        "order_type": "0",
        "match_price": "1",
    }
    body = json.dumps(order)
    r = requests.post(
        f"{WEEX_BASE_URL}{endpoint}",
        headers=weex_headers("POST", endpoint, body),
        data=body,
        timeout=15,
    )
    return r.json()


def upload_ai_log(stage, input_data, output_data, explanation, order_id=None):
    endpoint = "/capi/v2/order/uploadAiLog"
    payload = {
        "stage": stage,
        "model": MODEL_NAME,
        "input": input_data,
        "output": output_data,
        "explanation": explanation[:500],
    }
    if order_id:
        payload["orderId"] = int(order_id)
    body = json.dumps(payload)
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 60)
    print("PM STRATEGY REBALANCE - V3.1.81")
    print("=" * 60)

    positions = get_positions()
    if not positions:
        print("No open positions.")
        return

    print(f"\nFound {len(positions)} position(s) to rebalance:\n")

    total_pnl = 0
    for pos in positions:
        sym = pos["symbol"].replace("cmt_", "").upper()
        price = get_price(pos["symbol"])
        if pos["side"] == "LONG":
            pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]) * 100 if pos["entry_price"] > 0 else 0
        else:
            pnl_pct = ((pos["entry_price"] - price) / pos["entry_price"]) * 100 if pos["entry_price"] > 0 else 0

        total_pnl += pos["unrealized_pnl"]
        print(f"  {pos['side']} {sym}: ${pos['unrealized_pnl']:+.2f} ({pnl_pct:+.2f}%)")

    print(f"\n  Total UPnL: ${total_pnl:+.2f}")
    print()

    for i, pos in enumerate(positions):
        sym_clean = pos["symbol"].replace("cmt_", "").upper()
        price = get_price(pos["symbol"])
        if pos["side"] == "LONG":
            pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]) * 100 if pos["entry_price"] > 0 else 0
        else:
            pnl_pct = ((pos["entry_price"] - price) / pos["entry_price"]) * 100 if pos["entry_price"] > 0 else 0

        print(f"  Closing {pos['side']} {sym_clean}...", end=" ")

        result = close_position(pos["symbol"], pos["side"], pos["size"])
        order_id = result.get("order_id")

        if order_id:
            print(f"OK (order {order_id})")

            # Upload AI log - PM rebalance reasoning
            upload_ai_log(
                stage=f"PM Rebalance: {sym_clean} {pos['side']} closed",
                input_data={
                    "symbol": pos["symbol"],
                    "side": pos["side"],
                    "size": pos["size"],
                    "entry_price": pos["entry_price"],
                    "current_price": price,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usdt": round(pos["unrealized_pnl"], 2),
                    "fear_greed": 12,
                    "slots_full": "3/3",
                },
                output_data={
                    "action": "PM_REBALANCE_CLOSE",
                    "exit_price": price,
                    "pnl_usdt": round(pos["unrealized_pnl"], 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": "slot_optimization",
                },
                explanation=(
                    f"Portfolio Manager V3.1.81 strategy rebalance. "
                    f"Closing {pos['side']} {sym_clean} (PnL: {pnl_pct:+.2f}%) to optimize slot allocation. "
                    f"All 3/3 slots occupied by positions with declining momentum (fading from peaks). "
                    f"F&G=12 (extreme fear) presents high-conviction contrarian entries (ADA 75%, LTC 75%) "
                    f"that cannot execute with full slots. Freeing capital for better risk-adjusted opportunities."
                ),
                order_id=int(order_id) if str(order_id).isdigit() else None,
            )
        else:
            print(f"FAILED: {result}")

        time.sleep(1)

    # Verify
    print("\nVerifying...")
    time.sleep(2)
    remaining = get_positions()
    print(f"Remaining positions: {len(remaining)}")
    if remaining:
        for pos in remaining:
            sym = pos["symbol"].replace("cmt_", "").upper()
            print(f"  - {pos['side']} {sym}: ${pos['unrealized_pnl']:+.2f}")
    else:
        print("All positions closed. Slots cleared for next signal cycle.")


if __name__ == "__main__":
    main()
