import re

file_path = 'smt_nightly_trade_v3_1.py'
with open(file_path, 'r') as f:
    content = f.read()

# 1. Update TIER_CONFIG for "Runner Mode"
tier_config_new = """TIER_CONFIG = {
    "Tier 1": {"leverage": 10, "stop_loss": 0.025, "take_profit": 0.12, "trailing_stop": 0.015, "time_limit": 5760},
    "Tier 2": {"leverage": 8, "stop_loss": 0.03, "take_profit": 0.15, "trailing_stop": 0.02, "time_limit": 4320},
    "Tier 3": {"leverage": 6, "stop_loss": 0.04, "take_profit": 0.18, "trailing_stop": 0.025, "time_limit": 2880}
}"""
content = re.sub(r'TIER_CONFIG = \{.*?\n\}', tier_config_new, content, flags=re.DOTALL)

# 2. Inject Whale-Backer Instructions into the Judge Prompt
whale_prompt = """[STRATEGY UPDATE: WHALE-BACKER]
1. Your primary indicator is now the [WHALE] persona. 
2. If [WHALE] indicates accumulation (>70%), you MUST enter LONG, even if technicals are weak.
3. IGNORE "Overbought" RSI in extreme fear; follow whale accumulation.
4. TARGETS: We are swing trading. Aim for market reversals (12%+), not 2% scalps."""

content = content.replace('You are Gemini', whale_prompt + '\\n\\nYou are Gemini')

with open(file_path, 'w') as f:
    f.write(content)
print("Strategy Updated: Runner Mode & Whale-Backer Active.")
