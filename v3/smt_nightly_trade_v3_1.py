"""
SMT Nightly Trade V3.2.29 - Walk resistance list before discarding bad-TP trades
=============================================================
No partial closes. Higher conviction trades only.

V3.1.20 Changes (PREDATOR MODE):
- DISABLED all RUNNER_CONFIG - no more partial closes, let winners run to full TP
- MIN_CONFIDENCE_TO_TRADE: 60% -> 70% (match WeexAlphaHunter's 72%+ strategy)
- Goal: Fewer trades, bigger wins, less fee bleed

V3.1.18 Changes (DEAD CAT BOUNCE FIX):
- FLOW PERSONA: Regime-aware taker cap
  * In BEARISH regime, extreme taker buying (>3.0) = NEUTRAL (short covering)
  * In BEARISH regime, heavy taker buying (>2.0) = NEUTRAL (bounce?)
  * Prevents bot from going LONG on dead cat bounces
- JUDGE: Signal-aware SENTIMENT weighting
  * In BEARISH: SENTIMENT SHORT gets 2.0x weight, SENTIMENT LONG gets 0.8x
  * In BULLISH: SENTIMENT LONG gets 1.8x weight, SENTIMENT SHORT gets 0.8x
  * Trust structural break analysis (support/resistance) over hopium

V3.1.17 Changes:
- FLOW: Taker volume beats depth
- Altcoin momentum factor in regime
- Lower confidence thresholds

V3.1.4 Changes (CRITICAL FIXES):
- Reduced MAX_OPEN_POSITIONS from 8 to 5 (less exposure)
- Increased MIN_CONFIDENCE_TO_TRADE from 55% to 65% (better signals)
- Widened Tier 3 SL from 1.5% to 2.0% (stop getting whipsawed)
- Increased Tier 3 TP from 2.5% to 3.0% (better R:R ratio)
- Added MARKET TREND FILTER - don't go LONG when BTC is dropping!
- Reduced Tier 3 max hold from 6h to 4h (faster exits)

V3.1.3 Changes:
- Fixed explanation truncation: 2500 chars (500 words) instead of 500 chars

Tier Config:
- Tier 1 (BTC, ETH, BNB, LTC): 4% TP, 2% SL, 48h hold
- Tier 2 (SOL): 3% TP, 1.75% SL, 12h hold  
- Tier 3 (DOGE, XRP, ADA): 3% TP, 2% SL, 4h hold (UPDATED!)

Personas:
1. WHALE - On-chain whale intelligence (our unique edge)
2. SENTIMENT - Market sentiment via Gemini search
3. FLOW - Order flow analysis (taker ratio + depth)
4. TECHNICAL - RSI, SMA, momentum indicators
5. JUDGE - Final validator that weighs all personas + MARKET TREND

Run: python3 smt_nightly_trade_v3_1.py
Test: python3 smt_nightly_trade_v3_1.py --test
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import pickle
import requests
import numpy as np
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from collections import deque
# ============================================================
# V3.1.21: API CACHE SYSTEM (avoid rate limits)
# ============================================================
import time as cache_time_module

class APICache:
    """Simple TTL cache for API responses"""
    def __init__(self):
        self._cache = {}
    
    def get(self, key, ttl_seconds=900):
        """Get cached value if not expired"""
        if key in self._cache:
            timestamp, value = self._cache[key]
            if cache_time_module.time() - timestamp < ttl_seconds:
                return value
        return None
    
    def set(self, key, value):
        """Cache a value with current timestamp"""
        self._cache[key] = (cache_time_module.time(), value)
    
    def clear_expired(self, ttl_seconds=900):
        """Remove expired entries"""
        now = cache_time_module.time()
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < ttl_seconds}

# Global caches
SENTIMENT_CACHE = APICache()
REGIME_CACHE = APICache()
WHALE_CACHE = APICache()

# Rate limit tracking
LAST_GEMINI_CALL = 0
GEMINI_CALL_DELAY = 8  # V3.1.75: 8s between calls (was 5, caused empty responses)

def rate_limit_gemini():
    """Enforce delay between Gemini API calls"""
    global LAST_GEMINI_CALL
    now = cache_time_module.time()
    elapsed = now - LAST_GEMINI_CALL
    if elapsed < GEMINI_CALL_DELAY:
        sleep_time = GEMINI_CALL_DELAY - elapsed
        print(f"  [RATE LIMIT] Waiting {sleep_time:.1f}s before Gemini call...")
        cache_time_module.sleep(sleep_time)
    LAST_GEMINI_CALL = cache_time_module.time()


# V3.1.21: Sentiment cache to avoid rate limits
SENTIMENT_CACHE = {}
# ============================================================
# V3.1.22: REGIME STABILITY SYSTEM  
# ============================================================
REGIME_STATE = {
    "current_regime": "NEUTRAL",
    "regime_locked_until": 0,
    "regime_score_history": [],
    "trading_paused_until": 0,
}

def check_flash_crash() -> dict:
    result = {"flash_crash": False, "drop_pct": 0, "paused_until": 0}
    try:
        now = time.time()
        if REGIME_STATE.get("trading_paused_until", 0) > now:
            remaining = (REGIME_STATE["trading_paused_until"] - now) / 60
            result["flash_crash"] = True
            result["paused_until"] = REGIME_STATE["trading_paused_until"]
            print(f"  [FLASH CRASH] Paused for {remaining:.0f}m")
            return result
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=1m&limit=16"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 15:
            current = float(candles[0][4])
            ago_15m = float(candles[14][4])
            change = ((current - ago_15m) / ago_15m) * 100
            result["drop_pct"] = change
            if change < -2.5:
                REGIME_STATE["trading_paused_until"] = now + 14400
                result["flash_crash"] = True
                result["paused_until"] = REGIME_STATE["trading_paused_until"]
                print(f"  [FLASH CRASH] DETECTED! {change:+.1f}% in 15min - PAUSED 4h")
    except Exception as e:
        print(f"  [FLASH CRASH] Error: {e}")
    return result

def apply_regime_hysteresis(score: int, raw_regime: str, btc_4h_change: float = 0) -> str:
    """
    V3.1.23: RAPID REGIME DETECTION
    
    Changes:
    1. Lock reduced from 30 min to 10 min
    2. MOMENTUM OVERRIDE: If BTC 4h change > 1.5%, IMMEDIATE switch (bypass hysteresis)
    3. Strong signals (score >= 2 or <= -2) switch immediately
    """
    current = REGIME_STATE.get("current_regime", "NEUTRAL")
    history = REGIME_STATE.get("regime_score_history", [])
    history.append(score)
    if len(history) > 3: history = history[-3:]
    REGIME_STATE["regime_score_history"] = history
    now = time.time()
    
    # V3.1.23: MOMENTUM OVERRIDE - bypass hysteresis for strong 4h moves
    if btc_4h_change < -1.5 and current != "BEARISH":
        REGIME_STATE["current_regime"] = "BEARISH"
        REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock  # 10 min lock
        print(f"  [HYSTERESIS] MOMENTUM OVERRIDE: -> BEARISH (4h: {btc_4h_change:+.1f}%)")
        return "BEARISH"
    
    if btc_4h_change > 1.5 and current != "BULLISH":
        REGIME_STATE["current_regime"] = "BULLISH"
        REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock  # 10 min lock
        print(f"  [HYSTERESIS] MOMENTUM OVERRIDE: -> BULLISH (4h: {btc_4h_change:+.1f}%)")
        return "BULLISH"
    
    # V3.1.23: Reduced lock from 30 min to 10 min
    if REGIME_STATE.get("regime_locked_until", 0) > now:
        remaining = (REGIME_STATE["regime_locked_until"] - now) / 60
        return current  # Locked (silent — was spamming every 2 min)
    
    # V3.1.23: Strong signals (score >= 2 or <= -2) switch immediately
    if score <= -2 and current != "BEARISH":
        REGIME_STATE["current_regime"] = "BEARISH"
        REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock
        print(f"  [HYSTERESIS] STRONG BEARISH (score: {score}) -> BEARISH")
        return "BEARISH"
    
    if score >= 2 and current != "BULLISH":
        REGIME_STATE["current_regime"] = "BULLISH"
        REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock
        print(f"  [HYSTERESIS] STRONG BULLISH (score: {score}) -> BULLISH")
        return "BULLISH"
    
    # Normal hysteresis for weaker signals
    if len(history) >= 2:
        avg = sum(history[-2:]) / 2
        if current == "BEARISH" and avg >= 1:
            new_r = "NEUTRAL" if avg < 2 else "BULLISH"
            REGIME_STATE["current_regime"] = new_r
            REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock  # V3.1.23: 10 min
            print(f"  [HYSTERESIS] BEARISH -> {new_r}")
            return new_r
        if current == "BULLISH" and avg <= -1:
            new_r = "NEUTRAL" if avg > -2 else "BEARISH"
            REGIME_STATE["current_regime"] = new_r
            REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock  # V3.1.23: 10 min
            print(f"  [HYSTERESIS] BULLISH -> {new_r}")
            return new_r
        if current == "NEUTRAL":
            if all(s <= -1 for s in history[-2:]):
                REGIME_STATE["current_regime"] = "BEARISH"
                REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock
                return "BEARISH"
            if all(s >= 1 for s in history[-2:]):
                REGIME_STATE["current_regime"] = "BULLISH"
                REGIME_STATE["regime_locked_until"] = now + 1800  # V3.1.71: 30min lock
                return "BULLISH"
        return current
    REGIME_STATE["current_regime"] = raw_regime
    return raw_regime




SENTIMENT_CACHE_TTL = 900  # 15 minutes

# V3.1.21: Hot-reload settings
try:
    from hot_reload import get_confidence_threshold, should_pause, should_emergency_exit, is_direction_enabled, get_tp_sl_multipliers
    HOT_RELOAD_ENABLED = True
    print("  [HOT-RELOAD] Enabled")

except ImportError:
    HOT_RELOAD_ENABLED = False


# V3.1.61: Gemini timeout wrapper
def _gemini_with_timeout(client, model, contents, config, timeout=120):
    """Call Gemini with a thread-based timeout."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"  [GEMINI TIMEOUT] Call exceeded {timeout}s, cancelling")
            future.cancel()
            raise TimeoutError(f"Gemini call timed out after {timeout}s")


def _gemini_full_call(model, contents, config, timeout=90, use_grounding=False):
    """V3.1.69: BULLETPROOF Gemini call - wraps EVERYTHING in one timeout.
    
    This wraps client creation + generate_content in a single thread timeout.
    Prevents hangs from genai.Client() initialization or network issues.
    """
    import concurrent.futures
    
    def _do_call():
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client()
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"  [GEMINI TIMEOUT] Full call exceeded {timeout}s")
            future.cancel()
            raise TimeoutError(f"Gemini full call timed out after {timeout}s")



# ============================================================
# CONFIGURATION
# ============================================================

TEST_MODE = "--test" in sys.argv or os.getenv("SMT_TEST_MODE", "false").lower() == "true"
SIMULATED_BALANCE = 1000.0

# WEEX API
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

# Etherscan
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY', 'W7GTUDUM9BMBQPJUZXXMDBJH4JDPUQS9UR')
ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = 1

# V3.1.21: Whale flow history for divergence detection
WHALE_FLOW_HISTORY = deque(maxlen=6)
WHALE_FLOW_HISTORY_FILE = "whale_flow_history.json"

def load_whale_flow_history():
    global WHALE_FLOW_HISTORY
    try:
        if os.path.exists(WHALE_FLOW_HISTORY_FILE):
            with open(WHALE_FLOW_HISTORY_FILE, 'r') as f:
                WHALE_FLOW_HISTORY = deque(json.load(f), maxlen=6)
                print(f"  [WHALE] Loaded {len(WHALE_FLOW_HISTORY)} flow samples")
    except: pass

def save_whale_flow_history():
    try:
        with open(WHALE_FLOW_HISTORY_FILE, 'w') as f:
            json.dump(list(WHALE_FLOW_HISTORY), f)
    except: pass

load_whale_flow_history()


# Google Cloud
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')
GCS_BUCKET = os.getenv('GCS_BUCKET', 'smt-weex-2025-models')

# ============================================================
# V3.1.12: ENHANCED MULTI-FACTOR REGIME DETECTION
# ============================================================

def get_fear_greed_index() -> dict:
    """
    Fetch Fear & Greed Index from alternative.me
    CONTRARIAN indicator:
    - 0-25: Extreme Fear = BUY signal (others panic, we accumulate)
    - 75-100: Extreme Greed = SELL signal (others euphoric, we take profit)
    """
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json()
        if data.get("data"):
            value = int(data["data"][0]["value"])
            classification = data["data"][0]["value_classification"]
            return {"value": value, "classification": classification, "error": None}
    except Exception as e:
        pass
    return {"value": 50, "classification": "Neutral", "error": "API failed"}


def get_aggregate_funding_rate() -> dict:
    """
    Average funding rate across all pairs using WEEX currentFundRate endpoint.
    Response format: [{"symbol":"cmt_btcusdt","fundingRate":"0.00002559","collectCycle":480,"timestamp":...}]
    High positive (>0.05%) = overleveraged longs = expect dump
    Negative (<-0.03%) = overleveraged shorts = expect pump
    """
    try:
        import requests
        total_funding = 0
        count = 0
        
        for pair in ["btcusdt", "ethusdt", "solusdt", "adausdt"]:
            url = f"{WEEX_BASE_URL}/capi/v2/market/currentFundRate?symbol=cmt_{pair}"
            r = requests.get(url, timeout=5)
            data = r.json()
            # API returns array: [{"fundingRate": "0.00002559", ...}]
            if isinstance(data, list) and len(data) > 0:
                funding = float(data[0].get("fundingRate", 0))
                total_funding += funding
                count += 1
        
        if count > 0:
            return {"avg_funding": total_funding / count, "pairs_checked": count, "error": None}
    except Exception as e:
        print(f"  [REGIME] Funding API error: {e}")
    return {"avg_funding": 0, "pairs_checked": 0, "error": "API failed"}


# ============================================================
# V3.1.16: OPEN INTEREST SENSOR - The "Truth" of futures market
# ============================================================

def get_btc_open_interest() -> dict:
    """
    Get BTC Open Interest from WEEX.
    
    The Logic:
    - Price Drops + OI Rises: "Short Build-up" - people opening new shorts = Stay Bearish
    - Price Drops + OI Drops: "Long Liquidation" - weak hands forced out = Bottoming
    - Price Rises + OI Rises: "Long Build-up" - new longs entering = Stay Bullish
    - Price Rises + OI Drops: "Short Liquidation" - squeeze happening = May top soon
    """
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/open_interest?symbol=cmt_btcusdt"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) > 0:
            # base_volume is OI in BTC, target_volume is OI in USDT
            oi_btc = float(data[0].get("base_volume", 0))
            oi_usdt = float(data[0].get("target_volume", 0))
            return {"oi_btc": oi_btc, "oi_usdt": oi_usdt, "error": None}
    except Exception as e:
        pass
    return {"oi_btc": 0, "oi_usdt": 0, "error": "API failed"}


def get_oi_change_signal() -> dict:
    """
    V3.1.16: Detect OI direction combined with price direction.
    
    Uses 4h candles to compare:
    - Current OI vs estimate (we only have current snapshot, so use funding as proxy)
    - Price direction from candles
    
    Returns signal about market structure.
    """
    result = {
        "signal": "NEUTRAL",
        "reason": "",
        "oi_usdt": 0,
        "price_change_4h": 0,
        "funding_rate": 0
    }
    
    try:
        # Get current OI
        oi_data = get_btc_open_interest()
        result["oi_usdt"] = oi_data.get("oi_usdt", 0)
        
        # Get 4h price change
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=2"
        r = requests.get(url, timeout=10)
        candles = r.json()
        
        if isinstance(candles, list) and len(candles) >= 2:
            current_close = float(candles[0][4])
            prev_close = float(candles[1][4])
            price_change = ((current_close - prev_close) / prev_close) * 100
            result["price_change_4h"] = price_change
            
            # Get funding rate as OI direction proxy
            # High positive funding = longs piling in = OI rising on long side
            # Negative funding = shorts piling in = OI rising on short side
            funding_url = f"{WEEX_BASE_URL}/capi/v2/market/currentFundRate?symbol=cmt_btcusdt"
            fr = requests.get(funding_url, timeout=10)
            funding_data = fr.json()
            
            btc_funding = 0
            if isinstance(funding_data, list) and len(funding_data) > 0:
                btc_funding = float(funding_data[0].get("fundingRate", 0))
            
            result["funding_rate"] = btc_funding
            
            # Interpret the combination
            # Price dropping + High funding = Longs still holding (will liquidate) = MORE DUMP
            # Price dropping + Negative funding = Shorts building = BEARISH continuation
            # Price dropping + Low/neutral funding = Liquidations happening = Near bottom
            
            if price_change < -1.0:  # Price dropping
                if btc_funding > 0.0003:  # Longs overleveraged
                    result["signal"] = "BEARISH"
                    result["reason"] = f"Price -{abs(price_change):.1f}% but longs overleveraged (funding +{btc_funding:.4f}) - liquidations coming"
                elif btc_funding < -0.0002:  # Shorts building
                    result["signal"] = "BEARISH"
                    result["reason"] = f"Price -{abs(price_change):.1f}% + shorts piling in (funding {btc_funding:.4f}) - trend continuation"
                else:
                    result["signal"] = "NEUTRAL"
                    result["reason"] = f"Price -{abs(price_change):.1f}%, funding neutral - may be bottoming"
            
            elif price_change > 1.0:  # Price rising
                if btc_funding < -0.0002:  # Shorts getting squeezed
                    result["signal"] = "BULLISH"
                    result["reason"] = f"Price +{price_change:.1f}% squeezing shorts (funding {btc_funding:.4f}) - pump continuation"
                elif btc_funding > 0.0005:  # Longs overleveraged on pump
                    result["signal"] = "NEUTRAL"
                    result["reason"] = f"Price +{price_change:.1f}% but longs greedy (funding +{btc_funding:.4f}) - pullback risk"
                else:
                    result["signal"] = "BULLISH"
                    result["reason"] = f"Price +{price_change:.1f}%, healthy funding - uptrend"
            else:
                result["signal"] = "NEUTRAL"
                result["reason"] = "Choppy market, no clear direction"
                
    except Exception as e:
        result["reason"] = f"OI analysis error: {e}"
    
    return result


# ============================================================
# V3.1.16: ATR-BASED VOLATILITY SIZING
# ============================================================

def get_btc_atr() -> dict:
    """
    Calculate ATR (Average True Range) for BTC to measure volatility.
    
    Uses 4h candles, 14-period ATR.
    Returns current ATR and ratio vs 14-period average.
    
    High ATR ratio (>1.5) = high volatility = reduce position size
    Low ATR ratio (<0.7) = low volatility = normal position size
    """
    result = {
        "atr": 0,
        "atr_pct": 0,  # ATR as % of price
        "atr_ratio": 1.0,  # Current vs average
        "volatility": "NORMAL",
        "size_multiplier": 1.0,
        "error": None
    }
    
    try:
        # Get 4h candles (need 15 for 14-period ATR)
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=20"
        r = requests.get(url, timeout=10)
        candles = r.json()
        
        if isinstance(candles, list) and len(candles) >= 15:
            # Calculate True Range for each candle
            true_ranges = []
            for i in range(len(candles) - 1):
                high = float(candles[i][2])
                low = float(candles[i][3])
                prev_close = float(candles[i + 1][4])
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            if len(true_ranges) >= 14:
                # Current ATR (last 14 periods)
                current_atr = sum(true_ranges[:14]) / 14
                
                # Average ATR (all available)
                avg_atr = sum(true_ranges) / len(true_ranges)
                
                # Current price for percentage
                current_price = float(candles[0][4])
                
                result["atr"] = current_atr
                result["atr_pct"] = (current_atr / current_price) * 100
                result["atr_ratio"] = current_atr / avg_atr if avg_atr > 0 else 1.0
                
                # Determine volatility regime and position sizing
                if result["atr_ratio"] > 2.0:
                    result["volatility"] = "EXTREME"
                    result["size_multiplier"] = 0.3  # 30% of normal size
                elif result["atr_ratio"] > 1.5:
                    result["volatility"] = "HIGH"
                    result["size_multiplier"] = 0.5  # 50% of normal size
                elif result["atr_ratio"] > 1.2:
                    result["volatility"] = "ELEVATED"
                    result["size_multiplier"] = 0.7  # 70% of normal size
                elif result["atr_ratio"] < 0.7:
                    result["volatility"] = "LOW"
                    result["size_multiplier"] = 1.2  # Can size up slightly in calm markets
                else:
                    result["volatility"] = "NORMAL"
                    result["size_multiplier"] = 1.0
                    
    except Exception as e:
        result["error"] = str(e)

    return result


# V3.1.77: Per-pair ATR cache (avoid redundant API calls within same cycle)
_pair_atr_cache = {}
_pair_atr_cache_time = 0

def get_pair_atr(symbol: str) -> dict:
    """V3.1.77: Calculate ATR for ANY pair, not just BTC.
    Each pair has its own volatility profile. DOGE swings 5-8% daily while BTC swings 1-2%.
    Using BTC ATR for DOGE sets SL too tight, causing noise stop-outs.
    """
    global _pair_atr_cache, _pair_atr_cache_time

    # Cache for 15 minutes (matches signal check interval)
    now = time.time()
    if now - _pair_atr_cache_time < 900 and symbol in _pair_atr_cache:
        return _pair_atr_cache[symbol]

    if now - _pair_atr_cache_time >= 900:
        _pair_atr_cache = {}
        _pair_atr_cache_time = now

    result = {
        "atr": 0, "atr_pct": 0, "atr_ratio": 1.0,
        "volatility": "NORMAL", "size_multiplier": 1.0, "error": None
    }

    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=4h&limit=20"
        r = requests.get(url, timeout=10)
        candles = r.json()

        if isinstance(candles, list) and len(candles) >= 15:
            true_ranges = []
            for i in range(len(candles) - 1):
                high = float(candles[i][2])
                low = float(candles[i][3])
                prev_close = float(candles[i + 1][4])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                true_ranges.append(tr)

            if len(true_ranges) >= 14:
                current_atr = sum(true_ranges[:14]) / 14
                avg_atr = sum(true_ranges) / len(true_ranges)
                current_price = float(candles[0][4])

                result["atr"] = current_atr
                result["atr_pct"] = (current_atr / current_price) * 100
                result["atr_ratio"] = current_atr / avg_atr if avg_atr > 0 else 1.0

                if result["atr_ratio"] > 2.0:
                    result["volatility"] = "EXTREME"
                    result["size_multiplier"] = 0.3
                elif result["atr_ratio"] > 1.5:
                    result["volatility"] = "HIGH"
                    result["size_multiplier"] = 0.5
                elif result["atr_ratio"] > 1.2:
                    result["volatility"] = "ELEVATED"
                    result["size_multiplier"] = 0.7
                elif result["atr_ratio"] < 0.7:
                    result["volatility"] = "LOW"
                    result["size_multiplier"] = 1.2
                else:
                    result["volatility"] = "NORMAL"
                    result["size_multiplier"] = 1.0

        _pair_atr_cache[symbol] = result
    except Exception as e:
        result["error"] = str(e)
        # Fallback to BTC ATR if pair-specific fails
        btc_atr = get_btc_atr()
        if btc_atr.get("atr_pct", 0) > 0:
            result["atr_pct"] = btc_atr["atr_pct"]
            result["atr_ratio"] = btc_atr["atr_ratio"]
            print(f"  [ATR] {symbol} failed, using BTC ATR fallback: {btc_atr['atr_pct']:.2f}%")

    return result


# ============================================================
# V3.1.80: CHOP / SIDEWAYS MARKET DETECTION
# ============================================================
# Prevents entries into range-bound, choppy markets where price
# ping-pongs sideways instead of trending. Uses ADX, Bollinger
# Band width, and directional consistency to detect chop.
# LTC 2026-02-15 was the catalyst: 90% conf LONG, but market
# went completely sideways. This filter would have blocked it.

_chop_cache = {}
_chop_cache_time = 0

