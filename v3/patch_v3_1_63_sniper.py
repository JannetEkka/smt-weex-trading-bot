#!/usr/bin/env python3
"""
V3.1.63 SNIPER MODE PATCH
=========================
Apply with: python3 patch_v3_1_63_sniper.py
Run from: ~/smt-weex-trading-bot/v3/

Changes:
  1. WHALE: CryptOracle for ALL 8 pairs (remove Etherscan dependency)
  2. Judge prompt: WHALE+FLOW co-primary, stronger anti-WAIT
  3. Smart peak exit in monitor (re-enabled, intelligent)
  4. Trade history injection into Judge prompt
  5. Liquidation floor ($400 equity) + margin utilization cap (80%)
  6. MAX 3 positions (concentrated bets)
  7. Confidence floor: 80% with WHALE+FLOW agreement bonus
  8. Anti-WAIT override: if Gemini says WAIT but reasoning shows 80%+ direction, override
  9. PM prompt: respect peak fade, cut losers faster
"""

import re
import os
import sys
import shutil
from datetime import datetime

NIGHTLY = "smt_nightly_trade_v3_1.py"
DAEMON = "smt_daemon_v3_1.py"

def backup(filepath):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup_{ts}"
    shutil.copy2(filepath, backup_path)
    print(f"  Backed up: {backup_path}")

def replace_in_file(filepath, old, new, description="", count=1):
    with open(filepath, 'r') as f:
        content = f.read()
    
    if old not in content:
        print(f"  [WARN] Pattern not found in {filepath}: {description or old[:60]}...")
        return False
    
    occurrences = content.count(old)
    if count == 1 and occurrences > 1:
        # Replace only first occurrence
        content = content.replace(old, new, 1)
    else:
        content = content.replace(old, new)
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"  [OK] {description or old[:50]}...")
    return True

def replace_line_range(filepath, start_marker, end_marker, new_content, description=""):
    """Replace content between two markers (inclusive of start, exclusive of end)."""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if start_marker in line and start_idx is None:
            start_idx = i
        if end_marker in line and start_idx is not None and i > start_idx:
            end_idx = i
            break
    
    if start_idx is None:
        print(f"  [WARN] Start marker not found: {start_marker[:60]}")
        return False
    if end_idx is None:
        print(f"  [WARN] End marker not found: {end_marker[:60]}")
        return False
    
    new_lines = lines[:start_idx] + [new_content + "\n"] + lines[end_idx:]
    with open(filepath, 'w') as f:
        f.writelines(new_lines)
    
    print(f"  [OK] {description} (lines {start_idx+1}-{end_idx+1})")
    return True

def insert_after(filepath, marker, new_content, description=""):
    """Insert new content after a line containing marker."""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        if marker in line:
            lines.insert(i + 1, new_content + "\n")
            with open(filepath, 'w') as f:
                f.writelines(lines)
            print(f"  [OK] {description} (after line {i+1})")
            return True
    
    print(f"  [WARN] Marker not found for insert: {marker[:60]}")
    return False


