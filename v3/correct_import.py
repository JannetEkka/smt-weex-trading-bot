with open('smt_daemon_v3_1.py', 'r') as f:
    lines = f.readlines()

# Find the except block end
for i, line in enumerate(lines):
    if 'except ImportError as e:' in line:
        # Find the sys.exit(1) line which ends the except block
        j = i
        while 'sys.exit(1)' not in lines[j]:
            j += 1
        # Insert import AFTER the except block
        lines.insert(j + 1, '\n# V3.1.29: Pyramiding system import\n')
        lines.insert(j + 2, 'try:\n')
        lines.insert(j + 3, '    from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid\n')
        lines.insert(j + 4, '    logger.info("Pyramiding system loaded")\n')
        lines.insert(j + 5, 'except ImportError:\n')
        lines.insert(j + 6, '    logger.warning("Pyramiding system not available")\n')
        lines.insert(j + 7, '    move_sl_to_breakeven = lambda *args, **kwargs: False\n')
        lines.insert(j + 8, '    should_pyramid = lambda *args, **kwargs: {"should_add": False}\n')
        lines.insert(j + 9, '    execute_pyramid = lambda *args, **kwargs: {"success": False}\n')
        lines.insert(j + 10, '\n')
        break

with open('smt_daemon_v3_1.py', 'w') as f:
    f.writelines(lines)

print("✅ Import added correctly AFTER try/except block")