def detect_sideways_market(symbol: str) -> dict:
    """V3.1.101: Multi-factor chop/sideways detection with per-pair timeframes.

    V3.1.94: Uses 15M candles (4x resolution vs old 1H) for most pairs.
    V3.1.101: BTC uses 30M candles (institutional blocks, 15M is noise).

    Calculates:
    1. ADX (Average Directional Index, period=28) - trend strength
    2. Bollinger Band width (period=40) - volatility squeeze
    3. Directional consistency (lookback=24) - are candles flip-flopping?
    4. Micro-body threshold (0.1%) - filters noise candles as dojis
    5. Net displacement override - detects stair-step trending

    Returns:
        {
            "is_choppy": bool,
            "severity": "high" | "medium" | "low",
            "adx": float,
            "bb_width_pct": float,
            "directional_consistency": float,  # 0-1, higher = more trending
            "net_displacement": float,  # V3.1.94: net price move % over lookback
            "reason": str
        }
    """
    global _chop_cache, _chop_cache_time

    # Cache for 15 minutes (aligned to 15M candle close interval)
    now = time.time()
    if now - _chop_cache_time < 900 and symbol in _chop_cache:
        return _chop_cache[symbol]

    if now - _chop_cache_time >= 900:
        _chop_cache = {}
        _chop_cache_time = now

    result = {
        "is_choppy": False,
        "severity": "low",
        "adx": 25.0,  # Default = neutral (not choppy)
        "bb_width_pct": 3.0,
        "directional_consistency": 0.7,
        "reason": "OK",
        "error": None
    }

    try:
        # V3.1.105: Per-pair CHOP timeframe — all on 5m, tuned to each pair's observed chop window
        # Chart analysis: blue chips chop 2-4h, retail pairs 1.5-3h, XRP extends 5h+
        # All use ADX(14) on 5m — min 42 candles ensures BB(40) always computes
        PAIR_CHOP_TIMEFRAME = {
            "BTC":  ("5m", 48),   # 4h lookback — institutional, chops 2-4h
            "ETH":  ("5m", 48),   # 4h lookback — blue chip, chops 2-4h
            "BNB":  ("5m", 48),   # 4h lookback — mirrors ETH behavior
            "LTC":  ("5m", 42),   # 3.5h lookback — mid-cap, chops 1.5-2h
            "XRP":  ("5m", 60),   # 5h lookback — extended ranger, 5h+ chops
            "SOL":  ("5m", 42),   # 3.5h lookback — volatile, chops 1-2h
            "DOGE": ("5m", 42),   # 3.5h lookback — meme, chops fast
            "ADA":  ("5m", 42),   # 3.5h lookback — retail, chops fast
        }

        pair_name = None
        for _pair, _info in TRADING_PAIRS.items():
            if _info["symbol"] == symbol:
                pair_name = _pair
                break

        granularity, num_candles = PAIR_CHOP_TIMEFRAME.get(pair_name, ("15m", 60))
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity={granularity}&limit={num_candles}"
        r = requests.get(url, timeout=10)
        candles = r.json()

        # V3.1.105: min candles depends on timeframe and ADX period
        if granularity in ("1h", "5m"):
            min_candles = 16  # ADX(14) needs 15+ candles
        else:
            min_candles = 30  # ADX(28) needs 29+ candles (15m legacy fallback)
        if not isinstance(candles, list) or len(candles) < min_candles:
            result["error"] = "Insufficient candle data"
            _chop_cache[symbol] = result
            return result

        # Parse candle data (WEEX: [time, open, high, low, close, volume, value])
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
        opens = [float(c[1]) for c in candles]

        # ---- 1. ADX CALCULATION ----
        # ADX measures trend STRENGTH regardless of direction
        # V3.1.94: Period 28 on 15M candles. Thresholds lowered ~3pt (15M DX deflation)
        # V3.1.105: Period 14 for 5m and 1H (standard ADX, enough data points), 28 for 15m legacy
        period = 14 if granularity in ("1h", "5m") else 28
        if len(closes) >= period + 1:
            plus_dm_list = []
            minus_dm_list = []
            tr_list = []

            for i in range(len(candles) - 1):
                high_diff = highs[i] - highs[i + 1]
                low_diff = lows[i + 1] - lows[i]

                plus_dm = high_diff if (high_diff > low_diff and high_diff > 0) else 0
                minus_dm = low_diff if (low_diff > high_diff and low_diff > 0) else 0

                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i + 1]),
                    abs(lows[i] - closes[i + 1])
                )

                plus_dm_list.append(plus_dm)
                minus_dm_list.append(minus_dm)
                tr_list.append(tr)

            if len(tr_list) >= period:
                # Smoothed averages (Wilder's smoothing)
                atr_14 = sum(tr_list[:period]) / period
                plus_dm_14 = sum(plus_dm_list[:period]) / period
                minus_dm_14 = sum(minus_dm_list[:period]) / period

                # +DI and -DI
                plus_di = (plus_dm_14 / atr_14 * 100) if atr_14 > 0 else 0
                minus_di = (minus_dm_14 / atr_14 * 100) if atr_14 > 0 else 0

                # DX and ADX
                di_sum = plus_di + minus_di
                dx = (abs(plus_di - minus_di) / di_sum * 100) if di_sum > 0 else 0

                result["adx"] = round(dx, 1)

        # ---- 2. BOLLINGER BAND WIDTH (40-period on 15M = ~10h, 2 std dev) ----
        # Tight bands = low volatility = choppy/range-bound
        # BB Width % = (Upper - Lower) / Middle * 100
        bb_period = 40
        if len(closes) >= bb_period:
            bb_closes = closes[:bb_period]
            sma = sum(bb_closes) / bb_period
            std_dev = (sum((c - sma) ** 2 for c in bb_closes) / bb_period) ** 0.5

            upper_band = sma + (2 * std_dev)
            lower_band = sma - (2 * std_dev)

            bb_width_pct = ((upper_band - lower_band) / sma * 100) if sma > 0 else 3.0
            result["bb_width_pct"] = round(bb_width_pct, 2)

        # ---- 3. DIRECTIONAL CONSISTENCY (last 24 15M candles = ~6h) ----
        # Counts how many consecutive candles move in the same direction
        # Trending market: most candles agree. Choppy: alternating up/down
        lookback = min(24, len(closes) - 1)
        directions = []  # V3.1.93: init here so recency check can access it
        if lookback >= 6:
            # V3.1.94: Minimum body threshold — filter noise candles
            # Candles with body < 0.1% of price are market noise, not directional moves
            CANDLE_BODY_MIN_PCT = 0.001  # 0.1%

            for i in range(lookback):
                # Compare close to open (candle direction)
                body_pct = abs(closes[i] - opens[i]) / opens[i] if opens[i] > 0 else 0
                if body_pct < CANDLE_BODY_MIN_PCT:
                    directions.append(0)  # Noise candle — treat as doji
                elif closes[i] > opens[i]:
                    directions.append(1)  # Bullish candle
                elif closes[i] < opens[i]:
                    directions.append(-1)  # Bearish candle
                else:
                    directions.append(0)  # Doji

            # Count direction changes (flip-flops)
            flips = 0
            for i in range(len(directions) - 1):
                if directions[i] != 0 and directions[i + 1] != 0 and directions[i] != directions[i + 1]:
                    flips += 1

            max_flips = lookback - 1
            # Consistency: 1.0 = all same direction, 0.0 = alternating every candle
            consistency = 1.0 - (flips / max_flips) if max_flips > 0 else 0.5
            result["directional_consistency"] = round(consistency, 2)

        # V3.1.94: Net price displacement over lookback window
        # Detects stair-step trending (micro-body noise but net directional move)
        net_displacement = 0.0
        if lookback >= 6 and closes[lookback - 1] > 0:
            net_displacement = abs(closes[0] - closes[lookback - 1]) / closes[lookback - 1] * 100
        result["net_displacement"] = round(net_displacement, 2)

        # ---- COMPOSITE CHOP SCORE ----
        adx = result["adx"]
        bb_width = result["bb_width_pct"]
        consistency = result["directional_consistency"]

        chop_signals = 0
        reasons = []

        # ADX scoring — V3.1.94: thresholds lowered ~3pt for 15M DX deflation
        # V3.1.105: 5m uses ADX(14) same as 1H — standard thresholds apply
        if granularity in ("1h", "5m"):
            adx_very_weak, adx_weak = 18, 25  # Standard ADX(14) thresholds
        else:
            adx_very_weak, adx_weak = 12, 17  # 15m with period=28 (legacy)

        if adx < adx_very_weak:
            chop_signals += 2
            reasons.append(f"ADX={adx:.0f} (very weak trend)")
        elif adx < adx_weak:
            chop_signals += 1
            reasons.append(f"ADX={adx:.0f} (weak trend)")

        # BB width scoring - tier-aware thresholds
        # V3.1.89: Tier-specific thresholds
        # V3.1.94: Raised ~30% for 15M close variance
        tier = None
        for _pair, _info in TRADING_PAIRS.items():
            if _info["symbol"] == symbol:
                tier = _info["tier"]
                break
        if tier == 1:
            bb_very_tight, bb_tight = 1.3, 2.3   # BNB/ETH — raised from 1.0/1.8
        elif tier == 3:
            bb_very_tight, bb_tight = 2.6, 3.8   # SOL/DOGE/ADA — raised from 2.0/3.0
        else:
            bb_very_tight, bb_tight = 2.0, 3.2   # BTC/LTC/XRP — raised from 1.5/2.5

        if bb_width < bb_very_tight:
            chop_signals += 2
            reasons.append(f"BB={bb_width:.1f}% (very tight, T{tier} thresh={bb_very_tight})")
        elif bb_width < bb_tight:
            chop_signals += 1
            reasons.append(f"BB={bb_width:.1f}% (tight, T{tier} thresh={bb_tight})")

        # V3.1.93/94: TIER-AWARE RECENCY CHECK — if market was choppy overall but has
        # resolved into a trend recently, reduce the consistency penalty.
        # V3.1.105: 5m candles — recency window ~30-50% of full lookback (in candle counts)
        if granularity == "1h":
            recent_lookback = min(6, lookback)    # 6h — 1H candles (legacy)
        elif granularity == "5m":
            if tier == 3:
                recent_lookback = min(12, lookback)   # 1h of 5m — fast movers
            elif tier == 1:
                recent_lookback = min(20, lookback)   # 1.67h of 5m — blue chips
            else:
                recent_lookback = min(16, lookback)   # 1.33h of 5m — mid caps
        elif tier == 3:
            recent_lookback = min(12, lookback)   # 3h (12 x 15m) — fast movers (legacy)
        elif tier == 1:
            recent_lookback = min(20, lookback)   # 5h (20 x 15m) — blue chips (legacy)
        else:
            recent_lookback = min(16, lookback)   # 4h (16 x 15m) — mid caps (legacy)

        recent_cons = consistency  # Default: same as full window
        if directions and recent_lookback >= 3 and len(directions) >= recent_lookback:
            recent_dirs = directions[:recent_lookback]  # Most recent candles (newest first)
            recent_flips = 0
            for ri in range(len(recent_dirs) - 1):
                if recent_dirs[ri] != 0 and recent_dirs[ri+1] != 0 and recent_dirs[ri] != recent_dirs[ri+1]:
                    recent_flips += 1
            recent_max = recent_lookback - 1
            recent_cons = 1.0 - (recent_flips / recent_max) if recent_max > 0 else 0.5
            result["recent_consistency"] = round(recent_cons, 2)

        # Directional consistency scoring
        # V3.1.93: Recent consistency override
        # V3.1.94: Thresholds lowered 0.08 for 15M (natural intra-hour pullbacks)
        #          + net displacement override for stair-step trending
        if consistency < 0.22:
            # Tier-aware displacement thresholds
            disp_thresh = {1: 1.0, 2: 1.5, 3: 2.0}.get(tier, 1.5)

            if net_displacement >= disp_thresh:
                # V3.1.94: Price moved significantly — stair-step trending, not chop
                reasons.append(f"Dir={consistency:.0%} (stair-step, disp={net_displacement:.1f}%>={disp_thresh}%)")
            elif recent_cons >= 0.6:
                # Was choppy but recent candles trending. Trend resolved — no penalty.
                reasons.append(f"Dir={consistency:.0%} (was choppy, recent={recent_cons:.0%} T{tier} trending)")
            elif recent_cons >= 0.4:
                # Partially resolved. Mild penalty instead of severe.
                chop_signals += 1
                reasons.append(f"Dir={consistency:.0%} (resolving, recent={recent_cons:.0%})")
            else:
                # Still choppy even recently. Full penalty.
                chop_signals += 2
                reasons.append(f"Dir={consistency:.0%} (flip-flopping)")
        elif consistency < 0.37:
            chop_signals += 1
            reasons.append(f"Dir={consistency:.0%} (mixed)")

        # Final classification
        if chop_signals >= 4:
            result["is_choppy"] = True
            result["severity"] = "high"
            result["reason"] = f"HIGH CHOP: {'; '.join(reasons)}"
        elif chop_signals >= 2:
            result["is_choppy"] = True
            result["severity"] = "medium"
            result["reason"] = f"MEDIUM CHOP: {'; '.join(reasons)}"
        else:
            result["is_choppy"] = False
            result["severity"] = "low"
            result["reason"] = f"OK: ADX={adx:.0f}, BB={bb_width:.1f}%, Dir={consistency:.0%}"

        disp_str = f", disp={net_displacement:.1f}%" if net_displacement > 0 else ""
        print(f"  [CHOP-{granularity.upper()}] {symbol.replace('cmt_','').upper()}: {result['reason']}{disp_str}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  [CHOP] {symbol} error: {e}")

    _chop_cache[symbol] = result
    return result


# ============================================================
# V3.1.101: ENTRY CONFIRMATION GATE
# ============================================================
# Check if recent 15m price action confirms signal direction.
# Prevents early entries where ensemble detects direction but
# price hasn't turned yet (e.g. SHORT while price still going up).

def check_entry_confirmation(symbol: str, signal: str) -> dict:
    """V3.1.101: Check if recent price action confirms signal direction.
    Uses last 3 x 15m candles to detect if price is moving WITH or AGAINST the signal.
    Returns: {"confirmed": bool, "penalty": float, "detail": str}
    """
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=15m&limit=3"
        r = requests.get(url, timeout=10)
        candles = r.json()

        if not isinstance(candles, list) or len(candles) < 3:
            return {"confirmed": True, "penalty": 0, "detail": "insufficient data, allowing"}

        # candles[0] = most recent, candles[2] = oldest
        # Each candle: [time, open, high, low, close, volume, value]
        latest_close = float(candles[0][4])
        latest_open = float(candles[0][1])
        prev_close = float(candles[1][4])
        prev_open = float(candles[1][1])

        # Candle body direction (close vs open)
        latest_green = latest_close > latest_open
        prev_green = prev_close > prev_open

        # Short-term momentum (last 30min: 2 candles)
        momentum_30m = ((latest_close - prev_open) / prev_open) * 100

        if signal == "LONG":
            # For LONG: want green candles / upward momentum
            opposing = (not latest_green) and (not prev_green)  # Both red
            momentum_against = momentum_30m < -0.15  # Dropped 0.15%+ in 30min
        else:  # SHORT
            # For SHORT: want red candles / downward momentum
            opposing = latest_green and prev_green  # Both green
            momentum_against = momentum_30m > 0.15  # Rose 0.15%+ in 30min

        if opposing and momentum_against:
            # Price clearly moving OPPOSITE to signal — early entry
            return {
                "confirmed": False,
                "penalty": 0.10,
                "detail": f"OPPOSING: last 2 candles against {signal}, momentum {momentum_30m:+.2f}%"
            }
        elif opposing or momentum_against:
            # Partial opposition — mild concern
            return {
                "confirmed": False,
                "penalty": 0.05,
                "detail": f"WEAK: partial opposition to {signal}, momentum {momentum_30m:+.2f}%"
            }
        else:
            return {
                "confirmed": True,
                "penalty": 0,
                "detail": f"CONFIRMED: price action aligns with {signal}, momentum {momentum_30m:+.2f}%"
            }
    except Exception as e:
        return {"confirmed": True, "penalty": 0, "detail": f"error: {e}"}


# ============================================================
# V3.1.84: CHART-BASED TP/SL (Support/Resistance)
# ============================================================
# Instead of fixed % TP/SL, analyze actual candle swing highs/lows
# to find real support/resistance levels - like a human looking at a chart.
# Competition bounds: TP 0.8-2.0%, SL 0.5-2.0% for fast turnover.

_sr_cache = {}
_sr_cache_time = 0
_prev_flow_direction: dict = {}  # V3.2.1: Track FLOW direction per pair across cycles (for flip discount)

def _cluster_price_levels(levels: list, ref_price: float, threshold_pct: float = 0.3) -> list:
    """Cluster nearby price levels (within threshold_pct of each other).
    Returns the average of each cluster - merges S/R zones."""
    if not levels:
        return []
    sorted_levels = sorted(levels)
    clusters = []
    current_cluster = [sorted_levels[0]]
    for i in range(1, len(sorted_levels)):
        pct_diff = abs(sorted_levels[i] - current_cluster[-1]) / ref_price * 100
        if pct_diff <= threshold_pct:
            current_cluster.append(sorted_levels[i])
        else:
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [sorted_levels[i]]
    clusters.append(sum(current_cluster) / len(current_cluster))
    return clusters


def _find_swing_levels(candles: list) -> tuple:
    """Extract swing high/low pivot levels from candle data.
    Returns (raw_resistances, raw_supports) lists."""
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]

    raw_resistances = []
    for i in range(2, len(candles) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i+1] and
            highs[i] > highs[i-2] and highs[i] > highs[i+2]):
            raw_resistances.append(highs[i])

    raw_supports = []
    for i in range(2, len(candles) - 2):
        if (lows[i] < lows[i-1] and lows[i] < lows[i+1] and
            lows[i] < lows[i-2] and lows[i] < lows[i+2]):
            raw_supports.append(lows[i])

    # Add recent 12-candle extremes as additional S/R
    if len(highs) >= 12:
        raw_resistances.append(max(highs[:12]))
        raw_supports.append(min(lows[:12]))

    return raw_resistances, raw_supports


def find_chart_based_tp_sl(symbol: str, signal: str, entry_price: float) -> dict:
    """V3.2.12: Raw 1H candle wick anchors for TP/SL.

    TP: max high of last 2 COMPLETE 1H candles (immediate ceiling — where sellers appeared most recently).
        Using 2H instead of 6H prevents anchoring to the pre-dip peak when we enter at the bottom.
        Dip-bounce entry + 6H anchor = TP at the old range top = 2-3% away = never hits.
        Dip-bounce entry + 2H anchor = TP at the nearest resistance = 0.5-1.5% = exits fast.

    SL: lowest actual wick in last 12H = min(
            min-low of last 12 1H candles,
            min-low of last 3 4H candles   ← catches any deep wick the 1H grid missed
        )
        No swing detection. Raw candle lows only.

    For SHORT: reversed (min low of last 2 1H for TP, max high of 12H for SL).
    """
    global _sr_cache, _sr_cache_time

    # Cache for 10 minutes
    now = time.time()
    cache_key = f"{symbol}_{signal}"
    if now - _sr_cache_time < 600 and cache_key in _sr_cache:
        cached = _sr_cache[cache_key]
        if cached.get("method") in ("chart_mtf", "chart"):
            return cached

    if now - _sr_cache_time >= 600:
        _sr_cache = {}
        _sr_cache_time = now

    result = {
        "tp_pct": None, "sl_pct": None,
        "tp_price": None, "sl_price": None,
        "method": "fallback",
        "levels": {"resistances": [], "supports": [], "htf_resistances": [], "htf_supports": []}
    }

    # V3.2.24: MIN_TP_PCT removed — chart SR is the ground truth. Flooring to 0.3% was placing
    # TP beyond real resistance; price rejected at SR and TP never filled, bleeding to SL instead.
    MIN_SL_PCT = 1.0   # SL must be at least 1.0% from entry (20x = 20% margin loss min)

    try:
        # === 1H candles — primary source for both TP and SL ===
        # limit=13: candles[0] = current (may be partial), candles[1:7] = last 6 complete, candles[0:12] = last 12H
        url_1h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1h&limit=13"
        r_1h = requests.get(url_1h, timeout=10)
        candles_1h = r_1h.json()

        if not isinstance(candles_1h, list) or len(candles_1h) < 7:
            print(f"  [CHART-SR] {symbol}: Insufficient 1H candle data ({len(candles_1h) if isinstance(candles_1h, list) else 0})")
            return result

        highs_1h = [float(c[2]) for c in candles_1h]  # c[2] = high; candles[0] is most recent
        lows_1h  = [float(c[3]) for c in candles_1h]  # c[3] = low

        # TP anchor: highest high of the last 2 COMPLETE 1H candles (skip candles[0] = current partial)
        # V3.2.12: 6H→2H — dip entries anchored to pre-dip peak got 2-3% TPs, never hitting
        tp_high_2h = max(highs_1h[1:3])
        # TP anchor SHORT: lowest low of the last 2 complete 1H candles
        tp_low_2h  = min(lows_1h[1:3])

        # SL anchor 1H: lowest/highest actual wick in last 12H from 1H candles
        sl_low_12h_1h  = min(lows_1h[0:12])   # LONG SL reference
        sl_high_12h_1h = max(highs_1h[0:12])  # SHORT SL reference

        print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()} 1H anchors: "
              f"2H_high={tp_high_2h:.4f}, 2H_low={tp_low_2h:.4f}, "
              f"12H_low={sl_low_12h_1h:.4f}, 12H_high={sl_high_12h_1h:.4f}")

        # === 4H candles — SL only: catch any deep wick the 1H grid may miss ===
        # last 3 × 4H = last 12H; take min/max raw low/high (no swing detection)
        sl_low_12h_4h  = sl_low_12h_1h   # default: 1H value if 4H fetch fails
        sl_high_12h_4h = sl_high_12h_1h
        try:
            url_4h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=4h&limit=3"
            r_4h = requests.get(url_4h, timeout=10)
            candles_4h = r_4h.json()
            if isinstance(candles_4h, list) and len(candles_4h) >= 2:
                sl_low_12h_4h  = min(float(c[3]) for c in candles_4h)
                sl_high_12h_4h = max(float(c[2]) for c in candles_4h)
                print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()} 4H raw: "
                      f"low={sl_low_12h_4h:.4f}, high={sl_high_12h_4h:.4f}")
        except Exception as e4h:
            print(f"  [CHART-SR] 4H fetch failed ({e4h}), SL from 1H only")

        tp_found = False
        sl_found = False

        if signal == "LONG":
            # TP: just inside the 2H ceiling (where sellers appeared most recently)
            tp_price = tp_high_2h * 0.997
            tp_pct   = (tp_price - entry_price) / entry_price * 100
            if tp_price > entry_price:
                result["tp_pct"]   = round(tp_pct, 2)
                result["tp_price"] = round(entry_price * (1 + tp_pct / 100), 8)
                tp_found = True
                print(f"  [CHART-SR] LONG TP: 2H_high={tp_high_2h:.4f} → {tp_pct:.2f}%")
            else:
                # V3.2.20: 2H_high at/below entry (entered above recent ceiling) — scan 12H resistance list
                # V3.2.29: Walk full list ascending — take first candidate whose haircut still clears entry.
                # Only discard if ALL candidates fail. COMPETITION_FALLBACK_TP is NOT a resistance workaround.
                _cands = sorted([h for h in highs_1h[1:13] if h > entry_price])
                for _candidate_res in _cands:
                    _tp12_price = _candidate_res * 0.997
                    _tp12_pct   = (_tp12_price - entry_price) / entry_price * 100
                    if _tp12_price > entry_price:  # haircut clears entry — use this resistance
                        result["tp_pct"]   = round(_tp12_pct, 2)
                        result["tp_price"] = round(entry_price * (1 + _tp12_pct / 100), 8)
                        tp_found = True
                        print(f"  [CHART-SR] LONG TP (12H walk): {_candidate_res:.4f} → {_tp12_pct:.2f}%")
                        break
                    else:
                        print(f"  [CHART-SR] LONG skip {_candidate_res:.4f}: haircut={_tp12_price:.4f} <= entry")
                if not tp_found and _cands:
                    print(f"  [CHART-SR] LONG: all {len(_cands)} resistances below entry after haircut — tp_not_found")

            # SL: lowest actual wick in 12H from either timeframe ("take whichever is lowest")
            sl_price = min(sl_low_12h_1h, sl_low_12h_4h) * 0.997
            sl_pct   = (entry_price - sl_price) / entry_price * 100
            sl_pct   = max(sl_pct, MIN_SL_PCT)
            result["sl_pct"]   = round(sl_pct, 2)
            result["sl_price"] = round(entry_price * (1 - sl_pct / 100), 8)
            sl_found = True
            print(f"  [CHART-SR] LONG SL: min(1H={sl_low_12h_1h:.4f}, 4H={sl_low_12h_4h:.4f})={min(sl_low_12h_1h,sl_low_12h_4h):.4f} → {sl_pct:.2f}%")

        elif signal == "SHORT":
            # TP: just inside the 2H floor (where buyers appeared most recently)
            tp_price = tp_low_2h * 1.003
            tp_pct   = (entry_price - tp_price) / entry_price * 100
            if tp_price < entry_price:
                result["tp_pct"]   = round(tp_pct, 2)
                result["tp_price"] = round(entry_price * (1 - tp_pct / 100), 8)
                tp_found = True
                print(f"  [CHART-SR] SHORT TP: 2H_low={tp_low_2h:.4f} → {tp_pct:.2f}%")
            else:
                # V3.2.20: 2H_low at/above entry — scan 12H support list
                # V3.2.29: Walk full list descending — take first candidate whose haircut still clears entry.
                # Only discard if ALL candidates fail. COMPETITION_FALLBACK_TP is NOT a resistance workaround.
                _cands = sorted([l for l in lows_1h[1:13] if l < entry_price], reverse=True)
                for _candidate_sup in _cands:
                    _tp12_price = _candidate_sup * 1.003
                    _tp12_pct   = (entry_price - _tp12_price) / entry_price * 100
                    if _tp12_price < entry_price:  # haircut clears entry — use this support
                        result["tp_pct"]   = round(_tp12_pct, 2)
                        result["tp_price"] = round(entry_price * (1 - _tp12_pct / 100), 8)
                        tp_found = True
                        print(f"  [CHART-SR] SHORT TP (12H walk): {_candidate_sup:.4f} → {_tp12_pct:.2f}%")
                        break
                    else:
                        print(f"  [CHART-SR] SHORT skip {_candidate_sup:.4f}: haircut={_tp12_price:.4f} >= entry")
                if not tp_found and _cands:
                    print(f"  [CHART-SR] SHORT: all {len(_cands)} supports above entry after haircut — tp_not_found")

            # SL: highest actual wick in 12H from either timeframe
            sl_price = max(sl_high_12h_1h, sl_high_12h_4h) * 1.003
            sl_pct   = (sl_price - entry_price) / entry_price * 100
            sl_pct   = max(sl_pct, MIN_SL_PCT)
            result["sl_pct"]   = round(sl_pct, 2)
            result["sl_price"] = round(entry_price * (1 + sl_pct / 100), 8)
            sl_found = True
            print(f"  [CHART-SR] SHORT SL: max(1H={sl_high_12h_1h:.4f}, 4H={sl_high_12h_4h:.4f})={max(sl_high_12h_1h,sl_high_12h_4h):.4f} → {sl_pct:.2f}%")

        if tp_found and sl_found:
            result["method"] = "chart_mtf"
            print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()} {signal}: "
                  f"TP {result['tp_pct']:.2f}% (${result['tp_price']:.4f}), "
                  f"SL {result['sl_pct']:.2f}% (${result['sl_price']:.4f})")
        elif tp_found:
            result["method"] = "chart_mtf"
            print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()}: TP {result['tp_pct']:.2f}%, SL fallback")
        elif sl_found:
            result["method"] = "chart_mtf"
            print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()}: SL {result['sl_pct']:.2f}%, TP fallback")
        else:
            print(f"  [CHART-SR] {symbol.replace('cmt_','').upper()}: No levels found, using fallback")

    except Exception as e:
        print(f"  [CHART-SR] Error for {symbol}: {e}")

    _sr_cache[cache_key] = result
    return result