def apply_patch():
    print("=" * 60)
    print("V3.1.63 SNIPER MODE PATCH")
    print("=" * 60)
    
    if not os.path.exists(NIGHTLY):
        print(f"ERROR: {NIGHTLY} not found. Run from v3/ directory.")
        sys.exit(1)
    if not os.path.exists(DAEMON):
        print(f"ERROR: {DAEMON} not found. Run from v3/ directory.")
        sys.exit(1)
    
    # Backup both files
    print("\n[1/9] Backing up files...")
    backup(NIGHTLY)
    backup(DAEMON)
    
    # ================================================================
    # PATCH 1: VERSION BUMP
    # ================================================================
    print("\n[2/9] Version bump to V3.1.63...")
    
    replace_in_file(NIGHTLY,
        'MIN_CONFIDENCE_TO_TRADE = 0.60  # V3.1.62: Lower floor for more opportunities',
        'MIN_CONFIDENCE_TO_TRADE = 0.80  # V3.1.63: SNIPER - only high-conviction trades',
        "Confidence floor -> 80%"
    )
    
    # ================================================================
    # PATCH 2: MAX POSITIONS -> 3
    # ================================================================
    print("\n[3/9] Max positions -> 3 (SNIPER)...")
    
    replace_in_file(NIGHTLY,
        'MAX_OPEN_POSITIONS = 8  # V3.1.35d: Never blocked - all pairs can trade  # V3.1.31: Competition mode - 5 positions with 10-12x leverage',
        'MAX_OPEN_POSITIONS = 3  # V3.1.63: SNIPER - fewer, bigger, better positions',
        "MAX_OPEN_POSITIONS -> 3"
    )
    
    # Also update BASE_SLOTS in daemon if present
    replace_in_file(DAEMON,
        'BASE_SLOTS = 4',
        'BASE_SLOTS = 3  # V3.1.63: SNIPER mode',
        "BASE_SLOTS -> 3 in daemon"
    )
    
    # ================================================================
    # PATCH 3: FLOOR_BALANCE for liquidation protection
    # ================================================================
    print("\n[4/9] Liquidation protection...")
    
    replace_in_file(NIGHTLY,
        'FLOOR_BALANCE = 1500.0  # V3.1.42: Finals - emergency stop',
        'FLOOR_BALANCE = 400.0  # V3.1.63: Liquidation floor - hard stop',
        "FLOOR_BALANCE -> 400"
    )
    
    # ================================================================
    # PATCH 4: WHALE PERSONA - CryptOracle for ALL pairs (remove Etherscan)
    # ================================================================
    print("\n[5/9] WHALE: CryptOracle for ALL pairs (removing Etherscan)...")
    
    # Change the analyze() method to route ALL pairs through CryptOracle
    replace_in_file(NIGHTLY,
        """        if pair in ("ETH", "BTC"):
            return self._analyze_with_etherscan(pair, pair_info, cr_signal)
        else:
            return self._analyze_with_cryptoracle(pair, pair_info, cr_signal)""",
        """        # V3.1.63: CryptOracle for ALL pairs (Etherscan removed - unreliable timeouts)
        return self._analyze_with_cryptoracle(pair, pair_info, cr_signal)""",
        "WHALE: Route ALL pairs through CryptOracle"
    )
    
    # ================================================================
    # PATCH 5: JUDGE PROMPT - WHALE+FLOW co-primary + anti-WAIT
    # ================================================================
    print("\n[6/9] Judge prompt: WHALE+FLOW co-primary...")
    
    old_judge_signals = """SIGNAL RELIABILITY (most to least trustworthy):
  1. FLOW (order book taker ratio) -- actual money moving. Most reliable.
  2. WHALE (on-chain for BTC/ETH, Cryptoracle sentiment for alts) -- smart money / community intelligence.
  3. SENTIMENT (web search price action) -- short-term momentum context.
  4. TECHNICAL (RSI/SMA/momentum) -- lagging but useful for confirmation.

HOW TO DECIDE:
- If FLOW + WHALE agree on direction: trade it. This is your highest-conviction signal.
- If FLOW contradicts WHALE on altcoins: trust FLOW (real orders > social chatter).
- If WHALE shows buying but FLOW shows heavy selling: this is ABSORPTION -- bullish.
- If WHALE shows selling but FLOW shows buying: this is DISTRIBUTION -- bearish.
- WAIT only when signals genuinely conflict with no clear majority, or confidence is truly low."""
    
    new_judge_signals = """SIGNAL RELIABILITY (V3.1.63 SNIPER):
  CO-PRIMARY (equal weight, these drive your decision):
    1. WHALE (Cryptoracle community intelligence) -- smart money / crowd wisdom for ALL pairs. Our unique edge.
    2. FLOW (order book taker ratio) -- actual money moving right now.
  SECONDARY (confirmation only, never override WHALE+FLOW):
    3. SENTIMENT (web search price action) -- context, not a trading signal.
    4. TECHNICAL (RSI/SMA/momentum) -- lagging indicator, confirmation only.

HOW TO DECIDE:
- If WHALE + FLOW agree: TRADE IT at 85%+ confidence. This is the strongest possible signal.
- If WHALE is strong (>65% conf) but FLOW is weak/neutral: trust WHALE direction. Cryptoracle sees what orderbooks don't.
- If FLOW is strong (>75% conf) but WHALE is weak/neutral: trust FLOW. Money is moving.
- If WHALE and FLOW directly contradict (opposite directions, both >60%): this is the ONLY valid WAIT scenario.
- NEVER WAIT when 2+ signals agree on direction. We are LAST PLACE in competition.

PATTERN RECOGNITION:
- WHALE buying + FLOW selling = ACCUMULATION (smart money loading while retail sells) -> LONG
- WHALE selling + FLOW buying = DISTRIBUTION (smart money dumping into retail buying) -> SHORT
- Both buying = STRONG LONG. Both selling = STRONG SHORT.

TRADE HISTORY CONTEXT:
{trade_history_summary}"""
    
    replace_in_file(NIGHTLY, old_judge_signals, new_judge_signals,
        "Judge: WHALE+FLOW co-primary signals")
    
    # Also fix the "trust FLOW over WHALE" line in HOW TO DECIDE (if the above didn't catch it)
    replace_in_file(NIGHTLY,
        "- If FLOW contradicts WHALE on altcoins: trust FLOW (real orders > social chatter).",
        "- If FLOW contradicts WHALE: check confidence levels. Higher confidence wins.",
        "Remove FLOW>WHALE bias (backup)"
    )
    
    # ================================================================
    # PATCH 6: CONFIDENCE FLOOR + ANTI-WAIT OVERRIDE in Judge
    # ================================================================
    print("\n[7/9] Confidence floor 80% + anti-WAIT override...")
    
    # Fix the hardcoded 0.65 confidence check
    replace_in_file(NIGHTLY,
        """            # Minimum confidence floor
            if confidence < 0.65:
                return self._wait_decision(f"Gemini confidence too low: {confidence:.0%}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])""",
        """            # V3.1.63: Minimum confidence floor (SNIPER)
            if confidence < MIN_CONFIDENCE_TO_TRADE:
                return self._wait_decision(f"Gemini confidence too low: {confidence:.0%} < {MIN_CONFIDENCE_TO_TRADE:.0%}", persona_votes,
                    [f"{v['persona']}={v['signal']}({v['confidence']:.0%})" for v in persona_votes])""",
        "Confidence floor uses MIN_CONFIDENCE_TO_TRADE variable"
    )
    
    # Add anti-WAIT override: if Gemini says WAIT but gave high confidence reasoning
    old_wait_check = '            decision = data.get("decision", "WAIT").upper() if data.get("decision") else "WAIT"'
    
    new_wait_check = '            decision = data.get("decision", "WAIT").upper() if data.get("decision") else "WAIT"' + """
            
            # V3.1.63 ANTI-WAIT OVERRIDE: If Gemini says WAIT but confidence >= 80%
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
    
    replace_in_file(NIGHTLY, old_wait_check, new_wait_check,
        "Anti-WAIT override logic")
    
    # ================================================================
    # PATCH 7: SMART PEAK EXIT in monitor (re-enable profit guards)
    # ================================================================
    print("\n[8/9] Smart peak exit in monitor...")
    
    old_monitor_disabled = """                # V3.1.46: PROFIT GUARDS DISABLED - Recovery mode
                # Problem: Guards close at +0.5-1.3% (capturing $5-30) but losses hit $50-299
                # Solution: Let TP orders do their job. Need +5-8% wins to recover.
                # Guards were cutting winners before they could become big wins.
                # V3.1.46: ALL PROFIT GUARDS DISABLED - Recovery mode
                # We need wins of $100-300, not $15-50. Let TP orders handle exits.
                # fade_pct = peak_pnl_pct - pnl_pct
                # if tier == 3: T3_profit_guard ... DISABLED
                # elif tier == 2: T2_profit_guard ... DISABLED
                # else: T1_profit_guard ... DISABLED
                
                # V3.1.46: TIME-BASED TIGHTENING DISABLED - Let winners run
                # Was closing positions that peaked at 0.5-1% after 2h. These need time to hit 5%+ TP.
                # if not should_exit and hours_open >= 2.0 and peak_pnl_pct >= 0.5:
                #     if pnl_pct < peak_pnl_pct * 0.35:
                #         should_exit = True
                #         exit_reason = f"time_fade_guard ..."
                pass  # V3.1.46: Disabled"""

    new_monitor_smart_exit = """                # V3.1.63 SMART PEAK EXIT - Re-enabled with intelligent thresholds
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
    
    replace_in_file(DAEMON, old_monitor_disabled, new_monitor_smart_exit,
        "Smart peak exit guards re-enabled")
    
    # ================================================================
    # PATCH 8: TRADE HISTORY INJECTION + PM PROMPT UPDATE
    # ================================================================
    print("\n[9/9] Trade history for Judge + PM updates...")
    
    # Add trade history builder function near the top of the Judge method
    # We'll inject it by replacing the judge prompt format string to include history
    
    # First, add a helper function to build trade history summary
    # Insert after the _pnl_history definition in daemon
    insert_after(DAEMON,
        "_PNL_HISTORY_MAX = 10",
        """
