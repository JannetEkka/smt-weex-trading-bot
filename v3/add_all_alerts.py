import re

with open('smt_daemon_v3_1.py', 'r') as f:
    content = f.read()

# 1. TRADE EXECUTED ALERT (after line 627)
trade_executed_alert = '''                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        
                        # Telegram: Trade executed
                        try:
                            from telegram_alerts import send_telegram_alert
                            pair = opportunity["pair"]
                            decision = opportunity["decision"]
                            signal = decision["signal"]
                            confidence = decision["confidence"]
                            tier_cfg = trade_result
                            msg = f"""✅ <b>TRADE EXECUTED - {pair}</b>

{signal} opened!
Confidence: {confidence:.0%}
Entry: ${trade_result.get('entry_price'):,.2f}
TP: ${trade_result.get('tp_price'):,.2f} ({tier_cfg.get('tp_pct'):.1f}%)
SL: ${trade_result.get('sl_price'):,.2f} ({tier_cfg.get('sl_pct'):.1f}%)

Order ID: {trade_result.get('order_id')}"""
                            send_telegram_alert(msg)
                        except Exception as e:
                            logger.error(f"Telegram trade alert failed: {e}")
                        
                        tracker.add_trade(opportunity["pair_info"]["symbol"], trade_result)'''

content = content.replace(
    '''                        logger.info(f"Trade executed: {trade_result.get('order_id')}")
                        logger.info(f"  TP: {trade_result.get('tp_pct'):.1f}%, SL: {trade_result.get('sl_pct'):.1f}%")
                        
                        tracker.add_trade(opportunity["pair_info"]["symbol"], trade_result)''',
    trade_executed_alert
)

# 2. POSITION CLOSED ALERT (in monitor_positions after "CLOSED via TP/SL")
position_closed_alert = '''                    logger.info(f"  PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%)")
                    
                    # Telegram: Position closed
                    try:
                        from telegram_alerts import send_telegram_alert
                        symbol_clean = symbol.replace('cmt_', '').replace('usdt', '').upper()
                        emoji = "✅" if actual_pnl > 0 else "❌"
                        msg = f"""{emoji} <b>POSITION CLOSED - {symbol_clean}</b>

{side} closed via TP/SL
Entry: ${entry_price:,.2f}
Exit: ${current_price:,.2f}
PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%)

Position held: {hours_open:.1f}h"""
                        send_telegram_alert(msg)
                    except Exception as e:
                        logger.error(f"Telegram close alert failed: {e}")
                    
                    # Log RL outcome'''

# Find and replace
old_pnl_log = '''                    logger.info(f"  PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%)")
                    
                    # Log RL outcome'''

content = content.replace(old_pnl_log, position_closed_alert)

# 3. REGIME EXIT ALERT (in regime_aware_exit_check after closing)
regime_exit_alert = '''                
                logger.info(f"[REGIME EXIT] Closed {symbol_clean}, order: {order_id}")
                
                # Telegram: Regime exit
                try:
                    from telegram_alerts import send_telegram_alert
                    msg = f"""⚠️ <b>REGIME EXIT - {symbol_clean}</b>

{side} closed by AI
Reason: {reason}
PnL: ${pnl:.2f}

Market regime: {regime['regime']}
BTC 24h: {regime['change_24h']:+.1f}%"""
                    send_telegram_alert(msg)
                except Exception as e:
                    logger.error(f"Telegram regime exit alert failed: {e}")'''

old_regime_log = '''                
                logger.info(f"[REGIME EXIT] Closed {symbol_clean}, order: {order_id}")'''

content = content.replace(old_regime_log, regime_exit_alert)

with open('smt_daemon_v3_1.py', 'w') as f:
    f.write(content)

print("✅ ALL 4 TELEGRAM ALERTS ADDED:")
print("  1. Signal detected (75%+) - Already had")
print("  2. Trade executed - ADDED")
print("  3. Position closed (TP/SL) - ADDED")
print("  4. Regime exit - ADDED")
