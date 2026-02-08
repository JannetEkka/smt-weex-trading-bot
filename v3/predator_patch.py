import re

with open('smt_daemon_v3_1.py', 'r') as f:
    content = f.read()

# 1. Change thresholds from $15/$50 to $8/$25
content = content.replace('pnl < -15:', 'pnl < -8:')
content = content.replace('pnl < -50', 'pnl < -25')

# 2. Add volatility override to bypass 4-hour protection
old_code = '''            # V3.1.20 PREDATOR: No regime exits within first 4 hours - let trades breathe
            if hours_open < 4:
                continue'''

new_code = '''            # V3.1.20 PREDATOR: No regime exits within first 4 hours - UNLESS SPIKING
            is_spiking = abs(regime.get('change_1h', 0)) > 1.5
            if hours_open < 4 and not is_spiking:
                continue'''

content = content.replace(old_code, new_code)

with open('smt_daemon_v3_1.py', 'w') as f:
    f.write(content)

print("✅ PREDATOR PATCH APPLIED:")
print("  - Exit threshold: $15 → $8")
print("  - Hard stop: $50 → $25")
print("  - Volatility override: Bypasses 4h lock if BTC 1h > ±1.5%")
