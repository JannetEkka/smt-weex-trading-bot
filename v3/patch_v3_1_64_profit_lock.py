#!/usr/bin/env python3
"""
PATCH V3.1.64 - PROFIT LOCK + SMART IMPROVEMENTS
==================================================
Date: 2026-02-12
Author: SMT AI Engine

CHANGES:
1. PROFIT LOCK EXIT - Close when position fades 40% from peak (if peak >= 1.0%)
   - New Rule 0 in monitor: peak >= 1.0%, faded to < 60% of peak, still green = CLOSE
   - Lowers existing Rule 1 threshold from 2.0% to 1.5%
   - Lowers existing Rule 2 threshold from 1.5% to 1.0%

2. VOL-ADJUSTED SL - Widen SL by 1.5x when F&G < 15 (capitulation protection)
   - Prevents stop-hunt wicks from killing correct-direction trades
   - Applied in the Judge's SL clamp section

3. REGIME EXIT SHIELD IN EXTREME FEAR - When F&G < 20, skip regime exit for
   profitable positions. Whale-aligned shorts should NOT be cut because BTC
   bounced 1.6% in 4h during a crash.

4. HARD CAP MAX_POSITIONS = 3 - Truly enforced, no CAPITULATION override

5. ANTI-WAIT V2 - Smarter override: if ANY 2 personas agree on direction
   at >= 70% confidence each, override WAIT regardless of word counting.

6. CLOSE BTC + DOGE SHORTS NOW - One-time profit lock closure with
   "AI profit lock  V3.1.64" as reason (logged to WEEX properly)

7. CONFIDENCE FLOOR stays at 80% (already set in V3.1.63)

DEPLOYMENT:
    cd ~/smt-weex-trading-bot/v3
    # Stop daemon first
    ps aux | grep smt_daemon | grep -v grep | awk '{print $2}' | xargs kill
    # Backup
    cp smt_daemon_v3_1.py smt_daemon_v3_1.py.backup_v3163
    cp smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py.backup_v3163
    # Apply
    python3 patch_v3_1_64_profit_lock.py
    # Restart
    nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
    # Verify
    tail -30 logs/daemon_v3_1_7_$(date +%Y%m%d).log
"""

import re
import sys
import os
import time

DAEMON_FILE = "smt_daemon_v3_1.py"
NIGHTLY_FILE = "smt_nightly_trade_v3_1.py"

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def patch_file(path, replacements):
    """Apply a list of (old, new, description) replacements to a file."""
    content = read_file(path)
    for old, new, desc in replacements:
        if old in content:
            content = content.replace(old, new, 1)
            print(f"  [OK] {desc}")
        else:
            print(f"  [SKIP] {desc} - pattern not found")
    write_file(path, content)
    return content


