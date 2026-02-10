#!/bin/bash
# SMT V3.1.44 - HEDGE CONFLICT + FLOW CAP + place_order FIX
# Fixes:
#   1. place_order NameError in hedge reduce logic
#   2. Disable hedging when F&G < 15 (Capitulation = pick a side)
#   3. Cap Flow SHORT confidence when F&G < 10 (don't short the literal bottom)
#
# Run from: ~/smt-weex-trading-bot/v3/

set -e

echo "=== SMT V3.1.44 PATCH: Hedge Conflict + Flow Cap ==="
echo ""

DAEMON="smt_daemon_v3_1.py"
NIGHTLY="smt_nightly_trade_v3_1.py"

# ============================================================
# STEP 0: BACKUP
# ============================================================
echo "[0/3] Creating backups..."
cp "$DAEMON" "${DAEMON}.bak_v3143"
cp "$NIGHTLY" "${NIGHTLY}.bak_v3143"
echo "  Backed up both files"

# ============================================================
# PATCH 1: Fix place_order NameError in hedge reduce
# The function is imported in smt_nightly_trade but not in daemon scope
# Line ~786 in daemon
# ============================================================
echo "[1/3] Fixing place_order NameError in hedge reduce..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

old = """                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))
                            
                            # Calculate 50% close size
                            from smt_nightly_trade_v3_1 import round_size_to_step
                            close_size = round_size_to_step(opp_size * 0.5, sym)"""

new = """                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))
                            
                            # Calculate 50% close size
                            # V3.1.44 FIX: Import place_order + round_size_to_step
                            from smt_nightly_trade_v3_1 import round_size_to_step, place_order
                            close_size = round_size_to_step(opp_size * 0.5, sym)"""

if old in content:
    content = content.replace(old, new)
    with open("smt_daemon_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 1 applied: place_order now imported in hedge reduce scope")
else:
    print("  WARNING: Could not find hedge reduce block. Check manually.")
PYEOF

# ============================================================
# PATCH 2: Disable hedging when F&G < 15 (Capitulation)
# In the hedge decision points (lines ~543, ~560)
# We modify HEDGE_CONFIDENCE_THRESHOLD to be dynamic
# ============================================================
echo "[2/3] Disabling hedging during Capitulation (F&G < 15)..."

python3 << 'PYEOF'
with open("smt_daemon_v3_1.py", "r") as f:
    content = f.read()

# Replace the static hedge threshold with a dynamic one that checks F&G
old = """        # Confidence thresholds
        COOLDOWN_OVERRIDE_CONFIDENCE = 0.85
        HEDGE_CONFIDENCE_THRESHOLD = 0.80  # V3.1.37: Lowered from 90% to allow hedging"""

new = """        # Confidence thresholds
        COOLDOWN_OVERRIDE_CONFIDENCE = 0.85
        HEDGE_CONFIDENCE_THRESHOLD = 0.80  # V3.1.37: Lowered from 90% to allow hedging
        
        # V3.1.44: Disable hedging during Capitulation - pick a side, don't fight yourself
        # Fetch F&G early so we can use it for hedge decisions
        try:
            from smt_nightly_trade_v3_1 import get_fear_greed_index
            _fg_check = get_fear_greed_index()
            _fg_value = _fg_check.get("value", 50)
            if _fg_value < 15:
                HEDGE_CONFIDENCE_THRESHOLD = 1.0  # Impossible to meet = no hedges
                logger.info(f"CAPITULATION: F&G={_fg_value} < 15, hedging DISABLED (pick a side)")
        except:
            pass"""

if old in content:
    content = content.replace(old, new)
    with open("smt_daemon_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 2 applied: Hedging disabled when F&G < 15")
else:
    print("  WARNING: Could not find HEDGE_CONFIDENCE_THRESHOLD block.")
PYEOF

# ============================================================
# PATCH 3: Cap Flow SHORT confidence when F&G < 10
# In FlowPersona (smt_nightly_trade_v3_1.py lines ~1472-1478)
# When market is at literal capitulation bottom, reduce SHORT signals
# ============================================================
echo "[3/3] Capping Flow SHORT confidence during deep capitulation..."

python3 << 'PYEOF'
with open("smt_nightly_trade_v3_1.py", "r") as f:
    content = f.read()

# Add F&G cap after the signal generation block in FlowPersona
# We insert right after the funding rate section ends and before the final return
# Target: after all signals are built, cap SHORT confidence if F&G < 10

old = """            if extreme_selling:
                # V3.1.17: MASSIVE SELL PRESSURE - ignore depth entirely
                signals.append(("SHORT", 0.85, f"EXTREME taker selling: {taker_ratio:.2f}"))
                # Don't even add depth signal - it's fake/spoofing
            elif heavy_selling:
                # V3.1.17: Heavy selling - taker wins over depth
                signals.append(("SHORT", 0.70, f"Heavy taker selling: {taker_ratio:.2f}"))"""

new = """            # V3.1.44: Cap SHORT confidence during deep capitulation (F&G < 10)
            # Shorting the literal bottom of the fear index = missing the god candle
            fg_data = get_fear_greed_index()
            fg_val = fg_data.get("value", 50)
            capitulation_short_cap = fg_val < 10
            
            if extreme_selling:
                # V3.1.17: MASSIVE SELL PRESSURE - ignore depth entirely
                if capitulation_short_cap:
                    signals.append(("SHORT", 0.55, f"EXTREME taker selling: {taker_ratio:.2f} (CAPPED: F&G={fg_val})"))
                    print(f"  [FLOW] CAPITULATION CAP: Extreme sell 0.85->0.55 (F&G={fg_val})")
                else:
                    signals.append(("SHORT", 0.85, f"EXTREME taker selling: {taker_ratio:.2f}"))
                # Don't even add depth signal - it's fake/spoofing
            elif heavy_selling:
                # V3.1.17: Heavy selling - taker wins over depth
                if capitulation_short_cap:
                    signals.append(("SHORT", 0.40, f"Heavy taker selling: {taker_ratio:.2f} (CAPPED: F&G={fg_val})"))
                    print(f"  [FLOW] CAPITULATION CAP: Heavy sell 0.70->0.40 (F&G={fg_val})")
                else:
                    signals.append(("SHORT", 0.70, f"Heavy taker selling: {taker_ratio:.2f}"))"""

if old in content:
    content = content.replace(old, new)
    with open("smt_nightly_trade_v3_1.py", "w") as f:
        f.write(content)
    print("  PATCH 3 applied: Flow SHORT confidence capped when F&G < 10")
else:
    print("  WARNING: Could not find Flow extreme_selling block.")
PYEOF

# ============================================================
# VERIFY
# ============================================================
echo ""
echo "=== VERIFICATION ==="
echo ""
echo "Patch 1 (place_order import fix):"
grep -n "V3.1.44 FIX: Import place_order" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 2 (hedge disabled during Capitulation):"
grep -n "hedging DISABLED" "$DAEMON" && echo "  OK" || echo "  MISSING"
echo ""
echo "Patch 3 (Flow SHORT cap):"
grep -n "CAPITULATION CAP" "$NIGHTLY" && echo "  OK" || echo "  MISSING"
echo ""
echo "=== DONE ==="
echo ""
echo "Next steps:"
echo "  1. pkill -f smt_daemon"
echo "  2. nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &"
echo "  3. tail -f daemon.log  (watch for 'CAPITULATION: hedging DISABLED' and 'CAPITULATION CAP' messages)"
echo "  4. git add . && git commit -m 'V3.1.44: Fix place_order NameError, disable hedging in Capitulation, cap Flow SHORT when F&G<10' && git push"
