import re

with open('smt_daemon_v3_1.py', 'r') as f:
    content = f.read()

# 1. Add import after existing imports (find safe location)
import_location = content.find('from smt_nightly_trade_v3_1 import (')
if import_location > 0:
    # Find end of this import block
    end_import = content.find(')', import_location) + 1
    
    # Add pyramiding import
    new_import = '\nfrom pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid\n'
    content = content[:end_import] + new_import + content[end_import:]
    print("✅ Added imports")

# 2. Find monitor_positions function
monitor_func = content.find('def monitor_positions():')
if monitor_func > 0:
    # Find where regime_aware_exit_check is called
    regime_call = content.find('regime_aware_exit_check()', monitor_func)
    
    if regime_call > 0:
        # Insert pyramiding code BEFORE regime check
        pyramid_code = '''
        # V3.1.29: Pyramiding + Breakeven
        try:
            from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid
            
            for pos in positions:
                symbol = pos['symbol']
                side = pos['side']
                entry = pos.get('entry_price', 0)
                current = pos.get('current_price', entry)
                upnl = float(pos.get('unrealized_pnl', 0))
                
                if not entry:
                    continue
                
                trade = tracker.get_trade(symbol)
                if not trade:
                    continue
                
                try:
                    opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                    hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                except:
                    hours_open = 0
                
                # Breakeven SL at +2%
                if upnl > 0:
                    profit_pct = (upnl / (entry * pos['size'])) * 100 if pos['size'] else 0
                    if profit_pct >= 2.0:
                        try:
                            moved = move_sl_to_breakeven(symbol, side, entry, current, 2.0)
                            if moved:
                                logger.info(f"[BREAKEVEN] {symbol.replace('cmt_','').upper()} SL → BE")
                        except:
                            pass
                
                # Pyramiding
                pyramid_check = should_pyramid(symbol, entry, current, side, hours_open, pos['size'])
                if pyramid_check['should_add']:
                    logger.info(f"[PYRAMID] {symbol.replace('cmt_','').upper()}: {pyramid_check['reason']}")
                    try:
                        leverage = trade.get('leverage', 5)
                        result = execute_pyramid(symbol, side, pyramid_check['add_pct'], pos['size'], leverage)
                        if result.get('success'):
                            logger.info(f"[PYRAMID] Added to {symbol.replace('cmt_','').upper()}")
                    except Exception as e:
                        logger.error(f"[PYRAMID] Failed: {e}")
        except Exception as e:
            logger.error(f"[PYRAMID/BREAKEVEN] Error: {e}")
        
        '''
        
        # Find the line before regime_aware_exit_check
        lines = content[:regime_call].split('\n')
        indent = len(lines[-1]) - len(lines[-1].lstrip())
        
        # Indent pyramid code properly
        pyramid_lines = pyramid_code.split('\n')
        indented_pyramid = '\n'.join(' ' * indent + line if line.strip() else line for line in pyramid_lines)
        
        # Insert
        content = content[:regime_call] + indented_pyramid + '\n' + ' ' * indent + content[regime_call:]
        print("✅ Added pyramiding logic")

with open('smt_daemon_v3_1.py', 'w') as f:
    f.write(content)

print("✅ Clean integration complete")
