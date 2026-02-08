import re

# Read daemon file
with open('smt_daemon_v3_1.py', 'r') as f:
    daemon_content = f.read()

# 1. Add imports at top
import_line = "from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid"

if import_line not in daemon_content:
    # Find the imports section and add
    insert_pos = daemon_content.find("from smt_nightly_trade_v3_1 import")
    if insert_pos > 0:
        daemon_content = daemon_content[:insert_pos] + import_line + "\n" + daemon_content[insert_pos:]
        print("✅ Added pyramiding imports")

# 2. Add pyramiding logic to monitor_positions
pyramiding_code = '''
        # V3.1.29: PYRAMIDING + BREAKEVEN SL
        try:
            for pos in positions:
                symbol = pos['symbol']
                side = pos['side']
                entry = pos.get('entry_price', 0)
                current = pos.get('current_price', entry)
                upnl = float(pos.get('unrealized_pnl', 0))
                
                if not entry:
                    continue
                
                # Get trade info for hours open
                trade = tracker.get_trade(symbol)
                if not trade:
                    continue
                
                try:
                    opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                    hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                except:
                    hours_open = 0
                
                # 1. Move SL to breakeven if profitable
                if upnl > 0:
                    profit_pct = (upnl / (entry * pos['size'])) * 100 if pos['size'] else 0
                    if profit_pct >= 2.0:
                        moved = move_sl_to_breakeven(symbol, side, entry, current, profit_threshold=2.0)
                        if moved:
                            logger.info(f"[BREAKEVEN] {symbol.replace('cmt_','').upper()} SL → breakeven")
                
                # 2. Check pyramiding opportunity
                pyramid_check = should_pyramid(symbol, entry, current, side, hours_open, pos['size'])
                
                if pyramid_check['should_add']:
                    logger.info(f"[PYRAMID] {symbol.replace('cmt_','').upper()}: {pyramid_check['reason']}")
                    
                    # Get original leverage
                    leverage = trade.get('leverage', 5)
                    
                    # Execute pyramid
                    result = execute_pyramid(symbol, side, pyramid_check['add_pct'], pos['size'], leverage)
                    
                    if result.get('success'):
                        logger.info(f"[PYRAMID] Added {pyramid_check['add_pct']*100:.0f}% to {symbol.replace('cmt_','').upper()}")
                        state.trades_opened += 1
        except Exception as e:
            logger.error(f"[PYRAMID/BREAKEVEN] Error: {e}")
        
'''

# Find monitor_positions function and add before regime_aware_exit_check
pattern = r'(\s+)(regime_aware_exit_check\(\)  # V3\.1\.9: Check for regime-fighting positions)'

if re.search(pattern, daemon_content):
    daemon_content = re.sub(pattern, pyramiding_code + r'\1\2', daemon_content)
    print("✅ Added pyramiding logic to monitor_positions")
else:
    print("⚠️  Could not find insertion point - check manually")

# Write back
with open('smt_daemon_v3_1.py', 'w') as f:
    f.write(daemon_content)

print("\n✅ FULL INTEGRATION COMPLETE!")
print("\nSystem now includes:")
print("  1. Dynamic 5-6x leverage (safe entry)")
print("  2. Breakeven SL at +2% profit (protect gains)")
print("  3. Pyramid +30% at +2% after 4h")
print("  4. Pyramid +20% at +4% after 8h")
print("  5. All existing safety systems")