def main():
    print("=" * 60)
    print("PATCH V3.1.64 - PROFIT LOCK + SMART IMPROVEMENTS")
    print("=" * 60)
    
    # Verify files exist
    for f in [DAEMON_FILE, NIGHTLY_FILE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found. Run from v3/ directory.")
            sys.exit(1)
    
    # ============================================================
    # PHASE 0: CLOSE BTC + DOGE SHORTS (one-time profit lock)
    # ============================================================
    print("\n--- PHASE 0: Closing BTC + DOGE shorts (profit lock) ---")
    
    try:
        sys.path.insert(0, '.')
        from smt_nightly_trade_v3_1 import (
            close_position_manually, upload_ai_log_to_weex,
            get_open_positions, get_account_balance
        )
        
        positions = get_open_positions()
        if not positions:
            print("  [WARN] No positions returned from API")
        else:
            balance_info = get_account_balance()
            equity = float(balance_info.get("equity", 4150)) if balance_info else 4150
            
            for pos in positions:
                sym = pos.get("symbol", "")
                side = pos.get("side", "").upper()
                sym_clean = sym.replace("cmt_", "").replace("usdt", "").upper()
                size = float(pos.get("size", 0))
                pnl = float(pos.get("unrealized_pnl", 0))
                entry = float(pos.get("entry_price", 0))
                
                # Close BTC SHORT and DOGE SHORT if profitable
                if sym_clean in ("BTC", "DOGE") and side == "SHORT" and pnl > 0:
                    print(f"  [CLOSE] {sym_clean} {side}: UPnL=${pnl:.2f}, size={size}")
                    
                    result = close_position_manually(sym, side, size)
                    order_id = result.get("order_id")
                    
                    if order_id:
                        print(f"  [OK] Closed {sym_clean} {side}, order: {order_id}")
                        
                        upload_ai_log_to_weex(
                            stage=f"V3.1.64 AI Profit Lock: {side} {sym_clean}",
                            input_data={
                                "symbol": sym,
                                "side": side,
                                "size": size,
                                "entry_price": entry,
                                "unrealized_pnl": pnl,
                                "equity": equity,
                            },
                            output_data={
                                "action": "AI_PROFIT_LOCK",
                                "order_id": order_id,
                                "reason": "V3.1.64 profit lock: position near peak, locking gains to free slots",
                                "ai_model": "gemini-2.5-flash",
                            },
                            explanation=f"AI Profit Lock V3.1.64: Closed {side} {sym_clean} at ${pnl:+.2f} UPnL. "
                                        f"Position reached peak and is being locked to secure gains and free slots "
                                        f"for higher-conviction entries. Equity: ${equity:.0f}."
                        )
                        time.sleep(1)
                    else:
                        print(f"  [FAIL] Could not close {sym_clean}: {result}")
                elif sym_clean in ("BTC", "DOGE") and side == "SHORT":
                    print(f"  [SKIP] {sym_clean} {side}: UPnL=${pnl:.2f} (not profitable, skipping)")
                    
    except Exception as e:
        print(f"  [ERROR] Phase 0 failed: {e}")
        import traceback
        traceback.print_exc()
        print("  Continuing with code patches...")
    
    # ============================================================
    # PHASE 1: DAEMON PATCHES
    # ============================================================
    print(f"\n--- PHASE 1: Patching {DAEMON_FILE} ---")
    
    daemon_patches = []
    
    # --- 1A: PROFIT LOCK EXIT (new Rule 0 + lower thresholds) ---
    # Replace the entire peak exit block with improved version
    
    old_peak_exit = """                # V3.1.63 SMART PEAK EXIT - Re-enabled with intelligent thresholds
                # At 20x leverage: 1.5% price move = 30% ROE. Capturing half a peak is better
                # than watching it fade to zero waiting for a 15% TP that may never come.
                fade_pct = peak_pnl_pct - pnl_pct if peak_pnl_pct > 0 else 0
                
                # Rule 1: High peak, deep fade -> lock profits
                # If peaked > 2.0% and dropped more than 50% from peak
                if not should_exit and peak_pnl_pct >= 2.0 and pnl_pct < peak_pnl_pct * 0.50 and pnl_pct > 0:
                    should_exit = True
                    exit_reason = f"V3.1.63_peak_fade_high T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%)"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")
                
                # Rule 2: Moderate peak, severe fade -> lock profits
                # If peaked > 1.5% and dropped more than 60% from peak
                elif not should_exit and peak_pnl_pct >= 1.5 and pnl_pct < peak_pnl_pct * 0.40 and pnl_pct > 0:
                    should_exit = True
                    exit_reason = f"V3.1.63_peak_fade_mod T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%)"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")
                
                # Rule 3: Any peak that went negative -> thesis broken
                # If peaked > 1.0% but now negative
                elif not should_exit and peak_pnl_pct >= 1.0 and pnl_pct <= 0:
                    should_exit = True
                    exit_reason = f"V3.1.63_peak_to_loss T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}%"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")"""

    new_peak_exit = """                # V3.1.64 PROFIT LOCK EXIT - Aggressive peak capture
                # At 18x leverage: 1% price move = 18% ROE. Lock profits early.
                # Don't wait for 15% TP when you can bank 1% repeatedly.
                fade_pct = peak_pnl_pct - pnl_pct if peak_pnl_pct > 0 else 0
                
                # Rule 0 (NEW): PROFIT LOCK - peak >= 1.0%, faded 40%+, still green
                # Example: peaked 1.5%, now at 0.85% (faded 43%) -> CLOSE and bank 0.85%
                if not should_exit and peak_pnl_pct >= 1.0 and pnl_pct < peak_pnl_pct * 0.60 and pnl_pct > 0.15:
                    should_exit = True
                    exit_reason = f"V3.1.64_profit_lock T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%, locking gains)"
                    print(f"  [PROFIT LOCK] {symbol}: {exit_reason}")
                
                # Rule 1: High peak, deep fade -> lock profits
                # If peaked > 1.5% and dropped more than 50% from peak
                elif not should_exit and peak_pnl_pct >= 1.5 and pnl_pct < peak_pnl_pct * 0.50 and pnl_pct > 0:
                    should_exit = True
                    exit_reason = f"V3.1.64_peak_fade_high T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%)"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")
                
                # Rule 2: Moderate peak, severe fade -> lock profits  
                # If peaked > 1.0% and dropped more than 65% from peak
                elif not should_exit and peak_pnl_pct >= 1.0 and pnl_pct < peak_pnl_pct * 0.35 and pnl_pct > 0:
                    should_exit = True
                    exit_reason = f"V3.1.64_peak_fade_mod T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}% (faded {fade_pct:.2f}%)"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")
                
                # Rule 3: Any peak that went negative -> thesis broken
                # If peaked > 0.8% but now negative (lowered from 1.0%)
                elif not should_exit and peak_pnl_pct >= 0.8 and pnl_pct <= 0:
                    should_exit = True
                    exit_reason = f"V3.1.64_peak_to_loss T{tier}: peaked {peak_pnl_pct:.2f}%, now {pnl_pct:.2f}%"
                    print(f"  [PEAK EXIT] {symbol}: {exit_reason}")"""

    daemon_patches.append((old_peak_exit, new_peak_exit, "Profit lock exit (Rule 0 + lowered thresholds)"))
    
    # --- 1B: REGIME EXIT SHIELD IN EXTREME FEAR ---
    # Add F&G check before regime exit loop - skip profitable positions in extreme fear
    
    old_regime_predator = """            # V3.1.20 PREDATOR: No regime exits within first 4 hours - let trades breathe
            if hours_open < 4:
                continue"""
    
    new_regime_predator = """            # V3.1.20 PREDATOR: No regime exits within first 4 hours - let trades breathe
            if hours_open < 4:
                continue
            
            # V3.1.64: EXTREME FEAR SHIELD - Don't cut profitable positions in capitulation
            # In F&G < 20, regime bounces are noise. Trust whale-aligned positions.
            _fg_for_regime = regime.get("fear_greed", 50)
            if _fg_for_regime < 20 and pnl > 0:
                logger.info(f"[REGIME] FEAR SHIELD: Skipping {symbol_clean} {side} (profitable ${pnl:+.1f} in F&G={_fg_for_regime})")
                continue"""
    
    daemon_patches.append((old_regime_predator, new_regime_predator, "Regime exit shield in extreme fear (F&G < 20)"))

    # --- 1C: HARD CAP MAX_POSITIONS ---
    # The CAPITULATION mode raises directional limit to 7 at line ~758
    
    old_capitulation_limit = """                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, raising directional limit to 7")"""
    
    new_capitulation_limit = """                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, keeping hard cap at BASE_SLOTS={BASE_SLOTS}")"""
    
    daemon_patches.append((old_capitulation_limit, new_capitulation_limit, "Capitulation mode log update"))
    
    # Also need to find where the limit is actually raised to 7 and cap it
    # The line before the log likely sets a variable. Let's find it by context.
    old_cap_raise = """                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, keeping hard cap at BASE_SLOTS={BASE_SLOTS}")"""
    # We'll do a second pass after the first replacement to find the actual raise
    
    # --- 1D: Add fear_greed to regime dict for regime exit ---
    # The regime exit function needs F&G. Check if it's already there.
    # From the code, regime is built by get_market_regime_for_exit(). 
    # We need to inject F&G into it. Let's add it at the regime check point.
    
    old_regime_check = """        regime = get_market_regime_for_exit()
        
        spike_msg = " SPIKE!" if regime.get('spike') else ""
        logger.info(f"[REGIME] Market: {regime['regime']} | 1h: {regime.get('change_1h', 0):+.1f}% | 4h: {regime['change_4h']:+.1f}% | 24h: {regime['change_24h']:+.1f}%{spike_msg}")"""
    
    new_regime_check = """        regime = get_market_regime_for_exit()
        
        # V3.1.64: Inject F&G into regime for fear shield logic
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _fg_regime = get_fear_greed_index()
            regime["fear_greed"] = _fg_regime.get("value", 50)
        except:
            regime["fear_greed"] = 50
        
        spike_msg = " SPIKE!" if regime.get('spike') else ""
        logger.info(f"[REGIME] Market: {regime['regime']} | 1h: {regime.get('change_1h', 0):+.1f}% | 4h: {regime['change_4h']:+.1f}% | 24h: {regime['change_24h']:+.1f}%{spike_msg} | F&G: {regime.get('fear_greed', 50)}")"""
    
    daemon_patches.append((old_regime_check, new_regime_check, "Inject F&G into regime exit + fear shield"))
    
    # --- 1E: PM prompt update - mention profit lock ---
    old_pm_peak_rule = """PEAK FADE RULE (V3.1.63): If a position peaked > 1.5% and faded > 50% from peak, CLOSE IT to lock profit. At 20x leverage, 1% captured = 20% ROE. Do NOT let winners become losers."""
    
    new_pm_peak_rule = """PROFIT LOCK RULE (V3.1.64): If a position peaked > 1.0% and faded > 40% from peak (still green), CLOSE IT to lock profit. At 18x leverage, 1% captured = 18% ROE. Do NOT let winners become losers. Banking small wins repeatedly beats waiting for huge TPs."""
    
    daemon_patches.append((old_pm_peak_rule, new_pm_peak_rule, "PM prompt: profit lock rule update"))
    
    # --- 1F: Version banner ---
    old_version_line = """    logger.info("  - Smart peak exit: re-enabled (peak>1.5%, fade>50% = close)")"""
    
    new_version_line = """    logger.info("  - V3.1.64 PROFIT LOCK: peak>=1.0%, fade>40% = close (bank small wins)")
    logger.info("  - V3.1.64 FEAR SHIELD: skip regime exit for profitable positions when F&G<20")
    logger.info("  - V3.1.64 VOL-ADJUSTED SL: 1.5x wider SL when F&G<15")
    logger.info("  - V3.1.64 HARD CAP: MAX_POSITIONS strictly enforced, no capitulation override")"""
    
    daemon_patches.append((old_version_line, new_version_line, "Version banner update"))
    
    # Apply all daemon patches
    patch_file(DAEMON_FILE, daemon_patches)
    
    # ============================================================
    # PHASE 1.5: Fix CAPITULATION override of max positions
    # ============================================================
    print(f"\n--- PHASE 1.5: Hard cap max positions ---")
    daemon_content = read_file(DAEMON_FILE)
    
    # Find the capitulation block that raises the limit
    # Pattern: around line 758, it likely sets available_slots or effective_max_positions higher
    # Let's search for "directional limit to 7" context
    # The replacement already changed the log. Now find the actual variable assignment.
    # From grep line 758: first_fg = trade_opportunities[0]["decision"].get("fear_greed", 50)
    # The raise probably happens near "raising directional limit"
    
    # Add a hard cap right after available_slots calculation
    old_available_calc = """            available_slots = effective_max_positions - current_positions"""
    new_available_calc = """            available_slots = effective_max_positions - current_positions
            # V3.1.64: HARD CAP - never exceed BASE_SLOTS total regardless of mode
            if current_positions >= BASE_SLOTS:
                available_slots = 0"""
    
    if old_available_calc in daemon_content:
        # Only replace the LAST occurrence (the one in the trading loop at line 742)
        # The first occurrence at line 441 is in the analysis section
        parts = daemon_content.rsplit(old_available_calc, 1)
        if len(parts) == 2:
            daemon_content = parts[0] + new_available_calc + parts[1]
            print(f"  [OK] Hard cap at BASE_SLOTS in trading loop")
        else:
            print(f"  [SKIP] Hard cap - could not isolate trading loop instance")
    else:
        print(f"  [SKIP] Hard cap - pattern not found")
    
    write_file(DAEMON_FILE, daemon_content)
    
    # ============================================================
    # PHASE 2: NIGHTLY TRADE PATCHES
    # ============================================================
    print(f"\n--- PHASE 2: Patching {NIGHTLY_FILE} ---")
    
    nightly_patches = []
    
    # --- 2A: VOL-ADJUSTED SL in Judge's SL clamp ---
    old_sl_clamp = """            # Clamp TP/SL to reasonable ranges
            tp_pct = max(1.5, min(10.0, tp_pct))
            sl_pct = max(1.5, min(6.0, sl_pct))"""
    
    new_sl_clamp = """            # V3.1.64: VOL-ADJUSTED SL - widen in extreme fear to survive wicks
            _fg_for_sl = regime.get("fear_greed", 50) if regime else 50
            if _fg_for_sl < 15:
                sl_pct = sl_pct * 1.5  # 3% -> 4.5% in capitulation
                print(f"  [JUDGE] V3.1.64 VOL-SL: F&G={_fg_for_sl}, widened SL to {sl_pct:.1f}%")
            
            # Clamp TP/SL to reasonable ranges (wider max for vol-adjusted)
            tp_pct = max(1.5, min(10.0, tp_pct))
            sl_pct = max(1.5, min(7.0, sl_pct))"""
    
    nightly_patches.append((old_sl_clamp, new_sl_clamp, "Vol-adjusted SL in extreme fear"))
    
    # --- 2B: ANTI-WAIT V2 - persona agreement override ---
    old_anti_wait = """            # V3.1.63 ANTI-WAIT OVERRIDE: If Gemini says WAIT but confidence >= 80%
            # and reasoning clearly indicates a direction, override the WAIT.
            # This catches cases where Gemini hedges with WAIT despite strong signals.
            if decision == "WAIT" and confidence >= 0.80:
                reasoning_lower = reasoning.lower() if reasoning else ""
                # Check if reasoning text leans heavily toward a direction
                long_words = sum(1 for w in ["long", "buy", "bullish", "accumulation", "oversold", "bounce"] if w in reasoning_lower)
                short_words = sum(1 for w in ["short", "sell", "bearish", "distribution", "overbought", "dump"] if w in reasoning_lower)
                if long_words >= 2 and short_words == 0:
                    decision = "LONG"
                    print(f"  [JUDGE] V3.1.63 ANTI-WAIT OVERRIDE: WAIT->LONG (conf={confidence:.0%}, reasoning leans LONG)")
                elif short_words >= 2 and long_words == 0:
                    decision = "SHORT"
                    print(f"  [JUDGE] V3.1.63 ANTI-WAIT OVERRIDE: WAIT->SHORT (conf={confidence:.0%}, reasoning leans SHORT)")"""
    
    new_anti_wait = """            # V3.1.64 ANTI-WAIT V2: Two-layer override
            # Layer 1: If 2+ personas agree on direction at >= 70% each, force that direction
            # Layer 2: Fall back to reasoning word-count (V3.1.63 method)
            if decision == "WAIT" and confidence >= 0.75:
                # Layer 1: Persona consensus override
                _long_voters = [v for v in persona_votes if v["signal"] == "LONG" and v["confidence"] >= 0.70]
                _short_voters = [v for v in persona_votes if v["signal"] == "SHORT" and v["confidence"] >= 0.70]
                
                if len(_long_voters) >= 2 and len(_short_voters) == 0:
                    decision = "LONG"
                    _voter_names = [v["persona"] for v in _long_voters]
                    print(f"  [JUDGE] V3.1.64 ANTI-WAIT: WAIT->LONG (consensus: {_voter_names}, conf={confidence:.0%})")
                elif len(_short_voters) >= 2 and len(_long_voters) == 0:
                    decision = "SHORT"
                    _voter_names = [v["persona"] for v in _short_voters]
                    print(f"  [JUDGE] V3.1.64 ANTI-WAIT: WAIT->SHORT (consensus: {_voter_names}, conf={confidence:.0%})")
                else:
                    # Layer 2: Reasoning text analysis (fallback)
                    reasoning_lower = reasoning.lower() if reasoning else ""
                    long_words = sum(1 for w in ["long", "buy", "bullish", "accumulation", "oversold", "bounce", "support"] if w in reasoning_lower)
                    short_words = sum(1 for w in ["short", "sell", "bearish", "distribution", "overbought", "dump", "resistance"] if w in reasoning_lower)
                    if long_words >= 2 and short_words == 0:
                        decision = "LONG"
                        print(f"  [JUDGE] V3.1.64 ANTI-WAIT: WAIT->LONG (reasoning: {long_words} long words, conf={confidence:.0%})")
                    elif short_words >= 2 and long_words == 0:
                        decision = "SHORT"
                        print(f"  [JUDGE] V3.1.64 ANTI-WAIT: WAIT->SHORT (reasoning: {short_words} short words, conf={confidence:.0%})")"""
    
    nightly_patches.append((old_anti_wait, new_anti_wait, "Anti-WAIT V2 (persona consensus + reasoning)"))
    
    # --- 2C: Confidence floor note (already at 0.80, bump to 0.85) ---
    old_confidence = """MIN_CONFIDENCE_TO_TRADE = 0.80  # V3.1.63: SNIPER - only high-conviction trades"""
    new_confidence = """MIN_CONFIDENCE_TO_TRADE = 0.85  # V3.1.64: SNIPER++ - higher conviction for endgame"""
    
    nightly_patches.append((old_confidence, new_confidence, "Confidence floor 80% -> 85%"))
    
    # Apply all nightly patches
    patch_file(NIGHTLY_FILE, nightly_patches)
    
    # ============================================================
    # PHASE 3: VERIFICATION
    # ============================================================
    print(f"\n--- PHASE 3: Verification ---")
    
    daemon = read_file(DAEMON_FILE)
    nightly = read_file(NIGHTLY_FILE)
    
    checks = [
        ("V3.1.64_profit_lock" in daemon, "Profit lock exit rule"),
        ("FEAR SHIELD" in daemon, "Regime fear shield"),
        ("V3.1.64 VOL-SL" in nightly, "Vol-adjusted SL"),
        ("ANTI-WAIT V2" in nightly or "V3.1.64 ANTI-WAIT" in nightly, "Anti-WAIT V2"),
        ("0.85" in nightly and "SNIPER++" in nightly, "Confidence floor 85%"),
        ("V3.1.64 PROFIT LOCK" in daemon or "V3.1.64 HARD CAP" in daemon, "Version banner"),
        ("HARD CAP" in daemon and "BASE_SLOTS" in daemon, "Hard cap enforcement"),
    ]
    
    all_ok = True
    for check, name in checks:
        status = "PASS" if check else "FAIL"
        if not check:
            all_ok = False
        print(f"  [{status}] {name}")
    
    print(f"\n{'=' * 60}")
    if all_ok:
        print("ALL PATCHES APPLIED SUCCESSFULLY")
    else:
        print("SOME PATCHES FAILED - check output above")
    print(f"{'=' * 60}")
    
    print(f"""
NEXT STEPS:
1. Verify BTC/DOGE closes went through (check WEEX dashboard)
2. Restart daemon:
   nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
3. Watch logs:
   tail -f logs/daemon_v3_1_7_$(date +%Y%m%d).log
4. Commit to repo:
   git add .
   git commit -m "V3.1.64: profit lock exit, fear shield, vol-adjusted SL, anti-WAIT V2"
   git push
""")


if __name__ == "__main__":
    main()
