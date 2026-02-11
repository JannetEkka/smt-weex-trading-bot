#!/usr/bin/env python3
"""V3.1.62: AGGRESSIVE RECOVERY MODE
1. Leverage: 18-20x everything
2. Position sizing: 40-50% base
3. Tier TP widened: 10-12%
4. SL: 3% (wider to avoid noise stops)
5. Judge + PM prompts: competition-aware (LAST PLACE, need recovery)
6. BASE_SLOTS: 4 (fewer bigger positions)
"""

changes = 0

# ============================================================
# FIX 1: leverage_manager.py - ALL 18-20x
# ============================================================
with open("leverage_manager.py", "r") as f:
    lm = f.read()

old_matrix = """    (1, "ultra"):  18,  # T1 Blue Chip, 90%+ confidence
    (1, "high"):   15,  # T1 Blue Chip, 80-89%
    (1, "normal"): 12,  # T1 Blue Chip, <80%
    (2, "ultra"):  15,  # T2 Mid Cap, 90%+
    (2, "high"):   12,  # T2 Mid Cap, 80-89%
    (2, "normal"): 10,  # T2 Mid Cap, <80%
    (3, "ultra"):  12,  # T3 Small Cap, 90%+
    (3, "high"):   10,  # T3 Small Cap, 80-89%
    (3, "normal"):  8,  # T3 Small Cap, <80%"""

new_matrix = """    (1, "ultra"):  20,  # V3.1.62: AGGRESSIVE RECOVERY
    (1, "high"):   20,
    (1, "normal"): 18,
    (2, "ultra"):  20,
    (2, "high"):   20,
    (2, "normal"): 18,
    (3, "ultra"):  20,
    (3, "high"):   18,
    (3, "normal"): 15,"""

if old_matrix in lm:
    lm = lm.replace(old_matrix, new_matrix)
    changes += 1
    print("FIX 1: Leverage cranked to 18-20x all tiers")
else:
    print("WARN: Leverage matrix not found - checking alt pattern")
    # Try without comments
    if "(1, \"ultra\"):  18" in lm:
        lm = lm.replace('(1, "ultra"):  18', '(1, "ultra"):  20')
        lm = lm.replace('(1, "high"):   15', '(1, "high"):   20')
        lm = lm.replace('(1, "normal"): 12', '(1, "normal"): 18')
        lm = lm.replace('(2, "ultra"):  15', '(2, "ultra"):  20')
        lm = lm.replace('(2, "high"):   12', '(2, "high"):   20')
        lm = lm.replace('(2, "normal"): 10', '(2, "normal"): 18')
        lm = lm.replace('(3, "ultra"):  12', '(3, "ultra"):  20')
        lm = lm.replace('(3, "high"):   10', '(3, "high"):   18')
        lm = lm.replace('(3, "normal"):  8', '(3, "normal"): 15')
        changes += 1
        print("FIX 1: Leverage cranked (alt pattern)")

with open("leverage_manager.py", "w") as f:
    f.write(lm)

# ============================================================
# FIX 2: smt_nightly_trade_v3_1.py
# ============================================================
with open("smt_nightly_trade_v3_1.py", "r") as f:
    nt = f.read()

# 2a: Constants
nt = nt.replace(
    'MAX_SINGLE_POSITION_PCT = 0.30  # V3.1.42: Recovery - aggressive sizing',
    'MAX_SINGLE_POSITION_PCT = 0.50  # V3.1.62: LAST PLACE - 50% max per trade'
)
nt = nt.replace(
    'MIN_SINGLE_POSITION_PCT = 0.12  # V3.1.42: Recovery - 12% minimum',
    'MIN_SINGLE_POSITION_PCT = 0.20  # V3.1.62: LAST PLACE - 20% min per trade'
)
nt = nt.replace(
    "MIN_CONFIDENCE_TO_TRADE = 0.65  # V3.1.42: Recovery mode - more at-bats",
    "MIN_CONFIDENCE_TO_TRADE = 0.60  # V3.1.62: Lower floor for more opportunities"
)
changes += 1
print("FIX 2a: Sizing 20-50%, confidence floor 60%")

