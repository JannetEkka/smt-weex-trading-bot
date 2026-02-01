#!/usr/bin/env python3
"""
SMT Daemon Patch - V3.1.25 (RL Outcome Logging Only)
Adds log_outcome calls when positions close.
rl_data_collector.py already exists.

Usage:
    cd ~/smt-weex-trading-bot/v3
    python3 patch_rl_outcomes.py
"""

import os
import sys

DAEMON_FILE = "smt_daemon_v3_1.py"

PATCHES = [
    # Patch 1: Update version
    {
        "name": "Version update",
        "old": 'SMT Trading Daemon V3.1.24',
        "new": 'SMT Trading Daemon V3.1.25'
    },
    
    # Patch 2: TP/SL hit in position monitor
    {
        "name": "TP/SL outcome logging",
        "old": '''                if not position.get("is_open"):
                    logger.info(f"{symbol} CLOSED via TP/SL")
                    cleanup = cancel_all_orders_for_symbol(symbol)
                    tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                    state.trades_closed += 1
                    continue''',
        "new": '''                if not position.get("is_open"):
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
    },
    
    # Patch 3: Smart exit
    {
        "name": "Smart exit outcome logging",
        "old": '''                if should_exit:
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    logger.warning(f"{symbol_clean}: Force close - {exit_reason}")
                    
                    close_result = close_position_manually(
                        symbol=symbol,
                        side=position["side"],
                        size=position["size"]
                    )
                    
                    tracker.close_trade(symbol, {
                        "reason": exit_reason,
                        "tier": tier,
                        "hours_open": hours_open,
                        "final_pnl_pct": pnl_pct,
                        "close_result": close_result,
                    })
                    
                    state.trades_closed += 1''',
        "new": '''                if should_exit:
                    symbol_clean = symbol.replace("cmt_", "").upper()
                    logger.warning(f"{symbol_clean}: Force close - {exit_reason}")
                    
                    close_result = close_position_manually(
                        symbol=symbol,
                        side=position["side"],
                        size=position["size"]
                    )
                    
                    # V3.1.25: Log RL outcome
                    if RL_ENABLED and rl_collector:
                        try:
                            exit_type = "PROFIT_GUARD" if "profit_guard" in exit_reason else \\
                                       "TIMEOUT" if "max_hold" in exit_reason else \\
                                       "EARLY_EXIT" if "early_exit" in exit_reason else \\
                                       "FORCE_STOP"
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, exit_type)
                        except Exception as e:
                            logger.debug(f"RL outcome log error: {e}")
                    
                    tracker.close_trade(symbol, {
                        "reason": exit_reason,
                        "tier": tier,
                        "hours_open": hours_open,
                        "final_pnl_pct": pnl_pct,
                        "close_result": close_result,
                    })
                    
                    state.trades_closed += 1'''
    },
    
    # Patch 4: Regime exit
    {
        "name": "Regime exit outcome logging",
        "old": '''            if should_close:
                logger.warning(f"[REGIME EXIT] {symbol_clean}: {reason}")
                
                # Close the position
                close_result = close_position_manually(symbol, side, size)
                order_id = close_result.get("order_id")
                
                # Update tracker
                tracker.close_trade(symbol, {
                    "reason": f"regime_exit_{regime['regime'].lower()}",
                    "pnl": pnl,
                    "regime": regime["regime"],
                })
                
                state.trades_closed += 1''',
        "new": '''            if should_close:
                logger.warning(f"[REGIME EXIT] {symbol_clean}: {reason}")
                
                # Close the position
                close_result = close_position_manually(symbol, side, size)
                order_id = close_result.get("order_id")
                
                # V3.1.25: Log RL outcome
                if RL_ENABLED and rl_collector:
                    try:
                        trade = tracker.get_trade(symbol)
                        if trade:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            entry = trade.get("entry_price", 1)
                            pnl_pct = (pnl / (entry * size)) * 100 if entry and size else 0
                            rl_collector.log_outcome(symbol, pnl_pct, hours_open, "REGIME_EXIT")
                    except Exception as e:
                        logger.debug(f"RL outcome log error: {e}")
                
                # Update tracker
                tracker.close_trade(symbol, {
                    "reason": f"regime_exit_{regime['regime'].lower()}",
                    "pnl": pnl,
                    "regime": regime["regime"],
                })
                
                state.trades_closed += 1'''
    },
    
    # Patch 5: Quick cleanup
    {
        "name": "Quick cleanup outcome logging",
        "old": '''    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                position_closed = True''',
        "new": '''    try:
        for symbol in tracker.get_active_symbols():
            position = check_position_status(symbol)
            
            if not position.get("is_open"):
                logger.info(f"Quick check: {symbol} closed")
                
                # V3.1.25: Log RL outcome
                if RL_ENABLED and rl_collector:
                    try:
                        trade = tracker.get_trade(symbol)
                        if trade:
                            opened_at = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00"))
                            hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                            tier_cfg = get_tier_config(trade.get("tier", get_tier_for_symbol(symbol)))
                            est_pnl = tier_cfg.get("tp_pct", 2.0)
                            rl_collector.log_outcome(symbol, est_pnl, hours_open, "TP_SL")
                    except Exception as e:
                        logger.debug(f"RL outcome log error: {e}")
                
                cleanup = cancel_all_orders_for_symbol(symbol)
                tracker.close_trade(symbol, {"reason": "tp_sl_hit", "cleanup": cleanup})
                state.trades_closed += 1
                position_closed = True'''
    },
]


def main():
    print("=" * 60)
    print("SMT Daemon Patch - V3.1.25 (RL Outcome Logging)")
    print("=" * 60)
    
    if not os.path.exists(DAEMON_FILE):
        print(f"ERROR: {DAEMON_FILE} not found")
        print("Run from ~/smt-weex-trading-bot/v3/")
        sys.exit(1)
    
    # Read file
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    # Backup
    backup = DAEMON_FILE + ".bak"
    with open(backup, "w") as f:
        f.write(content)
    print(f"Backup: {backup}")
    
    # Apply patches
    applied = 0
    for p in PATCHES:
        if p["old"] in content:
            content = content.replace(p["old"], p["new"], 1)
            print(f"[OK] {p['name']}")
            applied += 1
        else:
            print(f"[SKIP] {p['name']} (already done or not found)")
    
    # Save
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    # Verify
    print("\nVerifying syntax...")
    if os.system(f"python3 -m py_compile {DAEMON_FILE}") == 0:
        print("Syntax OK")
    else:
        print("SYNTAX ERROR - restoring backup")
        os.system(f"cp {backup} {DAEMON_FILE}")
        sys.exit(1)
    
    # Create data dir
    os.makedirs("rl_training_data", exist_ok=True)
    
    print(f"\nDone: {applied} patches applied")
    print("\nRestart daemon:")
    print("  pkill -f smt_daemon")
    print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")


if __name__ == "__main__":
    main()
