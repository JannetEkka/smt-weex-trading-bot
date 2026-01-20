import shutil
from datetime import datetime

source = "smt_nightly_trade_v3_1.py"
backup = f"{source}.backup_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy(source, backup)
print(f"Backup: {backup}")

with open(source, 'r') as f:
    content = f.read()

changes = []

# 1. Add proper cache module at the top
cache_module = '''
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
GEMINI_CALL_DELAY = 5  # seconds between calls

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

'''

# Check if already added
if "class APICache:" not in content:
    # Add after the deque import
    content = content.replace(
        "from collections import deque",
        "from collections import deque" + cache_module
    )
    changes.append("Added APICache class and rate limiting")

# 2. Update SentimentPersona to use proper caching
old_sentiment_class = '''class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment."""
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5  # V3.1.7: Reduced from 2.0
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:'''

new_sentiment_class = '''class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment."""
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5  # V3.1.7: Reduced from 2.0
        self.cache_ttl = 1800  # 30 minutes cache
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        # V3.1.21: Check cache first
        cache_key = f"sentiment_{pair}"
        cached = SENTIMENT_CACHE.get(cache_key, self.cache_ttl)
        if cached:
            print(f"  [SENTIMENT] Using cached result for {pair} (saves API call)")
            return cached
        
        # V3.1.21: Rate limit before API call
        rate_limit_gemini()'''

if "cache_ttl = 1800" not in content:
    content = content.replace(old_sentiment_class, new_sentiment_class)
    changes.append("Added cache check to SentimentPersona")

# 3. Add cache save after successful sentiment analysis
# Find the return statement in SentimentPersona and add caching
old_sentiment_return = '''return {
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
            }'''

new_sentiment_return = '''result = {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:800],
            }
            # V3.1.21: Cache successful result
            SENTIMENT_CACHE.set(cache_key, result)
            return result
            
        except Exception as e:
            error_msg = str(e)[:100]
            # V3.1.21: On rate limit, return neutral but don't cache error
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"  [SENTIMENT] Rate limited - using NEUTRAL fallback")
                return {
                    "persona": self.name,
                    "signal": "NEUTRAL",
                    "confidence": 0.3,
                    "reasoning": "Rate limited - using neutral fallback",
                }
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.3,
                "reasoning": f"Sentiment analysis error: {error_msg}",
            }'''

if "Cache successful result" not in content:
    content = content.replace(old_sentiment_return, new_sentiment_return)
    changes.append("Added cache save and better error handling")

# 4. Also cache the regime data (called multiple times per cycle)
old_regime_func = '''def get_enhanced_market_regime() -> dict:
    """
    V3.1.12: Multi-factor regime detection'''

new_regime_func = '''def get_enhanced_market_regime() -> dict:
    """
    V3.1.12: Multi-factor regime detection
    V3.1.21: Cached for 5 minutes to reduce API calls
    """
    # Check cache first
    cached = REGIME_CACHE.get("regime", 300)  # 5 min cache
    if cached:
        return cached
    
    """
    V3.1.12: Multi-factor regime detection'''

if 'REGIME_CACHE.get("regime"' not in content:
    content = content.replace(old_regime_func, new_regime_func)
    changes.append("Added cache to regime detection")

# 5. Add cache save at end of regime function
old_regime_return = '''print(f"  [REGIME] OI Signal: {oi_signal['signal']} | Volatility: {atr_data['volatility']} | Alts: {result.get('altcoin_avg', 0):+.1f}%")
    for f in factors[:6]:
        print(f"  [REGIME]   > {f}")
    
    return result'''

new_regime_return = '''print(f"  [REGIME] OI Signal: {oi_signal['signal']} | Volatility: {atr_data['volatility']} | Alts: {result.get('altcoin_avg', 0):+.1f}%")
    for f in factors[:6]:
        print(f"  [REGIME]   > {f}")
    
    # V3.1.21: Cache the result
    REGIME_CACHE.set("regime", result)
    return result'''

if 'REGIME_CACHE.set("regime"' not in content:
    content = content.replace(old_regime_return, new_regime_return)
    changes.append("Added cache save to regime")

with open(source, 'w') as f:
    f.write(content)

print(f"\n{'='*60}")
print("CACHING FIXES APPLIED")
print('='*60)
for c in changes:
    print(f"  [OK] {c}")

print(f"\nCache settings:")
print(f"  - Sentiment: 30 min TTL")
print(f"  - Regime: 5 min TTL")
print(f"  - Gemini delay: 5 sec between calls")
print(f"\nRestart: pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
