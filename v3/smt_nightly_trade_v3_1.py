"""
SMT Nightly Trade V3.1.17 - TAKER VOLUME IS KING
=============================================================
Enhanced trading with advanced flow analysis.

V3.1.17 Changes (CRITICAL FLOW FIX):
- FLOW PERSONA OVERHAUL: Taker volume (ACTION) beats depth (INTENTION)
  * Taker ratio < 0.3 = EXTREME SELL, ignore all bid depth (spoofing)
  * Taker ratio < 0.5 = HEAVY SELL, taker 3x weight of depth
  * Bid depth in bear market is often FAKE (spoofing/exit liquidity)
- NEW: ALTCOIN MOMENTUM factor in regime detection
  * If avg altcoin change < -4% = BEARISH regardless of BTC
  * If avg altcoin change < -2% = score -2
  * Catches "BTC flat but alts bleeding" scenarios
- Regime now has 7 factors: BTC, 4h, F&G, Funding, OI, ATR, Altcoins

V3.1.16 Changes:
- Open Interest (OI) Sensor
- ATR-based Volatility Position Sizing
- Lower confidence threshold (85% -> 60%)
- Add weak_bearish mode for slight negative BTC

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
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

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


def get_enhanced_market_regime() -> dict:
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
    
    # ===== Factor 3: Fear & Greed (CONTRARIAN) =====
    fg = get_fear_greed_index()
    result["fear_greed"] = fg["value"]
    
    if fg["error"] is None:
        if fg["value"] <= 20: score += 2; factors.append(f"EXTREME FEAR ({fg['value']}): contrarian BUY")
        elif fg["value"] <= 35: score += 1; factors.append(f"Fear ({fg['value']})")
        elif fg["value"] >= 80: score -= 2; factors.append(f"EXTREME GREED ({fg['value']}): contrarian SELL")
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
    
    if score <= -3: result["regime"] = "BEARISH"; result["confidence"] = 0.85
    elif score <= -1: result["regime"] = "BEARISH"; result["confidence"] = 0.65
    elif score >= 3: result["regime"] = "BULLISH"; result["confidence"] = 0.85
    elif score >= 1: result["regime"] = "BULLISH"; result["confidence"] = 0.65
    else: result["regime"] = "NEUTRAL"; result["confidence"] = 0.5
    
    print(f"  [REGIME] {result['regime']} (score: {score}, conf: {result['confidence']:.0%})")
    print(f"  [REGIME] BTC 24h: {result['btc_24h']:+.1f}% | F&G: {result['fear_greed']} | Funding: {result['avg_funding']:.5f}")
    print(f"  [REGIME] OI Signal: {oi_signal['signal']} | Volatility: {atr_data['volatility']} | Alts: {result.get('altcoin_avg', 0):+.1f}%")
    for f in factors[:6]:
        print(f"  [REGIME]   > {f}")
    
    return result


# Competition
COMPETITION_START = datetime(2026, 1, 12, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 2, tzinfo=timezone.utc)
STARTING_BALANCE = 1000.0
FLOOR_BALANCE = 950.0  # Protect principal - stop trading below this

# Trading Parameters - V3.1.16 UPDATES
MAX_LEVERAGE = 20
MAX_OPEN_POSITIONS = 5  # V3.1.16: Reduced for focused positions
MAX_SINGLE_POSITION_PCT = 0.20  # V3.1.9: 20% per trade max (was 8%)
MIN_SINGLE_POSITION_PCT = 0.10  # V3.1.9: 10% minimum (was 3%)
MIN_CONFIDENCE_TO_TRADE = 0.60  # V3.1.16: Lowered from 0.85 - was blocking all trades!

# ============================================================
# V3.1.4: TIER-BASED PARAMETERS (UPDATED!)
# ============================================================
# Tier 1: Stable (BTC, ETH, BNB, LTC) - slow grind, need room to breathe
# Tier 2: Mid volatility (SOL) - volatile but not meme-tier
# Tier 3: Fast/Meme (DOGE, XRP, ADA) - WIDENED SL to stop whipsaw losses

TIER_CONFIG = {
    1: {  # BTC, ETH, BNB, LTC - Stable, slow movers
        "name": "STABLE",
        "tp_pct": 5.0,           # V3.1.9: Increased from 4% - let winners run
        "sl_pct": 2.0,           # Stop loss at 2%
        "max_hold_hours": 72,    # V3.1.9: Extended to 72h (3 days) like Team 2
        "early_exit_hours": 12,  # V3.1.9: Check early exit after 12h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 12h
        "force_exit_loss_pct": -4.0,  # Hard stop at -4%
    },
    2: {  # SOL - Mid volatility
        "name": "MID",
        "tp_pct": 4.0,           # V3.1.9: Increased from 3%
        "sl_pct": 1.75,          # Stop loss at 1.75%
        "max_hold_hours": 48,    # V3.1.9: Extended to 48h (2 days)
        "early_exit_hours": 6,   # Check early exit after 6h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h
        "force_exit_loss_pct": -4.0,  # Hard stop at -4%
    },
    3: {  # DOGE, XRP, ADA - V3.1.9: Extended hold for winners
        "name": "FAST",
        "tp_pct": 4.0,           # V3.1.9: Increased from 3%
        "sl_pct": 2.0,           # Keep at 2%
        "max_hold_hours": 24,    # V3.1.9: Extended to 24h
        "early_exit_hours": 6,   # Check early exit after 6h
        "early_exit_loss_pct": -1.5,  # Exit if -1.5% after 6h
        "force_exit_loss_pct": -4.0,  # Hard stop at -4%
    },
}

# Trading Pairs with correct tiers
TRADING_PAIRS = {
    "BTC": {"symbol": "cmt_btcusdt", "tier": 1, "has_whale_data": True},
    "ETH": {"symbol": "cmt_ethusdt", "tier": 1, "has_whale_data": True},
    "BNB": {"symbol": "cmt_bnbusdt", "tier": 1, "has_whale_data": False},
    "LTC": {"symbol": "cmt_ltcusdt", "tier": 1, "has_whale_data": False},
    "SOL": {"symbol": "cmt_solusdt", "tier": 2, "has_whale_data": False},
    "DOGE": {"symbol": "cmt_dogeusdt", "tier": 3, "has_whale_data": False},
    "XRP": {"symbol": "cmt_xrpusdt", "tier": 3, "has_whale_data": False},
    "ADA": {"symbol": "cmt_adausdt", "tier": 3, "has_whale_data": False},
}

# Pipeline Version
PIPELINE_VERSION = "SMT-v3.1.7-MultiPersonaAgreement"
MODEL_NAME = "CatBoost-Gemini-MultiPersona-v3.1.7"

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
    return TIER_CONFIG.get(tier, TIER_CONFIG[2])


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
    Analyzes on-chain whale behavior for ETH/BTC signals.
    Tracks large wallet movements via Etherscan API.
    """
    
    def __init__(self):
        self.name = "WHALE"
        self.weight = 2.0
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        """Analyze whale activity for trading signal"""
        
        if pair not in ("ETH", "BTC"):
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"No whale data for {pair}",
            }
        
        try:
            # Analyze top whales
            total_inflow = 0
            total_outflow = 0
            whale_signals = []
            whales_analyzed = 0
            
            for whale in TOP_WHALES[:5]:  # Check top 5 whales
                try:
                    flow = self._analyze_whale_flow(whale["address"], whale["label"])
                    if flow:
                        whales_analyzed += 1
                        total_inflow += flow["inflow"]
                        total_outflow += flow["outflow"]
                        
                        if flow["net"] > 100:  # Significant inflow (>100 ETH)
                            whale_signals.append(f"{whale['label']}: +{flow['net']:.0f} ETH")
                        elif flow["net"] < -100:  # Significant outflow
                            whale_signals.append(f"{whale['label']}: {flow['net']:.0f} ETH")
                    
                    time.sleep(0.25)  # Rate limit Etherscan
                except Exception as e:
                    print(f"  [WHALE] Error analyzing {whale['label']}: {e}")
                    continue
            
            if whales_analyzed == 0:
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.3,
                    "reasoning": "Could not fetch whale data from Etherscan",
                }
            
            net_flow = total_inflow - total_outflow
            
            # Determine signal based on net flow
            if net_flow > 500:  # Strong accumulation (>500 ETH net inflow)
                signal = "LONG"
                confidence = min(0.85, 0.5 + (net_flow / 5000))
                reasoning = f"Whale accumulation: +{net_flow:.0f} ETH net inflow"
            elif net_flow < -500:  # Strong distribution (>500 ETH net outflow)
                signal = "SHORT"
                confidence = min(0.85, 0.5 + (abs(net_flow) / 5000))
                reasoning = f"Whale distribution: {net_flow:.0f} ETH net outflow"
            elif net_flow > 100:  # Mild accumulation
                signal = "LONG"
                confidence = 0.55
                reasoning = f"Mild whale accumulation: +{net_flow:.0f} ETH"
            elif net_flow < -100:  # Mild distribution
                signal = "SHORT"
                confidence = 0.55
                reasoning = f"Mild whale distribution: {net_flow:.0f} ETH"
            else:
                signal = "NEUTRAL"
                confidence = 0.4
                reasoning = f"Whale activity balanced: {net_flow:+.0f} ETH"
            
            if whale_signals:
                reasoning += f" | {'; '.join(whale_signals[:3])}"
            
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
                },
            }
            
        except Exception as e:
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": f"Whale analysis error: {str(e)}",
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
# PERSONA 2: MARKET SENTIMENT (Gemini)
# ============================================================

