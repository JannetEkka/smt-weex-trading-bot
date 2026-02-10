path = 'smt_daemon_v3_1.py'
with open(path, 'r') as f:
    lines = f.readlines()

new_lines = []
found = False
for line in lines:
    if "def check_profit_guard" in line:
        new_lines.append("    def check_profit_guard(self, symbol, current_profit_pct, peak_profit_pct, whale_score=0):\n")
        new_lines.append("        if peak_profit_pct < 0.015: return False\n")
        new_lines.append("        if whale_score > 70:\n")
        new_lines.append("            self.logger.info(f'[WHALE-BACKER] Score {whale_score} > 70. Holding {symbol}.')\n")
        new_lines.append("            return False\n")
        new_lines.append("        fade_pct = (peak_profit_pct - current_profit_pct) / peak_profit_pct\n")
        new_lines.append("        return fade_pct > 0.40\n")
        found = True
        continue
    # Skip the old function body
    if found and line.strip() == "return fade_pct > 0.40":
        found = False
        continue
    if not found:
        new_lines.append(line)

with open(path, 'w') as f:
    f.writelines(new_lines)