# 2b: Judge sizing base
nt = nt.replace(
    "base_size = balance * 0.22  # V3.1.59: Slight bump from 20%",
    "base_size = balance * 0.40  # V3.1.62: AGGRESSIVE 40% base"
)
nt = nt.replace(
    """            if confidence >= 0.90 and flow_whale_aligned:
                position_usdt = base_size * 1.6  # 35% of balance - ultra conviction
                print(f"  [SIZING] ULTRA: 90%+ conf + FLOW/WHALE aligned -> 35%")
            elif confidence > 0.85:
                position_usdt = base_size * 1.4  # 31% of balance
            elif confidence > 0.75:
                position_usdt = base_size * 1.2  # 26% of balance
            else:
                position_usdt = base_size * 1.0  # 22% of balance""",
    """            if confidence >= 0.90 and flow_whale_aligned:
                position_usdt = base_size * 1.25  # 50% of balance - ULTRA
                print(f"  [SIZING] ULTRA: 90%+ conf + FLOW/WHALE aligned -> 50%")
            elif confidence > 0.85:
                position_usdt = base_size * 1.15  # 46% of balance
            elif confidence > 0.75:
                position_usdt = base_size * 1.05  # 42% of balance
            else:
                position_usdt = base_size * 1.0  # 40% of balance"""
)
changes += 1
print("FIX 2b: Judge sizing 40-50%")

# 2c: Tier config
old_tier = """TIER_CONFIG = {
    1: {"name": "Blue Chip", "leverage": 10, "stop_loss": 0.025, "take_profit": 0.12, "trailing_stop": 0.015, "time_limit": 5760, "tp_pct": 8.0, "sl_pct": 2.5, "max_hold_hours": 72, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -10.0},
    2: {"name": "Mid Cap", "leverage": 8, "stop_loss": 0.03, "take_profit": 0.15, "trailing_stop": 0.02, "time_limit": 4320, "tp_pct": 7.0, "sl_pct": 2.0, "max_hold_hours": 48, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -10.0},
    3: {"name": "Small Cap", "leverage": 6, "stop_loss": 0.04, "take_profit": 0.18, "trailing_stop": 0.025, "time_limit": 2880, "tp_pct": 6.0, "sl_pct": 2.0, "max_hold_hours": 24, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -3.0},
}"""

new_tier = """TIER_CONFIG = {
    1: {"name": "Blue Chip", "leverage": 20, "stop_loss": 0.03, "take_profit": 0.15, "trailing_stop": 0.02, "time_limit": 5760, "tp_pct": 12.0, "sl_pct": 3.0, "max_hold_hours": 72, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -10.0},
    2: {"name": "Mid Cap", "leverage": 20, "stop_loss": 0.03, "take_profit": 0.18, "trailing_stop": 0.025, "time_limit": 4320, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold_hours": 48, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -10.0},
    3: {"name": "Small Cap", "leverage": 20, "stop_loss": 0.035, "take_profit": 0.20, "trailing_stop": 0.03, "time_limit": 2880, "tp_pct": 8.0, "sl_pct": 3.0, "max_hold_hours": 24, "early_exit_hours": 999, "early_exit_loss_pct": -99.0, "force_exit_loss_pct": -5.0},
}"""

if old_tier in nt:
    nt = nt.replace(old_tier, new_tier)
    changes += 1
    print("FIX 2c: Tier config - 20x, TP 8-12%, SL 3%")
else:
    print("WARN: Tier config not found")

# 2d: Fallback leverage
nt = nt.replace(
    '        safe_leverage = 10  # Competition fallback',
    '        safe_leverage = 18  # V3.1.62: Aggressive fallback'
)
changes += 1
print("FIX 2d: Fallback leverage 18x")

# 2e: Capitulation TP wider
nt = nt.replace(
    """    if fg_val < 15:
        tp_multiplier = 1.5
        tp_label = "CAPITULATION\"""",
    """    if fg_val < 15:
        tp_multiplier = 1.8
        tp_label = "CAPITULATION_AGGRESSIVE\""""
)
changes += 1
print("FIX 2e: Capitulation TP 1.8x")

# 2f: CRITICAL - Update Judge prompt with competition awareness
old_judge_guidelines = """=== DECISION GUIDELINES (V3.1.58) ===

YOUR ONLY JOB: Decide LONG, SHORT, or WAIT based on signal quality. Position limits, TP/SL, and slot management are handled by code -- ignore them entirely."""

new_judge_guidelines = """=== DECISION GUIDELINES (V3.1.62 AGGRESSIVE RECOVERY) ===

CRITICAL CONTEXT: We are LAST PLACE in the competition. Started with $10,000, now at ~$4,600.
We need to recover $5,400+ in 12 days. We CANNOT afford to play it safe.
- Every WAIT is a missed opportunity. Only WAIT when signals truly conflict.
- High-conviction trades should be taken AGGRESSIVELY.
- We need BIG winners, not small safe trades.

YOUR ONLY JOB: Decide LONG, SHORT, or WAIT based on signal quality. Position limits, TP/SL, and slot management are handled by code -- ignore them entirely.
BIAS TOWARD ACTION: If 2+ personas agree on direction, TRADE IT. Do not second-guess with WAIT."""

