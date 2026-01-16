#!/usr/bin/env python3
"""
SMT V3.1.7 - Direct Injection of Regime Exit
=============================================
This script directly modifies the daemon to add regime exit.
Works regardless of previous patches.

Run: python3 inject_regime_exit.py
"""

import os
import re

def inject_regime_exit():
    filename = "smt_daemon_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    # Backup
    backup_name = f"{filename}.bak.before_regime"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup: {backup_name}")
    
    # Check if already has regime exit
    if "def regime_aware_exit_check" in content:
        print("regime_aware_exit_check already exists - removing old version first")
        # Remove old function
        content = re.sub(
            r'# ={10,}\n# V3\.1\.[67]: REGIME-AWARE SMART EXIT\n# ={10,}\n.*?(?=\ndef run_daemon|\nclass |\n# ={10,}\n# [A-Z])',
            '',
            content,
            flags=re.DOTALL
        )
    
    # Add imports if needed
    if "import requests" not in content:
        content = content.replace(
            "import traceback",
            "import traceback\nimport requests"
        )
        print("  [OK] Added requests import")
    
    # Add WEEX_BASE_URL to imports if needed
    if "WEEX_BASE_URL" not in content:
        content = content.replace(
            "from smt_nightly_trade_v3_1 import (",
            "from smt_nightly_trade_v3_1 import (\n        WEEX_BASE_URL,"
        )
        print("  [OK] Added WEEX_BASE_URL import")
    
    # The regime exit function to inject
    regime_function = '''

# ============================================================
# V3.1.7: REGIME-AWARE SMART EXIT
# ============================================================

def get_market_regime_for_exit():
    """Get BTC 24h trend for regime-based exits"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            # BEARISH if 24h down >2% OR (24h down and 4h down >1.5%)
            if change_24h < -2 or (change_24h < 0 and change_4h < -1.5):
                return {"regime": "BEARISH", "change_24h": change_24h, "change_4h": change_4h}
            # BULLISH if 24h up >2% OR (24h up and 4h up >1.5%)
            elif change_24h > 2 or (change_24h > 0 and change_4h > 1.5):
                return {"regime": "BULLISH", "change_24h": change_24h, "change_4h": change_4h}
    except Exception as e:
        logger.debug(f"Regime check error: {e}")
    
    return {"regime": "NEUTRAL", "change_24h": 0, "change_4h": 0}


def regime_aware_exit_check():
    """
    V3.1.7: AI cuts positions fighting the market regime.
    
    Logic:
    - BEARISH market + LONG losing > $8 = AI closes position
    - BULLISH market + SHORT losing > $8 = AI closes position
    
    This frees margin for regime-aligned trades.
    """
    try:
        positions = get_open_positions()
        if not positions:
            return
        
        regime = get_market_regime_for_exit()
        
        logger.info(f"[REGIME] Market: {regime['regime']} | 24h: {regime['change_24h']:+.1f}% | 4h: {regime['change_4h']:+.1f}%")
        
        if regime["regime"] == "NEUTRAL":
            logger.info("[REGIME] Neutral market - no regime exits needed")
            return
        
        closed_count = 0
        
        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            pnl = float(pos.get('unrealized_pnl', 0))
            size = float(pos['size'])
            
            symbol_clean = symbol.replace('cmt_', '').upper()
            
            should_close = False
            reason = ""
            
            # LONG losing in BEARISH market
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -8:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -8:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            if should_close:
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
                
                state.trades_closed += 1
                state.early_exits += 1
                closed_count += 1
                
                # Upload AI log
                upload_ai_log_to_weex(
                    stage=f"V3.1.7 Regime Exit: {side} {symbol_clean}",
                    input_data={
                        "symbol": symbol,
                        "side": side,
                        "size": size,
                        "unrealized_pnl": pnl,
                        "market_regime": regime["regime"],
                        "btc_24h_change": regime["change_24h"],
                        "btc_4h_change": regime["change_4h"],
                    },
                    output_data={
                        "action": "CLOSE",
                        "ai_decision": "REGIME_EXIT",
                        "reason": reason,
                    },
                    explanation=f"AI Regime Exit: {reason}. Cutting position fighting the trend to free margin for {regime['regime']}-aligned opportunities.",
                    order_id=order_id
                )
                
                logger.info(f"[REGIME EXIT] Closed {symbol_clean}, order: {order_id}")
        
        if closed_count > 0:
            logger.info(f"[REGIME EXIT] Closed {closed_count} positions fighting the trend")
            # Trigger signal check to find new opportunities
            logger.info("[REGIME EXIT] Checking for new opportunities...")
            check_trading_signals()
        else:
            logger.info("[REGIME] No positions need regime exit")
            
    except Exception as e:
        logger.error(f"[REGIME EXIT] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

'''

    # Find where to insert (before run_daemon)
    if "def run_daemon():" in content:
        content = content.replace(
            "def run_daemon():",
            regime_function + "\ndef run_daemon():"
        )
        print("  [OK] Injected regime_aware_exit_check function")
    else:
        print("  [ERROR] Could not find run_daemon()")
        return False
    
    # Now add the call in the main loop after monitor_positions()
    # Look for the pattern and add regime check
    
    old_pattern = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                last_position = now'''
    
    new_pattern = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                regime_aware_exit_check()  # V3.1.7: Cut positions fighting regime
                last_position = now'''
    
    if old_pattern in content:
        content = content.replace(old_pattern, new_pattern)
        print("  [OK] Added regime_aware_exit_check() call to main loop")
    elif "regime_aware_exit_check()" in content:
        print("  [SKIP] regime_aware_exit_check() call already in loop")
    else:
        print("  [WARN] Could not find main loop pattern - manual edit needed")
    
    # Update version strings
    content = content.replace("V3.1.4", "V3.1.7")
    content = content.replace("V3.1.5", "V3.1.7")
    content = content.replace("V3.1.6", "V3.1.7")
    content = content.replace("v3.1.4", "v3.1.7")
    content = content.replace("v3.1.5", "v3.1.7")
    content = content.replace("v3.1.6", "v3.1.7")
    content = content.replace("v3_1_4", "v3_1_7")
    content = content.replace("v3_1_5", "v3_1_7")
    content = content.replace("v3_1_6", "v3_1_7")
    print("  [OK] Updated version strings to V3.1.7")
    
    # Write file
    with open(filename, 'w') as f:
        f.write(content)
    
    print("\nDaemon updated!")
    return True


