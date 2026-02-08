# This will be inserted into check_trading_signals() function
# Right after BTC analysis completes

if pair == "BTC" and signal in ("LONG", "SHORT") and confidence >= MIN_CONFIDENCE_TO_TRADE:
    try:
        from telegram_alerts import send_telegram_alert
        
        tier_cfg = get_tier_config(tier)
        current_price = get_price("cmt_btcusdt")
        
        alert_msg = f"""
🚨 <b>SMT SIGNAL - BTC</b>

Direction: <b>{signal}</b>
Confidence: <b>{confidence:.0%}</b>

Entry: ${current_price:,.2f}
TP: {tier_cfg['tp_pct']}% (${current_price * (1 + tier_cfg['tp_pct']/100):,.2f})
SL: {tier_cfg['sl_pct']}% (${current_price * (1 - tier_cfg['sl_pct']/100):,.2f})

Reasoning:
{decision.get('reasoning', 'N/A')[:500]}
"""
        send_telegram_alert(alert_msg)
        logger.info("[TELEGRAM] BTC alert sent")
    except Exception as e:
        logger.error(f"[TELEGRAM] Alert failed: {e}")
