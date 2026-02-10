path = 'smt_nightly_trade_v3_1.py'
with open(path, 'r') as f:
    content = f.read()

# Force the aggressive profit targets (12%, 15%, 18%)
config_block = """TIER_CONFIG = {
    "Tier 1": {"leverage": 10, "stop_loss": 0.025, "take_profit": 0.12, "trailing_stop": 0.015, "time_limit": 5760},
    "Tier 2": {"leverage": 8, "stop_loss": 0.03, "take_profit": 0.15, "trailing_stop": 0.02, "time_limit": 4320},
    "Tier 3": {"leverage": 6, "stop_loss": 0.04, "take_profit": 0.18, "trailing_stop": 0.025, "time_limit": 2880}
}"""

# Inject instructions into the Judge's prompt
whale_rules = """[STRATEGY UPDATE: WHALE-BACKER]
1. Primary indicator: [WHALE].
2. If [WHALE] > 70%, MUST enter LONG.
3. IGNORE RSI; follow whales.
4. TARGETS: Swing for 12%+.

You are Gemini"""

import re
# Replace the old Tier Config
content = re.sub(r'TIER_CONFIG = \{.*?\}', config_block, content, flags=re.DOTALL)
# Inject the Whale rules
content = content.replace("You are Gemini", whale_rules)

with open(path, 'w') as f:
    f.write(content)
