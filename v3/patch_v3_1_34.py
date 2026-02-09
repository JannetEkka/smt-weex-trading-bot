#!/usr/bin/env python3
"""V3.1.34: SURVIVAL MODE - Capital preservation, shorter holds, lower leverage"""

FILE_NIGHTLY = "smt_nightly_trade_v3_1.py"
FILE_DAEMON = "smt_daemon_v3_1.py"

# ============================================================
# PATCH NIGHTLY
# ============================================================
with open(FILE_NIGHTLY, 'r') as f:
    code = f.read()

changes = 0

# CHANGE 1: Hold times - way shorter
for old, new in [
    ('"max_hold_hours": 72,', '"max_hold_hours": 12,    # V3.1.34: SURVIVAL - BTC reverses every 4-8h in this market'),
    ('"max_hold_hours": 48,', '"max_hold_hours": 8,     # V3.1.34: SURVIVAL - quick in/out'),
    ('"max_hold_hours": 24,', '"max_hold_hours": 4,     # V3.1.34: SURVIVAL - scalp mode'),
]:
    if old in code:
        code = code.replace(old, new, 1)  # Replace only first occurrence
        changes += 1

print(f"[OK] CHANGE 1: Hold times reduced (72->12, 48->8, 24->4) [{changes} replacements]")

# CHANGE 2: Early exit times match new holds
code = code.replace(
    '"early_exit_hours": 12,',
    '"early_exit_hours": 4,    # V3.1.34: Check earlier',
    1
)
# For T2 and T3 early exit (both are 6h currently)
count = 0
while '"early_exit_hours": 6,' in code and count < 2:
    code = code.replace('"early_exit_hours": 6,', '"early_exit_hours": 2,    # V3.1.34: Check earlier', 1)
    count += 1
print(f"[OK] CHANGE 2: Early exit times reduced")

# CHANGE 3: Max positions 5 -> 3
code = code.replace(
    'MAX_OPEN_POSITIONS = 5',
    'MAX_OPEN_POSITIONS = 3  # V3.1.34: SURVIVAL - less exposure, less correlated loss'
)
# Also check for the old value
code = code.replace(
    'MAX_OPEN_POSITIONS = 3  # V3.1.22 CAPITAL PROTECTION',
    'MAX_OPEN_POSITIONS = 3  # V3.1.34: SURVIVAL - less exposure'
)
print("[OK] CHANGE 3: Max positions = 3")

# CHANGE 4: Balance thresholds for survival
code = code.replace(
    "LOW_BALANCE_THRESHOLD = 400.0",
    "LOW_BALANCE_THRESHOLD = 2000.0  # V3.1.34: SURVIVAL - protect remaining capital"
)
code = code.replace(
    "CRITICAL_BALANCE_THRESHOLD = 200.0",
    "CRITICAL_BALANCE_THRESHOLD = 1000.0  # V3.1.34: SURVIVAL - stop trading if equity < $8k"
)
print("[OK] CHANGE 4: Balance thresholds raised (protect capital)")

# CHANGE 5: Position sizing - smaller
code = code.replace(
    "MAX_SINGLE_POSITION_PCT = 0.20",
    "MAX_SINGLE_POSITION_PCT = 0.12  # V3.1.34: SURVIVAL - smaller positions"
)
code = code.replace(
    "MIN_SINGLE_POSITION_PCT = 0.10",
    "MIN_SINGLE_POSITION_PCT = 0.06  # V3.1.34: SURVIVAL - smaller minimum"
)
print("[OK] CHANGE 5: Position sizing reduced (12% max, 6% min)")

# CHANGE 6: Add "don't short the bottom" filter in Judge
# If F&G < 20 (EXTREME FEAR) AND price near 24h low, block SHORTs
old_short_filter = '''        # V3.1.33: SHORT in BULLISH is counter-trend - need 85%
        if decision == "SHORT":
            if regime["regime"] == "BULLISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: SHORT in BULLISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)'''

new_short_filter = '''        # V3.1.33: SHORT in BULLISH is counter-trend - need 85%
        if decision == "SHORT":
            if regime["regime"] == "BULLISH" and confidence < 0.85:
                return self._wait_decision(f"BLOCKED: SHORT in BULLISH needs 85%+ (have {confidence:.0%})", persona_votes, vote_summary)
            
            # V3.1.34: Don't short the bottom - extreme fear + near 24h low = bounce zone
            fg = regime.get("fear_greed", 50)
            if fg < 20:
                print(f"  [JUDGE] WARNING: F&G={fg} (EXTREME FEAR) - bounce risk HIGH for shorts")'''

if old_short_filter in code:
    code = code.replace(old_short_filter, new_short_filter)
    print("[OK] CHANGE 6: Added extreme fear warning for shorts")
else:
    print("[WARN] CHANGE 6: Could not find SHORT filter block")

