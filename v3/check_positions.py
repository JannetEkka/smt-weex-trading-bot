import requests, hmac, hashlib, base64, time, json

WEEX_API_KEY = "weex_cda1971e60e00a1f6ce7393c1fa2cf86"
WEEX_API_SECRET = "15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c"
WEEX_API_PASSPHRASE = "weex8282888"
BASE_URL = "https://api-contract.weex.com"

def weex_headers(method, path, body=""):
    ts = str(int(time.time() * 1000))
    msg = ts + method.upper() + path + body
    sig = base64.b64encode(hmac.new(WEEX_API_SECRET.encode(), msg.encode(), hashlib.sha256).digest()).decode()
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": sig,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

r = requests.get(f"{BASE_URL}/capi/v2/account/position/allPosition", headers=weex_headers("GET", "/capi/v2/account/position/allPosition"), timeout=10)
data = r.json()

print("Open Positions:")
print("-" * 60)
if isinstance(data, list):
    for p in data:
        if float(p.get("amount", 0)) > 0:
            print(f"  {p['symbol']}: {p['side']} size={p['amount']} PnL=${p.get('unrealizePnl', '?')}")
else:
    print(json.dumps(data, indent=2))
