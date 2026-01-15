#!/usr/bin/env python3
"""
SMT AI Log Upload Test - V3.1.5
================================
Run this on VM to verify AI log uploads are working.

Usage:
    python3 test_ai_log_debug.py

This will:
1. Test WEEX API connectivity
2. Upload a test AI log
3. Show detailed response info
4. Help diagnose why logs aren't appearing in WEEX UI
"""

import os
import json
import time
import hmac
import hashlib
import base64
import requests
import sys
from datetime import datetime, timezone

# Configuration
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"
MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1.5"


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


def test_api_connectivity():
    """Test basic WEEX API connectivity"""
    print("\n[1] Testing WEEX API Connectivity...")
    print("-" * 40)
    
    # Test public endpoint (no auth needed)
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol=cmt_btcusdt", timeout=10)
        data = r.json()
        price = data.get("last", "N/A")
        print(f"  Public API: OK (BTC price: ${price})")
    except Exception as e:
        print(f"  Public API: FAILED - {e}")
        return False
    
    # Test authenticated endpoint
    try:
        endpoint = "/capi/v2/account/assets"
        r = requests.get(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("GET", endpoint),
            timeout=10
        )
        data = r.json()
        
        if isinstance(data, dict) and data.get("code"):
            code = data.get("code")
            msg = data.get("msg", "")
            if code != "00000":
                print(f"  Auth API: FAILED - code={code}, msg={msg}")
                return False
        
        print(f"  Auth API: OK (got {type(data).__name__})")
        
    except Exception as e:
        print(f"  Auth API: FAILED - {e}")
        return False
    
    return True


def test_ai_log_upload():
    """Test AI log upload to WEEX"""
    print("\n[2] Testing AI Log Upload...")
    print("-" * 40)
    
    endpoint = "/capi/v2/order/uploadAiLog"
    
    # Create test payload with realistic data
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    
    payload = {
        "stage": f"V3.1.5 DEBUG TEST - {timestamp}",
        "model": MODEL_NAME,
        "input": {
            "pair": "BTC",
            "tier": 1,
            "balance": 966.77,
            "has_long": True,
            "has_short": False,
            "on_cooldown": False,
            "test_timestamp": timestamp,
        },
        "output": {
            "decision": "LONG",
            "confidence": 0.89,
            "tp_pct": 4.0,
            "sl_pct": 2.0,
            "can_trade": False,
            "trade_type": "none",
        },
        "explanation": f"""Judge: LONG @ 89%. Tier 1 (STABLE): TP 4%, SL 2%, Hold 48h.

Test log uploaded at {timestamp} UTC.

Persona Votes:
- WHALE=LONG(70%): Whale accumulation detected, +350 ETH net inflow from top wallets
- SENTIMENT=LONG(92%): Bullish market sentiment via Gemini AI analysis
- FLOW=LONG(75%): Order flow bullish - taker buy ratio 0.58
- TECHNICAL=LONG(85%): RSI at 52, price above SMA20

This is a DEBUG TEST to verify AI log uploads are working correctly.
If you see this in WEEX UI under 'Model chat' tab, uploads are working!"""
    }
    
    body = json.dumps(payload)
    
    print(f"  Stage: {payload['stage']}")
    print(f"  Model: {payload['model']}")
    print(f"  Explanation length: {len(payload['explanation'])} chars")
    print(f"  Body size: {len(body)} bytes")
    
    print(f"\n  Sending to: {WEEX_BASE_URL}{endpoint}")
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        
        print(f"\n  HTTP Status: {r.status_code}")
        
        # Try to parse response
        try:
            result = r.json()
        except json.JSONDecodeError:
            print(f"  Response (raw): {r.text[:500]}")
            return False
        
        code = result.get("code", "unknown")
        msg = result.get("msg", "")
        data = result.get("data", "")
        
        print(f"  Response code: {code}")
        print(f"  Response msg: {msg}")
        print(f"  Response data: {data}")
        
        if code == "00000":
            print(f"\n  [SUCCESS] AI log uploaded successfully!")
            print(f"  Check WEEX UI -> Model chat tab -> Filter by 'Smart Money Tracker'")
            return True
        else:
            print(f"\n  [FAILED] WEEX returned error")
            print(f"\n  Common error codes:")
            print(f"    40006 = Invalid API Key")
            print(f"    40009 = API verification failed (signature issue)")
            print(f"    40018 = Invalid IP (not whitelisted)")
            print(f"    40020 = Parameter invalid")
            return False
            
    except requests.exceptions.Timeout:
        print(f"  [TIMEOUT] Request timed out after 15s")
        return False
    except requests.exceptions.RequestException as e:
        print(f"  [NETWORK ERROR] {e}")
        return False
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")
        return False


