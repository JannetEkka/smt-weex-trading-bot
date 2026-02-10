file_path = 'smt_daemon_v3_1.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "def check_profit_guard" in line:
        new_lines.append("    def check_profit_guard(self, symbol, current_profit_pct, peak_profit_pct, whale_score=0):\n")
        new_lines.append("        if peak_profit_pct < 0.015: return False\n")
        new_lines.append("        if whale_score > 70:\n")
        new_lines.append("            self.logger.info(f'[WHALE-BACKER] Score {whale_score} > 70. Holding {symbol}.')\n")
        new_lines.append("            return False\n")
        new_lines.append("        fade_pct = (peak_profit_pct - current_profit_pct) / peak_profit_pct\n")
        new_lines.append("        return fade_pct > 0.40\n")
        skip = True
        continue
    if skip and line.startswith("    def"): # Stop skipping at next function
        skip = False
    if not skip:
        new_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(new_lines)
print("Daemon Updated: Smart Hold Logic Implemented.")
