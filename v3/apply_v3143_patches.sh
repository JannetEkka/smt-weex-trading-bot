#!/bin/bash
# SMT V3.1.43 - PORTFOLIO MANAGER EXTREME FEAR FIX
# Fixes:
#   1. SHORT PnL% using margin (ROE) instead of notional (price %)
#   2. Portfolio Manager rules too aggressive during Capitulation
#   3. Grace period too short for Extreme Fear reversal entries
#
# Run from: ~/smt-weex-trading-bot/v3/

set -e

echo "=== SMT V3.1.43 PATCH: Portfolio Manager Extreme Fear Fix ==="
echo ""

DAEMON="smt_daemon_v3_1.py"

# ============================================================
# STEP 0: BACKUP
# ============================================================
echo "[0/3] Creating backup..."
cp "$DAEMON" "${DAEMON}.bak_v3142"
echo "  Backed up to ${DAEMON}.bak_v3142"

# ============================================================
# PATCH 1: Fix SHORT PnL calculation (line ~1221)
# Uses notional instead of margin for both sides
# ============================================================
echo "[1/3] Fixing SHORT PnL calculation (ROE -> Price %)..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

old = """                    notional = entry * size
                    if notional > 0:
                        pnl_pct = (pnl / notional) * 100
                    else:
                        pnl_pct = 0
                else:
                    pnl_pct = (pnl / (margin if margin > 0 else 1)) * 100"""

new = """                    notional = entry * size
                    if notional > 0:
                        pnl_pct = (pnl / notional) * 100
                    else:
                        pnl_pct = 0
                else:
                    # V3.1.43 FIX: SHORT also uses notional, not margin (margin = ROE, inflated by leverage)
                    notional = entry * size
                    if notional > 0:
                        pnl_pct = (pnl / notional) * 100
                    else:
                        pnl_pct = (pnl / (margin if margin > 0 else 1)) * 100"""

if old in content:
    content = content.replace(old, new)
    with open("smt_daemon_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 1 applied: SHORT PnL now uses notional (price %) instead of margin (ROE)")
else:
    print("  WARNING: Could not find SHORT PnL block. Check manually.")
PYEOF

# ============================================================
# PATCH 2: Update Portfolio Manager Rules 1, 4, 5, 10 for Extreme Fear
# ============================================================
echo "[2/3] Patching Portfolio Manager rules for Extreme Fear..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

# --- RULE 1: Raise directional limit during Capitulation ---
old_rule1 = """RULE 1 - DIRECTIONAL CONCENTRATION LIMIT:
Max 4 positions in the same direction. If 5+ LONGs or 5+ SHORTs, close the WEAKEST ones
(lowest PnL% or most faded from peak) until we have max 4. All-same-direction = cascade
liquidation risk in cross margin."""

new_rule1 = """RULE 1 - DIRECTIONAL CONCENTRATION LIMIT:
Max 4 positions in the same direction normally. If 5+ LONGs or 5+ SHORTs, close the WEAKEST ones
(lowest PnL% or most faded from peak) until we have max 4. All-same-direction = cascade
liquidation risk in cross margin.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 5 LONGs. Violent bounces move ALL alts together,
so being long across the board IS the correct play. Only enforce max 4 if F&G >= 15."""

if old_rule1 in content:
    content = content.replace(old_rule1, new_rule1)
    print("  Rule 1 patched: directional limit 4->5 during Capitulation")
else:
    print("  WARNING: Could not find Rule 1 text.")

# --- RULE 4: Strengthen protection for Extreme Fear LONGs ---
old_rule4 = """RULE 4 - F&G CONTRADICTION CHECK:
If F&G < 20 (extreme fear) but regime says BULLISH, the bounce may be fragile.
HOWEVER: extreme fear is ALSO when contrarian LONGs make the most money (violent bounces).
So do NOT close LONGs just because F&G is low. Only close LONGs in extreme fear IF:
  a) Position has peaked and is FADING (Rule 2 applies), OR
  b) Position has been held > 2h and is flat or negative, OR
  c) There are 5+ LONGs open (concentration risk)
Do NOT close a recently opened LONG (< 30min) that was entered with high conviction.
If F&G > 80 (extreme greed) and all positions are LONG, close the weakest 1-2."""

new_rule4 = """RULE 4 - F&G CONTRADICTION CHECK:
If F&G < 20 (extreme fear) but regime says BULLISH, the bounce may be fragile.
HOWEVER: extreme fear is ALSO when contrarian LONGs make the most money (violent bounces).
So do NOT close LONGs just because F&G is low. Only close LONGs in extreme fear IF:
  a) Position has peaked above +0.5% and faded below 30% of peak (Rule 2, but with 30% not 40% threshold), OR
  b) Position has been held > 4h (not 2h) and is flat or negative, OR
  c) There are 6+ LONGs open (raised from 5 during Capitulation)
Do NOT close a recently opened LONG (< 2h) that was entered during Extreme Fear (F&G < 20).
These are CAPITULATION REVERSAL entries - they need time to develop. The first 1-2 hours
are often choppy before the real bounce kicks in.
If F&G > 80 (extreme greed) and all positions are LONG, close the weakest 1-2."""

