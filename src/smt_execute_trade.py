"""
SMT Execute Trade - Signal-Based Execution (FIXED)
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

WEEX_BASE_URL = "https://api-contract.weex.com"
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')

SYMBOL = "cmt_btcusdt"
TRADE_SIZE_USD = 10
LEVERAGE = 20

def get_timestamp():
    return str(int(time.time() * 1000))

def sign(timestamp, method, path, body=""):
    message = timestamp + method.upper() + path + body
    signature = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(signature).decode()

def api_request(method, path, body=None):
    timestamp = get_timestamp()
    body_str = json.dumps(body) if body else ""
    signature = sign(timestamp, method, path, body_str)
    headers = {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": WEEX_PASSPHRASE,
        "Content-Type": "application/json"
    }
    url = WEEX_BASE_URL + path
    if method == "GET":
        response = requests.get(url, headers=headers, timeout=10)
    else:
        response = requests.post(url, headers=headers, data=body_str, timeout=10)
    return response.json()

def get_balance():
    data = api_request("GET", "/capi/v2/account/accounts")
    try:
        return float(data["collateral"][0]["amount"])
    except:
        return 0.0

def get_price():
    url = f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={SYMBOL}"
    response = requests.get(url, timeout=10).json()
    return float(response.get('last', 0))

def set_leverage(leverage):
    return api_request("POST", "/capi/v2/account/leverage", {
        "symbol": SYMBOL,
        "leverage": leverage,
        "marginMode": "crossed"
    })

def place_order(side, size):
    order = {
        "symbol": SYMBOL,
        "client_oid": f"smt_{int(time.time()*1000)}",
        "size": str(size),
        "type": side,
        "order_type": "0",
        "match_price": "1"
    }
    return api_request("POST", "/capi/v2/order/placeOrder", order)

def save_ai_log(action, decision, reasoning, data):
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "log_id": f"smt_{int(time.time()*1000)}",
        "action": action,
        "decision": decision,
        "reasoning": reasoning,
        "data": data
    }
    log_file = os.path.join(log_dir, f"ai_decisions_{datetime.now().strftime('%Y%m%d')}.json")
    logs = []
    if os.path.exists(log_file):
        with open(log_file) as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    logs.append(log_entry)
    with open(log_file, 'w') as f:
        json.dump(logs, f, indent=2)
    return log_entry

def main():
    print("=" * 70)
    print("SMT EXECUTE TRADE - Signal-Based Execution")
    print("=" * 70)
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_dir = os.path.dirname(script_dir)
    signal_file = os.path.join(repo_dir, "ai_logs", "signal_latest.json")
    
    print(f"\n[1] Loading Signal")
    
    if not os.path.exists(signal_file):
        print("[ERROR] Signal file not found!")
        return
    
    with open(signal_file) as f:
        signal_data = json.load(f)
    
    if not signal_data.get('ready_to_trade'):
        print("[ERROR] Signal not ready to trade!")
        return
    
    whale = signal_data.get('whale', {})
    flow = signal_data.get('flow', {})
    sig = signal_data.get('signal', {})
    val = signal_data.get('validation', {})
    
    print(f"\n[SIGNAL]")
    print(f"  Whale: {whale.get('sub_label')} - {flow.get('net_direction')} {flow.get('net_value', 0):.2f} BTC")
    print(f"  Signal: {sig.get('signal')} @ {sig.get('confidence', 0):.0%} confidence")
    
    print(f"\n[2] Market Data")
    balance = get_balance()
    price = get_price()
    
    # FIXED: stepSize is 0.0001 BTC minimum
    # 0.0001 BTC at $87,000 = $8.70 (close enough to 10 USDT)
    trade_size_btc = 0.0001  # Minimum valid size
    actual_usd = trade_size_btc * price
    
    print(f"  Balance: {balance:.2f} USDT")
    print(f"  BTC Price: ${price:,.2f}")
    print(f"  Trade Size: {trade_size_btc} BTC (~${actual_usd:.2f})")
    
    signal_direction = sig.get('signal', 'NEUTRAL')
    if signal_direction == 'LONG':
        open_side = "1"
        close_side = "3"
    elif signal_direction == 'SHORT':
        open_side = "2"
        close_side = "4"
    else:
        print("[ERROR] Signal is NEUTRAL")
        return
    
    print(f"\n[3] TRADE CONFIRMATION")
    print(f"  Direction: {signal_direction}")
    print(f"  Size: {trade_size_btc} BTC (~${actual_usd:.2f})")
    print(f"  Leverage: {LEVERAGE}x")
    
    print(f"\n*** REAL MONEY ***")
    confirm = input("Type 'EXECUTE' to proceed: ")
    
    if confirm != "EXECUTE":
        print("Cancelled.")
        return
    
    print(f"\n[4] Setting Leverage {LEVERAGE}x...")
    lev_result = set_leverage(LEVERAGE)
    print(f"  Result: {lev_result}")
    
    save_ai_log("OPEN_POSITION", "EXECUTE", f"Whale: {whale.get('sub_label')} {flow.get('net_direction')} {flow.get('net_value', 0):.2f} BTC. Signal: {signal_direction}", {"whale": whale, "signal": sig})
    
    print(f"\n[5] Opening {signal_direction}...")
    open_result = place_order(open_side, trade_size_btc)
    print(f"  Result: {open_result}")
    
    if open_result.get('code') != 0 and open_result.get('code') != '0':
        print(f"  [ERROR] Order failed: {open_result.get('msg')}")
        return
    
    save_ai_log("ORDER_EXECUTED", "SUCCESS", f"{signal_direction} opened", {"result": open_result})
    
    print(f"\n[6] Waiting 5 seconds...")
    time.sleep(5)
    
    print(f"\n[7] Closing Position...")
    close_result = place_order(close_side, trade_size_btc)
    print(f"  Result: {close_result}")
    
    save_ai_log("ORDER_EXECUTED", "SUCCESS", "Position closed", {"result": close_result})
    
    print(f"\n[8] Summary")
    final_balance = get_balance()
    pnl = final_balance - balance
    print(f"  Initial: {balance:.2f} USDT")
    print(f"  Final: {final_balance:.2f} USDT")
    print(f"  P/L: {pnl:+.4f} USDT")
    
    save_ai_log("TRADE_COMPLETE", "SUCCESS", f"PnL: {pnl:+.4f}", {"pnl": pnl})
    
    print(f"\n" + "=" * 70)
    print("DONE - Commit: git add . && git commit -m 'First AI trade' && git push")
    print("=" * 70)

if __name__ == "__main__":
    main()
