# This adds Telegram alerts for ALL pairs when confidence >= 75%

# INSERT AFTER line 388 (after logging the signal):

                # Telegram alerts for ALL tradeable signals
                if signal in ("LONG", "SHORT") and confidence >= 0.75:
                    try:
                        from telegram_alerts import send_telegram_alert
                        tier_cfg = get_tier_config(tier)
                        current_price = get_price(f"cmt_{pair.lower()}usdt")
                        
                        # Calculate targets
                        if signal == "LONG":
                            tp_price = current_price * (1 + tier_cfg["tp_pct"]/100)
                            sl_price = current_price * (1 - tier_cfg["sl_pct"]/100)
                        else:  # SHORT
                            tp_price = current_price * (1 - tier_cfg["tp_pct"]/100)
                            sl_price = current_price * (1 + tier_cfg["sl_pct"]/100)
                        
                        alert_msg = f"""🚨 <b>SMT SIGNAL - {pair}</b>

Direction: <b>{signal}</b>
Confidence: <b>{confidence:.0%}</b>
Tier: {tier} ({tier_cfg['name']})

Entry: ${current_price:,.2f}
TP: ${tp_price:,.2f} ({tier_cfg['tp_pct']}%)
SL: ${sl_price:,.2f} ({tier_cfg['sl_pct']}%)

Reasoning:
{decision.get('reasoning', 'N/A')[:400]}"""
                        
                        send_telegram_alert(alert_msg)
                        logger.info(f"[TELEGRAM] {pair} {signal} alert sent")
                    except Exception as e:
                        logger.error(f"[TELEGRAM] Alert failed: {e}")