# ============================================================
# V3.2.16: CHART CONTEXT FOR GEMINI JUDGE
# Pulls 1D (5-day) + 4H (32h) candles to give Gemini real
# structural context: trend, key S/R levels, 5D range.
# ============================================================
_chart_context_cache = {}
_chart_context_cache_time = 0

def get_chart_context(symbol: str) -> str:
    """V3.2.16: Build compact multi-TF chart context string for Gemini Judge.

    Pulls 1D candles (5 complete days) and 4H candles (8 complete = 32h).
    Returns a text block with trend, S/R levels, and 5D range.
    Cached for 10 minutes (same as signal check interval).
    """
    global _chart_context_cache, _chart_context_cache_time

    now = time.time()
    if now - _chart_context_cache_time < 600 and symbol in _chart_context_cache:
        return _chart_context_cache[symbol]

    if now - _chart_context_cache_time >= 600:
        _chart_context_cache = {}
        _chart_context_cache_time = now

    pair_label = symbol.replace("cmt_", "").replace("usdt", "").upper()

    try:
        # === 1D candles: 6 candles → skip [0] (current partial), use [1:6] = 5 complete days ===
        url_1d = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1Dutc&limit=6"
        r_1d = requests.get(url_1d, timeout=10)
        candles_1d = r_1d.json()

        # === 4H candles: 9 candles → skip [0] (current partial), use [1:9] = 8 complete = 32h ===
        url_4h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=4h&limit=9"
        r_4h = requests.get(url_4h, timeout=10)
        candles_4h = r_4h.json()

        if not isinstance(candles_1d, list) or len(candles_1d) < 4:
            return f"{pair_label}: Daily candle data insufficient"
        if not isinstance(candles_4h, list) or len(candles_4h) < 5:
            return f"{pair_label}: 4H candle data insufficient"

        # 1D: extract OHLC from complete candles [1:6]
        d_opens  = [float(c[1]) for c in candles_1d[1:6]]
        d_highs  = [float(c[2]) for c in candles_1d[1:6]]
        d_lows   = [float(c[3]) for c in candles_1d[1:6]]
        d_closes = [float(c[4]) for c in candles_1d[1:6]]

        d_high_5d = max(d_highs)
        d_low_5d  = min(d_lows)
        d_current = float(candles_1d[0][4])  # Current candle close = latest price

        # 5D trend: compare current vs 5 days ago close
        d_oldest_close = d_closes[-1]  # Oldest complete day
        d_change_5d = ((d_current - d_oldest_close) / d_oldest_close) * 100

        # Daily resistance: top 2 highs (sorted desc)
        d_res_sorted = sorted(d_highs, reverse=True)
        d_resistances = d_res_sorted[:2]

        # Daily support: bottom 2 lows (sorted asc)
        d_sup_sorted = sorted(d_lows)
        d_supports = d_sup_sorted[:2]

        # 4H: extract from complete candles [1:9]
        h4_highs  = [float(c[2]) for c in candles_4h[1:9]]
        h4_lows   = [float(c[3]) for c in candles_4h[1:9]]
        h4_closes = [float(c[4]) for c in candles_4h[1:9]]

        # 4H resistance: top 2 highs
        h4_res_sorted = sorted(h4_highs, reverse=True)
        h4_resistances = h4_res_sorted[:2]

        # 4H support: bottom 2 lows
        h4_sup_sorted = sorted(h4_lows)
        h4_supports = h4_sup_sorted[:2]

        # 32h trend
        h4_oldest_close = h4_closes[-1]
        h4_change = ((d_current - h4_oldest_close) / h4_oldest_close) * 100

        # Determine trend label
        if d_change_5d > 3:
            trend_5d = "Strong Uptrend"
        elif d_change_5d > 1:
            trend_5d = "Mild Uptrend"
        elif d_change_5d < -3:
            trend_5d = "Strong Downtrend"
        elif d_change_5d < -1:
            trend_5d = "Mild Downtrend"
        else:
            trend_5d = "Consolidating"

        # Format price with appropriate decimals
        def _fmt(p):
            if p >= 1000:
                return f"${p:,.1f}"
            elif p >= 1:
                return f"${p:.4f}"
            else:
                return f"${p:.6f}"

        context = (
            f"{pair_label} CHART CONTEXT:\n"
            f"  5D: High={_fmt(d_high_5d)} Low={_fmt(d_low_5d)} Current={_fmt(d_current)} ({d_change_5d:+.1f}%) Trend: {trend_5d}\n"
            f"  32H: {h4_change:+.1f}% from 32h ago\n"
            f"  Daily Resistance: {_fmt(d_resistances[0])}"
            + (f", {_fmt(d_resistances[1])}" if len(d_resistances) > 1 and d_resistances[1] != d_resistances[0] else "")
            + f" | 4H Resistance: {_fmt(h4_resistances[0])}"
            + (f", {_fmt(h4_resistances[1])}" if len(h4_resistances) > 1 and h4_resistances[1] != h4_resistances[0] else "")
            + f"\n"
            f"  Daily Support: {_fmt(d_supports[0])}"
            + (f", {_fmt(d_supports[1])}" if len(d_supports) > 1 and d_supports[1] != d_supports[0] else "")
            + f" | 4H Support: {_fmt(h4_supports[0])}"
            + (f", {_fmt(h4_supports[1])}" if len(h4_supports) > 1 and h4_supports[1] != h4_supports[0] else "")
        )

        print(f"  [CHART-CTX] {pair_label}: 5D {d_change_5d:+.1f}%, 32H {h4_change:+.1f}%, "
              f"Res={_fmt(d_resistances[0])}/{_fmt(h4_resistances[0])}, "
              f"Sup={_fmt(d_supports[0])}/{_fmt(h4_supports[0])}")

        _chart_context_cache[symbol] = context
        return context

    except Exception as e:
        fallback = f"{pair_label}: Chart context unavailable ({e})"
        print(f"  [CHART-CTX] {fallback}")
        _chart_context_cache[symbol] = fallback
        return fallback


# V3.2.0: Competition fallback TPs — dip-signal strategy, 0.5% grab-and-go
COMPETITION_FALLBACK_TP = {
    1: 0.5,   # Tier 1 (ETH, BNB): 0.5% fast exit
    2: 0.5,   # Tier 2 (BTC, LTC, XRP): 0.5% fast exit
    3: 0.5,   # Tier 3 (SOL, DOGE, ADA): 0.5% fast exit
}
COMPETITION_FALLBACK_SL = {
    1: 1.2,   # Tier 1: 1.5% → 1.2%
    2: 1.2,   # Tier 2: 1.5% → 1.2%
    3: 1.5,   # Tier 3: 1.8% → 1.5%
}

# V3.1.94: Per-pair overrides removed — flat 1.1% TP cap + chart SL (+0.5% buffer) for all
PAIR_TP_CAP = {}
PAIR_SL_FLOOR = {}


def detect_whale_absorption(whale_vote: dict, flow_vote: dict, regime: dict) -> dict:
    """
    V3.1.21: Detect whale absorption - whales buying while retail panic sells.
    
    BULLISH ABSORPTION: 
    - Extreme selling pressure (taker ratio < 0.5)
    - But whale flow is POSITIVE (accumulating)
    - Price hasn't broken 4h support
    = Whales absorbing the dip, prepare for reversal
    """
    result = {"absorption_detected": False, "type": "NONE", "boost": 1.0}
    
    try:
        whale_signal = whale_vote.get("signal", "NEUTRAL")
        whale_conf = whale_vote.get("confidence", 0)
        whale_data = whale_vote.get("data", {})
        net_flow = whale_data.get("net_flow", 0)
        
        flow_signal = flow_vote.get("signal", "NEUTRAL")
        
        # BULLISH ABSORPTION: Extreme selling but whales accumulating
        if flow_signal == "SHORT" and whale_signal == "LONG" and net_flow > 200:
            result = {
                "absorption_detected": True, 
                "type": "BULLISH_ABSORPTION",
                "boost": 1.5,
                "reason": f"Whales absorbing sell-off (+{net_flow:.0f} ETH)"
            }
            print(f"  [ABSORPTION] BULLISH: Retail panic selling but whales +{net_flow:.0f} ETH")
        
        # BEARISH DISTRIBUTION: Extreme buying but whales distributing
        elif flow_signal == "LONG" and whale_signal == "SHORT" and net_flow < -200:
            result = {
                "absorption_detected": True,
                "type": "BEARISH_DISTRIBUTION",
                "boost": 1.5,
                "reason": f"Whales distributing into rally ({net_flow:.0f} ETH)"
            }
            print(f"  [DISTRIBUTION] BEARISH: Retail FOMO but whales {net_flow:.0f} ETH")
    except:
        pass
    
    return result

