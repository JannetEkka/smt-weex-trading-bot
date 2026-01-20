#!/bin/bash
# Patch to add Gemini caching to SentimentPersona
# Run on VM: bash patch_gemini_cache.sh

cd ~/smt-weex-trading-bot

echo "============================================================"
echo "Adding Gemini Caching to SentimentPersona"
echo "============================================================"

# Backup
cp smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py.bak_gemini_$(date +%Y%m%d_%H%M%S)
echo "[OK] Backup created"

python3 << 'PYEOF'
import re

with open('smt_nightly_trade_v3_1.py', 'r') as f:
    content = f.read()

# Find and replace SentimentPersona class
old_sentiment_init = '''class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment."""
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5  # V3.1.7: Reduced from 2.0
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        try:'''

new_sentiment_init = '''class SentimentPersona:
    """Uses Gemini with Google Search grounding for market sentiment."""
    
    def __init__(self):
        self.name = "SENTIMENT"
        self.weight = 1.5  # V3.1.7: Reduced from 2.0
        self.cache = {}  # V3.1.22: Cache Gemini responses
        self.cache_ttl = 1800  # 30 minutes - sentiment doesn't change fast
        self.last_error_time = 0  # Track rate limit errors
        self.error_backoff = 300  # 5 min backoff on 429 errors
    
    def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        # V3.1.22: Check cache first
        cache_key = f"{pair}_{int(time.time() // self.cache_ttl)}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            print(f"  [SENTIMENT] Using cached result for {pair}")
            return cached
        
        # V3.1.22: Backoff if recently rate-limited
        if time.time() - self.last_error_time < self.error_backoff:
            remaining = self.error_backoff - (time.time() - self.last_error_time)
            print(f"  [SENTIMENT] Rate limit backoff: {remaining:.0f}s remaining")
            # Return last cached result if available
            for k, v in self.cache.items():
                if k.startswith(pair + "_"):
                    print(f"  [SENTIMENT] Returning stale cache for {pair}")
                    return v
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.3,
                "reasoning": "Rate limit backoff - using neutral",
            }
        
        try:'''

if old_sentiment_init in content:
    content = content.replace(old_sentiment_init, new_sentiment_init)
    print("[OK] Replaced SentimentPersona __init__ and analyze start")
else:
    print("[WARN] Could not find exact SentimentPersona init - trying flexible match")
    # Try more flexible pattern
    if "class SentimentPersona:" in content and "self.weight = 1.5" in content:
        # Find and inject cache init after weight line
        pattern = r'(self\.weight = 1\.5.*?\n)'
        replacement = r'''\1        self.cache = {}  # V3.1.22: Cache Gemini responses
        self.cache_ttl = 1800  # 30 minutes
        self.last_error_time = 0
        self.error_backoff = 300  # 5 min backoff on 429
'''
        content = re.sub(pattern, replacement, content, count=1)
        print("[OK] Injected cache variables")

# Now add caching at the end of successful analysis and error handling for 429
# Find the return statement after successful sentiment analysis

old_return = '''            return {
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

new_return = '''            result = {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:800],
            }
            
            # V3.1.22: Cache successful result
            self.cache[cache_key] = result
            print(f"  [SENTIMENT] Cached result for {pair} (TTL: {self.cache_ttl}s)")
            return result
            
        except Exception as e:
            error_str = str(e)
            
            # V3.1.22: Handle rate limit specifically
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                self.last_error_time = time.time()
                print(f"  [SENTIMENT] Rate limited! Backoff for {self.error_backoff}s")
                
                # Return stale cache if available
                for k, v in self.cache.items():
                    if k.startswith(pair + "_"):
                        print(f"  [SENTIMENT] Returning stale cache for {pair}")
                        v["reasoning"] = f"(cached) {v.get('reasoning', '')}"
                        return v
            
            return {
                "persona": self.name,
                "signal": "NEUTRAL",
                "confidence": 0.3,
                "reasoning": f"Sentiment analysis error: {error_str[:100]}",
            }'''

if old_return in content:
    content = content.replace(old_return, new_return)
    print("[OK] Added caching to return and 429 handling")
else:
    print("[WARN] Could not find exact return block")
    # Try to at least add 429 handling
    if '"Sentiment analysis error:' in content:
        old_err = 'reasoning": f"Sentiment analysis error: {str(e)}",'
        new_err = '''reasoning": f"Sentiment analysis error: {str(e)[:100]}",
            }
        
        # Note: For full caching, manual edit may be needed'''
        if old_err in content:
            content = content.replace(old_err, new_err)
            print("[PARTIAL] Added truncated error message")

with open('smt_nightly_trade_v3_1.py', 'w') as f:
    f.write(content)

print("\n[DONE] Gemini caching patch applied")
PYEOF

echo ""
echo "============================================================"
echo "Verifying..."
echo "============================================================"
grep -A5 "class SentimentPersona:" smt_nightly_trade_v3_1.py | head -10
grep "cache_ttl\|last_error_time" smt_nightly_trade_v3_1.py | head -5

echo ""
echo "============================================================"
echo "Next steps:"
echo "============================================================"
echo "git add ."
echo "git commit -m 'V3.1.22: Add Gemini caching to reduce 429 errors'"
echo "git push"