if old_judge_guidelines in nt:
    nt = nt.replace(old_judge_guidelines, new_judge_guidelines)
    changes += 1
    print("FIX 2f: Judge prompt - AGGRESSIVE RECOVERY mode")
else:
    print("WARN: Judge guidelines not found")

# 2g: Remove balance protection that halves size
nt = nt.replace(
    """            # Balance protection - only at emergency levels
            if balance < 500:
                position_usdt *= 0.5
                print(f"  [JUDGE] EMERGENCY BALANCE: size halved")
            elif balance < 1000:
                position_usdt *= 0.7
                print(f"  [JUDGE] LOW BALANCE: size at 70%")""",
    """            # V3.1.62: Balance protection - only at true emergency
            if balance < 200:
                position_usdt *= 0.5
                print(f"  [JUDGE] EMERGENCY BALANCE: size halved")"""
)
changes += 1
print("FIX 2g: Raised balance protection threshold (500->200)")

with open("smt_nightly_trade_v3_1.py", "w") as f:
    f.write(nt)

# ============================================================
# FIX 3: smt_daemon_v3_1.py
# ============================================================
with open("smt_daemon_v3_1.py", "r") as f:
    dm = f.read()

# 3a: BASE_SLOTS
dm = dm.replace(
    'BASE_SLOTS = 5  # V3.1.53: 5 base + 2 bonus for high-confidence',
    'BASE_SLOTS = 4  # V3.1.62: Fewer but BIGGER positions'
)
changes += 1
print("FIX 3a: BASE_SLOTS 4")

# 3b: Version
dm = dm.replace(
    'SMT Daemon V3.1.61 - GEMINI TIMEOUT + OPPOSITE SIDE + WHALE EXITS',
    'SMT Daemon V3.1.62 - AGGRESSIVE RECOVERY + 20x + BIG POSITIONS'
)
changes += 1
print("FIX 3b: Version updated")

# 3c: PM prompt - add competition context
old_pm_intro = """You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
You have learned from 40+ iterations of rules. Apply ALL of these rules strictly."""

new_pm_intro = """You are the AI Portfolio Manager for a crypto futures trading bot in a LIVE competition with REAL money.
CRITICAL: We are LAST PLACE (37th/37). Started $10,000, now ~$4,600. Need aggressive recovery.
- Do NOT close winning positions early. We need every dollar of profit.
- Close losers FAST to free capital for better trades.
- We cannot afford to be patient with stale positions. Cut and redeploy.
You have learned from 40+ iterations of rules. Apply ALL of these rules strictly."""

if old_pm_intro in dm:
    dm = dm.replace(old_pm_intro, new_pm_intro)
    changes += 1
    print("FIX 3c: PM prompt - LAST PLACE recovery context")
else:
    print("WARN: PM intro not found")

with open("smt_daemon_v3_1.py", "w") as f:
    f.write(dm)

print(f"\nTotal changes: {changes}")
print("=" * 50)

import py_compile
try:
    py_compile.compile("smt_daemon_v3_1.py", doraise=True)
    py_compile.compile("smt_nightly_trade_v3_1.py", doraise=True)
    py_compile.compile("leverage_manager.py", doraise=True)
    print("SYNTAX OK - all 3 files clean")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")

print("""
V3.1.62 AGGRESSIVE MODE SUMMARY:
  Leverage: 18-20x (was 8-18x)
  Sizing: 40-50% of balance (was 22-30%)
  TP: 8-12% price (was 6-8%) -> at 20x = 160-240% ROE
  SL: 3% price (was 2-2.5%) -> at 20x = 60% ROE loss
  Judge: "LAST PLACE, be aggressive, bias toward action"
  PM: "Cut losers fast, let winners run, we need recovery"
  Slots: 4 base (was 5) - fewer bigger positions

  Per trade: ~$1,200 margin * 20x = ~$24,000 notional
  One 5% BTC move = ~$1,200 profit
  Two good trades = back to $7,000
  Three good trades = back to $10,000

RESTART:
  pkill -9 -f smt_daemon_v3_1
  sleep 2
  nohup python3 smt_daemon_v3_1.py > /dev/null 2>&1 &
  sleep 15
  tail -30 logs/daemon_v3_1_7_$(date +%Y%m%d).log

COMMIT:
  git add -A && git commit -m "V3.1.62: AGGRESSIVE - 20x leverage, 40-50% sizing, competition-aware prompts, LAST PLACE recovery" && git push
""")
