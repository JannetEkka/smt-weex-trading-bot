#!/usr/bin/env python3
"""
SMT V3.1.7 Patch - Fix 24h Trend + Regime Exit Integration
==========================================================

Fixes:
1. JUDGE uses 24h BTC trend (not just 4h) for regime detection
2. Regime exit check properly integrated into daemon loop
3. Always shows regime in JUDGE output

Run: python3 patch_v317_trend_fix.py
"""

import os
import re

def patch_nightly_trade():
    """Fix JUDGE to use 24h trend"""
    filename = "smt_nightly_trade_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    # Backup
    backup_name = f"{filename}.bak.v316"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup: {backup_name}")
    
    changes = 0
    
    # Fix 1: Update version
    if "v3.1.6" in content.lower() or "V3.1.6" in content:
        content = content.replace("V3.1.6", "V3.1.7")
        content = content.replace("v3.1.6", "v3.1.7")
        content = content.replace("v3_1_6", "v3_1_7")
        changes += 1
        print("  [OK] Version -> V3.1.7")
    
    # Fix 2: Make sure _get_market_regime exists and uses 24h
    # Check if the old _get_btc_trend exists and replace it
    if "_get_btc_trend" in content and "_get_market_regime" not in content:
        # Old version - needs full replacement
        old_trend_method = '''    def _get_btc_trend(self) -> str:
        """Check BTC 4h trend - returns 'UP', 'DOWN', or 'NEUTRAL'
        
        This prevents going LONG on altcoins when BTC is dumping,
        or going SHORT when BTC is pumping.
        """
        import time as time_module
        
        # Cache for 15 minutes
        if time_module.time() - self._btc_trend_cache["timestamp"] < 900:
            return self._btc_trend_cache["trend"]
        
        try:
            # Get BTC 4h candles
            url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=6"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            if isinstance(data, list) and len(data) >= 3:
                # Get last 3 candles closes (most recent first)
                closes = [float(c[4]) for c in data[:3]]
                
                # Calculate 4h change
                if len(closes) >= 2:
                    change_pct = ((closes[0] - closes[1]) / closes[1]) * 100
                    
                    if change_pct > 1.0:  # BTC up more than 1% in 4h
                        trend = "UP"
                    elif change_pct < -1.0:  # BTC down more than 1% in 4h
                        trend = "DOWN"
                    else:
                        trend = "NEUTRAL"
                    
                    self._btc_trend_cache = {"trend": trend, "timestamp": time_module.time()}
                    print(f"  [JUDGE] BTC 4h trend: {trend} ({change_pct:+.2f}%)")
                    return trend
        except Exception as e:
            print(f"  [JUDGE] BTC trend check error: {e}")
        
        return "NEUTRAL"'''
        
        new_trend_method = '''    def _get_market_regime(self) -> dict:
        """Check BTC 24h trend for market regime detection.
        
        V3.1.7: Uses 24h trend (not just 4h) for better regime detection.
        Returns dict with regime, bias, and change percentages.
        """
        import time as time_module
        
        # Cache for 15 minutes
        cache_valid = (
            time_module.time() - self._btc_trend_cache.get("timestamp", 0) < 900
            and "regime" in self._btc_trend_cache
        )
        if cache_valid:
            return self._btc_trend_cache
        
        result = {
            "regime": "NEUTRAL",
            "bias": "NONE",
            "change_4h": 0,
            "change_24h": 0,
            "timestamp": time_module.time()
        }
        
        try:
            # Get BTC 4h candles (7 candles = 28h of data)
            url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
            r = requests.get(url, timeout=10)
            data = r.json()
            
            if isinstance(data, list) and len(data) >= 7:
                closes = [float(c[4]) for c in data]
                
                # 4h change
                change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
                
                # 24h change (6 candles ago)
                change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
                
                result["change_4h"] = change_4h
                result["change_24h"] = change_24h
                
                # Determine regime based on 24h trend (primary) and 4h (secondary)
                if change_24h < -2.0:
                    result["regime"] = "BEARISH"
                    result["bias"] = "SHORT"
                elif change_24h > 2.0:
                    result["regime"] = "BULLISH"
                    result["bias"] = "LONG"
                elif change_4h < -1.5:
                    result["regime"] = "BEARISH"
                    result["bias"] = "SHORT"
                elif change_4h > 1.5:
                    result["regime"] = "BULLISH"
                    result["bias"] = "LONG"
                
                print(f"  [JUDGE] Market: {result['regime']} | 24h: {change_24h:+.1f}% | 4h: {change_4h:+.1f}%")
                
        except Exception as e:
            print(f"  [JUDGE] Market regime error: {e}")
        
        self._btc_trend_cache = result
        return result'''
        
        if old_trend_method in content:
            content = content.replace(old_trend_method, new_trend_method)
            changes += 1
            print("  [OK] Replaced _get_btc_trend with _get_market_regime")
    
    # Fix 3: Update decide() to use _get_market_regime and always show it
    # Find where btc_trend is used and replace with regime
    if "btc_trend = self._get_btc_trend()" in content:
        content = content.replace(
            "btc_trend = self._get_btc_trend()",
            "regime = self._get_market_regime()"
        )
        content = content.replace(
            'if decision == "LONG" and btc_trend == "DOWN":',
            'if decision == "LONG" and regime["regime"] == "BEARISH":'
        )
        content = content.replace(
            'f"BLOCKED: LONG signal but BTC trending DOWN. Don\'t fight the market!"',
            'f"BLOCKED: LONG in BEARISH regime (24h: {regime[\'change_24h\']:+.1f}%)"'
        )
        changes += 1
        print("  [OK] Updated decide() to use regime")
    
    # Fix 4: Make sure regime is always checked (not just for non-BTC)
    # Find the "if pair != 'BTC':" block and update it
    old_btc_check = '''        # V3.1.4: MARKET TREND FILTER - Don't fight the trend!
        if pair != "BTC":  # Don't check BTC against itself
            btc_trend = self._get_btc_trend()
            
            if decision == "LONG" and btc_trend == "DOWN":
                return self._wait_decision(f"BLOCKED: LONG signal but BTC trending DOWN. Don't fight the market!", persona_votes, vote_summary)
            
            if decision == "SHORT" and btc_trend == "UP":
                # Less strict for shorts - only block if very strong uptrend
                # This allows shorting during mild uptrends (mean reversion)
                pass  # Allow shorts even in uptrend for now'''
    
    new_regime_check = '''        # V3.1.7: MARKET REGIME FILTER - Always check, use 24h trend
        regime = self._get_market_regime()
        
        # Block trades fighting strong regime (unless very high confidence)
        if decision == "LONG" and regime["regime"] == "BEARISH":
            if confidence < 0.80:
                return self._wait_decision(
                    f"BLOCKED: LONG in BEARISH regime (24h: {regime['change_24h']:+.1f}%)",
                    persona_votes, vote_summary
                )
        
        if decision == "SHORT" and regime["regime"] == "BULLISH":
            if confidence < 0.80:
                return self._wait_decision(
                    f"BLOCKED: SHORT in BULLISH regime (24h: {regime['change_24h']:+.1f}%)",
                    persona_votes, vote_summary
                )'''
    
    if old_btc_check in content:
        content = content.replace(old_btc_check, new_regime_check)
        changes += 1
        print("  [OK] Updated regime filter to use 24h and check all pairs")
    
    # Write file
    with open(filename, 'w') as f:
        f.write(content)
    
    print(f"\nNightly trade: {changes} changes")
    return changes > 0


