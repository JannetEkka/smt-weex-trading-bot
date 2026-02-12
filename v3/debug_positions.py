#!/usr/bin/env python3
"""Debug: dump raw WEEX allPosition response"""
import os, json, time, hmac, hashlib, base64, requests

WEEX_BASE_URL = "https://api-contract.weex.com"
API_KEY = os.environ.get("WEEX_API_KEY", "weex_cda1971e60e00a1f6ce7393c1fa2cf86")
API_SECRET = os.environ.get("WEEX_API_SECRET", "15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c")
PASSPHRASE = os.environ.get("WEEX_API_PASSPHRASE", "weex8282888")

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

path = "/capi/v2/account/position/allPosition"
r = requests.get(f"{WEEX_BASE_URL}{path}", headers=weex_headers('GET', path), timeout=15)
data = r.json()
print(json.dumps(data, indent=2)[:3000])