def detect_regime_shift() -> dict:
    """V3.1.21: Detect 4h trend flips for early entry"""
    result = {"shift_detected": False, "shift_type": "NONE", "confidence_adjustment": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=3"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 3:
            curr = float(candles[0][4])
            prev = float(candles[1][4])
            prev2 = float(candles[2][4])
            prev_4h = ((prev - prev2) / prev2) * 100
            curr_4h = ((curr - prev) / prev) * 100
            if prev_4h > 0.5 and curr_4h < -0.3:
                result = {"shift_detected": True, "shift_type": "BEARISH_SHIFT", "confidence_adjustment": -15}
                print(f"  [REGIME SHIFT] BEARISH: +{prev_4h:.1f}% -> {curr_4h:.1f}%")
            elif prev_4h < -0.5 and curr_4h > 0.3:
                result = {"shift_detected": True, "shift_type": "BULLISH_SHIFT", "confidence_adjustment": -10}
                print(f"  [REGIME SHIFT] BULLISH: {prev_4h:.1f}% -> +{curr_4h:.1f}%")
    except Exception as e:
        print(f"  [REGIME SHIFT] Error: {e}")
    return result



def get_support_proximity(symbol="cmt_btcusdt") -> dict:
    """V3.1.21: Check if near 24h low (support) - BULLISH equivalent of resistance"""
    result = {"near_support": False, "distance_pct": 0, "low_24h": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1h&limit=25"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 24:
            lows = [float(c[3]) for c in candles[:24]]
            low_24h = min(lows)
            current = float(candles[0][4])
            dist = ((current - low_24h) / low_24h) * 100
            result = {"near_support": dist < 1.0, "distance_pct": round(dist, 2), "low_24h": low_24h, "current_price": current}
            if result["near_support"]:
                print(f"  [SUPPORT] Near 24h low: ${current:.0f} vs ${low_24h:.0f} (+{dist:.1f}%)")
    except: pass
    return result

def get_resistance_proximity(symbol="cmt_btcusdt") -> dict:
    """V3.1.21: Check if near 24h high"""
    result = {"near_resistance": False, "distance_pct": 0}
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity=1h&limit=25"
        r = requests.get(url, timeout=10)
        candles = r.json()
        if isinstance(candles, list) and len(candles) >= 24:
            high_24h = max(float(c[2]) for c in candles[:24])
            current = float(candles[0][4])
            dist = ((current - high_24h) / high_24h) * 100
            result = {"near_resistance": dist > -1.0, "distance_pct": round(dist, 2), "high_24h": high_24h}
            if result["near_resistance"]:
                print(f"  [RESISTANCE] Near 24h high: ${current:.0f} vs ${high_24h:.0f}")
    except: pass
    return result


def get_enhanced_market_regime() -> dict:
    """
    V3.1.12: Multi-factor regime detection
    V3.1.21: Cached for 5 minutes to reduce API calls
    """
    # Check cache first
    cached = REGIME_CACHE.get("regime", 120)  # V3.1.23: 2 min cache for faster reaction
    if cached:
        return cached
    
    """
    V3.1.12: Multi-factor regime detection
    
    Factors (with weights):
    1. BTC 24h change: -3 to +3 (primary driver)
    2. BTC 4h change: -1 to +1 (short-term momentum)
    3. Fear & Greed: -2 to +2 (CONTRARIAN - fear=buy, greed=sell)
    4. Funding Rate: -2 to +2 (leverage positioning)
    
    Total score determines regime:
    - score <= -1: BEARISH
    - score >= +1: BULLISH
    - else: NEUTRAL
    """
    import requests
    
    result = {
        "regime": "NEUTRAL",
        "confidence": 0.5,
        "btc_24h": 0,
        "btc_4h": 0,
        "btc_1h": 0,  # V3.1.74: for freshness filter
        "fear_greed": 50,
        "avg_funding": 0,
        "factors": [],
        "score": 0
    }
    
    score = 0
    factors = []
    
    # ===== Factor 1 & 2: BTC Price =====
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            btc_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            btc_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            result["btc_24h"] = btc_24h
            result["btc_4h"] = btc_4h
            
            if btc_24h < -2: score -= 3; factors.append(f"BTC dumping: {btc_24h:+.1f}%")
            elif btc_24h < -1: score -= 2; factors.append(f"BTC dropping: {btc_24h:+.1f}%")
            elif btc_24h < -0.5: score -= 1; factors.append(f"BTC weak: {btc_24h:+.1f}%")
            elif btc_24h > 2: score += 3; factors.append(f"BTC pumping: {btc_24h:+.1f}%")
            elif btc_24h > 1: score += 2; factors.append(f"BTC rising: {btc_24h:+.1f}%")
            elif btc_24h > 0.5: score += 1; factors.append(f"BTC up: {btc_24h:+.1f}%")
            
            if btc_4h < -1: score -= 1; factors.append(f"4h down: {btc_4h:+.1f}%")
            elif btc_4h > 1: score += 1; factors.append(f"4h up: {btc_4h:+.1f}%")
    except Exception as e:
        factors.append(f"BTC error: {e}")

    # V3.1.74: 1h BTC change for freshness filter
    try:
        url_1h = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=1h&limit=2"
        r_1h = requests.get(url_1h, timeout=10)
        data_1h = r_1h.json()
        if isinstance(data_1h, list) and len(data_1h) >= 2:
            closes_1h = [float(c[4]) for c in data_1h]
            btc_1h = ((closes_1h[0] - closes_1h[1]) / closes_1h[1]) * 100
            result["btc_1h"] = btc_1h
    except:
        pass  # btc_1h stays 0

    # ===== Factor 3: Fear & Greed (CONTRARIAN) =====
    fg = get_fear_greed_index()
    result["fear_greed"] = fg["value"]
    
    if fg["error"] is None:
        # V3.1.88: Softened F&G regime impact from ±2 to ±1 (soft bias, not hard override)
        if fg["value"] <= 20: score += 1; factors.append(f"EXTREME FEAR ({fg['value']}): mild contrarian BUY bias")
        elif fg["value"] <= 35: score += 1; factors.append(f"Fear ({fg['value']})")
        elif fg["value"] >= 80: score -= 1; factors.append(f"EXTREME GREED ({fg['value']}): mild contrarian SELL bias")
        elif fg["value"] >= 65: score -= 1; factors.append(f"Greed ({fg['value']})")
    
    # ===== Factor 4: Aggregate Funding =====
    funding = get_aggregate_funding_rate()
    result["avg_funding"] = funding["avg_funding"]
    
    if funding["error"] is None:
        if funding["avg_funding"] > 0.0008: score -= 2; factors.append(f"High funding: longs overleveraged")
        elif funding["avg_funding"] > 0.0004: score -= 1; factors.append(f"Elevated funding")
        elif funding["avg_funding"] < -0.0004: score += 2; factors.append(f"Negative funding: shorts squeezable")
        elif funding["avg_funding"] < -0.0001: score += 1; factors.append(f"Low funding")
    
    # ===== V3.1.16: Factor 5 - Open Interest Signal =====
    oi_signal = get_oi_change_signal()
    result["oi_signal"] = oi_signal["signal"]
    result["oi_reason"] = oi_signal["reason"]
    
    if oi_signal["signal"] == "BEARISH":
        score -= 2
        factors.append(f"OI: {oi_signal['reason'][:50]}")
    elif oi_signal["signal"] == "BULLISH":
        score += 2
        factors.append(f"OI: {oi_signal['reason'][:50]}")
    
    # ===== V3.1.16: Factor 6 - ATR Volatility =====
    atr_data = get_btc_atr()
    result["volatility"] = atr_data["volatility"]
    result["size_multiplier"] = atr_data["size_multiplier"]
    result["atr_ratio"] = atr_data["atr_ratio"]
    
    if atr_data["volatility"] in ("EXTREME", "HIGH"):
        factors.append(f"ATR: {atr_data['volatility']} volatility (size x{atr_data['size_multiplier']:.1f})")
    
    # ===== V3.1.17: Factor 7 - ALTCOIN MOMENTUM =====
    # If BTC is flat but altcoins are bleeding, that's BEARISH
    try:
        altcoin_changes = []
        for alt_pair in ["solusdt", "dogeusdt", "adausdt", "xrpusdt"]:
            alt_url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_{alt_pair}&granularity=4h&limit=7"
            alt_r = requests.get(alt_url, timeout=5)
            alt_data = alt_r.json()
            if isinstance(alt_data, list) and len(alt_data) >= 7:
                alt_closes = [float(c[4]) for c in alt_data]
                alt_24h = ((alt_closes[0] - alt_closes[6]) / alt_closes[6]) * 100
                altcoin_changes.append(alt_24h)
        
        if altcoin_changes:
            avg_altcoin_change = sum(altcoin_changes) / len(altcoin_changes)
            result["altcoin_avg"] = avg_altcoin_change
            
            # V3.1.17: If alts avg < -2%, market is BEARISH regardless of BTC
            if avg_altcoin_change < -4:
                score -= 3
                factors.append(f"ALTCOINS BLEEDING: avg {avg_altcoin_change:+.1f}%")
            elif avg_altcoin_change < -2:
                score -= 2
                factors.append(f"Altcoins weak: avg {avg_altcoin_change:+.1f}%")
            elif avg_altcoin_change > 3:
                score += 2
                factors.append(f"Altcoins pumping: avg {avg_altcoin_change:+.1f}%")
    except Exception as e:
        result["altcoin_avg"] = 0
    
    # ===== Final Regime =====
    result["score"] = score
    result["factors"] = factors
    
    # V3.1.23: Determine raw regime from score
    if score <= -3: raw_regime = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: raw_regime = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: raw_regime = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: raw_regime = "BULLISH"; result["confidence"] = 0.65
    else: raw_regime = "NEUTRAL"; result["confidence"] = 0.5
    
    # V3.1.23: Apply hysteresis with momentum override (pass btc_4h for fast switching)
    result["regime"] = apply_regime_hysteresis(score, raw_regime, result.get("btc_4h", 0))
    
    print(f"  [REGIME] {result['regime']} (score: {score}, conf: {result['confidence']:.0%})")
    print(f"  [REGIME] BTC 24h: {result['btc_24h']:+.1f}% | F&G: {result['fear_greed']} | Funding: {result['avg_funding']:.5f}")
    print(f"  [REGIME] OI Signal: {oi_signal['signal']} | Volatility: {atr_data['volatility']} | Alts: {result.get('altcoin_avg', 0):+.1f}%")
    for f in factors[:6]:
        print(f"  [REGIME]   > {f}")
    
    # V3.1.21: Cache the result
    REGIME_CACHE.set("regime", result)
    return result


# Competition
COMPETITION_START = datetime(2026, 2, 8, 15, 0, 0, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 23, 23, 59, 0, tzinfo=timezone.utc)  # V3.1.77: Fixed - competition ends Feb 23
STARTING_BALANCE = 10000.0  # V3.1.42: Finals - started with 10K
FLOOR_BALANCE = 400.0  # V3.1.63: Liquidation floor - hard stop

# Trading Parameters - V3.1.16 UPDATES
MAX_LEVERAGE = 20
# V3.2.16: 7 pairs, 4 slots flat. BTC/ETH/BNB re-added (Gemini chart context makes them viable).
# V3.2.18: Shorts allowed for ALL pairs (was LTC only). 80% floor + chop filter = sufficient protection.
MAX_TOTAL_POSITIONS = 4  # Hard cap: 4 slots for 7 pairs

def get_max_positions_for_equity(equity: float) -> int:
    """V3.2.16: Fixed 4-slot system for 7 pairs. Equity no longer scales slots."""
    return MAX_TOTAL_POSITIONS
MAX_SINGLE_POSITION_PCT = 0.50  # V3.1.62: LAST PLACE - 50% max per trade
MIN_SINGLE_POSITION_PCT = 0.20  # V3.1.62: LAST PLACE - 20% min per trade
MIN_CONFIDENCE_TO_TRADE = 0.80  # V3.1.77b: 85%->80%. With 3 slots, fill with best signals only.
CHOP_FALLBACK_CONFIDENCE = 0.80  # V3.1.85: Raised to 80%. No sub-80% trades, period.

# V3.1.92: Equity-based sizing with liquidation safety floors
_sizing_equity_cache = {"sizing_base": 0, "equity": 0, "available": 0, "ts": 0}

def get_sizing_base(balance: float) -> float:
    """Get equity-aware sizing base with liquidation safety floors.

    Sizes off equity (balance + UPnL) to leverage unrealized gains,
    but with hard floors to prevent liquidation cascades.

    Floors:
    1. Never below balance (don't amplify losses)
    2. Never above balance * 2.5 (cap runaway sizing)
    3. If available margin < 15% of balance, returns 0 (skip trade)
    """
    global _sizing_equity_cache
    now = time.time()

    # Cache for 60s (one API call per signal cycle, not per pair)
    if now - _sizing_equity_cache["ts"] < 60 and _sizing_equity_cache["sizing_base"] > 0:
        return _sizing_equity_cache["sizing_base"]

    try:
        acct = get_account_equity()
        equity = acct.get("equity", 0)
        available = acct.get("available", 0)

        if equity <= 0:
            equity = balance  # API failed, fall back to balance

        # FLOOR 1: If available margin < $1000, signal "don't trade" — not enough free margin to size meaningfully
        # V3.2.26: fixed $1000 guard (was balance*0.15 ≈ $150 — too low, led to tiny rejected orders)
        if available > 0 and available < 1000.0:
            print(f"  [MARGIN GUARD] Available ${available:.0f} < $1000 minimum. Sizing blocked.")
            _sizing_equity_cache = {"sizing_base": 0, "equity": equity, "available": available, "ts": now}
            return 0

        # V3.2.25: Size from available free margin — equity minus what's already deployed.
        # V3.2.26: Floor at $1000 — always size off at least $1000 base → $250 margin min → $5k notional at 20x
        sizing_base = available if available > 0 else balance
        sizing_base = min(sizing_base, balance * 2.5)   # Cap runaway (e.g. huge UPnL inflating available)
        sizing_base = max(sizing_base, 1000.0)          # Floor: $1000 minimum sizing base

        print(f"  [SIZING] Equity: ${equity:.0f} | Available: ${available:.0f} | Balance: ${balance:.0f} | Sizing base: ${sizing_base:.0f}")

        _sizing_equity_cache = {"sizing_base": sizing_base, "equity": equity, "available": available, "ts": now}
        return sizing_base
    except Exception as e:
        print(f"  [SIZING] Equity fetch failed ({e}), using balance ${balance:.0f}")
        return balance

# ============================================================
# V3.1.78: TIER-BASED PARAMETERS (UPDATED!)
# ============================================================
# Tier 1: Blue Chip (ETH, BNB) - tight SL, long hold
# Tier 2: Mid Cap (BTC, LTC, XRP) - moderate vol, 12h hold
# Tier 3: Small Cap (SOL, DOGE, ADA) - high vol, short 8h hold


# ============================================================
# V3.1.21: GEMINI API RATE LIMITER
# ============================================================
_last_gemini_call = 0
_gemini_call_interval = 8.0  # V3.1.75: 8s between Gemini calls (4s caused 5/8 empty responses)

def _rate_limit_gemini():
    """Ensure minimum interval between Gemini API calls"""
    global _last_gemini_call
    import time
    now = time.time()
    elapsed = now - _last_gemini_call
    if elapsed < _gemini_call_interval:
        sleep_time = _gemini_call_interval - elapsed
        print(f"  [SENTIMENT] Rate limiting: sleeping {sleep_time:.1f}s")
        time.sleep(sleep_time)
    _last_gemini_call = time.time()

def _exponential_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Calculate backoff delay with jitter"""
    import random
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter

# V3.1.78: FLAT 20x LEVERAGE, competition only cares about final PnL
# Tier TP/SL unchanged, R:R floor removed (was broken - tier cap overrode it)
TIER_CONFIG = {
    1: {"name": "Blue Chip", "leverage": 20, "stop_loss": 0.015, "take_profit": 0.03, "trailing_stop": 0.01, "time_limit": 1440, "tp_pct": 3.0, "sl_pct": 1.5, "max_hold_hours": 24, "early_exit_hours": 6, "early_exit_loss_pct": -1.0, "force_exit_loss_pct": -2.0},
    2: {"name": "Mid Cap", "leverage": 20, "stop_loss": 0.015, "take_profit": 0.035, "trailing_stop": 0.012, "time_limit": 720, "tp_pct": 3.5, "sl_pct": 1.5, "max_hold_hours": 12, "early_exit_hours": 4, "early_exit_loss_pct": -1.0, "force_exit_loss_pct": -2.0},
    3: {"name": "Small Cap", "leverage": 20, "stop_loss": 0.018, "take_profit": 0.03, "trailing_stop": 0.015, "time_limit": 480, "tp_pct": 3.0, "sl_pct": 1.8, "max_hold_hours": 8, "early_exit_hours": 3, "early_exit_loss_pct": -1.0, "force_exit_loss_pct": -2.0},
}
# V3.1.78: Tier reassignment based on actual ATR/volatility analysis
# BTC T1→T2 (2.28% actual SL, +52% stretch - behaves mid-cap)
# SOL T2→T3 (3.36% actual SL, higher than ADA - needs short hold)
TRADING_PAIRS = {
    # V3.2.16: 7 pairs — BTC/ETH/BNB re-added with Gemini chart context for smarter TP targeting
    # DOGE removed V3.2.11 — erratic SL/orphan behavior (stays out)
    # V3.2.18: Shorts allowed for ALL pairs (was LTC only). Ensemble + 80% floor + chop filter = protection.
    "BTC": {"symbol": "cmt_btcusdt", "tier": 1, "has_whale_data": True},   # LONG + SHORT (re-added V3.2.16)
    "ETH": {"symbol": "cmt_ethusdt", "tier": 1, "has_whale_data": True},   # LONG + SHORT (re-added V3.2.16)
    "BNB": {"symbol": "cmt_bnbusdt", "tier": 2, "has_whale_data": True},   # LONG + SHORT (re-added V3.2.16)
    "LTC": {"symbol": "cmt_ltcusdt", "tier": 2, "has_whale_data": True},   # LONG + SHORT
    "XRP": {"symbol": "cmt_xrpusdt", "tier": 2, "has_whale_data": True},   # LONG + SHORT
    "SOL": {"symbol": "cmt_solusdt", "tier": 3, "has_whale_data": True},   # LONG + SHORT
    "ADA": {"symbol": "cmt_adausdt", "tier": 3, "has_whale_data": True},   # LONG + SHORT
}

# Pipeline Version
PIPELINE_VERSION = "SMT-v3.2.16-GeminiChartContext-7Pairs"
MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.2.16"

# Known step sizes
KNOWN_STEP_SIZES = {
    "cmt_btcusdt": 0.0001,
    "cmt_ethusdt": 0.01,
    "cmt_solusdt": 0.1,
    "cmt_dogeusdt": 100,
    "cmt_xrpusdt": 10,
    "cmt_adausdt": 10,
    "cmt_bnbusdt": 0.1,
    "cmt_ltcusdt": 0.1,
}

CEX_ADDRESSES = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
}

CONTRACT_INFO_CACHE = {}


# ============================================================
# TIER HELPER FUNCTIONS
# ============================================================

def get_tier_for_symbol(symbol: str) -> int:
    """Get tier number for a symbol (e.g., 'cmt_btcusdt' -> 1)"""
    for pair_name, pair_info in TRADING_PAIRS.items():
        if pair_info["symbol"] == symbol:
            return pair_info.get("tier", 2)
    return 2  # Default to mid tier


def get_tier_for_pair(pair_name: str) -> int:
    """Get tier number for a pair name (e.g., 'BTC' -> 1)"""
    if pair_name in TRADING_PAIRS:
        return TRADING_PAIRS[pair_name].get("tier", 2)
    return 2


def get_tier_config(tier: int) -> Dict:
    """Get tier configuration"""
    if isinstance(tier, str):
        tier = int(tier.replace("Tier ", ""))
    return TIER_CONFIG.get(tier, TIER_CONFIG[2])
    """Get tier configuration"""

def get_tier_config_for_symbol(symbol: str) -> Dict:
    """Get tier config for a symbol"""
    tier = get_tier_for_symbol(symbol)
    return get_tier_config(tier)


def get_tier_config_for_pair(pair_name: str) -> Dict:
    """Get tier config for a pair name"""
    tier = get_tier_for_pair(pair_name)
    return get_tier_config(tier)


# ============================================================
# WEEX API HELPERS
# ============================================================

def weex_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def weex_headers(method: str, path: str, body: str = "") -> Dict:
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


def get_balance() -> float:
    """Get available USDT balance from WEEX"""
    try:
        endpoint = "/capi/v2/account/assets"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        if isinstance(data, list):
            for asset in data:
                if asset.get("coinName") == "USDT":
                    available = float(asset.get("available", 0))
                    if available > 0:
                        return available
        
        endpoint2 = "/capi/v2/account/accounts"
        r2 = requests.get(f"{WEEX_BASE_URL}{endpoint2}", headers=weex_headers("GET", endpoint2), timeout=15)
        data2 = r2.json()
        if "collateral" in data2 and len(data2["collateral"]) > 0:
            amount = float(data2["collateral"][0].get("amount", 0))
            if amount > 0:
                return amount
        
        if TEST_MODE:
            return SIMULATED_BALANCE
        return SIMULATED_BALANCE
    except Exception as e:
        print(f"  [ERROR] get_balance: {e}")
        return SIMULATED_BALANCE


def get_account_equity() -> dict:
    """
    V3.1.19: Get full account info including equity from WEEX
    Returns: {"available": X, "equity": X, "unrealized_pnl": X}
    """
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
                        "unrealized_pnl": float(asset.get("unrealizePnl", 0)),
                        "frozen": float(asset.get("frozen", 0)),
                    }
        
        # Fallback
        return {"available": 0, "equity": 0, "unrealized_pnl": 0, "frozen": 0}
    except Exception as e:
        print(f"  [ERROR] get_account_equity: {e}")
        return {"available": 0, "equity": 0, "unrealized_pnl": 0, "frozen": 0}


def get_open_positions() -> List[Dict]:
    try:
        endpoint = "/capi/v2/account/position/allPosition"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        positions = []
        if isinstance(data, list):
            for pos in data:
                size = float(pos.get("size", 0))
                if size > 0:
                    margin = float(pos.get("marginSize", 0))
                    open_value = float(pos.get("open_value", 0))
                    entry_price = open_value / size if size > 0 else 0
                    positions.append({
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side", "").upper(),
                        "size": size,
                        "entry_price": entry_price,
                        "unrealized_pnl": float(pos.get("unrealizePnl", 0)),
                        "margin": margin,
                        # V3.1.81: Preserve position open time for accurate max_hold tracking
                        "ctime": pos.get("ctime", ""),
                        "utime": pos.get("utime", ""),
                    })
        return positions
    except:
        return []


def get_contract_info(symbol: str) -> Dict:
    global CONTRACT_INFO_CACHE
    if symbol in CONTRACT_INFO_CACHE:
        return CONTRACT_INFO_CACHE[symbol]
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/contracts?symbol={symbol}", timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            info = data[0]
            step_size = KNOWN_STEP_SIZES.get(symbol, 0.001)
            contract_info = {
                "symbol": symbol,
                "tick_size": info.get("tick_size", "2"),
                "min_order_size": float(info.get("minOrderSize", "0.001")),
                "step_size": step_size,
            }
            CONTRACT_INFO_CACHE[symbol] = contract_info
            return contract_info
    except:
        pass
    return {"symbol": symbol, "step_size": KNOWN_STEP_SIZES.get(symbol, 0.001), "tick_size": "2"}


def round_size_to_step(size: float, symbol: str) -> float:
    """Round size DOWN to nearest step"""
    contract_info = get_contract_info(symbol)
    step = contract_info.get("step_size", 0.001)
    
    import math
    floored = math.floor(size / step) * step
    
    if step >= 1:
        return int(floored)
    else:
        decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
        return round(floored, decimals)


def round_price_to_tick(price: float, symbol: str) -> float:
    contract_info = get_contract_info(symbol)
    tick_size = int(contract_info.get("tick_size", "2"))
    return round(price, tick_size)


# ============================================================
# PERSONA 1: WHALE INTELLIGENCE (FIXED - Actually uses data!)
# ============================================================

# Top whales to monitor (from our dataset - highest ETH holders)
TOP_WHALES = [
    {"address": "0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503", "label": "SushiSwap Whale", "balance": 554999},
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "label": "Binance Hot", "balance": 538622},
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "label": "Binance Main", "balance": 159563},
    {"address": "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "label": "Binance 2", "balance": 24971},
    {"address": "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "label": "Binance 3", "balance": 15215},
    {"address": "0x1d5a591eebb5bcb20f440d121e4f62e8d1689997", "label": "DEX Whale", "balance": 20753},
    {"address": "0xee1bf4d7c53af2beafc7dc1dcea222a8c6d87ad9", "label": "DEX Trader", "balance": 40086},
    {"address": "0x73af3bcf944a6559933396c1577b257e2054d935", "label": "Aave Whale", "balance": 303067},
]

class WhalePersona:
    """
    V3.1.45: Enhanced whale intelligence with Cryptoracle integration.
    
    BTC/ETH: Etherscan on-chain whale flow (primary) + Cryptoracle community sentiment (secondary)
    ALL OTHERS: Cryptoracle community sentiment analysis (no more "Skipped")
    
    Cryptoracle provides:
      - CO-A-02-03: Net sentiment direction (positive - negative ratio)
      - CO-S-01-01: Sentiment momentum Z-score (deviation from norm)
      - CO-S-01-05: Sentiment vs price dislocation (mean-reversion signal)
    """
    
    def __init__(self):
        self.name = "WHALE"
        self.weight = 2.0
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
        self._cryptoracle_data = None
        self._cryptoracle_fetched_at = 0
    
    def _get_cryptoracle_data(self) -> dict:
        """Fetch Cryptoracle data for all tokens (cached 10min)."""
        import time as _time
        now = _time.time()
        if self._cryptoracle_data and (now - self._cryptoracle_fetched_at) < 600:
            return self._cryptoracle_data
        
        try:
            from cryptoracle_client import get_all_trading_pair_sentiment, fetch_prediction_market
            import signal as _sig
            def _timeout_handler(signum, frame):
                raise TimeoutError("Cryptoracle API timeout (5s)")
            old_handler = _sig.signal(_sig.SIGALRM, _timeout_handler)
            _sig.alarm(5)  # 5 second hard timeout
            try:
                data = get_all_trading_pair_sentiment()
            finally:
                _sig.alarm(0)  # Cancel alarm
                _sig.signal(_sig.SIGALRM, old_handler)
            if data:
                self._cryptoracle_data = data
                self._cryptoracle_fetched_at = now
                return data
        except ImportError:
            print("  [WHALE] cryptoracle_client not found - using Etherscan fallback")
        except TimeoutError:
            print("  [WHALE] Cryptoracle TIMEOUT (5s) - cloud server down, using Etherscan fallback")
        except Exception as e:
            # V3.1.77b: Log actual error type and details for debugging
            _err_type = type(e).__name__
            _err_detail = str(e)[:200]
            print(f"  [WHALE] Cryptoracle error [{_err_type}]: {_err_detail} - using Etherscan fallback")
        
        return self._cryptoracle_data or {}
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        """Analyze whale/smart money activity for trading signal.

        V3.2.20: BTC/ETH always run Etherscan on-chain flow + Cryptoracle combined.
        Previously Etherscan was only used when Cryptoracle was neutral/down — wasting
        real whale wallet data even when Cryptoracle was perfectly healthy.
        Other pairs: Cryptoracle community sentiment only (no ERC-20 on-chain data).
        """

        # Fetch Cryptoracle (cached 10min, 5s hard timeout)
        cr_data = self._get_cryptoracle_data()
        cr_signal = cr_data.get(pair.upper()) if cr_data else None

        # V3.2.20: BTC/ETH always use Etherscan whale flow + Cryptoracle boost/veto combined
        if pair.upper() in ("BTC", "ETH"):
            return self._analyze_with_etherscan(pair, pair_info, cr_signal)

        # Other pairs: Cryptoracle only (no on-chain fallback available)
        if cr_signal and cr_signal.get("signal") != "NEUTRAL":
            return self._analyze_with_cryptoracle(pair, pair_info, cr_signal)

        if not cr_signal:
            print(f"  [WHALE] No data for {pair} (Cryptoracle down, no Etherscan fallback for altcoins)")
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.30,
                "reasoning": f"Cryptoracle unavailable, no on-chain fallback for {pair}. Deferring to FLOW+SENTIMENT.",
            }

        # Cryptoracle returned but neutral - pass it through
        return self._analyze_with_cryptoracle(pair, pair_info, cr_signal)
    
    def _analyze_with_etherscan(self, pair: str, pair_info: Dict, cr_signal: dict) -> Dict:
        """BTC/ETH: Etherscan whale flow (primary) + Cryptoracle (secondary)."""
        try:
            total_inflow = 0
            total_outflow = 0
            whale_signals = []
            whales_analyzed = 0
            
            for whale in TOP_WHALES[:5]:
                try:
                    flow = self._analyze_whale_flow(whale["address"], whale["label"])
                    if flow:
                        whales_analyzed += 1
                        total_inflow += flow["inflow"]
                        total_outflow += flow["outflow"]
                        
                        if flow["net"] > 100:
                            whale_signals.append(f"{whale['label']}: +{flow['net']:.0f} ETH")
                        elif flow["net"] < -100:
                            whale_signals.append(f"{whale['label']}: {flow['net']:.0f} ETH")
                    
                    time.sleep(0.25)
                except Exception as e:
                    print(f"  [WHALE] Error analyzing {whale['label']}: {e}")
                    continue
            
            if whales_analyzed == 0:
                if cr_signal:
                    return self._cryptoracle_to_vote(pair, cr_signal, "Etherscan unavailable, using Cryptoracle")
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.3,
                    "reasoning": "Could not fetch whale data from Etherscan",
                }
            
            net_flow = total_inflow - total_outflow
            
            if net_flow > 500:
                signal = "LONG"
                confidence = min(0.85, 0.5 + (net_flow / 5000))
                reasoning = f"Whale accumulation: +{net_flow:.0f} ETH net inflow"
            elif net_flow < -500:
                signal = "SHORT"
                confidence = min(0.85, 0.5 + (abs(net_flow) / 5000))
                reasoning = f"Whale distribution: {net_flow:.0f} ETH net outflow"
            elif net_flow > 100:
                signal = "LONG"
                confidence = 0.55
                reasoning = f"Mild whale accumulation: +{net_flow:.0f} ETH"
            elif net_flow < -100:
                signal = "SHORT"
                confidence = 0.55
                reasoning = f"Mild whale distribution: {net_flow:.0f} ETH"
            else:
                signal = "NEUTRAL"
                confidence = 0.4
                reasoning = f"Whale activity balanced: {net_flow:+.0f} ETH"
            
            if whale_signals:
                reasoning += f" | {'; '.join(whale_signals[:3])}"
            
            # V3.1.45: Cryptoracle boost/veto for BTC/ETH
            if cr_signal:
                cr_dir = cr_signal.get("signal", "NEUTRAL")
                cr_conf = cr_signal.get("confidence", 0.4)
                cr_net = cr_signal.get("net_sentiment", 0.5)
                cr_mom = cr_signal.get("sentiment_momentum", 0.0)
                
                if signal == cr_dir and cr_dir != "NEUTRAL":
                    boost = min(0.10, (cr_conf - 0.5) * 0.2)
                    confidence = min(0.85, confidence + boost)
                    reasoning += f" [CR confirms: sent={cr_net:.2f}, mom={cr_mom:.2f}]"
                elif signal != "NEUTRAL" and cr_dir != "NEUTRAL" and signal != cr_dir:
                    reasoning += f" [CR DIVERGES: community={cr_dir} sent={cr_net:.2f}]"
                    confidence = max(0.40, confidence - 0.05)
            
            # V3.1.58: Add prediction market data for BTC
            pm_data = None
            if pair == "BTC":
                try:
                    from cryptoracle_client import fetch_prediction_market
                    pm_data = fetch_prediction_market()
                    if pm_data:
                        pm_val = pm_data["pm_sentiment"]
                        pm_sig = pm_data["pm_signal"]
                        pm_str = pm_data["pm_strength"]
                        reasoning += f" [PM: {pm_sig} {pm_str} ({pm_val:+.3f})]"
                        # PM confirms whale signal = boost confidence
                        if pm_sig == signal and signal != "NEUTRAL" and pm_str == "STRONG":
                            confidence = min(0.90, confidence + 0.08)
                        elif pm_sig == signal and signal != "NEUTRAL":
                            confidence = min(0.85, confidence + 0.04)
                        # PM contradicts = slight reduction
                        elif pm_sig != "NEUTRAL" and signal != "NEUTRAL" and pm_sig != signal:
                            confidence = max(0.40, confidence - 0.05)
                except Exception as e:
                    print(f"  [WHALE] PM fetch error: {e}")
            
            return {
                "persona": self.name,
                "signal": signal,
                "confidence": confidence,
                "reasoning": reasoning,
                "data": {
                    "net_flow": net_flow,
                    "inflow": total_inflow,
                    "outflow": total_outflow,
                    "whales_analyzed": whales_analyzed,
                    "cryptoracle": cr_signal,
                    "prediction_market": pm_data,
                },
            }
            
        except Exception as e:
            if cr_signal:
                return self._cryptoracle_to_vote(pair, cr_signal, f"Etherscan error ({e}), using Cryptoracle")
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"Whale analysis error: {str(e)}",
            }
    
    def _analyze_with_cryptoracle(self, pair: str, pair_info: Dict, cr_signal: dict) -> Dict:
        """Non-BTC/ETH pairs: Cryptoracle community sentiment as primary signal."""
        if not cr_signal:
            print(f"  [WHALE] No data for {pair} (Cryptoracle unavailable)")
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"No whale data for {pair}",
            }
        
        return self._cryptoracle_to_vote(pair, cr_signal, "")
    
    def _cryptoracle_to_vote(self, pair: str, cr: dict, prefix: str) -> Dict:
        """Convert Cryptoracle signal to a whale persona vote."""
        signal = cr.get("signal", "NEUTRAL")
        confidence = cr.get("confidence", 0.40)
        net_sent = cr.get("net_sentiment", 0.5)
        momentum = cr.get("sentiment_momentum", 0.0)
        price_gap = cr.get("sentiment_price_gap", 0.0)
        trend = cr.get("trend_1h", "FLAT")
        
        parts = []
        if prefix:
            parts.append(prefix)
        
        if net_sent > 0.65:
            parts.append(f"Strong bullish community sentiment ({net_sent:.2f})")
        elif net_sent > 0.55:
            parts.append(f"Mild bullish sentiment ({net_sent:.2f})")
        elif net_sent < 0.35:
            parts.append(f"Strong bearish community sentiment ({net_sent:.2f})")
        elif net_sent < 0.45:
            parts.append(f"Mild bearish sentiment ({net_sent:.2f})")
        else:
            parts.append(f"Neutral community sentiment ({net_sent:.2f})")
        
        if abs(momentum) > 1.0:
            direction = "bullish" if momentum > 0 else "bearish"
            parts.append(f"Sentiment momentum {direction} (z={momentum:.2f})")
        
        if abs(price_gap) > 2.0:
            gap_dir = "ahead of" if price_gap > 0 else "behind"
            parts.append(f"Sentiment {gap_dir} price (gap={price_gap:.2f})")
        
        if trend != "FLAT":
            parts.append(f"Trend: {trend}")
        
        reasoning = "; ".join(parts)
        
        return {
            "persona": self.name,
            "signal": signal,
            "confidence": confidence,
            "reasoning": reasoning,
            "data": {
                "source": "cryptoracle",
                "net_sentiment": net_sent,
                "sentiment_momentum": momentum,
                "sentiment_price_gap": price_gap,
                "trend": trend,
                "cryptoracle": cr,
            },
        }

    def _analyze_whale_flow(self, address: str, label: str) -> Optional[Dict]:
        """Fetch recent transactions for a whale and calculate flow"""
        
        # Check cache
        cache_key = f"{address}_{int(time.time() // self.cache_ttl)}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        try:
            # Fetch recent transactions from Etherscan
            params = {
                "chainid": CHAIN_ID,
                "module": "account",
                "action": "txlist",
                "address": address,
                "page": 1,
                "offset": 50,  # Last 50 transactions
                "sort": "desc",
                "apikey": ETHERSCAN_API_KEY,
            }
            
            r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
            data = r.json()
            
            if data.get("status") != "1" or not data.get("result"):
                return None
            
            # Calculate flow in last 24 hours
            cutoff = time.time() - (24 * 3600)
            inflow = 0
            outflow = 0
            
            for tx in data["result"]:
                tx_time = int(tx.get("timeStamp", 0))
                if tx_time < cutoff:
                    continue
                
                value_eth = float(tx.get("value", 0)) / 1e18
                
                if value_eth < 1:  # Ignore small transactions
                    continue
                
                if tx.get("to", "").lower() == address.lower():
                    inflow += value_eth
                elif tx.get("from", "").lower() == address.lower():
                    outflow += value_eth
            
            result = {
                "address": address,
                "label": label,
                "inflow": inflow,
                "outflow": outflow,
                "net": inflow - outflow,
            }
            
            # Cache result
            self.cache[cache_key] = result
            return result
            
        except Exception as e:
            print(f"  [WHALE] Etherscan error for {label}: {e}")
            return None



# ============================================================
# PERSONA 2: MARKET SENTIMENT (Gemini) - V3.1.21 RATE LIMIT FIX
# ============================================================

class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment.
    
    V3.1.21: RATE LIMIT FIX
    - Exponential backoff on 429 errors
    - Rate limiting between API calls  
    - Combined grounding + analysis into single call (50% fewer API calls!)
    - 5-minute cache per pair
    """
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5
        self._cache = {}  # {pair: (timestamp, result)}
        self._cache_ttl = 300  # 5 minutes
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        import time
        
        # Check cache first
        cached = self._get_cached(pair)
        if cached:
            print(f"  [SENTIMENT] Using cached result for {pair}")
            return cached
        
        # Rate limit before making call
        _rate_limit_gemini()
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = self._analyze_with_retry(pair, pair_info, competition_status)
                self._set_cache(pair, result)
                return result
                
            except Exception as e:
                error_str = str(e).lower()
                
                if "429" in error_str or "resource exhausted" in error_str or "quota" in error_str:
                    if attempt < max_retries - 1:
                        backoff = _exponential_backoff(attempt)
                        print(f"  [SENTIMENT] 429 error, retrying in {backoff:.1f}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(backoff)
                        continue
                    else:
                        print(f"  [SENTIMENT] Rate limit exceeded after {max_retries} retries")
                        return self._fallback_result(pair, f"Rate limited after {max_retries} retries")
                else:
                    print(f"  [SENTIMENT] Error: {e}")
                    return self._fallback_result(pair, str(e))
        
        return self._fallback_result(pair, "Max retries exceeded")
    
    def _get_cached(self, pair: str):
        """Get cached result if still valid"""
        import time
        if pair in self._cache:
            timestamp, result = self._cache[pair]
            if time.time() - timestamp < self._cache_ttl:
                return result
            else:
                del self._cache[pair]
        return None
    
    def _set_cache(self, pair: str, result: dict):
        """Cache result with timestamp"""
        import time
        self._cache[pair] = (time.time(), result)
    
    def _analyze_with_retry(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        """V3.1.75: Robust Gemini sentiment with 3-retry + non-grounding fallback"""
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        import time as _time

        combined_prompt = f"""Search for "{pair} cryptocurrency price action last 24 hours" and analyze:

You are a SHORT-TERM crypto trader making a 1-4 hour trade decision for {pair}.

IGNORE: Long-term "moon" predictions, "institutional adoption", "ETF hopes", price targets for next year.
FOCUS ON: Last 1-4 hours price action, support/resistance breaks, volume on red vs green candles, liquidation data.

Based ONLY on short-term price action and momentum:
- If price is breaking DOWN through support or volume is spiking on RED candles = BEARISH
- If price is breaking UP through resistance or volume is spiking on GREEN candles = BULLISH
- If choppy/sideways with no clear direction = NEUTRAL

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "short-term reason only", "market_context": "brief summary of what you found"}}"""

        grounding_config = GenerateContentConfig(
            tools=[Tool(google_search=GoogleSearch())],
            temperature=0.2,
        )

        # V3.1.75: 3 retries with exponential backoff on empty responses
        max_empty_retries = 3
        for attempt in range(max_empty_retries):
            try:
                response = _gemini_full_call("gemini-2.5-flash", combined_prompt, grounding_config, timeout=90)
                if response and hasattr(response, 'text') and response.text:
                    clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
                    if clean_text:
                        data = json.loads(clean_text)
                        signal = "LONG" if data["sentiment"] == "BULLISH" else "SHORT" if data["sentiment"] == "BEARISH" else "NEUTRAL"
                        return {
                            "persona": self.name,
                            "signal": signal,
                            "confidence": data.get("confidence", 0.5),
                            "reasoning": data.get("key_factor", "Market sentiment analysis"),
                            "sentiment": data["sentiment"],
                            "market_context": data.get("market_context", "")[:800],
                        }
            except json.JSONDecodeError as je:
                print(f"  [SENTIMENT] JSON parse error for {pair}: {je}")
            except Exception as e:
                print(f"  [SENTIMENT] Grounding attempt {attempt+1} error for {pair}: {e}")

            if attempt < max_empty_retries - 1:
                backoff = 5 * (attempt + 1)  # 5s, 10s
                print(f"  [SENTIMENT] Empty/error from Gemini for {pair}, retry {attempt+1}/{max_empty_retries} in {backoff}s...")
                _time.sleep(backoff)

        # V3.1.75: FALLBACK - try WITHOUT grounding (no Google Search)
        # Grounding is the #1 cause of empty responses (search fails silently)
        print(f"  [SENTIMENT] Grounding failed {max_empty_retries}x for {pair}, trying WITHOUT grounding...")
        try:
            fallback_prompt = f"""You are a crypto trading analyst. Based on your knowledge of {pair} cryptocurrency:

Analyze the LIKELY current short-term (1-4 hour) price action for {pair}.
Consider: recent trend direction, typical volatility, market cycle position.

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "short-term reason only", "market_context": "analysis based on recent trends"}}"""

            no_ground_config = GenerateContentConfig(temperature=0.3)
            response = _gemini_full_call("gemini-2.5-flash", fallback_prompt, no_ground_config, timeout=60)

            if response and hasattr(response, 'text') and response.text:
                clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
                if clean_text:
                    data = json.loads(clean_text)
                    signal = "LONG" if data["sentiment"] == "BULLISH" else "SHORT" if data["sentiment"] == "BEARISH" else "NEUTRAL"
                    conf = min(data.get("confidence", 0.4), 0.6)  # Cap at 60% without grounding
                    print(f"  [SENTIMENT] Non-grounding fallback succeeded for {pair}: {signal} ({conf:.0%})")
                    return {
                        "persona": self.name,
                        "signal": signal,
                        "confidence": conf,
                        "reasoning": f"(no-grounding fallback) {data.get('key_factor', 'Model analysis')}",
                        "sentiment": data["sentiment"],
                        "market_context": data.get("market_context", "")[:800],
                    }
        except Exception as e:
            print(f"  [SENTIMENT] Non-grounding fallback also failed for {pair}: {e}")

        print(f"  [SENTIMENT] All attempts failed for {pair}, returning NEUTRAL")
        return self._fallback_result(pair, "All Gemini attempts failed (grounding + fallback)")
    
    def _fallback_result(self, pair: str, error_msg: str) -> Dict:
        """Return neutral result on error"""
        return {
            "persona": self.name,
            "signal": "NEUTRAL",
            "confidence": 0.3,
            "reasoning": f"Sentiment analysis error: {error_msg}",
        }

# ============================================================
# PERSONA 3: ORDER FLOW
# ============================================================

class FlowPersona:
    """Analyzes order flow (taker ratio, depth).
    
    V3.1.17: CRITICAL FIX - Taker volume (ACTION) beats depth (INTENTION)
    In bear markets, big bids are often spoofing/exit liquidity.
    When taker ratio < 0.5, IGNORE bid depth completely.
    
    V3.1.18: REGIME-AWARE TAKER CAP
    In BEARISH regime, high taker buying is likely SHORT COVERING, not reversal.
    Cap the LONG signal from extreme buying when regime is bearish.
    """
    
    def __init__(self):
        self.name = "FLOW"
        self.weight = 1.0
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        symbol = pair_info["symbol"]

        try:
            # V3.2.23: Fetch regime first so [REGIME] prints before [FLOW] data lines (not mid-block)
            regime = get_enhanced_market_regime()
            is_bearish = regime.get("regime") == "BEARISH" or regime.get("btc_24h", 0) < -0.3

            depth = self._get_order_book_depth(symbol)
            taker_ratio = self._get_taker_ratio(symbol)
            funding = self._get_funding_rate(symbol)
            
            signals = []
            
            # V3.1.17: TAKER VOLUME IS KING
            # Extreme taker selling (< 0.3) = IGNORE ALL BIDS, they are fake/exit liquidity
            # Heavy taker selling (< 0.5) = Taker signal 3x weight of depth
            
            extreme_selling = taker_ratio < 0.3
            heavy_selling = taker_ratio < 0.5
            extreme_buying = taker_ratio > 3.0
            heavy_buying = taker_ratio > 2.0
            
            # Log for debugging
            print(f"  [FLOW] Taker ratio: {taker_ratio:.2f} | Extreme sell: {extreme_selling} | Heavy sell: {heavy_selling}")
            
            # V3.1.96: REMOVED F&G cap on FLOW signals. The ensemble already handles
            # extreme F&G via REGIME contrarian bias + Judge context. Capping FLOW
            # before the Judge sees it weakens legitimate signals (e.g. ETH 85%->55%).

            if extreme_selling:
                # V3.1.17: MASSIVE SELL PRESSURE - ignore depth entirely
                signals.append(("SHORT", 0.85, f"EXTREME taker selling: {taker_ratio:.2f}"))
                # Don't even add depth signal - it's fake/spoofing
            elif heavy_selling:
                # V3.1.17: Heavy selling - taker wins over depth
                signals.append(("SHORT", 0.70, f"Heavy taker selling: {taker_ratio:.2f}"))
                # Depth signal at reduced weight
                if depth["ask_strength"] > 1.3:
                    signals.append(("SHORT", 0.3, "Ask depth confirms"))
                # IGNORE bid depth when heavy selling
            elif extreme_buying:
                signals.append(("LONG", 0.85, f"EXTREME taker buying: {taker_ratio:.2f}"))
            elif heavy_buying:
                signals.append(("LONG", 0.70, f"Heavy taker buying: {taker_ratio:.2f}"))
                if depth["bid_strength"] > 1.3:
                    signals.append(("LONG", 0.3, "Bid depth confirms"))
            else:
                # Normal range - use both taker and depth
                if taker_ratio > 1.2:
                    signals.append(("LONG", 0.5, f"Taker buy pressure: {taker_ratio:.2f}"))
                elif taker_ratio < 0.8:
                    signals.append(("SHORT", 0.5, f"Taker sell pressure: {taker_ratio:.2f}"))
                
                # V3.1.89: Graduated depth signals — extreme imbalance (3x+) gets stronger score
                if depth["bid_strength"] > 3.0:
                    signals.append(("LONG", 0.6, f"EXTREME bid depth: {depth['bid_strength']:.2f}x"))
                elif depth["bid_strength"] > 1.3:
                    signals.append(("LONG", 0.4, "Strong bid depth"))

                if depth["ask_strength"] > 3.0:
                    signals.append(("SHORT", 0.6, f"EXTREME ask depth: {depth['ask_strength']:.2f}x"))
                elif depth["ask_strength"] > 1.3:
                    signals.append(("SHORT", 0.4, "Strong ask depth"))
            
            # Funding rate (always include)
            if funding > 0.0005:
                signals.append(("SHORT", 0.3, f"High funding: {funding:.4f}"))
            elif funding < -0.0003:
                signals.append(("LONG", 0.3, f"Negative funding: {funding:.4f}"))
            
            if not signals:
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.3,
                    "reasoning": "No clear flow signal",
                }
            
            long_score = sum(s[1] for s in signals if s[0] == "LONG")
            short_score = sum(s[1] for s in signals if s[0] == "SHORT")
            neutral_score = sum(s[1] for s in signals if s[0] == "NEUTRAL")
            
            # V3.1.18: If neutral score is high (from capped buying), return neutral
            if neutral_score > long_score and neutral_score > short_score:
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.50,
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "NEUTRAL"),
                }
            
            if long_score > short_score and long_score >= 0.4:
                return {
                    "persona": self.name,
                    "signal": "LONG",
                    "confidence": min(0.85, long_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "LONG"),
                    "data": {
                        "nearest_ask_wall": depth.get("nearest_ask_wall"),
                        "nearest_bid_wall": depth.get("nearest_bid_wall"),
                    },
                }
            elif short_score > long_score and short_score >= 0.4:
                return {
                    "persona": self.name,
                    "signal": "SHORT",
                    "confidence": min(0.85, short_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "SHORT"),
                    "data": {
                        "nearest_ask_wall": depth.get("nearest_ask_wall"),
                        "nearest_bid_wall": depth.get("nearest_bid_wall"),
                    },
                }

            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.4,
                "reasoning": "Mixed flow signals",
            }
            
        except Exception as e:
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"Flow analysis error: {str(e)}",
            }
    
    def _get_order_book_depth(self, symbol: str) -> Dict:
        try:
            # V3.2.20: limit=200 for wall detection (15 levels too tight on liquid pairs)
            url = f"{WEEX_BASE_URL}/capi/v2/market/depth?symbol={symbol}&limit=200"
            r = requests.get(url, timeout=10)
            data = r.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            # WEEX format: [[price, quantity], ...]
            bid_volume = sum(float(b[1]) for b in bids[:10]) if bids else 0
            ask_volume = sum(float(a[1]) for a in asks[:10]) if asks else 0

            ratio = bid_volume / ask_volume if ask_volume > 0 else 1.0

            print(f"  [FLOW] Depth - Bids: {bid_volume:.2f}, Asks: {ask_volume:.2f}, Ratio: {ratio:.2f}")

            # V3.2.20: Find nearest significant order book wall for TP targeting.
            # asks are sorted low→high (nearest resistance first); bids high→low (nearest support first).
            # "Significant" = volume ≥ 1.5x average level volume. Fallback: highest-vol level in top 10.
            nearest_ask_wall = None  # Resistance → LONG TP anchor
            nearest_bid_wall = None  # Support    → SHORT TP anchor

            if asks and len(asks) >= 3:
                ask_levels = [(float(a[0]), float(a[1])) for a in asks[:15] if float(a[0]) > 0 and float(a[1]) > 0]
                if ask_levels:
                    avg_vol = sum(v for _, v in ask_levels) / len(ask_levels)
                    for price, vol in sorted(ask_levels, key=lambda x: x[0]):  # nearest first
                        if vol >= avg_vol * 1.5:
                            nearest_ask_wall = price
                            break
                    if nearest_ask_wall is None:
                        nearest_ask_wall = max(ask_levels[:10], key=lambda x: x[1])[0]

            if bids and len(bids) >= 3:
                bid_levels = [(float(b[0]), float(b[1])) for b in bids[:15] if float(b[0]) > 0 and float(b[1]) > 0]
                if bid_levels:
                    avg_vol = sum(v for _, v in bid_levels) / len(bid_levels)
                    for price, vol in sorted(bid_levels, key=lambda x: x[0], reverse=True):  # nearest first
                        if vol >= avg_vol * 1.5:
                            nearest_bid_wall = price
                            break
                    if nearest_bid_wall is None:
                        nearest_bid_wall = max(bid_levels[:10], key=lambda x: x[1])[0]

            _ask_str = f"${nearest_ask_wall:.6g}" if nearest_ask_wall else "none"
            _bid_str = f"${nearest_bid_wall:.6g}" if nearest_bid_wall else "none"
            print(f"  [FLOW] Walls - Ask: {_ask_str} (resistance/LONG-TP), Bid: {_bid_str} (support/SHORT-TP)")

            return {
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
                "bid_strength": ratio,
                "ask_strength": 1/ratio if ratio > 0 else 1.0,
                "nearest_ask_wall": nearest_ask_wall,
                "nearest_bid_wall": nearest_bid_wall,
            }
        except Exception as e:
            print(f"  [FLOW] Depth error: {e}")
            return {"bid_strength": 1.0, "ask_strength": 1.0, "nearest_ask_wall": None, "nearest_bid_wall": None}
    
    def _get_taker_ratio(self, symbol: str) -> float:
        try:
            url = f"{WEEX_BASE_URL}/capi/v2/market/trades?symbol={symbol}&limit=100"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            if isinstance(data, list) and len(data) > 0:
                # WEEX uses isBuyerMaker: true = seller was taker, false = buyer was taker
                buyer_taker_vol = sum(float(t.get("size", 0)) for t in data if not t.get("isBuyerMaker", True))
                seller_taker_vol = sum(float(t.get("size", 0)) for t in data if t.get("isBuyerMaker", False))
                
                total = buyer_taker_vol + seller_taker_vol
                if total > 0:
                    ratio = buyer_taker_vol / seller_taker_vol if seller_taker_vol > 0 else 2.0
                    print(f"  [FLOW] Taker - Buy: {buyer_taker_vol:.4f}, Sell: {seller_taker_vol:.4f}, Ratio: {ratio:.2f}")
                    return ratio
        except Exception as e:
            print(f"  [FLOW] Taker error: {e}")
        return 1.0
    
    def _get_funding_rate(self, symbol: str) -> float:
        try:
            url = f"{WEEX_BASE_URL}/capi/v2/market/currentFundRate?symbol={symbol}"
            r = requests.get(url, timeout=10)
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                rate = float(data[0].get("fundingRate", 0))
                print(f"  [FLOW] Funding rate: {rate:.6f}")
                return rate
        except Exception as e:
            print(f"  [FLOW] Funding error: {e}")
        return 0.0


