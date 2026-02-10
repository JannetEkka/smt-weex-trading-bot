import re

path = 'smt_nightly_trade_v3_1.py'
with open(path, 'r') as f:
    data = f.read()

# Force the TIER_CONFIG to the aggressive Whale-Backer values
new_config = """TIER_CONFIG = {
    "Tier 1": {"leverage": 10, "stop_loss": 0.025, "take_profit": 0.12, "trailing_stop": 0.015, "time_limit": 5760},
    "Tier 2": {"leverage": 8, "stop_loss": 0.03, "take_profit": 0.15, "trailing_stop": 0.02, "time_limit": 4320},
    "Tier 3": {"leverage": 6, "stop_loss": 0.04, "take_profit": 0.18, "trailing_stop": 0.025, "time_limit": 2880}
}"""
data = re.sub(r'TIER_CONFIG = \{.*?\n\}', new_config, data, flags=re.DOTALL)

# Inject the Whale instructions directly at the start of the Judge prompt
whale_instr = "[STRATEGY UPDATE: WHALE-BACKER]\n1. Primary indicator: [WHALE].\n2. If [WHALE] > 70%, MUST enter LONG.\n3. IGNORE RSI; follow whales.\n4. TARGETS: Swing for 12%+.\n\n"
data = data.replace('You are Gemini', whale_instr + 'You are Gemini')

with open(path, 'w') as f:
    f.write(data)
