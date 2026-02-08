"""
Add pyramiding + breakeven to monitoring loop
"""

# Add to daemon's position monitoring
integration_code = '''
        # V3.1.29: BREAKEVEN SL + PYRAMIDING
        from pyramiding_system import move_sl_to_breakeven, should_pyramid, execute_pyramid
        
        # Check each position for breakeven/pyramiding opportunities
        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            entry = pos['entry_price']
            current = pos.get('current_price', entry)
            upnl = pos['unrealized_pnl']
            
            # Get trade info
            trade = tracker.get_trade(symbol)
            if not trade:
                continue
            
            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
            
            # 1. Move SL to breakeven if +2% profit
            if upnl > 0:
                moved = move_sl_to_breakeven(symbol, side, entry, current, profit_threshold=2.0)
                if moved:
                    logger.info(f"[BREAKEVEN] {symbol} SL moved to breakeven")
            
            # 2. Check for pyramiding opportunity
            pyramid_check = should_pyramid(symbol, entry, current, side, hours_open, pos['size'])
            
            if pyramid_check['should_add']:
                logger.info(f"[PYRAMID] {symbol}: {pyramid_check['reason']}")
                
                # Execute pyramid
                leverage = trade.get('leverage', 5)  # Get original leverage
                result = execute_pyramid(symbol, side, pyramid_check['add_pct'], pos['size'], leverage)
                
                if result.get('success'):
                    logger.info(f"[PYRAMID] Successfully added to {symbol}")
'''

print("✅ Pyramiding + Breakeven system ready!")
print("\nTo integrate into daemon, add to monitor_positions() function")
print("Location: After position monitoring loop, before regime exit check")