# ============================================================
# PERSONA 4: TECHNICAL ANALYSIS
# ============================================================

class TechnicalPersona:
    """Simple technical indicators (RSI, SMA, momentum)."""
    
    def __init__(self):
        self.name = "TECHNICAL"
        self.weight = 1.2  # V3.1.7: Increased from 0.8
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        symbol = pair_info["symbol"]
        
        try:
            candles = self._get_candles(symbol, "1H", 50)
            
            if len(candles) < 20:
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "reasoning": "Insufficient data",
                }
            
            closes = [c["close"] for c in candles]
            
            rsi = self._calculate_rsi(closes, 14)
            sma_20 = np.mean(closes[-20:])
            sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else sma_20
            current_price = closes[-1]
            
            momentum = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
            
            signals = []
            
            if rsi < 30:
                signals.append(("LONG", 0.7, f"RSI oversold: {rsi:.1f}"))
            elif rsi > 70:
                signals.append(("SHORT", 0.7, f"RSI overbought: {rsi:.1f}"))
            
            if current_price > sma_20 > sma_50:
                signals.append(("LONG", 0.5, "Price above SMAs (uptrend)"))
            elif current_price < sma_20 < sma_50:
                signals.append(("SHORT", 0.5, "Price below SMAs (downtrend)"))
            
            if momentum > 2:
                signals.append(("LONG", 0.4, f"Strong momentum: +{momentum:.1f}%"))
            elif momentum < -2:
                signals.append(("SHORT", 0.4, f"Weak momentum: {momentum:.1f}%"))
            
            if not signals:
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.4,
                    "reasoning": f"No clear technical signal. RSI: {rsi:.1f}",
                }
            
            long_score = sum(s[1] for s in signals if s[0] == "LONG")
            short_score = sum(s[1] for s in signals if s[0] == "SHORT")
            
            if long_score > short_score:
                return {
                    "persona": self.name,
                    "signal": "LONG",
                    "confidence": min(0.8, long_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "LONG"),
                }
            elif short_score > long_score:
                return {
                    "persona": self.name,
                    "signal": "SHORT",
                    "confidence": min(0.8, short_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "SHORT"),
                }
            
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.4,
                "reasoning": "Mixed technical signals",
            }
            
        except Exception as e:
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"Technical analysis error: {str(e)}",
            }
    
    def _get_candles(self, symbol: str, interval: str, limit: int) -> List[Dict]:
        try:
            # WEEX uses 'granularity' not 'period', and format like '1h' not '1H'
            granularity = interval.lower()  # '1H' -> '1h'
            url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol={symbol}&granularity={granularity}&limit={limit}"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            if isinstance(data, list) and len(data) > 0:
                # WEEX candle format: [timestamp, open, high, low, close, volume, value]
                # Index: 0=time, 1=open, 2=high, 3=low, 4=close, 5=volume, 6=value
                candles = []
                for c in data:
                    if len(c) >= 5:
                        candles.append({
                            "close": float(c[4]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "open": float(c[1]),
                            "volume": float(c[5]) if len(c) > 5 else 0
                        })
                return candles
        except Exception as e:
            print(f"  [TECHNICAL] Candle fetch error: {e}")
        return []
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))


# ============================================================
# JUDGE: FINAL DECISION MAKER (V3.1.4 - Market Trend Filter)
# ============================================================

