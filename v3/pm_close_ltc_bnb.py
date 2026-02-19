"""
V3.2.18 PM Close: LTC + BNB
Portfolio Manager AI rebalancing decision.
Standalone — no numpy dependency.
"""
import sys, os, time, json, hmac, hashlib, base64, requests

# WEEX API credentials
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"
MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.2.18"

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
        "Content-Type": "application/json"
    }

def get_open_positions():
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

def cancel_all_orders(symbol):
    cancelled = []
    try:
        endpoint = f"/capi/v2/order/orders?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        orders = r.json() if isinstance(r.json(), list) else []
        for order in orders:
            oid = order.get("order_id")
            if oid:
                ep = "/capi/v2/order/cancel"
                body = json.dumps({"order_id": oid})
                requests.post(f"{WEEX_BASE_URL}{ep}", headers=weex_headers("POST", ep, body), data=body, timeout=15)
                cancelled.append(oid)
    except: pass
    try:
        endpoint = f"/capi/v2/order/plan_orders?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        orders = r.json() if isinstance(r.json(), list) else []
        for order in orders:
            oid = order.get("order_id")
            if oid:
                ep = "/capi/v2/order/cancel_plan"
                body = json.dumps({"order_id": oid})
                requests.post(f"{WEEX_BASE_URL}{ep}", headers=weex_headers("POST", ep, body), data=body, timeout=15)
                cancelled.append(f"plan_{oid}")
    except: pass
    return cancelled

def close_position(symbol, side, size):
    # Cancel orders first (orphan cleanup)
    cleaned = cancel_all_orders(symbol)
    if cleaned:
        print(f"  Cancelled {len(cleaned)} orders for {symbol}")
    time.sleep(0.5)
    # Place close order
    close_type = "3" if side == "LONG" else "4"
    endpoint = "/capi/v2/order/placeOrder"
    order = {
        "symbol": symbol,
        "client_oid": f"smtv318_pm_{int(time.time()*1000)}",
        "size": str(int(size)),
        "type": close_type,
        "order_type": "0",
        "match_price": "1"
    }
    body = json.dumps(order)
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()

def upload_ai_log(stage, input_data, output_data, explanation, order_id=None):
    endpoint = "/capi/v2/order/uploadAiLog"
    payload = {
        "stage": stage,
        "model": MODEL_NAME,
        "input": input_data,
        "output": output_data,
        "explanation": explanation[:1000]
    }
    if order_id:
        payload["orderId"] = int(order_id)
    body = json.dumps(payload)
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()


# ── MAIN ──
TARGETS = {
    "LTC": (
        "Portfolio Manager: Close {side} LTCUSDT",
        "PM rebalancing: LTC {side} closed. Descending channel from $56 to $53, TP at $54.43 is stale "
        "and unlikely to hit as pair grinds lower in extreme fear regime (F&G=8). Freeing slot for "
        "higher-conviction entries on pairs with clearer trend alignment. "
        "Entry ${entry:.4f}, UPnL ${pnl:+.2f}."
    ),
    "BNB": (
        "Portfolio Manager: Close {side} BNBUSDT",
        "PM rebalancing: BNB {side} closed. Entered ~$620, now ~$603 — significant drawdown in "
        "choppy, directionless market. BNB showing no sustained trend on any timeframe, just noise. "
        "Capital better deployed to trending pairs (ETH ascending trendline, SOL uptrend). "
        "Entry ${entry:.4f}, UPnL ${pnl:+.2f}."
    ),
}

print("=" * 60)
print("V3.2.18 PM Rebalancing: Close LTC + BNB")
print("=" * 60)

positions = get_open_positions()
print(f"Found {len(positions)} open position(s)\n")

for pos in positions:
    sym = pos["symbol"]
    side = pos["side"]
    size = pos["size"]
    pnl = pos["unrealized_pnl"]
    entry = pos["entry_price"]
    pair = sym.replace("cmt_", "").replace("usdt", "").upper()

    if pair not in TARGETS:
        print(f"  KEEP: {pair} {side} | entry=${entry:.4f} | UPnL=${pnl:+.2f}")
        continue

    stage_tpl, expl_tpl = TARGETS[pair]
    stage = stage_tpl.format(side=side)
    explanation = expl_tpl.format(side=side, entry=entry, pnl=pnl)

    print(f"  CLOSE: {pair} {side} | size={size} | entry=${entry:.4f} | UPnL=${pnl:+.2f}")

    # Close position
    result = close_position(sym, side, size)
    order_id = None
    if isinstance(result.get("data"), dict):
        order_id = result["data"].get("orderId") or result["data"].get("order_id")
    if not order_id:
        order_id = result.get("order_id") or result.get("orderId")

    if order_id:
        print(f"    CLOSED: order_id={order_id}")
    else:
        print(f"    RESULT: {result}")

    # Upload AI log
    log_result = upload_ai_log(
        stage=stage,
        input_data={"symbol": sym, "side": side, "size": size, "entry_price": entry, "unrealized_pnl": pnl},
        output_data={"action": "CLOSE", "reason": "pm_rebalancing", "order_id": order_id},
        explanation=explanation,
        order_id=order_id
    )
    log_code = log_result.get("code", "unknown")
    print(f"    AI LOG: {log_code}")

    time.sleep(1)

# Verify
print("\n" + "=" * 60)
time.sleep(2)
remaining = get_open_positions()
print(f"Remaining positions: {len(remaining)}")
for pos in remaining:
    pair = pos["symbol"].replace("cmt_", "").replace("usdt", "").upper()
    print(f"  {pair} {pos['side']} | entry=${pos['entry_price']:.4f} | UPnL=${pos['unrealized_pnl']:+.2f}")
print("=" * 60)
