#!/usr/bin/env python3
"""
SMT V3.1.6 Patch - Add Regime-Aware Smart Exit
===============================================
Adds Feature 10: Regime-Aware Smart Exit

This feature closes LONG positions that are losing in BEARISH regime,
and SHORT positions that are losing in BULLISH regime.

The AI logic:
- If market regime is BEARISH (BTC down >2% in 24h) AND position is LONG AND losing >$10
  -> AI decides to cut the loser to free margin for SHORT opportunities
- Same logic applies in reverse for BULLISH regime

Run: python3 add_regime_exit.py
"""

import os
import re

def add_regime_exit_to_daemon():
    filename = "smt_daemon_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    # Backup
    backup_name = f"{filename}.bak.pre_regime_exit"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup created: {backup_name}")
    
    # Check if already patched
    if "regime_aware_exit" in content or "REGIME-AWARE" in content:
        print("Regime-aware exit already present!")
        return True
    
    # Find the monitor_positions function and add regime exit logic
    # We'll add a new function and call it from monitor_positions
    
    new_function = '''

# ============================================================
# V3.1.6: REGIME-AWARE SMART EXIT
# ============================================================

def get_market_regime_for_exit() -> Dict:
    """Get current market regime for exit decisions"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            
            if change_24h < -2:
                return {"regime": "BEARISH", "change_24h": change_24h, "bias": "SHORT"}
            elif change_24h > 2:
                return {"regime": "BULLISH", "change_24h": change_24h, "bias": "LONG"}
            else:
                return {"regime": "NEUTRAL", "change_24h": change_24h, "bias": "NONE"}
    except Exception as e:
        logger.debug(f"Regime check error: {e}")
    
    return {"regime": "NEUTRAL", "change_24h": 0, "bias": "NONE"}


def regime_aware_exit_check():
    """
    V3.1.6 Feature 10: Regime-Aware Smart Exit
    
    AI analyzes positions against market regime and cuts losers that fight the trend.
    - BEARISH regime + LONG losing > threshold = EXIT
    - BULLISH regime + SHORT losing > threshold = EXIT
    
    This frees up margin for regime-aligned trades.
    """
    try:
        positions = get_open_positions()
        if len(positions) < MAX_OPEN_POSITIONS:
            # Only cut losers if we're at max capacity
            return
        
        regime = get_market_regime_for_exit()
        
        if regime["regime"] == "NEUTRAL":
            return  # Don't cut in neutral regime
        
        logger.info(f"[REGIME EXIT] Checking positions in {regime['regime']} regime (24h: {regime['change_24h']:+.1f}%)")
        
        # Find positions fighting the trend
        candidates = []
        
        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            pnl = pos['unrealized_pnl']
            size = pos['size']
            
            # LONG in BEARISH regime and losing
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -10:
                candidates.append({
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "pnl": pnl,
                    "reason": f"LONG fighting BEARISH trend, losing ${abs(pnl):.2f}"
                })
            
            # SHORT in BULLISH regime and losing
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -10:
                candidates.append({
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "pnl": pnl,
                    "reason": f"SHORT fighting BULLISH trend, losing ${abs(pnl):.2f}"
                })
        
        if not candidates:
            return
        
        # Sort by worst PnL first
        candidates.sort(key=lambda x: x['pnl'])
        
        # Close the worst 1-2 positions to free up margin
        max_to_close = min(2, len(candidates))
        
        for i in range(max_to_close):
            pos = candidates[i]
            symbol = pos['symbol']
            side = pos['side']
            size = pos['size']
            pnl = pos['pnl']
            reason = pos['reason']
            
            symbol_clean = symbol.replace('cmt_', '').upper()
            
            logger.warning(f"[REGIME EXIT] AI closing {side} {symbol_clean}: {reason}")
            
            # Execute close
            close_result = close_position_manually(symbol, side, size)
            
            # Update tracker
            tracker.close_trade(symbol, {
                "reason": f"regime_exit_{regime['regime'].lower()}",
                "pnl": pnl,
                "regime": regime['regime'],
                "change_24h": regime['change_24h'],
            })
            
            state.trades_closed += 1
            state.early_exits += 1
            
            # Upload AI log
            upload_ai_log_to_weex(
                stage=f"V3.1.6 Regime Exit: {side} {symbol_clean}",
                input_data={
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "unrealized_pnl": pnl,
                    "market_regime": regime['regime'],
                    "btc_24h_change": regime['change_24h'],
                },
                output_data={
                    "action": "CLOSE",
                    "ai_decision": "CUT_LOSER",
                    "reason": reason,
                },
                explanation=f"AI Regime-Aware Exit: {reason}. Market is {regime['regime']} (BTC 24h: {regime['change_24h']:+.1f}%). Cutting position fighting the trend to free margin for {regime['bias']} opportunities."
            )
            
            logger.info(f"[REGIME EXIT] Closed {symbol_clean}, freed ${abs(pnl):.2f} margin")
        
        # Trigger signal check to find new opportunities
        logger.info("[REGIME EXIT] Checking for regime-aligned opportunities...")
        check_trading_signals()
        
    except Exception as e:
        logger.error(f"[REGIME EXIT] Error: {e}")

'''

    # Find where to insert (before run_daemon function)
    insert_marker = "def run_daemon():"
    
    if insert_marker in content:
        content = content.replace(insert_marker, new_function + "\n" + insert_marker)
        print("  [OK] Added regime_aware_exit_check function")
    else:
        print("  [ERROR] Could not find insertion point")
        return False
    
    # Now add the call to regime_aware_exit_check in the main loop
    # Find the position monitor call and add regime check after it
    
    old_loop = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                last_position = now'''
    
    new_loop = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                regime_aware_exit_check()  # V3.1.6: Check for regime-fighting positions
                last_position = now'''
    
    if old_loop in content:
        content = content.replace(old_loop, new_loop)
        print("  [OK] Added regime_aware_exit_check call to main loop")
    else:
        print("  [WARN] Could not add to main loop - may need manual edit")
    
    # Add required imports if not present
    if "from smt_nightly_trade_v3_1 import" in content:
        # Check if WEEX_BASE_URL is imported
        if "WEEX_BASE_URL" not in content.split("from smt_nightly_trade_v3_1 import")[1].split(")")[0]:
            content = content.replace(
                "from smt_nightly_trade_v3_1 import (",
                "from smt_nightly_trade_v3_1 import (\n        WEEX_BASE_URL,"
            )
            print("  [OK] Added WEEX_BASE_URL import")
    
    # Add requests import if needed
    if "import requests" not in content:
        content = content.replace(
            "import traceback",
            "import traceback\nimport requests"
        )
        print("  [OK] Added requests import")
    
    # Write updated file
    with open(filename, 'w') as f:
        f.write(content)
    
    print("\nRegime-Aware Exit feature added!")
    return True


def main():
    print("=" * 60)
    print("SMT V3.1.6 - Add Regime-Aware Smart Exit")
    print("=" * 60)
    print()
    print("This adds Feature 10: Regime-Aware Smart Exit")
    print()
    print("Logic:")
    print("  - If BEARISH regime + LONG losing > $10 = AI cuts loser")
    print("  - If BULLISH regime + SHORT losing > $10 = AI cuts loser")
    print("  - Frees margin for regime-aligned trades")
    print()
    
    success = add_regime_exit_to_daemon()
    
    print()
    print("=" * 60)
    if success:
        print("PATCH COMPLETE!")
        print()
        print("Next steps:")
        print("  pkill -f smt_daemon")
        print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
        print("  tail -f daemon.log")
    else:
        print("PATCH FAILED - Check errors above")
    print("=" * 60)


if __name__ == "__main__":
    main()