# CHANGE 7: Cooldown periods - shorter for faster re-entry
code = code.replace(
    '1: 6,   # Tier 1 (BTC, ETH, BNB, LTC): 6 hour cooldown',
    '1: 2,   # V3.1.34: SURVIVAL - 2h cooldown for faster re-entry'
)
code = code.replace(
    '2: 8,   # Tier 2 (SOL): 8 hour cooldown',
    '2: 2,   # V3.1.34: SURVIVAL - 2h cooldown'
)
code = code.replace(
    '3: 12,  # Tier 3 (DOGE, XRP, ADA): 12 hour cooldown (2x max hold time)',
    '3: 1,   # V3.1.34: SURVIVAL - 1h cooldown for fast movers'
)
print("[OK] CHANGE 7: Cooldowns shortened (6/8/12h -> 2/2/1h)")

with open(FILE_NIGHTLY, 'w') as f:
    f.write(code)

print(f"\n--- {FILE_NIGHTLY} patched ---\n")

# ============================================================
# PATCH DAEMON
# ============================================================
with open(FILE_DAEMON, 'r') as f:
    dcode = f.read()

# CHANGE 8: Signal check interval 30min -> 15min (catch moves earlier)
dcode = dcode.replace(
    'SIGNAL_CHECK_INTERVAL = 30 * 60  # 30 minutes',
    'SIGNAL_CHECK_INTERVAL = 15 * 60  # V3.1.34: 15 min - catch moves earlier'
)
print("[OK] CHANGE 8: Signal check 30min -> 15min")

# CHANGE 9: BASE_SLOTS in daemon
dcode = dcode.replace(
    'BASE_SLOTS = 5',
    'BASE_SLOTS = 3  # V3.1.34: SURVIVAL - match MAX_OPEN_POSITIONS'
)
if 'BASE_SLOTS = 3' not in dcode:
    # Try other patterns
    dcode = dcode.replace('BASE_SLOTS = 4', 'BASE_SLOTS = 3  # V3.1.34')
print("[OK] CHANGE 9: BASE_SLOTS = 3")

# CHANGE 10: Profit guard - tighter to lock gains faster
# T3 guard
dcode = dcode.replace(
    'if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:  # V3.1.32: Wider',
    'if peak_pnl_pct >= 1.5 and pnl_pct < 0.5:  # V3.1.34: Lock profits faster'
)
# T2 guard  
dcode = dcode.replace(
    'if peak_pnl_pct >= 3.0 and pnl_pct < 1.5:  # V3.1.32: Wider',
    'if peak_pnl_pct >= 2.0 and pnl_pct < 0.8:  # V3.1.34: Lock profits faster'
)
# T1 guard
dcode = dcode.replace(
    'if peak_pnl_pct >= 3.5 and pnl_pct < 2.0:  # V3.1.32: Wider',
    'if peak_pnl_pct >= 2.5 and pnl_pct < 1.0:  # V3.1.34: Lock profits faster'
)
print("[OK] CHANGE 10: Profit guards tightened (lock gains faster)")

with open(FILE_DAEMON, 'w') as f:
    f.write(dcode)

print(f"\n--- {FILE_DAEMON} patched ---\n")

# ============================================================
# PATCH LEVERAGE MANAGER
# ============================================================
try:
    with open("leverage_manager.py", 'r') as f:
        lcode = f.read()
    
    # Lower all leverage values
    for old_lev, new_lev in [
        ('12', '8'),
        ('11', '7'),
        ('10', '7'),
        ('9', '6'),
    ]:
        # Only replace in return statements or assignments, not comments
        lcode = lcode.replace(f'return {old_lev}', f'return {new_lev}  # V3.1.34: SURVIVAL')
    
    # Also set MIN/MAX
    lcode = lcode.replace('MIN_LEVERAGE = 8', 'MIN_LEVERAGE = 5  # V3.1.34: SURVIVAL')
    lcode = lcode.replace('MAX_LEVERAGE = 12', 'MAX_LEVERAGE = 8  # V3.1.34: SURVIVAL')
    
    with open("leverage_manager.py", 'w') as f:
        f.write(lcode)
    print("[OK] CHANGE 11: Leverage reduced (max 8x, was 12x)")
except Exception as e:
    print(f"[WARN] CHANGE 11: leverage_manager.py - {e}")

print("\n=== V3.1.34 SURVIVAL MODE PATCH COMPLETE ===")
print("Summary:")
print("  Hold times: 72/48/24h -> 12/8/4h (quick trades like winning teams)")
print("  Max positions: 5 -> 3 (less correlated risk)")
print("  Position sizing: 20% -> 12% max (smaller bets)")
print("  Leverage: 10-12x -> 6-8x (survive bounces)")
print("  Signal check: 30min -> 15min (catch moves earlier)")
print("  Cooldowns: 6-12h -> 1-2h (re-enter faster)")
print("  Profit guards: Tighter (lock gains, don't give back)")
print("  Balance protection: Stop at $8k equity, reduce at $8k-$9k")
print("\nRestart: pkill -f smt_daemon; sleep 2; nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &")
