"""
V3.1.58b - Add Prediction Market (CO-P-01-01) to Cryptoracle client
Adds BTC prediction market sentiment from order book structure.
Separate endpoint /v2.1/pm with 1-minute data.
"""

CRYPTO_FILE = "cryptoracle_client.py"

def patch():
    with open(CRYPTO_FILE, "r") as f:
        content = f.read()
    
    if "CO-P-01-01" in content:
        print("CO-P-01-01 already present. Skipping.")
        return
    
    # 1. Add prediction market function before the self-test block
    pm_function = '''

# --- Prediction Market Data (CO-P-01-01) ---
# Separate endpoint: /v2.1/pm
# 1-minute granularity, BTC only
# Value > 0 = implied bullish expectations dominate
# Value < 0 = implied bearish expectations dominate
# Value ~ 0 = balanced / divergence

_pm_cache = {}
_PM_CACHE_TTL = 120  # 2 minutes (data updates every 1 min)


def fetch_prediction_market(minutes_back: int = 5) -> Optional[Dict]:
    """
    Fetch BTC prediction market implied sentiment from CO-P-01-01.
    
    Returns:
        {
            "pm_sentiment": 0.145,  # raw value, >0 = bullish, <0 = bearish
            "pm_signal": "LONG",    # derived
            "pm_strength": "MILD",  # STRONG / MILD / NEUTRAL
            "timestamp": "2026-..."
        }
        or None on failure.
    """
    cache_key = "pm_btc"
    if cache_key in _pm_cache:
        cached_time, cached_data = _pm_cache[cache_key]
        if time.time() - cached_time < _PM_CACHE_TTL:
            return cached_data
    
    try:
        _rate_limit()
        
        end_time = _utc8_now()
        start_time = _utc8_hours_ago(0)  # just recent minutes
        
        # Use minutes-ago for tighter window
        utc8 = timezone(timedelta(hours=8))
        t_start = datetime.now(utc8) - timedelta(minutes=minutes_back)
        start_time = t_start.strftime("%Y-%m-%d %H:%M:%S")
        
        payload = {
            "token": ["BTC"],
            "endpoints": ["CO-P-01-01"],
            "startTime": start_time,
            "endTime": end_time,
        }
        
        headers = {
            "X-API-KEY": CRYPTORACLE_API_KEY,
            "Content-Type": "application/json"
        }
        
        resp = requests.post(
            f"{CRYPTORACLE_BASE_URL}/v2.1/pm",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        if resp.status_code != 200:
            print(f"  [CRYPTORACLE-PM] HTTP {resp.status_code}")
            return None
        
        raw = resp.json()
        if raw.get("code") != 200:
            print(f"  [CRYPTORACLE-PM] API error: {raw.get('msg', 'unknown')}")
            return None
        
        data_list = raw.get("data", [])
        if not data_list:
            return None
        
        # Find BTC data
        btc_data = None
        for item in data_list:
            if item.get("token") == "BTC":
                btc_data = item
                break
        
        if not btc_data:
            return None
        
        periods = btc_data.get("timePeriods", [])
        if not periods:
            return None
        
        # Latest period
        latest = periods[0]
        pm_value = 0.0
        ts = latest.get("time", "")
        
        for d in latest.get("data", []):
            if d.get("endpoint") == "CO-P-01-01":
                try:
                    pm_value = float(d.get("value", "0"))
                except (ValueError, TypeError):
                    pm_value = 0.0
        
        # Derive signal
        if pm_value > 0.3:
            pm_signal = "LONG"
            pm_strength = "STRONG"
        elif pm_value > 0.1:
            pm_signal = "LONG"
            pm_strength = "MILD"
        elif pm_value < -0.3:
            pm_signal = "SHORT"
            pm_strength = "STRONG"
        elif pm_value < -0.1:
            pm_signal = "SHORT"
            pm_strength = "MILD"
        else:
            pm_signal = "NEUTRAL"
            pm_strength = "NEUTRAL"
        
        result = {
            "pm_sentiment": round(pm_value, 6),
            "pm_signal": pm_signal,
            "pm_strength": pm_strength,
            "timestamp": ts,
        }
        
        _pm_cache[cache_key] = (time.time(), result)
        return result
        
    except Exception as e:
        print(f"  [CRYPTORACLE-PM] Error: {e}")
        return None

'''

    # Insert before "# --- Self-test ---"
    marker = "# --- Self-test ---"
    if marker not in content:
        print(f"ERROR: Could not find '{marker}' in {CRYPTO_FILE}")
        return
    
    content = content.replace(marker, pm_function + marker)
    
    # 2. Update self-test to include PM test
    old_test_end = '''    print()
    print("Testing single token (BTC)...")
    btc = get_token_sentiment("BTC")
    if btc:
        print(f"  BTC: {btc}")
    else:
        print("  FAILED")'''
    
    new_test_end = '''    print()
    print("Testing single token (BTC)...")
    btc = get_token_sentiment("BTC")
    if btc:
        print(f"  BTC: {btc}")
    else:
        print("  FAILED")
    
    print()
    print("Testing Prediction Market (CO-P-01-01)...")
    pm = fetch_prediction_market()
    if pm:
        print(f"  BTC PM: sentiment={pm['pm_sentiment']:.4f} signal={pm['pm_signal']} strength={pm['pm_strength']}")
    else:
        print("  PM: no data (may not be available yet)")'''
    
    content = content.replace(old_test_end, new_test_end)
    
    # 3. Update docstring
    old_doc = "Endpoints used:\n  CO-A-02-03"
    new_doc = "Endpoints used:\n  CO-P-01-01: Prediction market implied sentiment (BTC, 1-min, /v2.1/pm)\n  CO-A-02-03"
    content = content.replace(old_doc, new_doc)
    
    with open(CRYPTO_FILE, "w") as f:
        f.write(content)
    
    print("Patched cryptoracle_client.py with CO-P-01-01 prediction market support.")
    print("New function: fetch_prediction_market() -> Dict or None")

if __name__ == "__main__":
    patch()