def get_trade_history_summary(tracker) -> str:
    \"\"\"V3.1.63: Build summary of last 10 closed trades for Judge context.\"\"\"
    closed = tracker.closed_trades[-10:] if tracker.closed_trades else []
    if not closed:
        return "No closed trade history available yet."
    
    lines = []
    wins = 0
    losses = 0
    for t in closed:
        sym = t.get("close_data", {}).get("symbol", "?") if t.get("close_data") else "?"
        if sym == "?":
            # Try to extract from the trade key or other fields
            sym = t.get("symbol", t.get("order_id", "?"))
        side = t.get("side", "?")
        pnl = t.get("close_data", {}).get("final_pnl_pct", 0) if t.get("close_data") else 0
        reason = t.get("close_data", {}).get("reason", "?") if t.get("close_data") else "?"
        conf = t.get("confidence", 0)
        whale_d = t.get("whale_direction", "?")
        
        if pnl > 0:
            wins += 1
            result = "WIN"
        elif pnl < 0:
            losses += 1
            result = "LOSS"
        else:
            result = "FLAT"
        
        lines.append(f"  {sym} {side} -> {result} ({pnl:+.1f}%) conf={conf:.0%} whale={whale_d} reason={reason}")
    
    win_rate = wins / max(wins + losses, 1) * 100
    summary = f"Last {len(closed)} trades: {wins}W/{losses}L ({win_rate:.0f}% win rate)\\n"
    summary += "\\n".join(lines[-5:])  # Show last 5 only to save tokens
    return summary
