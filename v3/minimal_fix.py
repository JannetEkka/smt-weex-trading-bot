with open('smt_daemon_v3_1.py', 'r') as f:
    lines = f.readlines()

# Find line with "from smt_nightly_trade_v3_1 import (" and add import after it
for i, line in enumerate(lines):
    if 'from smt_nightly_trade_v3_1 import (' in line:
        # Find the closing parenthesis
        j = i
        while ')' not in lines[j]:
            j += 1
        # Insert import after the closing line
        lines.insert(j + 1, 'from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid\n')
        break

with open('smt_daemon_v3_1.py', 'w') as f:
    f.writelines(lines)

print("✅ Import added")
