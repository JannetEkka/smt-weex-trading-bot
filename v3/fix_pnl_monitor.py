#!/usr/bin/env python3
"""Fix monitor_positions to calculate actual PnL instead of estimated"""

TARGET = "smt_daemon_v3_1.py"

OLD = '''                if not position.get("is_open"):
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    
                    # V3.1.25: Log RL outcome
                    if RL_ENABLED and rl_collector:
                        try:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            tier_cfg = get_tier_config(trade.get("tier", get_tier_for_symbol(symbol)))
                            est_pnl = tier_cfg.get("tp_pct", 2.0)
                            rl_collector.log_outcome(symbol, est_pnl, hours_open, "TP_SL")
                        except Exception as e:
                            logger.debug(f"RL outcome log error: {e}")
                    
                    tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                    state.trades_closed += 1
                    continue'''

NEW = '''                if not position.get("is_open"):
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    
                    # V3.1.26: Calculate ACTUAL PnL from trade data
                    entry_price = trade.get("entry_price", 0)
                    side = trade.get("side", "LONG")
                    position_usdt = trade.get("position_usdt", 0)
                    actual_pnl = 0
                    pnl_pct = 0
                    
                    try:
                        current_price = get_price(symbol)
                        if entry_price > 0 and current_price > 0:
                            if side == "LONG":
                                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                            else:
                                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                            actual_pnl = position_usdt * (pnl_pct / 100)
                        logger.info(f"  PnL: ${actual_pnl:.2f} ({pnl_pct:+.2f}%)")
                    except Exception as e:
                        logger.debug(f"PnL calc error: {e}")
                    
                    # Log RL outcome with ACTUAL PnL
                    if RL_ENABLED and rl_collector:
                        try:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, "TP_SL")
                        except Exception as e:
                            logger.debug(f"RL outcome log error: {e}")
                    
                    tracker.close_trade(symbol, {
                        "reason": "tp_sl_hit",
                        "cleanup": cleanup,
                        "symbol": symbol,
                        "pnl": actual_pnl,
                        "pnl_pct": pnl_pct
                    })
                    state.trades_closed += 1
                    continue'''

with open(TARGET, 'r') as f:
    content = f.read()

if OLD not in content:
    print("[ERROR] Old block not found - maybe already patched or code changed")
    exit(1)

content = content.replace(OLD, NEW)

with open(TARGET, 'w') as f:
    f.write(content)

print("[OK] Patched monitor_positions with actual PnL calculation")