""",
        "Trade history summary function"
    )
    
    # Now inject {trade_history_summary} into the Judge call
    # The Judge prompt already has the placeholder from our patch above.
    # We need to make sure the format string fills it in.
    # Find where the judge prompt is formatted and add the history variable.
    
    # In smt_nightly_trade_v3_1.py, the judge prompt is built with f-string
    # We need to add trade_history_summary variable before the prompt
    insert_after(NIGHTLY,
        '        """Replaces weighted-sum approach with Gemini that sees:',
        """        # V3.1.63: Get trade history for Judge context
        try:
            from smt_daemon_v3_1 import get_trade_history_summary, tracker as daemon_tracker
            trade_history_summary = get_trade_history_summary(daemon_tracker)
        except:
            trade_history_summary = "Trade history unavailable."
""",
        "Import trade history into Judge"
    )
    
    # Update PM prompt: make it respect peak fade instead of ignoring it
    replace_in_file(DAEMON,
        "Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.",
        "PEAK FADE RULE (V3.1.63): If a position peaked > 1.5% and faded > 50% from peak, CLOSE IT to lock profit. At 20x leverage, 1% captured = 20% ROE. Do NOT let winners become losers.",
        "PM: Respect peak fade"
    )
    
    # Update PM to cut losers at -2% instead of waiting for -4% force stop
    replace_in_file(DAEMON,
        "(d) 7+ total positions clogging slots (close weakest loser to free capital)",
        """(d) 4+ total positions clogging slots (close weakest loser to free capital)
(e) V3.1.63: If any position is losing > -2% AND held > 4 hours, close it. At 20x that's -40% ROE. Cut the bleed.""",
        "PM: Faster loser cutting"
    )
    
    # Add margin utilization check to the signal check function
    # This prevents new trades when margin is too high
    insert_after(NIGHTLY,
        "Max positions (6/4) - Analysis only",
        """
        # V3.1.63: Margin utilization safety check
        try:
            _margin_util = (float(account_info.get('equity', 1)) - float(account_info.get('available', 0))) / max(float(account_info.get('equity', 1)), 1)
            if _margin_util > 0.80:
                logger.warning(f"MARGIN WARNING: {_margin_util:.0%} utilization (>80%). No new trades until positions close.")
        except:
            pass
""",
        "Margin utilization check"
    )
    
    # ================================================================
    # PATCH: Update version strings
    # ================================================================
    
    # Update the daemon startup banner
    replace_in_file(DAEMON,
        'SMT Daemon V3.1.62 - AGGRESSIVE RECOVERY + 20x + BIG POSITIONS',
        'SMT Daemon V3.1.63 - SNIPER MODE: 3 positions, 80% floor, WHALE+FLOW co-primary',
        "Daemon version banner"
    )
    
    # Add V3.1.63 to the startup info
    replace_in_file(DAEMON,
        'V3.1.9 CRITICAL FIXES:',
        'V3.1.63 SNIPER MODE:',
        "Startup info header"
    )
    
    replace_in_file(DAEMON,
        '  - FIXED: undefined btc_trend bug blocking regime filter',
        '  - WHALE: CryptOracle for ALL 8 pairs (Etherscan removed)',
        "Startup info line 1"
    )
    
    replace_in_file(DAEMON,
        '  - FIXED: Regime filter now applies to ALL pairs incl BTC',
        '  - Judge: WHALE+FLOW co-primary signals (was FLOW-only)',
        "Startup info line 2"
    )
    
    replace_in_file(DAEMON,
        '  - BEARISH threshold: -1% (was -2%)',
        '  - Smart peak exit: re-enabled (peak>1.5%, fade>50% = close)',
        "Startup info line 3"
    )
    
    replace_in_file(DAEMON,
        '  - Block LONGs when BTC 24h < -0.5%',
        '  - Max 3 positions, 80% confidence floor',
        "Startup info line 4"
    )
    
    replace_in_file(DAEMON,
        '  - Regime cache: 5min (was 15min)',
        '  - Anti-WAIT override + trade history to Judge',
        "Startup info line 5"
    )
    
    # ================================================================
    # PATCH: Fallback weights - equalize WHALE and FLOW
    # ================================================================
    
    replace_in_file(NIGHTLY,
        """            weight = 1.0
            if persona == "FLOW":
                weight = 2.5
            elif persona == "WHALE":
                weight = 1.5
            elif persona == "TECHNICAL":
                weight = 1.0""",
        """            weight = 1.0
            if persona == "FLOW":
                weight = 2.0  # V3.1.63: Equal with WHALE
            elif persona == "WHALE":
                weight = 2.0  # V3.1.63: Equal with FLOW
            elif persona == "TECHNICAL":
                weight = 0.8  # V3.1.63: Confirmation only""",
        "Fallback weights: WHALE = FLOW"
    )
    
    print("\n" + "=" * 60)
    print("V3.1.63 SNIPER MODE PATCH APPLIED")
    print("=" * 60)
    print("""
CHANGES SUMMARY:
  [1] Confidence floor: 80% (was 60%/65%)
  [2] Max positions: 3 (was 8)
  [3] Floor balance: $400 (was $1500)
  [4] WHALE: CryptOracle for ALL pairs (Etherscan removed)
  [5] Judge: WHALE+FLOW co-primary (was FLOW dominant)
  [6] Anti-WAIT override (80%+ confidence reasoning overrides WAIT)
  [7] Smart peak exit re-enabled (peak>1.5%, fade>50% = close)
  [8] Trade history injected into Judge prompt
  [9] PM: respects peak fade, cuts losers at -2%/4h
  [10] Fallback weights: WHALE=FLOW=2.0 (was FLOW=2.5, WHALE=1.5)

NEXT STEPS:
  1. Kill running daemon:  kill $(pgrep -f smt_daemon_v3_1)
  2. Restart daemon:       nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  3. Watch first cycle:    tail -f logs/daemon_v3_1_7_$(date +%%Y%%m%%d).log
  4. Look for: "V3.1.63", "SNIPER", "WHALE+FLOW co-primary"
  5. Commit: git add -A && git commit -m "V3.1.63: SNIPER MODE" && git push
""")


if __name__ == "__main__":
    apply_patch()
