"""
Cryptoracle API Client for SMT V3.1
====================================
Provides community sentiment intelligence from Cryptoracle's social/prediction market data.
Enhances the WHALE persona by giving sentiment signals for ALL trading pairs (not just BTC/ETH).

API: https://service.cryptoracle.network/openapi/v2.1
Auth: X-API-KEY header
Data: UTC+8 (Beijing Time) timestamps

Endpoints used:
  CO-P-01-01: Prediction market implied sentiment (BTC, 1-min, /v2.1/pm)
  CO-A-02-03: Net sentiment direction (positive - negative ratio)
  CO-S-01-01: Sentiment momentum Z-score (deviation from historical norm)
  CO-S-01-05: Sentiment vs price dislocation (mean-reversion signal)

Integration: Called by WHALE persona in smt_nightly_trade_v3_1.py
Graceful degradation: Returns None on any failure, persona falls back to Etherscan-only logic.
"""

import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

# --- Configuration ---
CRYPTORACLE_API_KEY = os.environ.get("CRYPTORACLE_API_KEY", "e0c686dc-a813-4d1b-82a0-d8f312141056")
CRYPTORACLE_BASE_URL = "https://service.cryptoracle.network/openapi"

# Endpoints we query
SENTIMENT_ENDPOINTS = ["CO-A-02-03", "CO-S-01-01", "CO-S-01-05"]

# Cache: avoid hitting API too often (data has 10min latency anyway)
_cache = {}
_CACHE_TTL = 600  # 10 minutes

# Rate limit
_last_call_time = 0
_MIN_CALL_INTERVAL = 1.0  # 1 second between calls


def _utc8_now() -> str:
    """Get current time in UTC+8 (Beijing Time) format required by Cryptoracle."""
    utc8 = timezone(timedelta(hours=8))
    return datetime.now(utc8).strftime("%Y-%m-%d %H:%M:%S")


def _utc8_hours_ago(hours: int) -> str:
    """Get time N hours ago in UTC+8 format."""
    utc8 = timezone(timedelta(hours=8))
    t = datetime.now(utc8) - timedelta(hours=hours)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _rate_limit():
    """Simple rate limiter."""
    global _last_call_time
    now = time.time()
    elapsed = now - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call_time = time.time()