class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment."""
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5  # V3.1.7: Reduced from 2.0
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            client = genai.Client()
            
            # V3.1.11: Reality Check prompt - focus on SHORT-TERM price action, not "moon" news
            search_query = f"{pair} cryptocurrency price action last 24 hours breaking support resistance selling pressure"
            
            grounding_config = GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())],
                temperature=0.3
            )
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=search_query,
                config=grounding_config
            )
            
            market_context = response.text[:1500] if response.text else ""
            
            # V3.1.11: Reality Check - ignore long-term hopium, focus on 4-24h trading window
            sentiment_prompt = f"""You are a SHORT-TERM crypto trader making a 4-24 hour trade decision for {pair}.

IGNORE: Long-term "moon" predictions, "institutional adoption", "ETF hopes", price targets for next year.
FOCUS ON: Last 24 hours price action, support/resistance breaks, volume on red vs green candles, liquidation data.

Market Context:
{market_context}

Based ONLY on short-term price action and momentum:
- If price is breaking DOWN through support or volume is spiking on RED candles = BEARISH
- If price is breaking UP through resistance or volume is spiking on GREEN candles = BULLISH  
- If choppy/sideways with no clear direction = NEUTRAL

Respond with JSON only:
{{"sentiment": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 0.0-1.0, "key_factor": "short-term reason only"}}"""
            
            json_config = GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json"
            )
            
            result = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=sentiment_prompt,
                config=json_config
            )
            
            data = json.loads(result.text)
            
            signal = "LONG" if data["sentiment"] == "BULLISH" else "SHORT" if data["sentiment"] == "BEARISH" else "NEUTRAL"
            
            return {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:800],
            }
            
        except Exception as e:
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.3,
                "reasoning": f"Sentiment analysis error: {str(e)}",
            }


# ============================================================
# PERSONA 3: ORDER FLOW
# ============================================================

class FlowPersona:
    """Analyzes order flow (taker ratio, depth).
    
    V3.1.17: CRITICAL FIX - Taker volume (ACTION) beats depth (INTENTION)
    In bear markets, big bids are often spoofing/exit liquidity.
    When taker ratio < 0.5, IGNORE bid depth completely.
    """
    
    def __init__(self):
        self.name = "FLOW"
        self.weight = 1.0
    
    def analyze(self, pair: str, pair_info: Dict) -> Dict:
        symbol = pair_info["symbol"]
        
        try:
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
                
                if depth["bid_strength"] > 1.3:
                    signals.append(("LONG", 0.4, "Strong bid depth"))
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
            
            if long_score > short_score and long_score > 0.4:
                return {
                    "persona": self.name,
                    "signal": "LONG",
                    "confidence": min(0.85, long_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "LONG"),
                }
            elif short_score > long_score and short_score > 0.4:
                return {
                    "persona": self.name,
                    "signal": "SHORT",
                    "confidence": min(0.85, short_score),
                    "reasoning": "; ".join(s[2] for s in signals if s[0] == "SHORT"),
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
            # WEEX uses limit=15 or limit=200
            url = f"{WEEX_BASE_URL}/capi/v2/market/depth?symbol={symbol}&limit=15"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            # WEEX format: [[price, quantity], ...]
            bid_volume = sum(float(b[1]) for b in bids[:10]) if bids else 0
            ask_volume = sum(float(a[1]) for a in asks[:10]) if asks else 0
            
            ratio = bid_volume / ask_volume if ask_volume > 0 else 1.0
            
            print(f"  [FLOW] Depth - Bids: {bid_volume:.2f}, Asks: {ask_volume:.2f}, Ratio: {ratio:.2f}")
            
            return {
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
                "bid_strength": ratio,
                "ask_strength": 1/ratio if ratio > 0 else 1.0,
            }
        except Exception as e:
            print(f"  [FLOW] Depth error: {e}")
            return {"bid_strength": 1.0, "ask_strength": 1.0}
    
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
        """Make final trading decision with TIER-BASED TP/SL and MARKET TREND FILTER"""
        
        # Get tier config for this pair
        tier_config = get_tier_config_for_pair(pair)
        tier = get_tier_for_pair(pair)
        
        # Calculate weighted votes
        long_score = 0
        short_score = 0
        neutral_score = 0
        
        vote_summary = []
        
        # V3.1.7: Count raw votes for agreement check
        long_votes = 0
        short_votes = 0
        
        # V3.1.11: Get regime FIRST for dynamic weight adjustment
        regime = get_enhanced_market_regime()
        
        # V3.1.16: More sensitive bearish detection
        # Even slight negative = "weak bearish" which favors SHORT signals
        is_bearish = regime["regime"] == "BEARISH" or regime.get("btc_24h", 0) < -0.3
        is_weak_bearish = regime.get("btc_24h", 0) < 0 and not is_bearish  # Between -0.3% and 0%
        is_bullish = regime["regime"] == "BULLISH" or regime.get("btc_24h", 0) > 1.0
        
        for vote in persona_votes:
            persona = vote["persona"]
            signal = vote["signal"]
            confidence = vote["confidence"]
            
            # V3.1.15: AGGRESSIVE BEARISH WEIGHTS - Whale/Sentiment "buying the dip" is toxic
            # In bearish markets, FLOW shows actual selling, TECHNICAL shows momentum
            if is_bearish:
                weights = {
                    "WHALE": 0.5,      # V3.1.15: Whales accumulating = catching falling knife
                    "SENTIMENT": 1.5,  # V3.1.16: Sentiment SHORT signals are valid in downtrend
                    "FLOW": 2.5,       # V3.1.15: TRUST actual order flow/selling pressure
                    "TECHNICAL": 2.0   # V3.1.15: TRUST RSI/momentum in trends
                }
            elif is_weak_bearish:
                # V3.1.16: Slight negative = still favor SHORT signals
                weights = {
                    "WHALE": 0.8,      # Reduce whale (accumulation noise)
                    "SENTIMENT": 1.5,  # Trust sentiment SHORT signals
                    "FLOW": 1.8,       # Trust flow
                    "TECHNICAL": 1.8   # Trust technicals
                }
            elif is_bullish:
                weights = {
                    "WHALE": 2.0,      # Whales lead the way up
                    "SENTIMENT": 1.5,  # News drives FOMO
                    "FLOW": 1.0,       # Normal
                    "TECHNICAL": 1.2   # Normal
                }
            else:  # NEUTRAL
                weights = {
                    "WHALE": 1.5,      # Slightly reduced
                    "SENTIMENT": 1.0,  # Reduced - noise in chop
                    "FLOW": 1.5,       # Increased - flow matters in chop
                    "TECHNICAL": 1.5   # Increased - technicals guide in ranges
                }
            
            weight = weights.get(persona, 1.0)
            
            weighted_conf = confidence * weight
            
            if signal == "LONG":
                long_score += weighted_conf
                long_votes += 1
            elif signal == "SHORT":
                short_score += weighted_conf
                short_votes += 1
            else:
                neutral_score += weighted_conf
            
            vote_summary.append(f"{persona}={signal}({confidence:.0%})x{weight:.1f}")
        
        total = long_score + short_score + neutral_score
        
        if total == 0:
            return self._wait_decision("No valid persona votes", persona_votes, vote_summary)
        
        long_pct = long_score / total
        short_pct = short_score / total
        
        # V3.1.16: LOWERED THRESHOLDS - previous settings blocked all trades
        num_votes = len(persona_votes)
        threshold = 0.35 if num_votes <= 3 else 0.38  # LOWERED from 0.40/0.45
        ratio_req = 1.1 if num_votes <= 3 else 1.15   # LOWERED from 1.2/1.25
        
        if long_pct > threshold and long_score > short_score * ratio_req:
            decision = "LONG"
            confidence = min(0.90, long_pct)
            agreeing_votes = long_votes
            opposing_votes = short_votes
        elif short_pct > threshold and short_score > long_score * ratio_req:
            decision = "SHORT"
            confidence = min(0.90, short_pct)
            agreeing_votes = short_votes
            opposing_votes = long_votes
        else:
            return self._wait_decision(f"No consensus: LONG={long_pct:.0%}, SHORT={short_pct:.0%}", persona_votes, vote_summary)
        
        # V3.1.16: Relaxed Tier 3 requirements (was blocking too many trades)
        if tier == 3:
            # Only block if there's strong opposition (2+ at high confidence)
            if opposing_votes >= 2 and agreeing_votes < 2:
                return self._wait_decision(f"Tier 3 blocked: {opposing_votes} personas oppose {decision}", persona_votes, vote_summary)
        
        # V3.1.15: Regime-aware confidence thresholds
        # In bearish market, LOWER threshold for SHORTs (lean into trend)
        # In bullish market, LOWER threshold for LONGs
        min_confidence = MIN_CONFIDENCE_TO_TRADE
        if tier == 3:
            min_confidence = 0.65  # V3.1.16: Lowered from 0.70
        
        # V3.1.16: LEAN INTO THE TREND
        if (is_bearish or is_weak_bearish) and decision == "SHORT":
            min_confidence = 0.50  # V3.1.16: Lower threshold - market wants to go down
            print(f"  [JUDGE] BEARISH/WEAK regime: Lowered SHORT threshold to 50%")
        elif is_bullish and decision == "LONG":
            min_confidence = 0.50  # Lower threshold - market wants to go up
            print(f"  [JUDGE] BULLISH regime: Lowered LONG threshold to 50%")
        
        if confidence < min_confidence:
            return self._wait_decision(f"Confidence too low: {confidence:.0%} (Tier {tier} needs {min_confidence:.0%})", persona_votes, vote_summary)
        
        # V3.1.8: STRICTER MARKET TREND FILTER - Don't fight the trend!
        # Now applies to ALL pairs including BTC
        # V3.1.11: regime already fetched above for dynamic weights
        
        # Block LONGs in bearish regime (ANY negative 24h = bearish for safety)
        if decision == "LONG":
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            # V3.1.8: Also block if 24h is negative even if not "BEARISH" threshold
            if regime.get("btc_24h", 0) < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
            
            # V3.1.16: Block LONGs if OI shows short buildup (futures market truth)
            if regime.get("oi_signal") == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG rejected by OI sensor - {regime.get('oi_reason', 'short buildup')[:60]}", persona_votes, vote_summary)
            
            # V3.1.10: Block new LONGs if existing LONGs bleeding
            if hasattr(self, '_open_positions') and self._open_positions:
                total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in self._open_positions if p.get('side') == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
                total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in self._open_positions if p.get('side') == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
                
                if total_long_loss > 15:
                    return self._wait_decision(f"BLOCKED: Existing LONGs losing ${total_long_loss:.1f}", persona_votes, vote_summary)
                if total_short_gain > 20 and total_long_loss > 8:
                    return self._wait_decision(f"BLOCKED: SHORTs +${total_short_gain:.1f} outperforming LONGs -${total_long_loss:.1f}", persona_votes, vote_summary)
        
        # V3.1.15: Only block SHORTs in STRONG bullish (>2% up)
        # In bearish/neutral, allow SHORTs to profit from downtrend
        if decision == "SHORT" and regime["regime"] == "BULLISH" and regime.get("btc_24h", 0) > 2.0:
            return self._wait_decision(f"BLOCKED: SHORT in STRONG BULLISH regime (24h: {regime.get('btc_24h', 0):+.1f}%)", persona_votes, vote_summary)
        
        # V3.1.16: VOLATILITY-BASED POSITION SIZING
        # In high volatility, reduce position size to survive whipsaws
        volatility_multiplier = regime.get("size_multiplier", 1.0)
        volatility_regime = regime.get("volatility", "NORMAL")
        
        # V3.1.9: Increased position sizing (was 7%)
        base_size = balance * 0.15
        
        if confidence > 0.80:
            position_usdt = base_size * 1.3  # 19.5%
        elif confidence > 0.70:
            position_usdt = base_size * 1.15  # 17.25%
        elif confidence > 0.60:
            position_usdt = base_size * 1.0  # 15%
        else:
            position_usdt = base_size * 0.85  # 12.75%
        
        # V3.1.16: Apply volatility adjustment
        position_usdt = position_usdt * volatility_multiplier
        
        if volatility_multiplier < 1.0:
            print(f"  [JUDGE] Volatility adjustment: {volatility_regime} -> size x{volatility_multiplier:.1f}")
        
        position_usdt = max(position_usdt, balance * MIN_SINGLE_POSITION_PCT)
        position_usdt = min(position_usdt, balance * MAX_SINGLE_POSITION_PCT)
        
        # V3.1.4: TIER-BASED TP/SL
        tp_pct = tier_config["tp_pct"]
        sl_pct = tier_config["sl_pct"]
        max_hold = tier_config["max_hold_hours"]
        
        return {
            "decision": decision,
            "confidence": confidence,
            "recommended_position_usdt": position_usdt,
            "take_profit_percent": tp_pct,
            "stop_loss_percent": sl_pct,
            "hold_time_hours": max_hold,
            "tier": tier,
            "tier_name": tier_config["name"],
            "reasoning": f"Judge: {decision} @ {confidence:.0%}. Tier {tier} ({tier_config['name']}): TP {tp_pct}%, SL {sl_pct}%, Hold {max_hold}h. Votes: {', '.join(vote_summary)}",
            "persona_votes": persona_votes,
            "vote_breakdown": {
                "long_score": round(long_score, 2),
                "short_score": round(short_score, 2),
                "neutral_score": round(neutral_score, 2),
            },
        }
    
    def _wait_decision(self, reason: str, persona_votes: List[Dict] = None, vote_summary: List[str] = None) -> Dict:
        """Return WAIT decision with full vote details for logging"""
        votes_str = ', '.join(vote_summary) if vote_summary else "No votes"
        return {
            "decision": "WAIT",
            "confidence": 0.0,
            "reasoning": f"{reason}. Votes: {votes_str}",
            "persona_votes": persona_votes or [],
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
        
        # 1. Whale Persona (ONLY for ETH/BTC)
        if pair in ("ETH", "BTC"):
            print(f"  [WHALE] Analyzing...")
            whale_vote = self.whale.analyze(pair, pair_info)
            votes.append(whale_vote)
            print(f"  [WHALE] {whale_vote['signal']} ({whale_vote['confidence']:.0%}): {whale_vote['reasoning']}")
        else:
            print(f"  [WHALE] Skipped (no whale data for {pair})")
        
        # 2. Sentiment Persona
        print(f"  [SENTIMENT] Analyzing...")
        sentiment_vote = self.sentiment.analyze(pair, pair_info, competition_status)
        votes.append(sentiment_vote)
        print(f"  [SENTIMENT] {sentiment_vote['signal']} ({sentiment_vote['confidence']:.0%}): {sentiment_vote['reasoning']}")
        
        # 3. Flow Persona
        print(f"  [FLOW] Analyzing...")
        flow_vote = self.flow.analyze(pair, pair_info)
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
    body = json.dumps({"symbol": symbol, "leverage": leverage})
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()


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
            # SUCCESS
            order_str = f" (order: {order_id})" if order_id else ""
            print(f"  [AI LOG OK] {stage}{order_str}", flush=True)
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
    
    position_usdt = decision.get("recommended_position_usdt", balance * 0.07)
    position_usdt = max(position_usdt, balance * MIN_SINGLE_POSITION_PCT)
    position_usdt = min(position_usdt, balance * MAX_SINGLE_POSITION_PCT)
    
    notional_usdt = position_usdt * MAX_LEVERAGE
    raw_size = notional_usdt / current_price
    
    if raw_size <= 0:
        return {"executed": False, "reason": f"Invalid size: {raw_size}"}
    
    size = round_size_to_step(raw_size, symbol)
    
    if size <= 0:
        return {"executed": False, "reason": f"Size too small: {size}"}
    
    order_type = "1" if signal == "LONG" else "2"
    
    # V3.1.1: Use tier-based TP/SL from decision (set by Judge)
    tp_pct = decision.get("take_profit_percent", tier_config["tp_pct"]) / 100
    sl_pct = decision.get("stop_loss_percent", tier_config["sl_pct"]) / 100
    
    if signal == "LONG":
        tp_price = current_price * (1 + tp_pct)
        sl_price = current_price * (1 - sl_pct)
    else:
        tp_price = current_price * (1 - tp_pct)
        sl_price = current_price * (1 + sl_pct)
    
    set_leverage(symbol, MAX_LEVERAGE)
    
    print(f"  [TRADE] {signal} {symbol}: {size} @ ${current_price:.4f}")
    print(f"  [TRADE] Tier {tier} ({tier_config['name']}): TP ${tp_price:.4f} ({tp_pct*100:.1f}%), SL ${sl_price:.4f} ({sl_pct*100:.1f}%)")
    
    result = place_order(symbol, order_type, size, tp_price, sl_price)
    
    order_id = result.get("order_id")
    
    if not order_id:
        return {"executed": False, "reason": f"Order failed: {result}"}
    
    # Upload AI log
    upload_ai_log_to_weex(
        stage=f"V3.1.4 Trade: {signal} {symbol.replace('cmt_', '').upper()}",
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

# Cooldown periods by tier (prevents revenge trading)
COOLDOWN_HOURS = {
    1: 6,   # Tier 1 (BTC, ETH, BNB, LTC): 6 hour cooldown
    2: 8,   # Tier 2 (SOL): 8 hour cooldown  
    3: 12,  # Tier 3 (DOGE, XRP, ADA): 12 hour cooldown (2x max hold time)
}

# ============================================================
# V3.1.2: RUNNER LOGIC CONFIGURATION
# ============================================================
# When position hits 50% of TP, close half and let rest run
# Only for Tier 1 and Tier 2 - Tier 3 is scalp only

RUNNER_CONFIG = {
    1: {  # BTC, ETH, BNB, LTC
        "enabled": True,
        "trigger_pct": 2.5,      # V3.1.9: Trigger at +2.5% (50% of 5% TP)
        "close_pct": 50,         # Close 50% of position
        "move_sl_to_entry": True,  # Move SL to breakeven
        "remove_tp": True,       # Let remaining 50% run
    },
    2: {  # SOL
        "enabled": True,
        "trigger_pct": 2.0,      # V3.1.9: Trigger at +2% (50% of 4% TP)
        "close_pct": 50,
        "move_sl_to_entry": True,
        "remove_tp": True,
    },
    3: {  # DOGE, XRP, ADA - V3.1.9: ENABLED runners
        "enabled": True,
        "trigger_pct": 2.0,      # Trigger at +2% (50% of 4% TP)
        "close_pct": 50,
        "move_sl_to_entry": True,
        "remove_tp": True,
    },
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
        self.load_state()
    
    def load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.active_trades = data.get("active", {})
                    self.closed_trades = data.get("closed", [])
                    self.cooldowns = data.get("cooldowns", {})
        except:
            pass
    
    def save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump({
                "active": self.active_trades, 
                "closed": self.closed_trades,
                "cooldowns": self.cooldowns
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
            
            # Add cooldown if closed at a loss or force-exited
            if pnl_pct < 0 or "early_exit" in reason or "force_stop" in reason or "max_hold" in reason:
                tier = trade.get("tier", 2)
                cooldown_hours = COOLDOWN_HOURS.get(tier, 12)
                cooldown_until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                self.cooldowns[symbol] = cooldown_until.isoformat()
                print(f"  [COOLDOWN] {symbol} on cooldown for {cooldown_hours}h until {cooldown_until.strftime('%Y-%m-%d %H:%M UTC')}")
            
            self.save_state()
    
    def is_on_cooldown(self, symbol: str) -> bool:
        """Check if a symbol is on cooldown (recently closed at loss)"""
        if symbol not in self.cooldowns:
            return False
        
        try:
            cooldown_until = datetime.fromisoformat(self.cooldowns[symbol].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < cooldown_until:
                remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
                print(f"  [COOLDOWN] {symbol} still on cooldown ({remaining:.1f}h remaining)")
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
    
    def get_active_symbols(self) -> List[str]:
        return list(self.active_trades.keys())
    
    def get_active_trade(self, symbol: str) -> Optional[Dict]:
        return self.active_trades.get(symbol)


# ============================================================
# POSITION MANAGEMENT
# ============================================================

def check_position_status(symbol: str) -> Dict:
    positions = get_open_positions()
    for pos in positions:
        if pos["symbol"] == symbol:
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


def close_position_manually(symbol: str, side: str, size: float) -> Dict:
    close_type = "3" if side == "LONG" else "4"
    return place_order(symbol, close_type, size)


def execute_runner_partial_close(symbol: str, side: str, current_size: float, 
                                  entry_price: float, current_price: float) -> Dict:
    """
    Execute Runner Logic: Close 50% of position, move SL to breakeven
    
    V3.1.5 FIX: Cancel old TP/SL orders and place new ones with remaining size
    
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
    
    if close_size <= 0:
        return {"executed": False, "reason": "Close size too small"}
    
    # Close 50% at market
    close_type = "3" if side == "LONG" else "4"  # Close long = 3, Close short = 4
    
    print(f"  [RUNNER] Closing {close_pct*100:.0f}% of {symbol}: {close_size} units")
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
        stage=f"V3.1.2 Runner: Partial Close {symbol.replace('cmt_', '').upper()}",
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
    print(f"  [LOG] Saved: {filename}")


# ============================================================
# MAIN (for testing)
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("SMT V3.1.1 - Tier-Based Multi-Persona Trading")
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