if old_rule4 in content:
    content = content.replace(old_rule4, new_rule4)
    print("  Rule 4 patched: stronger protection for Extreme Fear LONGs")
else:
    print("  WARNING: Could not find Rule 4 text.")

# --- RULE 5: Relax correlated pair limit during Capitulation ---
old_rule5 = """RULE 5 - CORRELATED PAIR LIMIT:
BTC, ETH, SOL, DOGE all move together. If BTC LONG is open, max 2 more altcoin LONGs
in the same direction. Close the weakest correlated altcoin positions."""

new_rule5 = """RULE 5 - CORRELATED PAIR LIMIT:
BTC, ETH, SOL, DOGE all move together. If BTC LONG is open, max 2 more altcoin LONGs
in the same direction normally. Close the weakest correlated altcoin positions.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 4 correlated altcoin LONGs alongside BTC.
During capitulation bounces, correlation is your FRIEND - everything bounces together.
Only enforce the strict 2-altcoin limit when F&G >= 15."""

if old_rule5 in content:
    content = content.replace(old_rule5, new_rule5)
    print("  Rule 5 patched: correlated limit relaxed during Capitulation")
else:
    print("  WARNING: Could not find Rule 5 text.")

# --- RULE 10: Extend grace period during Extreme Fear ---
old_rule10 = """RULE 10 - GRACE PERIOD FOR HIGH-CONVICTION ENTRIES:
Positions opened < 30 minutes ago with confidence >= 85% get a GRACE PERIOD.
Do NOT close them unless they are losing more than -1%. Give the trade time to work.
Positions opened < 30 min with < 85% confidence OR positions older than 30 min
have no protection -- apply all other rules normally."""

new_rule10 = """RULE 10 - GRACE PERIOD FOR HIGH-CONVICTION ENTRIES:
Positions opened < 30 minutes ago with confidence >= 85% get a GRACE PERIOD.
Do NOT close them unless they are losing more than -1%. Give the trade time to work.
EXTREME FEAR EXTENSION: If F&G < 20, the grace period extends to 120 MINUTES (2 hours)
for ALL positions opened during this period, regardless of confidence level.
Capitulation reversals are choppy - the first 1-2 hours often show red before the real
bounce materializes. Closing a -0.3% position at 45 minutes kills the reversal play.
Only close Extreme Fear entries within the first 2h if they breach -2% (hard stop).
Positions outside grace period (> 30min normal, > 2h extreme fear) have no protection."""

if old_rule10 in content:
    content = content.replace(old_rule10, new_rule10)
    print("  Rule 10 patched: 2h grace period during Extreme Fear")
else:
    print("  WARNING: Could not find Rule 10 text.")

with open("smt_daemon_v3_1.py", "w") as f:
    f.write(content)
PYEOF

# ============================================================
# PATCH 3: Increase MAX_SAME_DIRECTION during Extreme Fear
# (hardcoded check at line ~710)
# ============================================================
echo "[3/3] Patching directional limit in trade execution..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

old = """            MAX_SAME_DIRECTION = 4  # Never more than 4 LONGs or 4 SHORTs
            
            for opportunity in trade_opportunities:"""

new = """            MAX_SAME_DIRECTION = 4  # Never more than 4 LONGs or 4 SHORTs
            # V3.1.43: Allow 5 LONGs during Capitulation (F&G < 15)
            if trade_opportunities:
                first_fg = trade_opportunities[0]["decision"].get("fear_greed", 50)
                if first_fg < 15:
                    MAX_SAME_DIRECTION = 5
                    logger.info(f"CAPITULATION MODE: F&G={first_fg}, raising directional limit to 5")
            
            for opportunity in trade_opportunities:"""

if old in content:
    content = content.replace(old, new)
    with open("smt_daemon_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 3 applied: MAX_SAME_DIRECTION 4->5 during Capitulation")
else:
    print("  WARNING: Could not find MAX_SAME_DIRECTION block.")
PYEOF

# ============================================================
# VERIFY
# ============================================================
echo ""
echo "=== VERIFICATION ==="
echo ""
echo "Patch 1 (SHORT PnL fix):"
grep -n "V3.1.43 FIX: SHORT also uses notional" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2a (Rule 1 - directional limit exception):"
grep -n "allow up to 5 LONGs" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2b (Rule 4 - extended hold time):"
grep -n "CAPITULATION REVERSAL entries" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2c (Rule 5 - correlated pair exception):"
grep -n "allow up to 4 correlated altcoin" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2d (Rule 10 - 2h grace period):"
grep -n "EXTREME FEAR EXTENSION" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 3 (MAX_SAME_DIRECTION dynamic):"
grep -n "CAPITULATION MODE" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "=== DONE ==="
echo ""
echo "Next steps:"
echo "  1. pkill -f smt_daemon"
echo "  2. nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &"
echo "  3. tail -f daemon.log"
echo "  4. git add . && git commit -m 'V3.1.43: Portfolio Manager Extreme Fear fix - relaxed directional limits, 2h grace period, SHORT PnL fix' && git push"