def fetch_sentiment(tokens: list, hours_back: int = 4, time_type: str = "1h") -> Optional[Dict]:
    """
    Fetch sentiment data for multiple tokens from Cryptoracle.
    
    Args:
        tokens: List of token symbols e.g. ["BTC", "ETH", "SOL"]
        hours_back: How many hours of data to fetch (default 4)
        time_type: Granularity - "15m", "1h", "4h", "1d"
    
    Returns:
        Dict keyed by token with latest sentiment values, or None on failure.
        Example: {
            "BTC": {
                "net_sentiment": 0.58,      # CO-A-02-03: >0.5 = bullish, <0.5 = bearish
                "sentiment_momentum": 0.82,  # CO-S-01-01: Z-score, >1 = overheated, <-1 = panic
                "sentiment_price_gap": 1.95, # CO-S-01-05: >2 = sentiment way ahead of price (reversal risk)
                "trend_1h": "RISING",        # sentiment direction over last hour
                "signal": "LONG",            # derived signal
                "confidence": 0.70,          # derived confidence
            },
            ...
        }
    """
    # Check cache
    cache_key = f"sentiment_{'_'.join(sorted(tokens))}_{time_type}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < _CACHE_TTL:
            return cached_data
    
    try:
        _rate_limit()
        
        end_time = _utc8_now()
        start_time = _utc8_hours_ago(hours_back)
        
        payload = {
            "endpoints": SENTIMENT_ENDPOINTS,
            "startTime": start_time,
            "endTime": end_time,
            "timeType": time_type,
            "token": [t.upper() for t in tokens]
        }
        
        headers = {
            "X-API-KEY": CRYPTORACLE_API_KEY,
            "Content-Type": "application/json"
        }
        
        resp = requests.post(
            f"{CRYPTORACLE_BASE_URL}/v2.1/endpoint",
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if resp.status_code != 200:
            print(f"  [CRYPTORACLE] HTTP {resp.status_code}")
            return None
        
        raw = resp.json()
        
        # Check for API error
        if raw.get("code") != 200:
            print(f"  [CRYPTORACLE] API error: {raw.get('msg', 'unknown')}")
            return None
        
        data_list = raw.get("data", [])
        if not data_list:
            return None
        
        result = {}
        
        for token_data in data_list:
            token = token_data.get("token", "?")
            periods = token_data.get("timePeriods", [])
            
            if not periods:
                continue
            
            # Periods come sorted newest first -- take latest
            latest = periods[0]
            prev = periods[1] if len(periods) > 1 else None
            
            # Parse indicator values from latest period
            values = {}
            for d in latest.get("data", []):
                ep = d.get("endpoint", "")
                val = d.get("value", "0")
                try:
                    values[ep] = float(val)
                except (ValueError, TypeError):
                    values[ep] = 0.0
            
            # Parse previous period for trend detection
            prev_values = {}
            if prev:
                for d in prev.get("data", []):
                    ep = d.get("endpoint", "")
                    val = d.get("value", "0")
                    try:
                        prev_values[ep] = float(val)
                    except (ValueError, TypeError):
                        prev_values[ep] = 0.0
            
            net_sentiment = values.get("CO-A-02-03", 0.5)
            momentum = values.get("CO-S-01-01", 0.0)
            price_gap = values.get("CO-S-01-05", 0.0)
            
            prev_net = prev_values.get("CO-A-02-03", net_sentiment)
            
            # Derive trend
            sentiment_delta = net_sentiment - prev_net
            if sentiment_delta > 0.03:
                trend = "RISING"
            elif sentiment_delta < -0.03:
                trend = "FALLING"
            else:
                trend = "FLAT"
            
            # Derive signal and confidence
            signal, confidence = _derive_signal(net_sentiment, momentum, price_gap, trend)
            
            result[token] = {
                "net_sentiment": round(net_sentiment, 4),
                "sentiment_momentum": round(momentum, 4),
                "sentiment_price_gap": round(price_gap, 4),
                "trend_1h": trend,
                "signal": signal,
                "confidence": confidence,
                "timestamp": latest.get("startTime", ""),
            }
        
        # Cache it
        _cache[cache_key] = (time.time(), result)
        
        return result
    
    except requests.exceptions.Timeout:
        print("  [CRYPTORACLE] Timeout")
        return None
    except requests.exceptions.ConnectionError:
        print("  [CRYPTORACLE] Connection failed")
        return None
    except Exception as e:
        print(f"  [CRYPTORACLE] Error: {e}")
        return None


def _derive_signal(net_sentiment: float, momentum: float, price_gap: float, trend: str) -> tuple:
    """
    Derive a trading signal from Cryptoracle data.
    
    Logic:
    - net_sentiment > 0.6 = community is bullish
    - net_sentiment < 0.4 = community is bearish
    - momentum > 1.0 = sentiment overheated (contrarian SHORT risk)
    - momentum < -1.0 = sentiment panic (contrarian LONG opportunity)
    - price_gap > 2.0 = sentiment way ahead of price (reversion risk)
    - price_gap < -2.0 = price way ahead of sentiment (reversion risk)
    
    Returns: (signal, confidence) where signal is "LONG"/"SHORT"/"NEUTRAL"
    """
    score = 0.0  # positive = LONG, negative = SHORT
    
    # Net sentiment direction (primary signal)
    if net_sentiment > 0.65:
        score += 1.5
    elif net_sentiment > 0.55:
        score += 0.5
    elif net_sentiment < 0.35:
        score -= 1.5
    elif net_sentiment < 0.45:
        score -= 0.5
    
    # Momentum (Z-score) -- confirms or warns
    if momentum > 1.5:
        # Overheated -- could be contrarian SHORT signal
        score -= 0.5  # Slight bearish bias (sentiment too hot)
    elif momentum > 0.5:
        score += 0.5  # Healthy bullish momentum
    elif momentum < -1.5:
        # Panic -- contrarian LONG signal
        score += 1.0  # Strong contrarian bullish
    elif momentum < -0.5:
        score -= 0.3  # Mild bearish
    
    # Price-sentiment gap (mean reversion signal)
    if price_gap > 2.5:
        # Sentiment way ahead of price -- sentiment might be right, price catching up
        score += 0.3
    elif price_gap < -2.5:
        # Price way ahead of sentiment -- could snap back
        score -= 0.3
    
    # Trend adds conviction
    if trend == "RISING":
        score += 0.3
    elif trend == "FALLING":
        score -= 0.3
    
    # Convert score to signal
    if score >= 1.5:
        return ("LONG", min(0.85, 0.55 + abs(score) * 0.1))
    elif score >= 0.5:
        return ("LONG", min(0.70, 0.50 + abs(score) * 0.1))
    elif score <= -1.5:
        return ("SHORT", min(0.85, 0.55 + abs(score) * 0.1))
    elif score <= -0.5:
        return ("SHORT", min(0.70, 0.50 + abs(score) * 0.1))
    else:
        return ("NEUTRAL", 0.40)


def get_token_sentiment(token: str) -> Optional[Dict]:
    """
    Convenience: get sentiment for a single token.
    Returns the token's sentiment dict or None.
    """
    result = fetch_sentiment([token])
    if result and token.upper() in result:
        return result[token.upper()]
    return None


def get_all_trading_pair_sentiment() -> Optional[Dict]:
    """
    Fetch sentiment for all SMT trading pairs in one API call.
    Returns dict keyed by token symbol.
    """
    tokens = ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "BNB", "LTC"]
    return fetch_sentiment(tokens, hours_back=4, time_type="1h")




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

# --- Self-test ---
if __name__ == "__main__":
    print("Testing Cryptoracle API client...")
    print(f"API Key: {CRYPTORACLE_API_KEY[:8]}...")
    print(f"Base URL: {CRYPTORACLE_BASE_URL}")
    print()
    
    result = get_all_trading_pair_sentiment()
    if result:
        print(f"Got data for {len(result)} tokens:")
        for token, data in result.items():
            print(f"  {token}: signal={data['signal']} conf={data['confidence']:.0%} "
                  f"net_sent={data['net_sentiment']:.3f} momentum={data['sentiment_momentum']:.3f} "
                  f"gap={data['sentiment_price_gap']:.3f} trend={data['trend_1h']}")
    else:
        print("FAILED - no data returned")
    
    print()
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
        print("  PM: no data (may not be available yet)")
