#!/usr/bin/env python3
"""
Cryptoracle API Client for SMT V4
Integrates whale behavior classification and sentiment data
"""

import requests
import time
from typing import Dict, Optional, List
from datetime import datetime, timezone

CRYPTORACLE_BASE_URL = "https://v1.api.cryptoracle.io"
CRYPTORACLE_API_KEY = "e0c686dc-a813-4d1b-82a0-d8f312141056"

class CryptoracleClient:
    def __init__(self, api_key: str = CRYPTORACLE_API_KEY):
        self.api_key = api_key
        self.base_url = CRYPTORACLE_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({"X-API-KEY": api_key})
        
        # Cache for rate limiting
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    def _get_cached(self, key: str) -> Optional[Dict]:
        """Get cached response if still valid"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return data
        return None
    
    def _set_cache(self, key: str, data: Dict):
        """Cache response with timestamp"""
        self.cache[key] = (data, time.time())
    
    def get_whale_flows(self, ticker: str) -> Optional[Dict]:
        """
        Get whale net inflow/outflow for a ticker
        
        Args:
            ticker: BTC, ETH, SOL, etc.
        
        Returns:
            {
                "ticker": "BTC",
                "net_flow_24h": -1200.5,  # negative = outflow
                "large_tx_count": 47,
                "regime": "BEARISH"
            }
        """
        cache_key = f"flows_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            url = f"{self.base_url}/whales/flows/{ticker.upper()}"
            r = self.session.get(url, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                self._set_cache(cache_key, data)
                return data
            else:
                print(f"[CRYPTORACLE] Whale flows error {r.status_code}: {r.text[:100]}")
                return None
        except Exception as e:
            print(f"[CRYPTORACLE] Whale flows exception: {e}")
            return None
    
    def get_whale_behavior(self, ticker: str) -> Optional[Dict]:
        """
        Get whale behavior classification for a ticker
        
        Args:
            ticker: BTC, ETH, SOL, etc.
        
        Returns:
            {
                "ticker": "SOL",
                "regime_conviction": 0.82,
                "top_whales": [
                    {
                        "address": "6dna...4vH",
                        "classification": "ACCUMULATOR",  # or DISTRIBUTOR, EXITED
                        "last_move_usd": 4200000,
                        "sentiment_alignment": "BULLISH"  # or BEARISH, NEUTRAL
                    }
                ],
                "global_sentiment": {
                    "score": 0.45,  # -1 to 1
                    "label": "NEUTRAL_BULLISH"
                }
            }
        """
        cache_key = f"behavior_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            url = f"{self.base_url}/whales/behavior/{ticker.lower()}"
            r = self.session.get(url, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                self._set_cache(cache_key, data)
                return data
            else:
                print(f"[CRYPTORACLE] Whale behavior error {r.status_code}: {r.text[:100]}")
                return None
        except Exception as e:
            print(f"[CRYPTORACLE] Whale behavior exception: {e}")
            return None
    
    def get_sentiment(self, ticker: str) -> Optional[Dict]:
        """
        Get social sentiment for a ticker
        
        Args:
            ticker: BTC, ETH, SOL, etc.
        
        Returns:
            {
                "ticker": "BTC",
                "score": 0.67,  # -1 (bearish) to 1 (bullish)
                "label": "BULLISH",
                "volume_24h": 145000,  # social mentions
                "trend": "RISING"
            }
        """
        cache_key = f"sentiment_{ticker}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            url = f"{self.base_url}/sentiment/social/{ticker.upper()}"
            r = self.session.get(url, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                self._set_cache(cache_key, data)
                return data
            else:
                print(f"[CRYPTORACLE] Sentiment error {r.status_code}: {r.text[:100]}")
                return None
        except Exception as e:
            print(f"[CRYPTORACLE] Sentiment exception: {e}")
            return None
    
    def detect_smart_money_divergence(self, ticker: str) -> Dict:
        """
        Detect when whale behavior contradicts sentiment
        This is the "veto" signal for avoiding flash crashes
        
        Returns:
            {
                "divergence_detected": True,
                "whales_bearish": True,  # whales distributing
                "sentiment_bullish": True,  # social is bullish
                "recommendation": "VETO_LONG",  # or "VETO_SHORT" or "ALIGNED"
                "conviction": 0.85
            }
        """
        behavior = self.get_whale_behavior(ticker)
        sentiment = self.get_sentiment(ticker)
        
        if not behavior or not sentiment:
            return {"divergence_detected": False, "recommendation": "NO_DATA"}
        
        # Count accumulator vs distributor whales
        top_whales = behavior.get('top_whales', [])
        if not top_whales:
            return {"divergence_detected": False, "recommendation": "NO_WHALE_DATA"}
        
        accumulators = sum(1 for w in top_whales if w.get('classification') == 'ACCUMULATOR')
        distributors = sum(1 for w in top_whales if w.get('classification') == 'DISTRIBUTOR')
        
        whale_bearish = distributors > accumulators
        whale_bullish = accumulators > distributors
        
        sentiment_score = sentiment.get('score', 0)
        sentiment_bullish = sentiment_score > 0.3
        sentiment_bearish = sentiment_score < -0.3
        
        # Detect divergence
        divergence = False
        recommendation = "ALIGNED"
        conviction = 0.0
        
        if whale_bearish and sentiment_bullish:
            divergence = True
            recommendation = "VETO_LONG"
            conviction = abs(sentiment_score) * 0.5 + (distributors / len(top_whales)) * 0.5
        elif whale_bullish and sentiment_bearish:
            divergence = True
            recommendation = "VETO_SHORT"
            conviction = abs(sentiment_score) * 0.5 + (accumulators / len(top_whales)) * 0.5
        
        return {
            "divergence_detected": divergence,
            "whales_bearish": whale_bearish,
            "whales_bullish": whale_bullish,
            "sentiment_bullish": sentiment_bullish,
            "sentiment_bearish": sentiment_bearish,
            "recommendation": recommendation,
            "conviction": round(conviction, 2),
            "whale_ratio": f"{accumulators}/{distributors}",
            "sentiment_score": sentiment_score
        }


# Singleton instance
_client = None

def get_cryptoracle_client() -> CryptoracleClient:
    """Get or create singleton Cryptoracle client"""
    global _client
    if _client is None:
        _client = CryptoracleClient()
    return _client


# Test function
if __name__ == "__main__":
    print("Testing Cryptoracle API...")
    client = CryptoracleClient()
    
    for ticker in ["BTC", "ETH", "SOL"]:
        print(f"\n{'='*50}")
        print(f"Testing {ticker}")
        print('='*50)
        
        # Test flows
        print("\n1. Whale Flows:")
        flows = client.get_whale_flows(ticker)
        if flows:
            print(f"   ✓ {flows}")
        else:
            print("   ✗ Failed")
        
        # Test behavior
        print("\n2. Whale Behavior:")
        behavior = client.get_whale_behavior(ticker)
        if behavior:
            print(f"   ✓ {behavior}")
        else:
            print("   ✗ Failed")
        
        # Test sentiment
        print("\n3. Social Sentiment:")
        sentiment = client.get_sentiment(ticker)
        if sentiment:
            print(f"   ✓ {sentiment}")
        else:
            print("   ✗ Failed")
        
        # Test divergence detection
        print("\n4. Smart Money Divergence:")
        divergence = client.detect_smart_money_divergence(ticker)
        print(f"   {divergence}")
        
        time.sleep(1)  # Rate limit between tickers
