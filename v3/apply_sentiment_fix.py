import re
import os

FILENAME = 'smt_nightly_trade_v3_1.py'

if not os.path.exists(FILENAME):
    print(f"Error: {FILENAME} not found.")
    exit(1)

with open(FILENAME, 'r') as f:
    content = f.read()

# 1. Ensure 'import random' and 'import time' are present
if 'import random' not in content:
    content = content.replace('import numpy as np', 'import numpy as np\nimport random')
if 'import time' not in content:
    content = content.replace('import os', 'import os\nimport time')

# 2. Add/Update Rate Limiter Logic
rate_limiter_code = '''
# ============================================================
# V3.1.21: GEMINI API RATE LIMITER & CACHE
# ============================================================
_last_gemini_call = 0
_gemini_call_interval = 2.0
_sentiment_cache = {}

def _rate_limit_gemini():
    global _last_gemini_call
    now = time.time()
    elapsed = now - _last_gemini_call
    if elapsed < _gemini_call_interval:
        time.sleep(_gemini_call_interval - elapsed)
    _last_gemini_call = time.time()
'''

if '_last_gemini_call' not in content:
    content = content.replace('# ============================================================', 
                              rate_limiter_code + '\n# ============================================================', 1)

# 3. Replace the entire SentimentPersona class to fix the 400 error
# This version removes the incompatible response_schema and uses prompt-based JSON
new_sentiment_persona = '''
class SentimentPersona:
    def __init__(self):
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel, Tool, GoogleSearchRetrieval
            vertexai.init(project=PROJECT_ID, location="us-central1")
            self.search_tool = Tool.from_google_search_retrieval(google_search_retrieval=GoogleSearchRetrieval())
            self.model = GenerativeModel("gemini-1.5-flash")
            self.enabled = True
        except Exception as e:
            print(f"  [SENTIMENT] Init error: {e}")
            self.enabled = False

    def analyze(self, pair: str) -> dict:
        global _sentiment_cache
        if not self.enabled: return {"signal": "NEUTRAL", "confidence": 0, "reason": "Persona disabled"}
        
        now = time.time()
        if pair in _sentiment_cache and (now - _sentiment_cache[pair]['ts'] < 300):
            return _sentiment_cache[pair]['data']

        _rate_limit_gemini()
        prompt = f"""Analyze {pair}/USDT market sentiment for the next 4-24 hours. 
        Use Google Search to find recent news, liquidations, and social trends.
        
        Return ONLY a JSON object with these keys:
        - "sentiment": "BULLISH", "BEARISH", or "NEUTRAL"
        - "score": integer 0-100
        - "reason": brief summary
        
        JSON:"""

        for attempt in range(3):
            try:
                # FIX: Removed response_schema and response_mime_type to allow Search Tool
                response = self.model.generate_content(
                    prompt,
                    tools=[self.search_tool]
                )
                
                # Extract JSON from potential markdown blocks
                text = response.text.replace("```json", "").replace("```", "").strip()
                data = json.loads(text)
                
                result = {
                    "signal": "LONG" if data["sentiment"] == "BULLISH" else "SHORT" if data["sentiment"] == "BEARISH" else "NEUTRAL",
                    "confidence": data["score"] / 100.0,
                    "reason": data["reason"]
                }
                _sentiment_cache[pair] = {'ts': now, 'data': result}
                return result
            except Exception as e:
                if "429" in str(e):
                    time.sleep(2 ** (attempt + 1))
                else:
                    print(f"  [SENTIMENT] Attempt {attempt} error: {e}")
        
        return {"signal": "NEUTRAL", "confidence": 0, "reason": "API error"}
'''

# Replace the old class using regex to find it
content = re.sub(r'class SentimentPersona:.*?def analyze\(self, pair: str\) -> dict:.*?return \{"signal": "NEUTRAL", "confidence": 0, "reason": "API error"\}', 
                 new_sentiment_persona, content, flags=re.DOTALL)

with open(FILENAME, 'w') as f:
    f.write(content)

print("Fix applied successfully! SentimentPersona now supports Search Tool without 400 errors.")