class JudgePersona:
    """Weighs all persona votes and makes final decision with tier-based TP/SL.
    
    V3.1.4: Added MARKET TREND FILTER - don't go against BTC trend!
    """
    
    def __init__(self):
        self.name = "JUDGE"
        self._btc_trend_cache = {"trend": None, "timestamp": 0}
    
    def _get_market_regime(self) -> dict:
        """Check BTC 24h trend for market regime detection.
        
        V3.1.7: Uses 24h trend (not just 4h) for better regime detection.
        Returns dict with regime, bias, and change percentages.
        """
        import time as time_module
        
        # Cache for 5 minutes (was 15 - too slow for volatile markets)
        cache_valid = (
            time_module.time() - self._btc_trend_cache.get("timestamp", 0) < 300
            and "regime" in self._btc_trend_cache
        )
        if cache_valid:
            return self._btc_trend_cache
        
        result = {
            "regime": "NEUTRAL",
            "bias": "NONE",
            "change_4h": 0,
            "change_24h": 0,
            "timestamp": time_module.time()
        }
        
        try:
            # Get BTC 4h candles (7 candles = 28h of data)
            url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            if isinstance(data, list) and len(data) >= 7:
                closes = [float(c[4]) for c in data]
                
                # 4h change
                change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
                
                # 24h change (6 candles ago)
                change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
                
                result["change_4h"] = change_4h
                result["change_24h"] = change_24h
                
                # V3.1.8: STRICTER regime thresholds
                # Determine regime based on 24h trend (primary) and 4h (secondary)
                if change_24h < -1.0:  # Was -2.0, now -1.0
                    result["regime"] = "BEARISH"
                    result["bias"] = "SHORT"
                elif change_24h > 1.5:  # Was 2.0, now 1.5
                    result["regime"] = "BULLISH"
                    result["bias"] = "LONG"
                elif change_4h < -1.0:  # Was -1.5, now -1.0
                    result["regime"] = "BEARISH"
                    result["bias"] = "SHORT"
                elif change_4h > 1.0:  # Was 1.5, now 1.0
                    result["regime"] = "BULLISH"
                    result["bias"] = "LONG"
                
                print(f"  [JUDGE] Market: {result['regime']} | 24h: {change_24h:+.1f}% | 4h: {change_4h:+.1f}%")
                
        except Exception as e:
            print(f"  [JUDGE] Market regime error: {e}")
        
        self._btc_trend_cache = result
        return result
    
    def decide(self, persona_votes: List[Dict], pair: str, balance: float, 
               competition_status: Dict) -> Dict:
        """V3.1.39: Gemini-Powered Judge - LLM makes contextual trading decisions.
        
        Replaces weighted-sum approach with Gemini that sees:
        - All persona votes + full reasoning
        - Market regime, F&G, funding rates
        - Active positions and PnL
        - Competition status
        
        Returns single best decision (LONG, SHORT, or WAIT).
        """
        import time as time_module
        
        tier_config = get_tier_config_for_pair(pair)
        tier = get_tier_for_pair(pair)
        regime = get_enhanced_market_regime()
        
        # Build persona summary with FULL reasoning
        persona_summary = []
        for vote in persona_votes:
            persona_summary.append(
                f"- {vote['persona']}: {vote['signal']} ({vote['confidence']:.0%}) - {vote.get('reasoning', 'N/A')[:200]}"
            )
        personas_text = "\n".join(persona_summary)
        
        # Get active positions for this symbol
        symbol = TRADING_PAIRS.get(pair, {}).get("symbol", f"cmt_{pair.lower()}usdt")
        try:
            positions = get_open_positions()
            pair_positions = [p for p in positions if p.get("symbol") == symbol]
            all_positions = [
                f"  {p.get('symbol','?').replace('cmt_','').upper()}: {p.get('side')} entry=${float(p.get('entry_price',0)):.2f} PnL=${float(p.get('unrealized_pnl',0)):.2f} size={p.get('size')}"
                for p in positions
            ]
        except:
            pair_positions = []
            all_positions = []
        
        has_long = any(p.get("side","").upper() == "LONG" for p in pair_positions)
        has_short = any(p.get("side","").upper() == "SHORT" for p in pair_positions)
        
        positions_text = "\n".join(all_positions) if all_positions else "  None"
        
        pair_pos_text = "None"
        if pair_positions:
            parts = []
            for p in pair_positions:
                parts.append(f"{p.get('side')} entry=${float(p.get('entry_price',0)):.4f} PnL=${float(p.get('unrealized_pnl',0)):.2f}")
            pair_pos_text = "; ".join(parts)
        
        # Competition context
        days_left = competition_status.get("days_left", 15)
        pnl = competition_status.get("pnl", 0)
        pnl_pct = competition_status.get("pnl_pct", 0)
        
        # Build the prompt
        # V3.1.63: Build trade history summary for Judge
        try:
            from smt_daemon_v3_1 import get_trade_history_summary
            _hist_tracker = TradeTracker(state_file="trade_state_v3_1_7.json")
            trade_history_summary = get_trade_history_summary(_hist_tracker)
        except Exception:
            trade_history_summary = "Trade history unavailable."

        # V3.1.77: RL-based pair performance for Judge
        try:
            from smt_daemon_v3_1 import get_rl_performance_summary
            rl_performance = get_rl_performance_summary()
        except Exception:
            rl_performance = "Historical pair performance unavailable."

        # V3.2.17: Signal cycle history for Gemini Judge — gives memory across cycles
        signal_history_text = ""
        try:
            _sh = _hist_tracker.signal_history
            if _sh:
                _sh_lines = []
                for _sh_pair, _sh_data in _sh.items():
                    _sh_dir = _sh_data.get("direction", "?")
                    _sh_conf = _sh_data.get("confidence", 0)
                    _sh_count = _sh_data.get("count", 0)
                    _sh_first = _sh_data.get("entry_time", "")
                    _sh_last = _sh_data.get("last_seen", "")
                    # Calculate age in minutes since first seen
                    _sh_age_min = 0
                    if _sh_first:
                        try:
                            _sh_first_dt = datetime.fromisoformat(_sh_first.replace("Z", "+00:00"))
                            _sh_age_min = int((datetime.now(timezone.utc) - _sh_first_dt).total_seconds() / 60)
                        except Exception:
                            pass
                    _is_current = " ← THIS PAIR" if _sh_pair == pair else ""
                    _sh_lines.append(
                        f"  {_sh_pair}: {_sh_dir} {_sh_conf:.0%} × {_sh_count} cycles ({_sh_age_min}min ago){_is_current}"
                    )
                signal_history_text = "\n".join(_sh_lines)
        except Exception as _sh_err:
            print(f"  [JUDGE] Signal history error: {_sh_err}")

        # V3.2.20: Extract FLOW order book wall data for Judge context
        flow_wall_text = ""
        _flow_v = next((v for v in persona_votes if v.get("persona") == "FLOW"), None)
        if _flow_v and _flow_v.get("data"):
            _fw = _flow_v["data"]
            _aw = _fw.get("nearest_ask_wall")
            _bw = _fw.get("nearest_bid_wall")
            if _aw or _bw:
                parts = []
                if _aw:
                    parts.append(f"Nearest significant ASK wall (resistance): ${_aw:.6g}")
                if _bw:
                    parts.append(f"Nearest significant BID wall (support): ${_bw:.6g}")
                flow_wall_text = "\n".join(parts)

        # V3.2.16: Multi-TF chart context for Gemini Judge — 1D + 4H structural levels
        chart_context_text = ""
        try:
            chart_ctx = get_chart_context(symbol)
            if chart_ctx and "unavailable" not in chart_ctx and "insufficient" not in chart_ctx:
                chart_context_text = chart_ctx
        except Exception as _ctx_err:
            print(f"  [JUDGE] Chart context error: {_ctx_err}")

        # V3.2.17: Live chop/ranging detection for Gemini Judge — ADX, BB width, directional consistency
        chop_context_text = ""
        try:
            _chop = detect_sideways_market(symbol)
            _chop_adx = _chop.get("adx", 25)
            _chop_bb = _chop.get("bb_width_pct", 3.0)
            _chop_dir = _chop.get("directional_consistency", 0.7)
            _chop_disp = _chop.get("net_displacement", 0)
            _chop_sev = _chop.get("severity", "low")
            _chop_reason = _chop.get("reason", "OK")
            _chop_recent = _chop.get("recent_consistency")

            chop_lines = [
                f"  ADX: {_chop_adx:.0f} (>25=trending, <18=no trend, <12=dead flat)",
                f"  BB Width: {_chop_bb:.1f}% (wider=volatile/trending, tighter=ranging)",
                f"  Directional Consistency: {_chop_dir:.0%} (>60%=trending, <35%=flip-flopping)",
                f"  Net Displacement: {_chop_disp:.1f}% (actual price move over lookback window)",
                f"  Verdict: {_chop_sev.upper()} — {_chop_reason}",
            ]
            if _chop_recent is not None:
                chop_lines.append(f"  Recent Consistency: {_chop_recent:.0%} (last ~1h of candles — if higher than full, trend is resolving)")
            chop_context_text = "\n".join(chop_lines)
        except Exception as _chop_err:
            print(f"  [JUDGE] Chop context error: {_chop_err}")

        prompt = f"""You are the AI Judge for a crypto futures trading bot. Real money. Be disciplined.
Your job: analyze all signals and decide the SINGLE BEST action for {pair} right now.
STRATEGY: High-frequency dip/bounce trades. TP targets are 0.3-0.5% (6-10% ROE at 20x). Volume of good trades beats waiting for perfect ones.

=== MARKET REGIME ===
Regime: {regime.get('regime', 'NEUTRAL')}
BTC 24h change: {regime.get('change_24h', 0):+.1f}%
BTC 4h change: {regime.get('change_4h', 0):+.1f}%
Fear & Greed Index: {regime.get('fear_greed', 50)}
Funding rate (BTC): {regime.get('btc_funding', 0):.6f}

=== PERSONA VOTES FOR {pair} (Tier {tier}: {tier_config['name']}) ===
{personas_text}

=== CHART STRUCTURE (1D + 4H) ===
{chart_context_text if chart_context_text else "Chart data unavailable — use persona votes only."}

=== SIGNAL CYCLE HISTORY (V3.2.17) ===
{signal_history_text if signal_history_text else "No recent signal history (first cycle or all signals expired)."}
NOTE: Each entry shows the pair, direction, confidence, how many consecutive 10-min cycles it persisted, and when it first appeared.
- 2+ cycles same direction = signal is REAL and confirmed. Commit harder (boost confidence).
- 1 cycle only = fresh signal, could be noise. Normal confidence.
- If THIS PAIR just flipped direction from last cycle (was SHORT now LONG or vice versa) = HIGH NOISE RISK. Favor WAIT unless WHALE+FLOW are both very strong (>75%).
- If THIS PAIR has no history = first time seeing a signal for it. Treat normally.
- Other pairs' history gives you cross-market context (is everything flipping? broad trend shift?).

=== MICROSTRUCTURE / CHOP DETECTION (5m candles) ===
{chop_context_text if chop_context_text else "Chop data unavailable."}
USE THIS AS CONTEXT — not a hard veto. This tells you if the pair is currently ranging or trending at the micro level.
- HIGH chop + weak signals = strong WAIT. Market is going nowhere.
- MEDIUM chop + strong WHALE+FLOW = proceed cautiously. The dip-bounce strategy works in ranges IF the signal is strong.
- LOW chop (trending) = normal trading. Trust your signals.
- If ADX is rising and recent consistency is higher than full consistency, the market is BREAKING OUT of a range — good entry opportunity.

=== FLOW ORDER BOOK WALLS (live depth, 200 levels) ===
{flow_wall_text if flow_wall_text else "No significant walls detected in current order book."}
These are the nearest price levels where large resting orders cluster (>=1.5x average level volume).
- ASK wall = resistance above current price (where sellers are waiting). Relevant for LONG tp_price.
- BID wall = support below current price (where buyers are waiting). Relevant for SHORT tp_price.
NOTE: Order book walls are ephemeral and can be pulled. Use them as ONE input for tp_price alongside chart structure, not as the sole basis.

=== CURRENT POSITIONS ON {pair} ===
{pair_pos_text}

=== ALL OPEN POSITIONS ===
{positions_text}

=== COMPETITION STATUS ===
Days remaining: {days_left}
PnL: ${pnl:.0f} ({pnl_pct:+.1f}%)
Available balance: ${balance:.0f}

=== DECISION GUIDELINES (V3.2.17 CHART+MEMORY) ===

YOUR ONLY JOB: Decide LONG, SHORT, or WAIT based on signal quality. Position limits, TP/SL, and slot management are handled by code -- ignore them entirely.

CRITICAL: Your confidence score MUST reflect actual signal quality, NOT rules or bias.
- F&G rules below force your DIRECTION (LONG or SHORT), but confidence must honestly reflect how strong the setup is.
- If all signals weakly agree: 60-70% confidence. If signals are mixed but rules force direction: 50-65%.
- Only give 85%+ when WHALE + FLOW strongly agree AND align with direction.

SIGNAL RELIABILITY:
  CO-PRIMARY (equal weight, these drive your decision):
    1. WHALE (Cryptoracle community intelligence) -- smart money / crowd wisdom. Our unique edge.
    2. FLOW (order book taker ratio) -- actual money moving right now.
  SECONDARY (confirmation only, never override WHALE+FLOW):
    3. SENTIMENT (web search price action) -- context, not a trading signal.
    4. TECHNICAL (RSI/SMA/momentum) -- lagging indicator, confirmation only. In fear markets (F&G < 30), SMA signals are especially lagged (price already below SMAs); discount TECHNICAL when it conflicts with FLOW+WHALE.

HOW TO DECIDE:
- If WHALE + FLOW agree: TRADE IT at 85%+ confidence. Strongest possible signal.
- If WHALE is strong (>65% conf) but FLOW is weak/neutral: trust WHALE direction.
- If FLOW is strong (>75% conf) but WHALE is weak/neutral: trust FLOW. Money is moving.
- If WHALE and FLOW directly contradict (opposite directions, both >60%): WAIT.
- If neither co-primary signal exceeds 60%: WAIT. No clear edge.

TP TARGET (V3.2.20 — USE CHART STRUCTURE + FLOW WALLS):
Look at CHART STRUCTURE and FLOW ORDER BOOK WALLS above. If you decide LONG or SHORT:
1. Chart structure (4H/Daily levels) = durable historical S/R. Primary reference.
2. FLOW walls (live order book) = where large orders sit RIGHT NOW. Secondary reference.
3. If both agree on a level, high confidence in tp_price. If they conflict, prefer chart structure (more durable).
Return "tp_price" = the actual price level where you expect the move to stall. REALISTIC near-term target, not the 5D high.
If neither chart nor walls provide a clear target, omit tp_price and code will use its 0.5% fallback.

=== HISTORICAL PAIR PERFORMANCE (from RL training data) ===
{rl_performance}
USE THIS AS CONTEXT ONLY — not a veto. Poor historical win rate = note it in reasoning, then follow WHALE+FLOW if they clearly agree.
If a pair has >15% win rate, it has proven itself — trust stronger signals on it.

FEAR & GREED (SOFT BIAS - V3.1.88):
- F&G < 15 (EXTREME FEAR): Slightly favor LONG (contrarian bounce possible), but allow SHORT if WHALE+FLOW+SENTIMENT confirm bearish. Sustained fear ≠ imminent bounce. Trust the signals.
- F&G < 30 (FEAR): Mild LONG bias. SHORT is fine if signals confirm.
- F&G > 85 (EXTREME GREED): Slightly favor SHORT (contrarian top possible), but allow LONG if WHALE+FLOW+SENTIMENT confirm bullish.
- F&G > 70 (GREED): Mild SHORT bias. LONG is fine if signals confirm.
- F&G 30-70 (NEUTRAL ZONE): Use WHALE+FLOW signals normally, no directional bias.
IMPORTANT: F&G is ONE input, not a hard rule. If 3+ personas say SHORT in extreme fear, trust them - the market IS bearish.

TRADE HISTORY CONTEXT:
{trade_history_summary}

IMPORTANT: Say LONG or SHORT if WHALE + FLOW support it. WAIT is valid when signals are weak or contradictory.
Frequency of good trades compounds into competition wins — don't over-filter.
SHORT ASYMMETRY: In EXTREME FEAR (F&G < 15), require 2+ co-primary signals confirming SHORT before taking it (bounce risk is real). Otherwise treat LONG and SHORT equally — both directions are valid entries.

Respond with JSON ONLY (no markdown, no backticks):
{{"decision": "LONG" or "SHORT" or "WAIT", "confidence": 0.0-0.95, "reasoning": "2-3 sentences explaining your decision", "tp_price": null or a number (structural price target from chart — omit or null if unsure)}}"""

        try:
            _rate_limit_gemini()
            
            from google.genai.types import GenerateContentConfig
            
            config = GenerateContentConfig(
                temperature=0.1,
            )
            
            response = _gemini_full_call("gemini-2.5-flash", prompt, config, timeout=90)

            # V3.1.75: Retry once on empty Judge response
            if not response or not hasattr(response, 'text') or not response.text:
                print(f"  [JUDGE] Empty Gemini response for {pair}, retrying in 8s...")
                import time as _jtime
                _jtime.sleep(8)
                _rate_limit_gemini()
                response = _gemini_full_call("gemini-2.5-flash", prompt, config, timeout=90)

            clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            
            decision = data.get("decision", "WAIT").upper() if data.get("decision") else "WAIT"
            raw_conf = data.get("confidence")
            confidence = min(0.95, max(0.0, float(raw_conf))) if raw_conf is not None else 0.0
            reasoning = data.get("reasoning") or "Gemini Judge decision"
            
            # V3.1.75 FIX #6: ANTI-WAIT V3 - respects F&G context
            # Layer 1: If 2+ personas agree on direction at >= 70% each, force that direction
            # Layer 2: Reasoning word-count fallback
            # GUARD: Never force SHORT in extreme fear or LONG in extreme greed
            _fg_for_antiwait = regime.get("fear_greed", 50) if regime else 50
            if decision == "WAIT" and confidence >= 0.75:
                _long_voters = [v for v in persona_votes if v["signal"] == "LONG" and v["confidence"] >= 0.70]
                _short_voters = [v for v in persona_votes if v["signal"] == "SHORT" and v["confidence"] >= 0.70]

                # F&G guard: block nonsensical forced overrides
                if _fg_for_antiwait < 20:
                    _short_voters = []  # Never force SHORT in extreme fear
                    if _long_voters:
                        print(f"  [JUDGE] ANTI-WAIT F&G guard: F&G={_fg_for_antiwait}, only allowing LONG override")
                elif _fg_for_antiwait > 80:
                    _long_voters = []  # Never force LONG in extreme greed
                    if _short_voters:
                        print(f"  [JUDGE] ANTI-WAIT F&G guard: F&G={_fg_for_antiwait}, only allowing SHORT override")

                if len(_long_voters) >= 2 and len(_short_voters) == 0:
                    decision = "LONG"
                    _voter_names = [v["persona"] for v in _long_voters]
                    print(f"  [JUDGE] ANTI-WAIT: WAIT->LONG (consensus: {_voter_names}, conf={confidence:.0%})")
                elif len(_short_voters) >= 2 and len(_long_voters) == 0:
                    decision = "SHORT"
                    _voter_names = [v["persona"] for v in _short_voters]
                    print(f"  [JUDGE] ANTI-WAIT: WAIT->SHORT (consensus: {_voter_names}, conf={confidence:.0%})")
                else:
                    # Layer 2: Reasoning text analysis (fallback) - also F&G guarded
                    reasoning_lower = reasoning.lower() if reasoning else ""
                    long_words = sum(1 for w in ["long", "buy", "bullish", "accumulation", "oversold", "bounce", "support"] if w in reasoning_lower)
                    short_words = sum(1 for w in ["short", "sell", "bearish", "distribution", "overbought", "dump", "resistance"] if w in reasoning_lower)
                    if _fg_for_antiwait < 20:
                        short_words = 0  # Block SHORT reasoning override in extreme fear
                    elif _fg_for_antiwait > 80:
                        long_words = 0  # Block LONG reasoning override in extreme greed
                    if long_words >= 2 and short_words == 0:
                        decision = "LONG"
                        print(f"  [JUDGE] ANTI-WAIT: WAIT->LONG (reasoning: {long_words} long words, conf={confidence:.0%})")
                    elif short_words >= 2 and long_words == 0:
                        decision = "SHORT"
                        print(f"  [JUDGE] ANTI-WAIT: WAIT->SHORT (reasoning: {short_words} short words, conf={confidence:.0%})")
            raw_tp = data.get("tp_pct")
            tp_pct = float(raw_tp) if raw_tp is not None else tier_config["tp_pct"]
            raw_sl = data.get("sl_pct")
            sl_pct = float(raw_sl) if raw_sl is not None else tier_config["sl_pct"]

            # V3.2.16: Parse Gemini's structural tp_price (actual price target from chart context)
            gemini_tp_price = None
            raw_tp_price = data.get("tp_price")
            if raw_tp_price is not None:
                try:
                    gemini_tp_price = float(raw_tp_price)
                    if gemini_tp_price > 0:
                        print(f"  [JUDGE] Gemini tp_price: ${gemini_tp_price:.4f} (structural target from chart)")
                    else:
                        gemini_tp_price = None
                except (ValueError, TypeError):
                    gemini_tp_price = None

            # Clamp TP/SL to reasonable ranges (wider max for vol-adjusted)
            tp_pct = max(1.5, min(10.0, tp_pct))
            sl_pct = max(1.5, min(7.0, sl_pct))

            print(f"  [JUDGE] Gemini: {decision} ({confidence:.0%})")
            print(f"  [JUDGE] Reasoning: {reasoning[:600]}")
            
            if decision == "WAIT":
                return self._wait_decision(f"Gemini Judge: {reasoning}", persona_votes, 
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])
            
            if decision not in ("LONG", "SHORT"):
                return self._wait_decision(f"Gemini returned invalid decision: {decision}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])
            
            # Safety: block if already have same direction
            if decision == "LONG" and has_long:
                return self._wait_decision(f"Gemini says LONG but already have LONG on {pair}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])
            if decision == "SHORT" and has_short:
                return self._wait_decision(f"Gemini says SHORT but already have SHORT on {pair}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])
            
            # V3.1.63/V3.1.80: Minimum confidence floor (SNIPER) with fallback support
            # 75-79%: Pass through as fallback candidate (only used if chop filter frees a slot)
            # <75%: Hard block
            if confidence < CHOP_FALLBACK_CONFIDENCE:
                return self._wait_decision(f"Gemini confidence too low: {confidence:.0%} < {CHOP_FALLBACK_CONFIDENCE:.0%}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])

            # V3.1.59: Confidence-tiered position sizing with FLOW+WHALE alignment
            flow_whale_aligned = False
            flow_vote = next((v for v in persona_votes if v.get("persona") == "FLOW"), None)
            whale_vote = next((v for v in persona_votes if v.get("persona") == "WHALE"), None)
            if flow_vote and whale_vote:
                if (flow_vote.get("signal") == decision == whale_vote.get("signal")
                    and flow_vote.get("confidence", 0) >= 0.60
                    and whale_vote.get("confidence", 0) >= 0.60):
                    flow_whale_aligned = True

            # V3.1.92: Equity-based sizing — use UPnL to size bigger (was balance-only V3.1.90)
            # Scale: 25% base, 31.25% high-conf, 37.5% ultra. Cap so all slots fit in ~85% of sizing_base.
            sizing_base = get_sizing_base(balance)
            if sizing_base <= 0:
                return self._wait_decision("Margin guard: available margin too low", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])
            base_size = sizing_base * 0.25
            if confidence >= 0.90 and flow_whale_aligned:
                position_usdt = base_size * 1.5  # ULTRA
                print(f"  [SIZING] ULTRA: 90%+ conf + FLOW/WHALE aligned -> {position_usdt/sizing_base*100:.0f}% of sizing_base")
            elif confidence > 0.85:
                position_usdt = base_size * 1.25  # HIGH
            else:
                position_usdt = base_size * 1.0  # NORMAL

            # V3.2.25: No per-slot cap — sizing_base is already the available free margin.
            # MAX_SINGLE_POSITION_PCT (50%) and MIN_SINGLE_POSITION_PCT (20%) are the bounds.

            # Balance protection
            if balance < 200:
                position_usdt *= 0.5
                print(f"  [JUDGE] EMERGENCY BALANCE: size halved")

            # Volatility adjustment from regime
            position_usdt *= regime.get("size_multiplier", 1.0)

            position_usdt = max(position_usdt, balance * MIN_SINGLE_POSITION_PCT)
            position_usdt = min(position_usdt, sizing_base * MAX_SINGLE_POSITION_PCT)
            
            max_hold = tier_config["max_hold_hours"]
            
            vote_summary = [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes]

            # ===== COMPUTE WHALE BIAS (for Smart Hold) =====
            whale_bias = next(
                (v.get("confidence", 0.0)
                 for v in persona_votes
                 if v.get("persona") == "WHALE"),
                0.0
            )



            
            # V3.1.39b: Upload AI decision log to WEEX for compliance
            try:
                upload_ai_log_to_weex(
                    stage=f"Gemini Judge Decision: {pair} (Tier {tier})",
                    input_data={
                        "pair": pair,
                        "tier": tier,
                        "tier_name": tier_config["name"],
                        "balance": balance,
                        "regime": regime.get("regime", "NEUTRAL"),
                        "fear_greed": regime.get("fear_greed", 50),
                        "btc_24h_change": regime.get("change_24h", 0),
                        "btc_4h_change": regime.get("change_4h", 0),
                        "btc_funding": regime.get("btc_funding", 0),
                        "existing_long": has_long,
                        "existing_short": has_short,
                        "days_left": days_left,
                        "competition_pnl": pnl,
                        "persona_votes": [
                            {"persona": v["persona"], "signal": v["signal"], "confidence": v["confidence"], 
                             "reasoning": v.get("reasoning", "")[:400]}
                            for v in persona_votes
                        ],
                    },
                    output_data={
                        "decision": decision,
                        "confidence": confidence,
                        "tp_pct": tp_pct,
                        "sl_pct": sl_pct,
                        "max_hold_hours": max_hold,
                        "position_usdt": round(position_usdt, 2),
                        "ai_model": "gemini-2.5-flash",
                        "judge_version": "gemini-judge",
                    },
                    explanation=f"Gemini AI Judge Decision for {pair}: {decision} at {confidence:.0%} confidence. {reasoning} Market: {regime.get('regime','NEUTRAL')} regime, F&G={regime.get('fear_greed',50)}, BTC {regime.get('change_24h',0):+.1f}% 24h. TP={tp_pct}% SL={sl_pct}%. Persona votes: {', '.join(vote_summary)}"
                )
            except Exception as log_err:
                print(f"  [JUDGE] AI log upload error: {log_err}")
            
            # V3.1.80: Mark 75-79% trades as fallback-only (need chop slot to execute)
            is_fallback = CHOP_FALLBACK_CONFIDENCE <= confidence < MIN_CONFIDENCE_TO_TRADE

            return {
                "decision": decision,
                "confidence": confidence,
                "recommended_position_usdt": position_usdt,
                "take_profit_percent": tp_pct,
                "stop_loss_percent": sl_pct,
                "hold_time_hours": max_hold,
                "tier": tier,
                "tier_name": tier_config["name"],
                "reasoning": f"Gemini Judge: {reasoning}. Votes: {', '.join(vote_summary)}",
                "persona_votes": persona_votes,
                "vote_breakdown": {
                    "long_score": 0,
                    "short_score": 0,
                    "neutral_score": 0,
                },
                "fear_greed": regime.get("fear_greed", 50),
                "regime": regime.get("regime", "NEUTRAL"),
                "fallback_only": is_fallback,
                "gemini_tp_price": gemini_tp_price,  # V3.2.16: structural TP from chart context
            }
            
        except json.JSONDecodeError as e:
            print(f"  [JUDGE] Gemini JSON parse error: {e}")
            print(f"  [JUDGE] Raw response: {response.text[:200] if response else 'None'}")
            return self._fallback_decide(persona_votes, pair, balance, competition_status, tier, tier_config, regime)
        except Exception as e:
            print(f"  [JUDGE] Gemini Judge error: {e}")
            return self._fallback_decide(persona_votes, pair, balance, competition_status, tier, tier_config, regime)
    
    def _fallback_decide(self, persona_votes, pair, balance, competition_status, tier, tier_config, regime):
        """Fallback to simple weighted-sum if Gemini fails"""
        print(f"  [JUDGE] Falling back to weighted-sum logic")
        
        long_score = 0
        short_score = 0
        vote_summary = []
        
        for vote in persona_votes:
            weight = 1.0
            if vote["persona"] == "FLOW":
                weight = 2.0  # V3.1.63: Equal with WHALE
            elif vote["persona"] == "TECHNICAL":
                # V3.2.1: Reduce weight in fear markets — SMA signals lag when price is in freefall.
                # At F&G < 30, TECHNICAL is likely stuck below SMAs and voting SHORT/NEUTRAL everywhere,
                # adding noise rather than signal. Cut it to 0.4 so WHALE+FLOW dominate.
                _fg_fallback = regime.get("fear_greed", 50) if regime else 50
                weight = 0.4 if _fg_fallback < 30 else 0.8
            elif vote["persona"] == "SENTIMENT":
                weight = 1.0
            elif vote["persona"] == "WHALE":
                weight = 2.0  # V3.1.63: Equal with FLOW
            
            if vote["signal"] == "LONG":
                long_score += vote["confidence"] * weight
            elif vote["signal"] == "SHORT":
                short_score += vote["confidence"] * weight
            
            vote_summary.append(f"{vote['persona']}={vote['signal']}({vote['confidence']:.0%})")
        
        total = long_score + short_score
        if total == 0:
            return self._wait_decision("Fallback: no votes", persona_votes, vote_summary)
        
        if long_score > short_score * 1.2 and (long_score / total) > 0.55:
            decision = "LONG"
            confidence = min(0.85, long_score / total)
        elif short_score > long_score * 1.2 and (short_score / total) > 0.55:
            decision = "SHORT"
            confidence = min(0.85, short_score / total)
        else:
            return self._wait_decision(f"Fallback: no consensus L={long_score:.1f} S={short_score:.1f}", persona_votes, vote_summary)
        
        if confidence < 0.70:
            return self._wait_decision(f"Fallback: confidence too low {confidence:.0%}", persona_votes, vote_summary)
        
        position_usdt = balance * 0.15
        
        return {
            "decision": decision,
            "confidence": confidence,
            "recommended_position_usdt": position_usdt,
            "take_profit_percent": tier_config["tp_pct"],
            "stop_loss_percent": tier_config["sl_pct"],
            "hold_time_hours": tier_config["max_hold_hours"],
            "tier": tier,
            "tier_name": tier_config["name"],
            "reasoning": f"FALLBACK Judge: {decision} @ {confidence:.0%}. Votes: {', '.join(vote_summary)}",
            "persona_votes": persona_votes,
            "vote_breakdown": {"long_score": round(long_score, 2), "short_score": round(short_score, 2), "neutral_score": 0},
        }

    def _wait_decision(self, reason: str, persona_votes: List[Dict] = None, vote_summary: List[str] = None) -> Dict:
        """Return WAIT decision with full vote details for logging"""
        votes_str = ', '.join(vote_summary) if vote_summary else "No votes"
        
        # V3.1.39b: Log WAIT decisions to WEEX too (shows AI is analyzing even when not trading)
        try:
            upload_ai_log_to_weex(
                stage="Gemini Judge: WAIT",
                input_data={
                    "persona_votes": [
                        {"persona": v["persona"], "signal": v["signal"], "confidence": v["confidence"]}
                        for v in (persona_votes or [])
                    ],
                },
                output_data={
                    "decision": "WAIT",
                    "confidence": 0.0,
                    "judge_version": "gemini-judge",
                },
                explanation=f"AI Judge decided WAIT: {reason}. {votes_str}"
            )
        except:
            pass
        
        return {
            "decision": "WAIT",
            "confidence": 0.0,
            "reasoning": f"{reason}. Votes: {votes_str}",
            "persona_votes": persona_votes or [],
            "whale_bias": 0.0,

            "vote_summary": vote_summary or [],
        }


# ============================================================
# MULTI-PERSONA ANALYZER
# ============================================================

class MultiPersonaAnalyzer:
    """Coordinates all personas and produces final signal."""
    
    def __init__(self):
        self.whale = WhalePersona()
        self.sentiment = SentimentPersona()
        self.flow = FlowPersona()
        self.technical = TechnicalPersona()
        self.judge = JudgePersona()
    
    def analyze(self, pair: str, pair_info: Dict, balance: float, 
                competition_status: Dict, open_positions: List[Dict]) -> Dict:
        """Run all personas and get final decision"""
        
        tier = pair_info.get("tier", 2)
        tier_config = get_tier_config(tier)
        
        print(f"\n  === Multi-Persona Analysis: {pair} (Tier {tier}: {tier_config['name']}) ===")
        
        votes = []
        
        # 1. Whale Persona (V3.1.45: ALL pairs via Cryptoracle, BTC/ETH also use Etherscan)
        print(f"  [WHALE] Analyzing...")
        whale_vote = self.whale.analyze(pair, pair_info)
        votes.append(whale_vote)
        print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")
        
        # 2. Sentiment Persona
        print(f"  [SENTIMENT] Analyzing...")
        sentiment_vote = self.sentiment.analyze(pair, pair_info, competition_status)
        votes.append(sentiment_vote)
        print(f"  [SENTIMENT] {sentiment_vote['signal']} ({sentiment_vote['confidence']:.0%}): {sentiment_vote['reasoning']}")
        
        # 3. Flow Persona
        print(f"  [FLOW] Analyzing...")
        flow_vote = self.flow.analyze(pair, pair_info)

        # V3.2.1: FLOW stability — if direction flipped 180° from last cycle, discount confidence 50%.
        # Rationale: FLOW that flip-flops is noise (reacting to a single large print), not signal.
        # Sustained FLOW direction = real money moving. A one-cycle flip = wait for confirmation.
        _flow_sym = pair_info.get("symbol", pair)
        _flow_prev = _prev_flow_direction.get(_flow_sym)
        _flow_dir = flow_vote.get("signal", "NEUTRAL")
        if (_flow_prev and _flow_prev in ("LONG", "SHORT") and
                _flow_dir in ("LONG", "SHORT") and _flow_prev != _flow_dir):
            _disc = round(flow_vote["confidence"] * 0.5, 2)
            print(f"  [FLOW] FLIP DISCOUNT: {_flow_prev}->{_flow_dir}, conf {flow_vote['confidence']:.0%} -> {_disc:.0%} (wait for confirmation)")
            flow_vote = dict(flow_vote)
            flow_vote["confidence"] = _disc
            flow_vote["reasoning"] = flow_vote.get("reasoning", "") + f" [FLIP {_flow_prev}->{_flow_dir}, conf discounted]"
        _prev_flow_direction[_flow_sym] = _flow_dir

        votes.append(flow_vote)
        print(f"  [FLOW] {flow_vote['signal']} ({flow_vote['confidence']:.0%}): {flow_vote['reasoning']}")
        
        # 4. Technical Persona
        print(f"  [TECHNICAL] Analyzing...")
        tech_vote = self.technical.analyze(pair, pair_info)
        votes.append(tech_vote)
        print(f"  [TECHNICAL] {tech_vote['signal']} ({tech_vote['confidence']:.0%}): {tech_vote['reasoning']}")
        
        # 5. Judge makes final decision
        print(f"  [JUDGE] Deciding...")
        final = self.judge.decide(votes, pair, balance, competition_status)
        print(f"  [JUDGE] Final: {final['decision']} ({final.get('confidence', 0):.0%})")
        
        if final['decision'] in ("LONG", "SHORT"):
            print(f"  [JUDGE] TP: {final.get('take_profit_percent')}%, SL: {final.get('stop_loss_percent')}%, Max Hold: {final.get('hold_time_hours')}h")
        
        # V3.1.97: REMOVED freshness filter. Ensemble sees momentum data already.

        # V3.1.80: CHOP FILTER - detect choppy/sideways markets
        # V3.2.18: PENALTIES REMOVED — trust the signals. 80% confidence floor + 0.5% TP cap = protection.
        # Chop filter was blocking correct signals (ADA 88% SHORT, BNB 88% LONG) and letting wrong ones
        # through via FLOW EXTREME overrides. Detection kept for logging only.
        if final['decision'] in ("LONG", "SHORT"):
            symbol = pair_info["symbol"]
            chop = detect_sideways_market(symbol)
            final['chop_data'] = chop  # Attach for daemon visibility

            if chop.get("is_choppy", False):
                severity = chop.get("severity", "medium")
                # Log only — no penalty, no block
                print(f"  [CHOP_FILTER] DETECTED {severity.upper()} chop: {chop['reason']} — NO PENALTY (V3.2.18: trust signals)")
                final['chop_original_decision'] = final['decision']
                final['chop_pre_penalty_confidence'] = final.get('confidence', 0)
                final['chop_detected'] = True
                final['chop_severity'] = severity
        else:
            final['chop_data'] = None

        # V3.1.104: REMOVED entry confirmation gate.
        # The gate fired at reversals (tops/bottoms) — exactly when you want to enter.
        # Ensemble already incorporates price momentum via FLOW (taker ratios) and
        # TECHNICAL (RSI/SMA). Signal persistence in the daemon replaces this gate.

        return final


# ============================================================
# COMPETITION STATUS
# ============================================================

def get_competition_status(balance: float) -> Dict:
    """Get competition status (phase tracking only, TP/SL now tier-based)"""
    now = datetime.now(timezone.utc)
    days_left = (COMPETITION_END - now).days
    pnl = balance - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100
    
    if days_left > 15:
        phase = "early"
        strategy = "growth"
    elif days_left > 7:
        phase = "mid"
        strategy = "balanced"
    elif days_left > 3:
        phase = "late"
        strategy = "conservative"
    else:
        phase = "final"
        strategy = "protect" if pnl > 0 else "push"
    
    return {
        "days_left": days_left,
        "phase": phase,
        "strategy_mode": strategy,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


# ============================================================
# ORDER PLACEMENT
# ============================================================

def set_leverage(symbol: str, leverage: int) -> Dict:
    endpoint = "/capi/v2/account/leverage"
    lev_str = str(leverage)
    body = json.dumps({
        "symbol": symbol,
        "marginMode": 1,
        "longLeverage": lev_str,
        "shortLeverage": lev_str
    })
    # V3.1.82: Retry up to 3 times if WEEX rejects due to pending orders (slot swap race)
    for _lev_attempt in range(3):
        r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
        result = r.json()
        if result.get("code") == "200":
            print(f"  [LEVERAGE OK] {symbol} set to {leverage}x cross")
            return result
        if "open orders" in str(result.get("msg", "")).lower() and _lev_attempt < 2:
            print(f"  [LEVERAGE RETRY] Pending orders blocking, waiting 3s... (attempt {_lev_attempt+1}/3)")
            time.sleep(3)
            continue
        print(f"  [LEVERAGE WARNING] set_leverage failed: {result}")
        return result
    return result


def place_order(symbol: str, side: str, size: float, tp_price: float = None, sl_price: float = None) -> Dict:
    endpoint = "/capi/v2/order/placeOrder"
    rounded_size = round_size_to_step(size, symbol)
    current_price = get_price(symbol)
    
    if tp_price and current_price > 0:
        if side in ("1", "4"):
            limit_price = round_price_to_tick(current_price * 1.002, symbol)
        else:
            limit_price = round_price_to_tick(current_price * 0.998, symbol)
        
        order = {
            "symbol": symbol,
            "client_oid": f"smtv311_{int(time.time()*1000)}",
            "size": str(rounded_size),
            "type": side,
            "order_type": "0",
            "match_price": "0",
            "price": str(limit_price),
        }
        if tp_price:
            order["presetTakeProfitPrice"] = str(round_price_to_tick(tp_price, symbol))
        if sl_price:
            order["presetStopLossPrice"] = str(round_price_to_tick(sl_price, symbol))
    else:
        order = {
            "symbol": symbol,
            "client_oid": f"smtv311_{int(time.time()*1000)}",
            "size": str(rounded_size),
            "type": side,
            "order_type": "0",
            "match_price": "1"
        }
    
    body = json.dumps(order)
    
    if TEST_MODE:
        print(f"  [TEST] Would place: {order}")
        return {"order_id": f"test_{int(time.time())}", "test_mode": True}
    
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()


# ============================================================
# AI LOG UPLOAD
# ============================================================

def upload_ai_log_to_weex(stage: str, input_data: Dict, output_data: Dict, 
                          explanation: str, order_id: int = None) -> Dict:
    """
    Upload AI decision log to WEEX
    
    V3.1.5 FIX: Proper error logging and response validation
    - Uses flush=True for immediate output visibility in daemon.log
    - Checks WEEX response code ("00000" = success)
    - Logs detailed error info when upload fails
    """
    endpoint = "/capi/v2/order/uploadAiLog"
    
    payload = {
        "stage": stage,
        "model": MODEL_NAME,
        "input": input_data,
        "output": output_data,
        "explanation": explanation[:1000]  # WEEX allows 500 words (~2500 chars)
    }
    
    if order_id:
        payload["orderId"] = int(order_id)
    
    body = json.dumps(payload)
    
    if TEST_MODE:
        print(f"  [AI LOG TEST] Would upload: {stage}", flush=True)
        return {"test_mode": True, "code": "00000"}
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        
        result = r.json()
        code = result.get("code", "unknown")
        msg = result.get("msg", "")
        
        if code == "00000":
            pass  # Success - silent (was flooding logs with [AI LOG OK] every call)
        else:
            # FAILURE - log detailed error
            print(f"  [AI LOG FAIL] {stage}", flush=True)
            print(f"  [AI LOG FAIL] code={code}, msg={msg}", flush=True)
        
        sys.stdout.flush()
        return result
        
    except requests.exceptions.Timeout:
        print(f"  [AI LOG TIMEOUT] {stage}", flush=True)
        sys.stdout.flush()
        return {"error": "timeout", "code": "timeout"}
    except requests.exceptions.RequestException as e:
        print(f"  [AI LOG NET ERROR] {stage}: {e}", flush=True)
        sys.stdout.flush()
        return {"error": str(e), "code": "network_error"}
    except Exception as e:
        print(f"  [AI LOG ERROR] {stage}: {type(e).__name__}: {e}", flush=True)
        sys.stdout.flush()
        return {"error": str(e), "code": "unknown"}


# ============================================================
# TRADE EXECUTION (V3.1.1 - Tier-Based)
# ============================================================

def execute_trade(pair_info: Dict, decision: Dict, balance: float) -> Dict:
    """Execute trade with TIER-BASED TP/SL"""
    
    symbol = pair_info["symbol"]
    signal = decision["decision"]
    tier = pair_info.get("tier", 2)
    tier_config = get_tier_config(tier)
    
    if signal not in ("LONG", "SHORT"):
        return {"executed": False, "reason": "No trade signal"}
    
    current_price = get_price(symbol)
    if current_price == 0:
        return {"executed": False, "reason": "Could not get price"}
    
    # V3.1.92: Equity-based sizing (mirrors Judge sizing)
    sizing_base = get_sizing_base(balance)
    if sizing_base <= 0:
        return {"executed": False, "reason": "Margin guard: available margin too low"}

    position_usdt = decision.get("recommended_position_usdt", sizing_base * 0.07)
    position_usdt = max(position_usdt, balance * MIN_SINGLE_POSITION_PCT)   # Floor stays on balance
    position_usdt = min(position_usdt, sizing_base * MAX_SINGLE_POSITION_PCT)  # Cap scales with equity

    # V3.2.25: No per-slot cap — sizing_base is already available free margin; MIN/MAX_SINGLE_POSITION_PCT bound it.

    # V3.1.77: RL-based sizing adjustment - reduce size for historically losing pairs
    try:
        from smt_daemon_v3_1 import get_pair_sizing_multiplier
        _sizing_mult = get_pair_sizing_multiplier(symbol)
        if _sizing_mult < 1.0:
            position_usdt *= _sizing_mult
            print(f"  [SIZING] RL adjustment: {symbol.replace('cmt_','').upper()} size x{_sizing_mult} (historical performance)")
    except Exception:
        pass
    
    # V3.1.59: Confidence-tiered leverage
    trade_confidence = decision.get("confidence", 0.75)
    try:
        from leverage_manager import get_safe_leverage
        regime_data = REGIME_CACHE.get("regime", 300)
        current_regime = regime_data.get("regime", "NEUTRAL") if regime_data else "NEUTRAL"
        safe_leverage = get_safe_leverage(tier, regime=current_regime, confidence=trade_confidence)
        conf_bracket = "ULTRA" if trade_confidence >= 0.90 else "HIGH" if trade_confidence >= 0.80 else "NORMAL"
        print(f"  [LEVERAGE] Tier {tier} ({current_regime}, {conf_bracket} {trade_confidence:.0%}): Using {safe_leverage}x")
    except Exception as e:
        safe_leverage = 20  # V3.1.75: 20x flat - user mandate
        print(f"  [LEVERAGE] Fallback to {safe_leverage}x: {e}")
    notional_usdt = position_usdt * safe_leverage
    raw_size = notional_usdt / current_price

    # V3.1.59: AI log for leverage/sizing decision
    upload_ai_log_to_weex(
        stage=f"Leverage Decision: {signal} {symbol.replace('cmt_', '').upper()}",
        input_data={
            "tier": tier,
            "confidence": trade_confidence,
            "regime": current_regime if 'current_regime' in dir() else "UNKNOWN",
            "balance": balance,
            "position_usdt_margin": round(position_usdt, 2),
        },
        output_data={
            "leverage": safe_leverage,
            "notional_usdt": round(notional_usdt, 2),
            "conf_bracket": conf_bracket if 'conf_bracket' in dir() else "UNKNOWN",
        },
        explanation=f"Confidence-tiered leverage: {safe_leverage}x for Tier {tier} at {trade_confidence:.0%} confidence. Margin: ${position_usdt:.0f}, Notional: ${notional_usdt:.0f}."
    )

    if raw_size <= 0:
        return {"executed": False, "reason": f"Invalid size: {raw_size}"}
    
    size = round_size_to_step(raw_size, symbol)
    
    if size <= 0:
        return {"executed": False, "reason": f"Size too small: {size}"}
    
    order_type = "1" if signal == "LONG" else "2"
    
    # V3.1.84: CHART-BASED TP/SL (replaces fixed % approach)
    # Step 1: Try chart-based S/R levels (like a human trader)
    # Step 2: If chart fails, use competition fallback (tighter than old base)
    # Step 3: ATR only used as SL safety net, not primary
    _in_competition = COMPETITION_START <= datetime.now(timezone.utc) <= COMPETITION_END

    chart_sr = find_chart_based_tp_sl(symbol, signal, current_price)

    # V3.2.16: Gemini structural TP override — uses 1D+4H chart context to identify
    # the REAL nearest resistance/support, bypassing the 2H anchor when Gemini gives a target.
    _gemini_tp = decision.get("gemini_tp_price")
    _gemini_tp_used = False
    if _gemini_tp and _gemini_tp > 0 and current_price > 0:
        if signal == "LONG" and _gemini_tp > current_price:
            _g_tp_pct = ((_gemini_tp - current_price) / current_price) * 100
            if 0.3 <= _g_tp_pct <= 5.0:  # Sanity: 0.3% to 5.0%
                print(f"  [TP/SL] GEMINI TP OVERRIDE (LONG): ${_gemini_tp:.4f} = {_g_tp_pct:.2f}% from ${current_price:.4f}")
                chart_sr["tp_pct"] = round(_g_tp_pct, 2)
                chart_sr["tp_price"] = _gemini_tp
                if chart_sr["method"] == "fallback":
                    chart_sr["method"] = "chart_mtf"  # Promote so chart path is used
                _gemini_tp_used = True
        elif signal == "SHORT" and _gemini_tp < current_price:
            _g_tp_pct = ((current_price - _gemini_tp) / current_price) * 100
            if 0.3 <= _g_tp_pct <= 5.0:
                print(f"  [TP/SL] GEMINI TP OVERRIDE (SHORT): ${_gemini_tp:.4f} = {_g_tp_pct:.2f}% from ${current_price:.4f}")
                chart_sr["tp_pct"] = round(_g_tp_pct, 2)
                chart_sr["tp_price"] = _gemini_tp
                if chart_sr["method"] == "fallback":
                    chart_sr["method"] = "chart_mtf"
                _gemini_tp_used = True

    if chart_sr["method"] in ("chart", "chart_mtf") and chart_sr["tp_pct"] and chart_sr["sl_pct"]:
        # CHART-BASED: Real S/R levels from candle swing highs/lows (or Gemini structural target)
        tp_pct_raw = chart_sr["tp_pct"]
        sl_pct_raw = chart_sr["sl_pct"]
        tp_price = chart_sr["tp_price"]
        sl_price = chart_sr["sl_price"]
        tp_pct = tp_pct_raw / 100
        sl_pct = sl_pct_raw / 100
        mtf_label = "GEMINI" if _gemini_tp_used else ("MTF" if chart_sr["method"] == "chart_mtf" else "1H")
        print(f"  [TP/SL] CHART-BASED [{mtf_label}]: TP {tp_pct_raw:.2f}% SL {sl_pct_raw:.2f}% (Tier {tier})")

        # V3.2.12: In extreme fear, cap ALL TP at competition fallback (0.5%) — both LONG and SHORT.
        # V3.2.9 only capped SHORTs; LONGs assumed to have "naturally small" TPs but they weren't.
        # 2H anchor helps a lot, but fear markets can still produce 1-2% TPs when 2H had a big swing.
        # XRP at +0.28% peak, ADA at +0.25% peak both had chart TPs 2-3% away — positions never exited.
        try:
            _fg_tp_regime = REGIME_CACHE.get("regime", 300)
            _fg_tp = _fg_tp_regime.get("fear_greed", 50) if _fg_tp_regime else 50
            _tp_cap = COMPETITION_FALLBACK_TP.get(tier, 0.5)
            if _fg_tp < 20 and tp_pct_raw > _tp_cap:
                _dir = "LONG" if signal == "LONG" else "SHORT"
                print(f"  [TP/SL] Extreme fear {_dir} TP capped: {tp_pct_raw:.2f}% → {_tp_cap:.2f}% (F&G={_fg_tp})")
                tp_pct_raw = _tp_cap
                tp_pct = tp_pct_raw / 100
                tp_price = current_price * (1 + tp_pct) if signal == "LONG" else current_price * (1 - tp_pct)
        except Exception:
            pass

        # V3.2.15: XRP-specific TP cap — historical fills show XRP moves 0.34–0.48%.
        # V3.2.16: Only apply if Gemini didn't give us a structural target (Gemini sees the real chart).
        if symbol == "cmt_xrpusdt" and tp_pct_raw > 0.70 and not _gemini_tp_used:
            print(f"  [TP/SL] XRP TP cap: {tp_pct_raw:.2f}% → 0.70% (historical range, no Gemini override)")
            tp_pct_raw = 0.70
            tp_pct = tp_pct_raw / 100
            tp_price = current_price * (1 + tp_pct) if signal == "LONG" else current_price * (1 - tp_pct)

    else:
        # FALLBACK: Use competition-tightened percentages (NOT the old wide 3.0-3.5%)
        if _in_competition:
            tp_pct_raw = COMPETITION_FALLBACK_TP.get(tier, 1.5)
            sl_pct_raw = COMPETITION_FALLBACK_SL.get(tier, 1.2)
            print(f"  [TP/SL] COMP FALLBACK: TP {tp_pct_raw:.2f}% SL {sl_pct_raw:.2f}% (Tier {tier})")
        else:
            # Normal mode: use old tier-based approach with ATR SL
            tp_pct_raw = tier_config["tp_pct"]
            sl_pct_raw = tier_config["sl_pct"]
            try:
                atr_data = get_pair_atr(symbol)
                atr_pct = atr_data.get("atr_pct", 0)
                if atr_pct > 0:
                    dynamic_sl = round(atr_pct * 1.5, 2)
                    sl_pct_raw = max(dynamic_sl, tier_config["sl_pct"])
                    _force_exit_sl = abs(tier_config.get("force_exit_loss_pct", -2.0))
                    _sl_cap = min(_force_exit_sl + 0.5, 3.0)
                    sl_pct_raw = min(sl_pct_raw, _sl_cap)
            except Exception:
                pass
            # F&G scaling (non-competition only)
            try:
                _fg_regime = REGIME_CACHE.get("regime", 300)
                _fg_val = _fg_regime.get("fear_greed", 50) if _fg_regime else 50
                if _fg_val < 15:
                    tp_pct_raw = round(tp_pct_raw * 1.5, 2)
                elif _fg_val < 30:
                    tp_pct_raw = round(tp_pct_raw * 1.25, 2)
                elif _fg_val > 80:
                    tp_pct_raw = round(tp_pct_raw * 0.50, 2)
                elif _fg_val > 60:
                    tp_pct_raw = round(tp_pct_raw * 0.65, 2)
            except Exception:
                pass
            print(f"  [TP/SL] NORMAL FALLBACK: TP {tp_pct_raw:.2f}% SL {sl_pct_raw:.2f}% (Tier {tier})")

        # Calculate TP/SL prices from percentages (only if not set by chart)
        tp_pct = tp_pct_raw / 100
        sl_pct = sl_pct_raw / 100
        if signal == "LONG":
            tp_price = current_price * (1 + tp_pct)
            sl_price = current_price * (1 - sl_pct)
        else:
            tp_price = current_price * (1 - tp_pct)
            sl_price = current_price * (1 + sl_pct)

    # V3.1.84: ATR safety net - if chart SL is tighter than 0.5x ATR, widen it
    # Prevents noise stop-outs on volatile pairs even with chart-based SL
    try:
        _atr_check = get_pair_atr(symbol)
        _atr_pct_check = _atr_check.get("atr_pct", 0)
        if _atr_pct_check > 0:
            _min_atr_sl = round(_atr_pct_check * 0.8, 2)  # At least 0.8x ATR
            if sl_pct_raw < _min_atr_sl:
                print(f"  [ATR-SAFETY] SL {sl_pct_raw:.2f}% < 0.8x ATR ({_min_atr_sl:.2f}%), widening to {_min_atr_sl:.2f}%")
                sl_pct_raw = _min_atr_sl
                sl_pct = sl_pct_raw / 100
                if signal == "LONG":
                    sl_price = current_price * (1 - sl_pct)
                else:
                    sl_price = current_price * (1 + sl_pct)
    except Exception:
        pass

    # V3.1.94: Per-pair overrides removed — flat 1.1% TP + chart SL (+0.5%) for all

    # V3.1.89: Removed R:R gate — the 1.5:1 requirement conflicted with chart-based
    # TP/SL and ATR safety, blocking high-confidence signals (e.g. 85% SOL SHORT).
    # Trust the 80% confidence floor + chart S/R levels instead.
    _rr_ratio = tp_pct_raw / sl_pct_raw if sl_pct_raw > 0 else 0
    print(f"  [FINAL] TP: ${tp_price:.4f} ({tp_pct_raw:.2f}%) | SL: ${sl_price:.4f} ({sl_pct_raw:.2f}%) | R:R {_rr_ratio:.1f}:1 | Method: {chart_sr['method']}")

    # V3.1.83: Cancel any orphan trigger orders BEFORE setting leverage.
    # Orphan TP/SL triggers from previous trades block leverage changes on WEEX.
    # V3.2.6: Sleep 2s after cancellation so WEEX finishes processing before leverage set.
    try:
        _pre_cleanup = cancel_all_orders_for_symbol(symbol)
        if _pre_cleanup.get("cancelled"):
            print(f"  [PRE-TRADE] Cancelled {len(_pre_cleanup['cancelled'])} orphan orders on {symbol}")
            time.sleep(2)
    except Exception:
        pass

    set_leverage(symbol, safe_leverage)

    # V3.2.28: Final TP direction guard — if TP landed on wrong side of entry (haircut + slippage),
    # discard the trade. Entry is at or past resistance/support — bad entry, don't take it.
    if signal == "LONG" and tp_price <= current_price:
        return {"executed": False, "reason": f"TP {tp_price:.4f} <= entry {current_price:.4f} — resistance too close after slippage, skip"}
    elif signal == "SHORT" and tp_price >= current_price:
        return {"executed": False, "reason": f"TP {tp_price:.4f} >= entry {current_price:.4f} — support too close after slippage, skip"}

    print(f"  [TRADE] {signal} {symbol}: {size} @ ${current_price:.4f}")
    print(f"  [TRADE] Tier {tier} ({tier_config['name']}): TP ${tp_price:.4f} ({tp_pct*100:.1f}%), SL ${sl_price:.4f} ({sl_pct*100:.1f}%)")

    result = place_order(symbol, order_type, size, tp_price, sl_price)

    order_id = result.get("order_id")

    if not order_id:
        return {"executed": False, "reason": f"Order failed: {result}"}

    # V3.2.28: Invalidate sizing cache after trade — available has changed, next trade must recalculate
    _sizing_equity_cache["ts"] = 0

    # V3.2.6: For DOGE, cancel preset plan orders and re-place TP/SL explicitly.
    # Prevents SL price drift caused by orphan interference with WEEX preset processing.
    if symbol == "cmt_dogeusdt":
        _fix_plan_orders(symbol, signal, size, tp_price, sl_price)
    
    # Upload AI log
    upload_ai_log_to_weex(
        stage=f"Trade: {signal} {symbol.replace('cmt_', '').upper()}",
        input_data={
            "pair": symbol,
            "balance": balance,
            "tier": tier,
            "tier_name": tier_config["name"],
        },
        output_data={
            "signal": signal,
            "confidence": decision["confidence"],
            "size": size,
            "entry_price": current_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "tp_pct": tp_pct * 100,
            "sl_pct": sl_pct * 100,
        },
        explanation=decision.get("reasoning", "")[:2500],  # WEEX allows 500 words
        order_id=int(order_id) if str(order_id).isdigit() else None
    )
    
    return {
        "executed": True,
        "order_id": order_id,
        "symbol": symbol,
        "signal": signal,
        "size": size,
        "entry_price": current_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "tp_pct": tp_pct * 100,
        "sl_pct": sl_pct * 100,
        "position_usdt": position_usdt,
        "tier": tier,
        "tier_name": tier_config["name"],
        "max_hold_hours": tier_config["max_hold_hours"],
    }


# ============================================================
# TRADE TRACKER (with Cooldown for Losing Trades)
# ============================================================

# V3.1.73: Fee-aware cooldowns - calibrated to fee structure
# Round-trip taker fee ~0.12% on notional (0.06% per side)
# At 20x leverage = ~2.4% ROE round-trip cost
# Cooldown must ensure enough time for market to move 3x+ fees (0.36%+)
# After losses: longer cooldown (trend reversal, need clarity)
# After wins/profit lock: no cooldown (trend confirmed, re-entry OK)
# After timeout: short cooldown (direction unknown)
COOLDOWN_HOURS = {
    1: 2,   # T1 base: BTC/ETH ~0.25%/hr, need 1.5h min for fee coverage
    2: 1.5, # T2 base: SOL ~0.4%/hr, need 1h min for fee coverage
    3: 1,   # T3 base: DOGE/XRP/ADA ~0.3%/hr, need 1.2h min
}

# V3.1.89: Per-symbol cooldowns REMOVED. The 80% confidence floor, chop filter,
# consecutive loss block, and force-stop blacklist are sufficient entry gates.
# Cooldowns were blocking legitimate re-entries after reversals.
COOLDOWN_MULTIPLIERS = {
    "sl_hit": 0.0,
    "force_stop": 0.0,
    "early_exit": 0.0,
    "max_hold": 0.0,
    "profit_lock": 0.0,
    "tp_hit": 0.0,
    "regime_exit": 0.0,
    "default": 0.0,
}

# ============================================================
# V3.1.2: RUNNER LOGIC CONFIGURATION
# ============================================================
# When position hits 50% of TP, close half and let rest run
# Only for Tier 1 and Tier 2 - Tier 3 is scalp only

# V3.1.75: Runners DISABLED - with 3-3.5% TPs, runner triggers at higher levels make no sense
# Previous runners triggered at 3-4% (above TP!) so they never actually fired
# Re-enable when TP targets are widened again
RUNNER_CONFIG = {
    1: {"enabled": False, "trigger_pct": 4.0, "close_pct": 40, "move_sl_to_entry": True, "remove_tp": False},
    2: {"enabled": False, "trigger_pct": 3.5, "close_pct": 40, "move_sl_to_entry": True, "remove_tp": False},
    3: {"enabled": False, "trigger_pct": 3.0, "close_pct": 40, "move_sl_to_entry": True, "remove_tp": False},
}


def get_runner_config(tier: int) -> Dict:
    """Get runner configuration for a tier"""
    return RUNNER_CONFIG.get(tier, {"enabled": False})

class TradeTracker:
    def __init__(self, state_file: str = "trade_state_v3_1.json"):
        self.state_file = state_file
        self.active_trades: Dict = {}
        self.closed_trades: List = []
        self.cooldowns: Dict = {}  # symbol -> cooldown_until timestamp
        self.force_stop_blacklist: Dict = {}  # V3.1.81: symbol -> blacklist_until timestamp (hard block after force_stop)
        self.recent_force_stops: Dict = {}  # V3.1.81: symbol -> list of {time, direction, pnl} for consecutive loss tracking
        self.signal_history: Dict = {}  # V3.2.6: per-pair signal persistence, survives daemon restarts
        self.load_state()
    
    def load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.active_trades = data.get("active", {})
                    self.closed_trades = data.get("closed", [])
                    self.cooldowns = data.get("cooldowns", {})
                    self.force_stop_blacklist = data.get("force_stop_blacklist", {})
                    self.recent_force_stops = data.get("recent_force_stops", {})
                    # V3.2.6: Load signal_history, drop entries older than 20min (2 cycles)
                    _cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
                    self.signal_history = {
                        pair: sh for pair, sh in data.get("signal_history", {}).items()
                        if sh.get("last_seen", "") >= _cutoff
                    }
        except:
            pass
    
    def save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump({
                "active": self.active_trades,
                "closed": self.closed_trades,
                "cooldowns": self.cooldowns,
                "force_stop_blacklist": self.force_stop_blacklist,
                "recent_force_stops": self.recent_force_stops,
                "signal_history": self.signal_history,  # V3.2.6: persist across restarts
            }, f, indent=2, default=str)
    
    def add_trade(self, symbol: str, trade_data: Dict):
        self.active_trades[symbol] = {
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "order_id": trade_data.get("order_id"),
            "side": trade_data.get("signal"),
            "size": trade_data.get("size"),
            "entry_price": trade_data.get("entry_price"),
            "tp_price": trade_data.get("tp_price"),
            "sl_price": trade_data.get("sl_price"),
            "position_usdt": trade_data.get("position_usdt"),
            "tier": trade_data.get("tier"),
            "max_hold_hours": trade_data.get("max_hold_hours"),
            "confidence": trade_data.get("confidence", 0.0),
            "whale_confidence": trade_data.get("whale_confidence", 0.0),
            "whale_direction": trade_data.get("whale_direction", "NEUTRAL"),
        }
        self.save_state()
    
    def close_trade(self, symbol: str, close_data: Dict = None):
        if symbol in self.active_trades:
            trade = self.active_trades.pop(symbol)
            trade["closed_at"] = datetime.now(timezone.utc).isoformat()
            trade["close_data"] = close_data
            self.closed_trades.append(trade)
            
            # Check if it was a losing trade - add cooldown
            pnl_pct = close_data.get("final_pnl_pct", 0) if close_data else 0
            reason = close_data.get("reason", "") if close_data else ""
            
            # V3.1.73: Fee-aware cooldown based on exit reason
            # Determine cooldown multiplier from exit reason
            tier = trade.get("tier", 2)
            base_cooldown = COOLDOWN_HOURS.get(tier, 2)

            # Classify exit reason for cooldown multiplier
            if "force_stop" in reason or "force_exit" in reason:
                cd_mult = COOLDOWN_MULTIPLIERS["force_stop"]
                cd_type = "SL/FORCE"
            elif "early_exit" in reason:
                cd_mult = COOLDOWN_MULTIPLIERS["early_exit"]
                cd_type = "EARLY_EXIT"
            elif "max_hold" in reason:
                cd_mult = COOLDOWN_MULTIPLIERS["max_hold"]
                cd_type = "TIMEOUT"
            elif "profit_lock" in reason or "peak_fade" in reason:
                cd_mult = COOLDOWN_MULTIPLIERS["profit_lock"]
                cd_type = "PROFIT_LOCK"
            elif "regime_exit" in reason:
                cd_mult = COOLDOWN_MULTIPLIERS["regime_exit"]
                cd_type = "REGIME"
            elif pnl_pct > 0:
                cd_mult = COOLDOWN_MULTIPLIERS["tp_hit"]
                cd_type = "WIN"
            elif pnl_pct < 0:
                cd_mult = COOLDOWN_MULTIPLIERS["sl_hit"]
                cd_type = "LOSS"
            else:
                cd_mult = COOLDOWN_MULTIPLIERS["default"]
                cd_type = "DEFAULT"

            cooldown_hours = base_cooldown * cd_mult
            if cooldown_hours > 0:
                cooldown_until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                self.cooldowns[symbol] = cooldown_until.isoformat()
                # V3.1.81: Also set cooldown on plain symbol key (not just sided key)
                plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
                self.cooldowns[plain_sym] = cooldown_until.isoformat()
            else:
                if symbol in self.cooldowns:
                    del self.cooldowns[symbol]

            # V3.1.91: LOSS BLACKLIST - block re-entry after ANY losing close
            # V3.1.81 only tracked force_stop/early_exit. But SL hits via WEEX triggers
            # (reason "tp_sl_hit") and PM closes (reason "portfolio_manager_*") with negative
            # PnL were never recorded — so DOGE could get re-entered 4x in one day.
            # Now ALL losses count toward both blacklist AND consecutive loss tracking.
            plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
            is_force = "force_stop" in reason or "force_exit" in reason or "early_exit" in reason
            is_loss = pnl_pct < -0.1  # Any meaningful loss (SL hit, PM close, etc.)

            if is_force:
                # Original: hard blacklist 4-12h for force exits
                max_hold = trade.get("max_hold_hours", 8)
                blacklist_hours = max(max_hold, 4)
                blacklist_until = datetime.now(timezone.utc) + timedelta(hours=blacklist_hours)
                self.force_stop_blacklist[plain_sym] = blacklist_until.isoformat()
                print(f"  [BLACKLIST] {plain_sym.replace('cmt_','').upper()} blocked for {blacklist_hours}h after {cd_type}")
            elif is_loss:
                # V3.1.91: SL hits and PM losses get 2h blacklist
                blacklist_hours = 2
                blacklist_until = datetime.now(timezone.utc) + timedelta(hours=blacklist_hours)
                self.force_stop_blacklist[plain_sym] = blacklist_until.isoformat()
                print(f"  [BLACKLIST] {plain_sym.replace('cmt_','').upper()} blocked for {blacklist_hours}h after SL/loss ({reason})")

            # V3.1.91: Track ALL losses for consecutive loss counting (was force_stop only)
            if is_force or is_loss:
                direction = trade.get("side", "UNKNOWN")
                if plain_sym not in self.recent_force_stops:
                    self.recent_force_stops[plain_sym] = []
                self.recent_force_stops[plain_sym].append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "direction": direction,
                    "pnl_pct": pnl_pct,
                    "reason": reason,
                })
                # Keep only last 10 entries
                self.recent_force_stops[plain_sym] = self.recent_force_stops[plain_sym][-10:]

            self.save_state()
    
    def is_on_cooldown(self, symbol: str) -> bool:
        """Check if a symbol is on cooldown (recently closed at loss)"""
        if symbol not in self.cooldowns:
            return False
        
        try:
            cooldown_until = datetime.fromisoformat(self.cooldowns[symbol].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < cooldown_until:
                remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
                return True
            else:
                # Cooldown expired, remove it
                del self.cooldowns[symbol]
                self.save_state()
                return False
        except:
            return False
    
    def get_cooldown_remaining(self, symbol: str) -> float:
        """Get remaining cooldown hours for a symbol"""
        if symbol not in self.cooldowns:
            return 0
        
        try:
            cooldown_until = datetime.fromisoformat(self.cooldowns[symbol].replace("Z", "+00:00"))
            remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
            return max(0, remaining)
        except:
            return 0
    
    def is_blacklisted(self, symbol: str) -> bool:
        """V3.1.81: Check if symbol is blacklisted after force_stop (hard re-entry block)"""
        plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if plain_sym not in self.force_stop_blacklist:
            return False
        try:
            bl_until = datetime.fromisoformat(self.force_stop_blacklist[plain_sym].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < bl_until:
                return True
            else:
                del self.force_stop_blacklist[plain_sym]
                self.save_state()
                return False
        except:
            return False

    def get_blacklist_remaining(self, symbol: str) -> float:
        """V3.1.81: Get remaining blacklist hours"""
        plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if plain_sym not in self.force_stop_blacklist:
            return 0
        try:
            bl_until = datetime.fromisoformat(self.force_stop_blacklist[plain_sym].replace("Z", "+00:00"))
            remaining = (bl_until - datetime.now(timezone.utc)).total_seconds() / 3600
            return max(0, remaining)
        except:
            return 0

    def consecutive_losses(self, symbol: str, direction: str, hours: int = 24) -> int:
        """V3.1.81: Count consecutive force_stop losses for same symbol+direction within N hours"""
        plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if plain_sym not in self.recent_force_stops:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        count = 0
        for entry in reversed(self.recent_force_stops[plain_sym]):
            if entry.get("time", "") < cutoff:
                break
            if entry.get("direction", "").upper() == direction.upper():
                count += 1
            else:
                break  # Different direction = reset streak
        return count

    def was_recently_force_stopped(self, symbol: str, within_hours: float = 2) -> bool:
        """V3.1.81: Check if symbol had a force_stop recently (for PM coordination)"""
        plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if plain_sym not in self.recent_force_stops:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
        for entry in reversed(self.recent_force_stops[plain_sym]):
            if entry.get("time", "") > cutoff:
                return True
        return False

    def last_force_stop_time(self, symbol: str) -> Optional[str]:
        """V3.1.95: Get ISO timestamp of most recent loss event for symbol"""
        plain_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if plain_sym not in self.recent_force_stops or not self.recent_force_stops[plain_sym]:
            return None
        return self.recent_force_stops[plain_sym][-1].get("time")

    def get_active_symbols(self) -> List[str]:
        return list(self.active_trades.keys())

    def get_active_trade(self, symbol: str) -> Optional[Dict]:
        return self.active_trades.get(symbol)


# ============================================================
# POSITION MANAGEMENT
# ============================================================

def check_position_status(symbol: str) -> Dict:
    """V3.1.53: Handle symbol:SIDE keys (e.g. cmt_bnbusdt:SHORT)"""
    positions = get_open_positions()
    # Extract real symbol and optional side filter
    if ":" in symbol:
        real_symbol, side_filter = symbol.split(":", 1)
    else:
        real_symbol, side_filter = symbol, None
    
    for pos in positions:
        if pos["symbol"] == real_symbol:
            if side_filter and pos["side"] != side_filter:
                continue
            return {
                "is_open": True,
                "side": pos["side"],
                "size": pos["size"],
                "entry_price": pos["entry_price"],
                "unrealized_pnl": pos["unrealized_pnl"],
            }
    return {"is_open": False}


def cancel_all_orders_for_symbol(symbol: str) -> Dict:
    """Cancel all pending orders for a symbol"""
    result = {"cancelled": []}
    
    try:
        endpoint = "/capi/v2/order/orders"
        if symbol:
            endpoint += f"?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        orders = r.json() if isinstance(r.json(), list) else []
        
        for order in orders:
            oid = order.get("order_id")
            if oid:
                cancel_endpoint = "/capi/v2/order/cancel"
                body = json.dumps({"order_id": oid})
                requests.post(f"{WEEX_BASE_URL}{cancel_endpoint}", 
                            headers=weex_headers("POST", cancel_endpoint, body), 
                            data=body, timeout=15)
                result["cancelled"].append(oid)
    except:
        pass
    
    try:
        endpoint = "/capi/v2/order/plan_orders"
        if symbol:
            endpoint += f"?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        orders = r.json() if isinstance(r.json(), list) else []
        
        for order in orders:
            oid = order.get("order_id")
            if oid:
                cancel_endpoint = "/capi/v2/order/cancel_plan"
                body = json.dumps({"order_id": oid})
                requests.post(f"{WEEX_BASE_URL}{cancel_endpoint}", 
                            headers=weex_headers("POST", cancel_endpoint, body), 
                            data=body, timeout=15)
                result["cancelled"].append(f"plan_{oid}")
    except:
        pass
    
    return result


def _fix_plan_orders(symbol: str, signal: str, size: float, tp_price: float, sl_price: float) -> None:
    """V3.2.6: After order placement, cancel stale preset plan orders and re-place TP/SL
    as fresh explicit trigger orders. Prevents SL price drift from orphan interference
    with WEEX preset processing (recurring DOGE SL mismatch bug).
    """
    try:
        time.sleep(2)  # Let WEEX register the new position before cancelling
        cancel_all_orders_for_symbol(symbol)
        time.sleep(1)  # Let cancellations settle

        close_type = "3" if signal == "LONG" else "4"
        ep = '/capi/v2/order/plan_order'

        for label, price, oid_prefix in [("SL", sl_price, "sl"), ("TP", tp_price, "tp")]:
            body = json.dumps({
                'symbol': symbol,
                'client_oid': f'smt_{oid_prefix}_{int(time.time()*1000)}',
                'size': str(int(size)),
                'type': close_type,
                'match_type': '1',
                'execute_price': '0',
                'trigger_price': str(round_price_to_tick(price, symbol))
            })
            r = requests.post(f"{WEEX_BASE_URL}{ep}",
                              headers=weex_headers('POST', ep, body),
                              data=body, timeout=10)
            resp = r.json() if r.status_code == 200 else r.text
            ok = resp.get('code') == '00000' if isinstance(resp, dict) else False
            print(f"  [PLAN-FIX] {symbol.replace('cmt_','').upper()} {label} @ ${price:.4f}: {'OK' if ok else resp}")
            time.sleep(0.5)
    except Exception as e:
        print(f"  [PLAN-FIX] Warning: plan re-placement failed for {symbol}: {e}")


def close_position_manually(symbol: str, side: str, size: float) -> Dict:
    # V3.1.83: ALWAYS cancel trigger orders (TP/SL) BEFORE closing position.
    # Without this, orphan triggers persist on WEEX after close, causing:
    #   1. Leverage set failures on next trade ("open orders" blocking)
    #   2. Old SL triggers executing at wrong prices on new positions
    #   3. Ghost TP/SL from previous trades interfering with current ones
    try:
        cleanup = cancel_all_orders_for_symbol(symbol)
        if cleanup.get("cancelled"):
            print(f"  [CLOSE] Cancelled {len(cleanup['cancelled'])} orphan orders for {symbol} before closing")
    except Exception as e:
        print(f"  [CLOSE] Warning: trigger cleanup failed for {symbol}: {e}")
    close_type = "3" if side == "LONG" else "4"
    return place_order(symbol, close_type, size)


def execute_runner_partial_close(symbol: str, side: str, current_size: float, 
                                  entry_price: float, current_price: float) -> Dict:
    """
    Execute Runner Logic: Close 50% of position, move SL to breakeven
    
    V3.1.5 FIX: Cancel old TP/SL orders and place new ones with remaining size
    V3.1.18 FIX: If position too small to split, close 100% instead
    
    Returns: {"executed": True/False, "closed_size": X, "remaining_size": X, ...}
    """
    tier = get_tier_for_symbol(symbol)
    runner_config = get_runner_config(tier)
    tier_config = get_tier_config(tier)
    
    if not runner_config.get("enabled"):
        return {"executed": False, "reason": "Runner not enabled for this tier"}
    
    # Calculate close size (50% of position)
    close_pct = runner_config.get("close_pct", 50) / 100
    close_size = round_size_to_step(current_size * close_pct, symbol)
    remaining_size = round_size_to_step(current_size - close_size, symbol)
    
    # V3.1.18: If close_size is 0 but position exists, close 100% instead
    if close_size <= 0:
        close_size = round_size_to_step(current_size, symbol)
        remaining_size = 0
        if close_size <= 0:
            return {"executed": False, "reason": "Position size too small to close"}
        print(f"  [RUNNER] Position too small to split, closing 100% instead")
    
    # Close at market
    close_type = "3" if side == "LONG" else "4"  # Close long = 3, Close short = 4
    
    print(f"  [RUNNER] Closing {symbol}: {close_size} units (remaining: {remaining_size})")
    print(f"  [RUNNER] Entry: ${entry_price:.4f}, Current: ${current_price:.4f}")
    
    # Place partial close order (NO TP/SL on close order)
    close_result = place_order(symbol, close_type, close_size, tp_price=None, sl_price=None)
    
    if not close_result.get("order_id"):
        return {"executed": False, "reason": f"Close order failed: {close_result}"}
    
    # Calculate profit locked
    if side == "LONG":
        profit_per_unit = current_price - entry_price
    else:
        profit_per_unit = entry_price - current_price
    
    profit_locked = profit_per_unit * close_size
    
    # Upload AI log for runner
    upload_ai_log_to_weex(
        stage=f"Runner: Partial Close {symbol.replace('cmt_', '').upper()}",
        input_data={
            "symbol": symbol,
            "side": side,
            "original_size": current_size,
            "close_size": close_size,
            "remaining_size": remaining_size,
            "entry_price": entry_price,
            "current_price": current_price,
        },
        output_data={
            "profit_locked": profit_locked,
            "close_order_id": close_result.get("order_id"),
        },
        explanation=f"Runner triggered at +{((current_price/entry_price - 1) * 100):.1f}%. Closed {close_pct*100:.0f}% ({close_size} units), locked ${profit_locked:.2f} profit. Remaining {remaining_size} units running free."
    )
    
    # V3.1.5: Cancel old TP/SL orders and place new ones for remaining size
    try:
        # Cancel existing plan orders for this symbol
        plan_endpoint = f'/capi/v2/order/currentPlan?symbol={symbol}'
        r = requests.get(f"{WEEX_BASE_URL}{plan_endpoint}", headers=weex_headers("GET", plan_endpoint), timeout=10)
        old_orders = r.json() if isinstance(r.json(), list) else []
        
        for order in old_orders:
            order_id = order.get('order_id')
            if order_id:
                cancel_endpoint = '/capi/v2/order/cancel_plan'
                cancel_body = json.dumps({'orderId': str(order_id)})
                requests.post(f"{WEEX_BASE_URL}{cancel_endpoint}",
                            headers=weex_headers('POST', cancel_endpoint, cancel_body),
                            data=cancel_body, timeout=10)
                print(f"  [RUNNER] Cancelled old order {order_id}")
        
        # Place new SL order at breakeven (entry price) for remaining size
        if runner_config.get("move_sl_to_entry") and remaining_size > 0:
            new_sl_price = entry_price
            plan_order_endpoint = '/capi/v2/order/plan_order'
            sl_body = json.dumps({
                'symbol': symbol,
                'client_oid': f'smt_runner_sl_{int(time.time()*1000)}',
                'size': str(remaining_size),
                'type': close_type,
                'match_type': '1',
                'execute_price': '0',
                'trigger_price': str(round_price_to_tick(new_sl_price, symbol))
            })
            sl_r = requests.post(f"{WEEX_BASE_URL}{plan_order_endpoint}",
                               headers=weex_headers('POST', plan_order_endpoint, sl_body),
                               data=sl_body, timeout=10)
            print(f"  [RUNNER] New SL at breakeven ${new_sl_price:.2f}: {sl_r.status_code}")
        
        # Place new TP order for remaining size (let it run to full TP)
        if remaining_size > 0:
            tp_pct = tier_config["tp_pct"] / 100
            if side == "LONG":
                new_tp_price = entry_price * (1 + tp_pct)
            else:
                new_tp_price = entry_price * (1 - tp_pct)
            
            tp_body = json.dumps({
                'symbol': symbol,
                'client_oid': f'smt_runner_tp_{int(time.time()*1000)}',
                'size': str(remaining_size),
                'type': close_type,
                'match_type': '1',
                'execute_price': '0',
                'trigger_price': str(round_price_to_tick(new_tp_price, symbol))
            })
            tp_r = requests.post(f"{WEEX_BASE_URL}{plan_order_endpoint}",
                               headers=weex_headers('POST', plan_order_endpoint, tp_body),
                               data=tp_body, timeout=10)
            print(f"  [RUNNER] New TP at ${new_tp_price:.2f}: {tp_r.status_code}")
            
    except Exception as e:
        print(f"  [RUNNER] Warning: Could not update TP/SL orders: {e}")
    
    return {
        "executed": True,
        "closed_size": close_size,
        "remaining_size": remaining_size,
        "profit_locked": profit_locked,
        "close_order_id": close_result.get("order_id"),
        "new_sl_price": entry_price if runner_config.get("move_sl_to_entry") else None,
    }


def save_local_log(log_data: Dict, timestamp: str):
    os.makedirs("logs", exist_ok=True)
    filename = f"logs/v3_1_1_{timestamp}.json"
    with open(filename, 'w') as f:
        json.dump(log_data, f, indent=2, default=str)
    pass  # Saved local log silently


# ============================================================
# MAIN (for testing)
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("SMT V3.1.77 - Multi-Persona Trading")
    print("=" * 60)
    
    print("\nTier Configuration:")
    for tier, config in TIER_CONFIG.items():
        pairs = [p for p, info in TRADING_PAIRS.items() if info["tier"] == tier]
        print(f"  Tier {tier} ({config['name']}): {', '.join(pairs)}")
        print(f"    TP: {config['tp_pct']}%, SL: {config['sl_pct']}%, Max Hold: {config['max_hold_hours']}h")
    
    balance = get_balance()
    positions = get_open_positions()
    competition = get_competition_status(balance)
    
    print(f"\nBalance: {balance:.2f} USDT")
    print(f"Positions: {len(positions)}")
    print(f"Phase: {competition['phase']}, Days left: {competition['days_left']}")
    
    # Test analysis on one pair from each tier
    analyzer = MultiPersonaAnalyzer()
    
    for pair in ["BTC", "SOL", "DOGE"]:
        print(f"\n{'='*60}")
        result = analyzer.analyze(pair, TRADING_PAIRS[pair], balance, competition, positions)
        print(f"\nFINAL for {pair}: {result['decision']} ({result.get('confidence', 0):.0%})")