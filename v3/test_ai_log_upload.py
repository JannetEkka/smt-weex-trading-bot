#!/usr/bin/env python3
"""
Quick test to verify AI log uploads to WEEX are working
"""

import os
import json
import time
import hmac
import hashlib
import base64
import requests

WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

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

def test_upload():
    print("=" * 50)
    print("Testing AI Log Upload to WEEX")
    print("=" * 50)
    
    endpoint = "/capi/v2/order/uploadAiLog"
    
    # Test payload with full persona details
    payload = {
        "stage": "V3.1.4 Test - BTC Analysis",
        "model": "CatBoost-Gemini-MultiPersona-v3.1.4",
        "input": {
            "pair": "BTC",
            "tier": 1,
            "balance": 926.70,
            "has_long": True,
            "has_short": False,
        },
        "output": {
            "decision": "LONG",
            "confidence": 0.72,
            "tp_pct": 4.0,
            "sl_pct": 2.0,
        },
        "explanation": """Judge: LONG @ 72%. Tier 1 (STABLE): TP 4%, SL 2%, Hold 48h.

Votes: WHALE=LONG(70%): Whale accumulation detected, +350 ETH net inflow from top wallets; SENTIMENT=LONG(75%): Bullish market sentiment based on news analysis; FLOW=NEUTRAL(50%): Mixed order flow signals; TECHNICAL=LONG(68%): RSI at 45, price above SMA20

Market: Bitcoin showing strength with institutional buying pressure. Multiple analysts predict continuation of uptrend based on favorable macro conditions and ETF inflows."""
    }
    
    body = json.dumps(payload)
    
    print(f"\nSending test log...")
    print(f"Stage: {payload['stage']}")
    print(f"Explanation length: {len(payload['explanation'])} chars")
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        
        print(f"\nResponse status: {r.status_code}")
        print(f"Response: {r.text}")
        
        result = r.json()
        if result.get("code") == "00000":
            print("\n[SUCCESS] AI log uploaded successfully!")
        else:
            print(f"\n[FAILED] Error: {result}")
            
    except Exception as e:
        print(f"\n[ERROR] {e}")

if __name__ == "__main__":
    test_upload()
