"""
Cryptoracle wrapper with graceful degradation
If API is down, returns None and personas use existing logic
"""
from cryptoracle_client import get_cryptoracle_client

def safe_get_whale_behavior(ticker: str):
    try:
        client = get_cryptoracle_client()
        return client.get_whale_behavior(ticker)
    except:
        return None

def safe_get_sentiment(ticker: str):
    try:
        client = get_cryptoracle_client()
        return client.get_sentiment(ticker)
    except:
        return None

def safe_detect_divergence(ticker: str):
    try:
        client = get_cryptoracle_client()
        return client.detect_smart_money_divergence(ticker)
    except:
        return {"divergence_detected": False, "recommendation": "API_DOWN"}