def patch_daemon():
    """Add regime exit check to daemon"""
    filename = "smt_daemon_v3_1.py"
    
    if not os.path.exists(filename):
        print(f"ERROR: {filename} not found!")
        return False
    
    with open(filename, 'r') as f:
        content = f.read()
    
    # Backup
    backup_name = f"{filename}.bak.v316"
    with open(backup_name, 'w') as f:
        f.write(content)
    print(f"Backup: {backup_name}")
    
    changes = 0
    
    # Update version
    content = content.replace("V3.1.6", "V3.1.7")
    content = content.replace("v3.1.6", "v3.1.7")
    content = content.replace("v3_1_6", "v3_1_7")
    changes += 1
    print("  [OK] Version -> V3.1.7")
    
    # Check if regime_aware_exit already exists
    if "def regime_aware_exit_check" in content:
        print("  [SKIP] regime_aware_exit_check already exists")
    else:
        # Add the function before run_daemon
        regime_exit_function = '''
# ============================================================
# V3.1.7: REGIME-AWARE SMART EXIT
# ============================================================

def get_market_regime_for_exit():
    """Get current market regime for exit decisions"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=7"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 7:
            closes = [float(c[4]) for c in data]
            change_24h = ((closes[0] - closes[6]) / closes[6]) * 100
            change_4h = ((closes[0] - closes[1]) / closes[1]) * 100
            
            if change_24h < -2 or (change_24h < 0 and change_4h < -1.5):
                return {"regime": "BEARISH", "change_24h": change_24h, "change_4h": change_4h}
            elif change_24h > 2 or (change_24h > 0 and change_4h > 1.5):
                return {"regime": "BULLISH", "change_24h": change_24h, "change_4h": change_4h}
    except Exception as e:
        logger.debug(f"Regime check error: {e}")
    
    return {"regime": "NEUTRAL", "change_24h": 0, "change_4h": 0}


def regime_aware_exit_check():
    """
    V3.1.7: AI closes positions fighting the market regime.
    
    - BEARISH regime + LONG losing > $8 = CUT
    - BULLISH regime + SHORT losing > $8 = CUT
    """
    try:
        positions = get_open_positions()
        if not positions:
            return
        
        regime = get_market_regime_for_exit()
        
        if regime["regime"] == "NEUTRAL":
            return
        
        logger.info(f"[REGIME] Checking in {regime['regime']} market (24h: {regime['change_24h']:+.1f}%)")
        
        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            pnl = float(pos.get('unrealized_pnl', 0))
            size = float(pos['size'])
            
            symbol_clean = symbol.replace('cmt_', '').upper()
            
            should_close = False
            reason = ""
            
            # LONG losing in BEARISH = cut
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -8:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market"
            
            # SHORT losing in BULLISH = cut  
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -8:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market"
            
            if should_close:
                logger.warning(f"[REGIME EXIT] {symbol_clean}: {reason}")
                
                close_result = close_position_manually(symbol, side, size)
                
                tracker.close_trade(symbol, {
                    "reason": f"regime_exit_{regime['regime'].lower()}",
                    "pnl": pnl,
                })
                
                state.trades_closed += 1
                state.early_exits += 1
                
                upload_ai_log_to_weex(
                    stage=f"V3.1.7 Regime Exit: {side} {symbol_clean}",
                    input_data={
                        "symbol": symbol, "side": side, "pnl": pnl,
                        "regime": regime["regime"], "change_24h": regime["change_24h"],
                    },
                    output_data={"action": "CLOSE", "reason": reason},
                    explanation=f"AI Regime Exit: {reason}. Market is {regime['regime']} (BTC 24h: {regime['change_24h']:+.1f}%). Cutting to free margin.",
                    order_id=close_result.get("order_id")
                )
                
                logger.info(f"[REGIME EXIT] Closed {symbol_clean}")
                
    except Exception as e:
        logger.error(f"[REGIME EXIT] Error: {e}")


'''
        
        # Insert before run_daemon
        if "def run_daemon():" in content:
            content = content.replace(
                "def run_daemon():",
                regime_exit_function + "def run_daemon():"
            )
            changes += 1
            print("  [OK] Added regime_aware_exit_check function")
    
    # Add call to regime exit in main loop
    old_monitor_call = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                last_position = now'''
    
    new_monitor_call = '''            if now - last_position >= POSITION_MONITOR_INTERVAL:
                monitor_positions()
                regime_aware_exit_check()  # V3.1.7: Cut positions fighting regime
                last_position = now'''
    
    if old_monitor_call in content and "regime_aware_exit_check()" not in content.split("if now - last_position")[1].split("last_position = now")[0]:
        content = content.replace(old_monitor_call, new_monitor_call)
        changes += 1
        print("  [OK] Added regime exit call to main loop")
    elif "regime_aware_exit_check()" in content:
        print("  [SKIP] Regime exit call already in loop")
    
    # Make sure imports are there
    if "WEEX_BASE_URL" not in content:
        # Add to imports
        if "from smt_nightly_trade_v3_1 import (" in content:
            old_import = "from smt_nightly_trade_v3_1 import ("
            new_import = "from smt_nightly_trade_v3_1 import (\n    WEEX_BASE_URL,"
            content = content.replace(old_import, new_import)
            changes += 1
            print("  [OK] Added WEEX_BASE_URL import")
    
    if "import requests" not in content:
        content = content.replace(
            "import traceback",
            "import traceback\nimport requests"
        )
        changes += 1
        print("  [OK] Added requests import")
    
    with open(filename, 'w') as f:
        f.write(content)
    
    print(f"\nDaemon: {changes} changes")
    return changes > 0


def main():
    print("=" * 60)
    print("SMT V3.1.7 PATCH - 24h Trend + Regime Exit")
    print("=" * 60)
    print()
    print("Fixes:")
    print("  1. JUDGE uses 24h BTC trend (not just 4h)")
    print("  2. Regime check for ALL pairs (including BTC)")
    print("  3. Regime exit properly integrated")
    print("  4. Cuts LONGs in BEARISH, SHORTs in BULLISH")
    print()
    
    print("Patching nightly trade...")
    print("-" * 40)
    s1 = patch_nightly_trade()
    
    print()
    print("Patching daemon...")
    print("-" * 40)
    s2 = patch_daemon()
    
    print()
    print("=" * 60)
    if s1 or s2:
        print("PATCH COMPLETE!")
        print()
        print("Next:")
        print("  pkill -f smt_daemon")
        print("  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
        print("  tail -f daemon.log")
        print()
        print("Commit:")
        print("  git add .")
        print('  git commit -m "V3.1.7: 24h regime detection, regime exit for all pairs"')
        print("  git push")
    else:
        print("No changes needed or errors occurred")
    print("=" * 60)


if __name__ == "__main__":
    main()
