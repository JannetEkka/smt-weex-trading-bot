#!/usr/bin/env python3
"""
Check and Close All Positions
=============================
Shows all open positions and allows closing them.
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


def get_all_positions() -> list:
    """Get ALL open positions"""
    try:
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
                        "margin": float(pos.get("marginSize", 0)),
                        "leverage": pos.get("leverage", "20"),
                        "created_time": pos.get("created_time"),
                    })
        return positions
    except Exception as e:
        print(f"Error getting positions: {e}")
        return []


def get_balance() -> dict:
    """Get account balance"""
    try:
        endpoint = "/capi/v2/account/assets"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        if isinstance(data, list):
            for asset in data:
                if asset.get("coinName") == "USDT":
                    return {
                        "available": float(asset.get("available", 0)),
                        "equity": float(asset.get("equity", 0)),
                    }
        return {"available": 0, "equity": 0}
    except Exception as e:
        print(f"Error getting balance: {e}")
        return {"available": 0, "equity": 0}


def close_position(symbol: str, side: str, size: float) -> dict:
    """Close a position at market"""
    close_type = "3" if side == "LONG" else "4"
    
    endpoint = "/capi/v2/order/placeOrder"
    order = {
        "symbol": symbol,
        "client_oid": f"smt_close_{int(time.time()*1000)}",
        "size": str(size),
        "type": close_type,
        "order_type": "0",
        "match_price": "1"
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


def stop_daemon():
    """Try to stop the daemon service"""
    print("\n[!] Attempting to stop daemon...")
    import subprocess
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", "smt-trading-v31"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print("    Daemon stopped successfully")
            return True
        else:
            print(f"    Could not stop daemon: {result.stderr}")
            return False
    except Exception as e:
        print(f"    Error stopping daemon: {e}")
        return False


def main():
    print("=" * 70)
    print("SMT POSITION CHECKER & CLOSER")
    print("=" * 70)
    
    # Check if we should stop daemon first
    if "--stop-daemon" in sys.argv:
        stop_daemon()
        time.sleep(2)
    
    # Get balance
    print("\n[1] ACCOUNT BALANCE")
    print("-" * 40)
    balance = get_balance()
    print(f"    Equity: ${balance['equity']:.2f}")
    print(f"    Available: ${balance['available']:.2f}")
    
    # Get all positions
    print("\n[2] ALL OPEN POSITIONS")
    print("-" * 40)
    positions = get_all_positions()
    
    if not positions:
        print("    No open positions found!")
        return
    
    print(f"    Found {len(positions)} open position(s):\n")
    
    total_pnl = 0
    for i, pos in enumerate(positions, 1):
        symbol_clean = pos['symbol'].replace('cmt_', '').upper()
        current_price = get_price(pos['symbol'])
        
        # Calculate PnL %
        if pos['side'] == "LONG":
            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100 if pos['entry_price'] > 0 else 0
        else:
            pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100 if pos['entry_price'] > 0 else 0
        
        total_pnl += pos['unrealized_pnl']
        
        # Created time
        created = "Unknown"
        if pos.get('created_time'):
            try:
                dt = datetime.fromtimestamp(int(pos['created_time']) / 1000, tz=timezone.utc)
                created = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
            except:
                pass
        
        print(f"    [{i}] {pos['side']} {symbol_clean}")
        print(f"        Size: {pos['size']}")
        print(f"        Entry: ${pos['entry_price']:,.4f}")
        print(f"        Current: ${current_price:,.4f}")
        print(f"        PnL: ${pos['unrealized_pnl']:+.2f} ({pnl_pct:+.2f}%)")
        print(f"        Margin: ${pos['margin']:.2f}")
        print(f"        Opened: {created}")
        print()
    
    print(f"    TOTAL UNREALIZED PnL: ${total_pnl:+.2f}")
    
    # Ask what to do
    print("\n[3] OPTIONS")
    print("-" * 40)
    print("    1. Close ALL positions")
    print("    2. Close specific position(s)")
    print("    3. Exit (do nothing)")
    
    if "--close-all" in sys.argv:
        choice = "1"
        print("\n    Auto-selected: Close ALL (--close-all flag)")
    else:
        choice = input("\n    Enter choice (1/2/3): ").strip()
    
    if choice == "1":
        print("\n[4] CLOSING ALL POSITIONS")
        print("-" * 40)
        
        for pos in positions:
            symbol_clean = pos['symbol'].replace('cmt_', '').upper()
            current_price = get_price(pos['symbol'])
            
            print(f"\n    Closing {pos['side']} {symbol_clean}...")
            
            result = close_position(pos['symbol'], pos['side'], pos['size'])
            order_id = result.get("order_id")
            
            if order_id:
                print(f"    Order ID: {order_id}")
                
                # Calculate final PnL
                if pos['side'] == "LONG":
                    pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                else:
                    pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
                
                # Upload AI log
                ai_result = upload_ai_log(
                    stage=f"V3.1 Close Position - {symbol_clean}",
                    input_data={
                        "symbol": pos['symbol'],
                        "side": pos['side'],
                        "size": pos['size'],
                        "entry_price": pos['entry_price'],
                    },
                    output_data={
                        "action": "CLOSE_POSITION",
                        "exit_price": current_price,
                        "pnl_usdt": pos['unrealized_pnl'],
                        "pnl_pct": round(pnl_pct, 2),
                    },
                    explanation=f"Closing {pos['side']} {symbol_clean} position. Entry: ${pos['entry_price']:,.2f}, Exit: ${current_price:,.2f}. Realized PnL: {pnl_pct:+.2f}%",
                    order_id=int(order_id) if str(order_id).isdigit() else None
                )
                print(f"    AI Log: {ai_result.get('msg', ai_result)}")
            else:
                print(f"    Close failed: {result}")
            
            time.sleep(1)
        
        print("\n    All positions closed!")
        
    elif choice == "2":
        positions_to_close = input("    Enter position numbers to close (comma-separated, e.g., 1,3,5): ").strip()
        indices = [int(x.strip()) - 1 for x in positions_to_close.split(",") if x.strip().isdigit()]
        
        for idx in indices:
            if 0 <= idx < len(positions):
                pos = positions[idx]
                symbol_clean = pos['symbol'].replace('cmt_', '').upper()
                current_price = get_price(pos['symbol'])
                
                print(f"\n    Closing {pos['side']} {symbol_clean}...")
                
                result = close_position(pos['symbol'], pos['side'], pos['size'])
                order_id = result.get("order_id")
                
                if order_id:
                    print(f"    Order ID: {order_id}")
                    
                    if pos['side'] == "LONG":
                        pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                    else:
                        pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
                    
                    ai_result = upload_ai_log(
                        stage=f"V3.1 Close Position - {symbol_clean}",
                        input_data={
                            "symbol": pos['symbol'],
                            "side": pos['side'],
                            "size": pos['size'],
                            "entry_price": pos['entry_price'],
                        },
                        output_data={
                            "action": "CLOSE_POSITION",
                            "exit_price": current_price,
                            "pnl_usdt": pos['unrealized_pnl'],
                            "pnl_pct": round(pnl_pct, 2),
                        },
                        explanation=f"Closing {pos['side']} {symbol_clean} position. Entry: ${pos['entry_price']:,.2f}, Exit: ${current_price:,.2f}. Realized PnL: {pnl_pct:+.2f}%",
                        order_id=int(order_id) if str(order_id).isdigit() else None
                    )
                    print(f"    AI Log: {ai_result.get('msg', ai_result)}")
                else:
                    print(f"    Close failed: {result}")
                
                time.sleep(1)
    else:
        print("\n    Exiting without changes.")
    
    # Final check
    print("\n[5] FINAL POSITION CHECK")
    print("-" * 40)
    time.sleep(2)
    final_positions = get_all_positions()
    print(f"    Remaining positions: {len(final_positions)}")
    
    if final_positions:
        for pos in final_positions:
            symbol_clean = pos['symbol'].replace('cmt_', '').upper()
            print(f"    - {pos['side']} {symbol_clean}: {pos['size']} (${pos['unrealized_pnl']:+.2f})")


if __name__ == "__main__":
    main()
