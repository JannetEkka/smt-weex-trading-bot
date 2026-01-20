import shutil
from datetime import datetime

source = "smt_nightly_trade_v3_1.py"
backup = f"{source}.backup_ratelimit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy(source, backup)
print(f"Backup: {backup}")

with open(source, 'r') as f:
    content = f.read()

# Add sentiment cache at the top (after imports)
cache_code = '''
# V3.1.21: Sentiment cache to avoid rate limits
SENTIMENT_CACHE = {}
SENTIMENT_CACHE_TTL = 900  # 15 minutes
'''

if "SENTIMENT_CACHE = {}" not in content:
    content = content.replace(
        "from collections import deque",
        "from collections import deque" + cache_code
    )
    print("Added sentiment cache")

# Update SentimentPersona.analyze to use cache
old_analyze_start = '''def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        try:'''

new_analyze_start = '''def analyze(self, pair: str, pair_info: Dict, competition_status: Dict) -> Dict:
        # V3.1.21: Check cache first to avoid rate limits
        import time as time_module
        cache_key = pair
        if cache_key in SENTIMENT_CACHE:
            cached_time, cached_result = SENTIMENT_CACHE[cache_key]
            if time_module.time() - cached_time < SENTIMENT_CACHE_TTL:
                print(f"  [SENTIMENT] Using cached result for {pair}")
                return cached_result
        
        # Add delay between API calls
        time_module.sleep(3)
        
        try:'''

if old_analyze_start in content and "Check cache first" not in content:
    content = content.replace(old_analyze_start, new_analyze_start)
    print("Added cache check at start of analyze")

# Add cache save before return
old_return = '''return {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:800],
            }'''

new_return = '''result = {
                "persona": self.name,
                "signal": signal,
                "confidence": data.get("confidence", 0.5),
                "reasoning": data.get("key_factor", "Market sentiment analysis"),
                "sentiment": data["sentiment"],
                "market_context": market_context[:800],
            }
            # V3.1.21: Cache the result
            SENTIMENT_CACHE[cache_key] = (time_module.time(), result)
            return result'''

if old_return in content and "Cache the result" not in content:
    content = content.replace(old_return, new_return)
    print("Added cache save before return")

with open(source, 'w') as f:
    f.write(content)

print("\nDone! Sentiment now cached for 15 minutes.")
print("Restart daemon: pkill -f smt_daemon && nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
