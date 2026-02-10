path = 'smt_daemon_v3_1.py'
with open(path, 'r') as f:
    content = f.read()

import re
# This regex finds the old profit guard and replaces the whole block
old_pattern = r'def check_profit_guard\(self, symbol, current_profit_pct, peak_profit_pct\):.*?return fade_pct > 0.40'
new_block = """def check_profit_guard(self, symbol, current_profit_pct, peak_profit_pct, whale_score=0):
        if peak_profit_pct < 0.015: return False
        if whale_score > 70:
            self.logger.info(f'[WHALE-BACKER] Score {whale_score} > 70. Holding {symbol}.')
            return False
        fade_pct = (peak_profit_pct - current_profit_pct) / peak_profit_pct
        return fade_pct > 0.40"""

new_content = re.sub(old_pattern, new_block, content, flags=re.DOTALL)
with open(path, 'w') as f:
    f.write(new_content)