def test_multiple_uploads():
    """Test multiple consecutive uploads (like the daemon does)"""
    print("\n[3] Testing Multiple Consecutive Uploads...")
    print("-" * 40)
    
    pairs = ["BTC", "ETH", "SOL"]
    results = []
    
    for pair in pairs:
        endpoint = "/capi/v2/order/uploadAiLog"
        
        payload = {
            "stage": f"V3.1.5 Multi-Test - {pair}",
            "model": MODEL_NAME,
            "input": {"pair": pair, "test": True},
            "output": {"decision": "LONG", "confidence": 0.75},
            "explanation": f"Test upload for {pair} pair"
        }
        
        body = json.dumps(payload)
        
        try:
            r = requests.post(
                f"{WEEX_BASE_URL}{endpoint}",
                headers=weex_headers("POST", endpoint, body),
                data=body,
                timeout=15
            )
            
            result = r.json()
            code = result.get("code", "unknown")
            
            if code == "00000":
                print(f"  {pair}: OK")
                results.append(True)
            else:
                print(f"  {pair}: FAILED (code={code})")
                results.append(False)
                
        except Exception as e:
            print(f"  {pair}: ERROR - {e}")
            results.append(False)
        
        time.sleep(0.5)  # Small delay between uploads
    
    success_count = sum(results)
    print(f"\n  Result: {success_count}/{len(results)} uploads succeeded")
    
    return all(results)


def check_recent_logs():
    """Check if there's an endpoint to verify logs were uploaded"""
    print("\n[4] Checking Environment...")
    print("-" * 40)
    
    print(f"  VM Static IP should be: 34.87.116.79")
    print(f"  API Key (first 20 chars): {WEEX_API_KEY[:20]}...")
    print(f"  API Key length: {len(WEEX_API_KEY)}")
    print(f"  Passphrase: {WEEX_API_PASSPHRASE}")
    print(f"  Base URL: {WEEX_BASE_URL}")
    
    # Check if we can get account info (verifies auth is working)
    try:
        endpoint = "/capi/v2/account/assets"
        r = requests.get(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("GET", endpoint),
            timeout=10
        )
        data = r.json()
        
        if isinstance(data, list):
            for asset in data:
                if asset.get("coinName") == "USDT":
                    balance = float(asset.get("available", 0))
                    print(f"  Account balance: {balance:.2f} USDT")
                    break
        elif isinstance(data, dict) and data.get("code") != "00000":
            print(f"  Account check failed: {data.get('msg')}")
            
    except Exception as e:
        print(f"  Could not check account: {e}")


def main():
    print("=" * 60)
    print("SMT AI Log Upload Debug Test - V3.1.5")
    print("=" * 60)
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    
    # Run tests
    connectivity_ok = test_api_connectivity()
    
    if not connectivity_ok:
        print("\n" + "=" * 60)
        print("[CRITICAL] API connectivity failed - check credentials and IP whitelist")
        print("=" * 60)
        sys.exit(1)
    
    upload_ok = test_ai_log_upload()
    
    if upload_ok:
        multi_ok = test_multiple_uploads()
    else:
        multi_ok = False
    
    check_recent_logs()
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if upload_ok:
        print("[OK] AI log upload is working!")
        print("")
        print("If logs still don't appear in WEEX UI, possible issues:")
        print("  1. UI filtering - make sure you select 'Smart Money Tracker' in filter")
        print("  2. UI lag - logs may take a few minutes to appear")
        print("  3. Wrong tab - check 'Model chat' tab, not 'Completed trades'")
    else:
        print("[FAILED] AI log upload is NOT working!")
        print("")
        print("Check:")
        print("  1. IP whitelist - is 34.87.116.79 still whitelisted by WEEX?")
        print("  2. API credentials - have they changed?")
        print("  3. Account status - is your account still active?")
        print("  4. Contact WEEX support in Telegram: https://t.me/weexaiwars")


if __name__ == "__main__":
    main()