def verify_injection():
    """Verify the injection worked"""
    filename = "smt_daemon_v3_1.py"
    
    with open(filename, 'r') as f:
        content = f.read()
    
    checks = [
        ("regime_aware_exit_check function", "def regime_aware_exit_check" in content),
        ("get_market_regime_for_exit function", "def get_market_regime_for_exit" in content),
        ("regime check call in loop", "regime_aware_exit_check()" in content),
        ("WEEX_BASE_URL import", "WEEX_BASE_URL" in content),
        ("requests import", "import requests" in content),
    ]
    
    print("\nVerification:")
    all_ok = True
    for name, ok in checks:
        status = "OK" if ok else "MISSING"
        print(f"  [{status}] {name}")
        if not ok:
            all_ok = False
    
    return all_ok


def main():
    print("=" * 60)
    print("SMT V3.1.7 - REGIME EXIT INJECTION")
    print("=" * 60)
    print()
    print("This injects regime-aware exit directly into daemon.")
    print()
    print("What it does:")
    print("  - Every 2 min, checks market regime (BTC 24h trend)")
    print("  - BEARISH + LONG losing >$8 = AI closes position")
    print("  - BULLISH + SHORT losing >$8 = AI closes position")
    print("  - Logs [REGIME] messages so you can see it working")
    print()
    
    success = inject_regime_exit()
    
    if success:
        verify_injection()
    
    print()
    print("=" * 60)
    if success:
        print("INJECTION COMPLETE!")
        print()
        print("Test syntax:")
        print("  python3 -m py_compile smt_daemon_v3_1.py")
        print()
        print("Restart daemon:")
        print("  pkill -f smt_daemon")
        print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
        print("  tail -f daemon.log")
        print()
        print("You should see [REGIME] messages every 2 minutes!")
    else:
        print("INJECTION FAILED - Check errors above")
    print("=" * 60)


if __name__ == "__main__":
    main()
