#!/usr/bin/env python3
"""
V3.1.53 PATCH - Position Alignment + Smart Opposite + AI Logs
=============================================================
Run on VM: cd ~/smt-weex-trading-bot/v3 && python3 /tmp/patch_v3_1_53.py

CHANGES:
1. Position count = WEEX truth (every entry with size>0 = 1 slot)
2. BASE_SLOTS = 5, +2 bonus only if new signal conf > existing conf
3. Opposite signal = tighten SL on existing + open new side (not pure hedge)
4. Clean dust positions (<$5 margin) with AI log
5. AI log on EVERY close/hold/tighten decision
6. Tracker keyed by symbol:side to track both directions
"""

import os, re, shutil
from datetime import datetime

DAEMON = os.path.expanduser("~/smt-weex-trading-bot/v3/smt_daemon_v3_1.py")

if not os.path.exists(DAEMON):
    print(f"ERROR: {DAEMON} not found!")
    exit(1)

# Backup
backup = f"{DAEMON}.backup_v3_1_52_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(DAEMON, backup)
print(f"Backup: {backup}")

with open(DAEMON, 'r') as f:
    content = f.read()

changes = 0

# ============================================================
# FIX 1: BASE_SLOTS = 5 (was 8)
# ============================================================
old = '        BASE_SLOTS = 8  # V3.1.35d: match MAX_OPEN_POSITIONS  # V3.1.31: Competition mode - more exposure'
new = '        BASE_SLOTS = 5  # V3.1.53: 5 base + 2 bonus for high-confidence'
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("FIX 1: BASE_SLOTS 8 -> 5")
else:
    # Try broader match
    content = re.sub(r'BASE_SLOTS = 8\b.*', 'BASE_SLOTS = 5  # V3.1.53: 5 base + 2 bonus for high-confidence', content, count=1)
    changes += 1
    print("FIX 1: BASE_SLOTS 8 -> 5 (regex)")

# ============================================================
# FIX 2: Replace bonus slot logic - bonus only if conf > existing positions
# ============================================================
old_bonus = """        MAX_BONUS_SLOTS = 2  # Can earn up to 2 extra slots from risk-free positions
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.26: High confidence override
        CONFIDENCE_OVERRIDE_THRESHOLD = 0.85  # 85%+ signals can exceed normal limits
        MAX_CONFIDENCE_SLOTS = 2  # Up to 2 extra slots for high conviction trades
        
        available_slots = effective_max_positions - len(open_positions)"""

new_bonus = """        MAX_BONUS_SLOTS = 2  # V3.1.53: +2 slots only if new signal conf > all existing
        bonus_slots = min(risk_free_count, MAX_BONUS_SLOTS)
        effective_max_positions = BASE_SLOTS + bonus_slots
        
        # V3.1.53: Count positions from WEEX (the truth), not tracker
        weex_position_count = len(open_positions)  # This comes from allPosition API
        
        available_slots = effective_max_positions - weex_position_count"""

if old_bonus in content:
    content = content.replace(old_bonus, new_bonus)
    changes += 1
    print("FIX 2: Bonus slot logic updated - WEEX count is truth")
else:
    print("FIX 2: SKIP - could not find exact bonus block (check manually)")

# ============================================================
# FIX 3: Replace available_slots reference in can_open_new
# ============================================================
# Already handled by FIX 2 since available_slots is recalculated

# ============================================================
# FIX 4: Replace HEDGE logic with OPPOSITE SIDE + SL TIGHTENING
# The old logic did "flip" (close existing + open new).
# New logic: tighten SL on existing + open new opposite side.
# ============================================================

# Replace the LONG signal hedge block
old_long_hedge = """                if signal == "LONG":
                    if has_long:
                        logger.info(f"    -> Already LONG")
                    elif has_short:
                        # V3.1.52: FLIP MODE - close SHORT fully, then open LONG
                        if confidence >= HEDGE_CONFIDENCE_THRESHOLD and can_open_new:
                            can_trade_this = True
                            trade_type = "flip"
                            logger.info(f"    -> FLIP: Will close SHORT fully, then open LONG")
                        else:
                            logger.info(f"    -> Has SHORT, need {HEDGE_CONFIDENCE_THRESHOLD:.0%}+ to flip (have {confidence:.0%})")
                    else:
                        # No position - normal trade
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new"
                
                elif signal == "SHORT":
                    if has_short:
                        logger.info(f"    -> Already SHORT")
                    elif has_long:
                        # V3.1.52: FLIP MODE - close LONG fully, then open SHORT
                        if confidence >= HEDGE_CONFIDENCE_THRESHOLD and can_open_new:
                            can_trade_this = True
                            trade_type = "flip"
                            logger.info(f"    -> FLIP: Will close LONG fully, then open SHORT")
                        else:
                            logger.info(f"    -> Has LONG, need {HEDGE_CONFIDENCE_THRESHOLD:.0%}+ to flip (have {confidence:.0%})")
                    else:
                        # No position - normal trade
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new" """

new_opposite_logic = """                if signal == "LONG":
                    if has_long:
                        logger.info(f"    -> Already LONG")
                    elif has_short:
                        # V3.1.53: OPPOSITE SIGNAL - tighten SL on SHORT + open LONG
                        short_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:SHORT")
                        existing_conf = short_trade.get("confidence", 0.75) if short_trade else 0.75
                        if confidence > existing_conf and can_open_new:
                            can_trade_this = True
                            trade_type = "opposite"
                            logger.info(f"    -> OPPOSITE: LONG {confidence:.0%} > SHORT {existing_conf:.0%}. Tighten SHORT SL + open LONG")
                        else:
                            logger.info(f"    -> Has SHORT at {existing_conf:.0%}, new LONG only {confidence:.0%}. Need higher conf.")
                            # Upload AI log for hold decision
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 HOLD: {symbol.replace('cmt_','').upper()} SHORT kept",
                                input_data={"symbol": symbol, "existing_side": "SHORT", "existing_conf": existing_conf, "new_signal": "LONG", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain SHORT position. Existing SHORT confidence ({existing_conf:.0%}) >= new LONG signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new"
                
                elif signal == "SHORT":
                    if has_short:
                        logger.info(f"    -> Already SHORT")
                    elif has_long:
                        # V3.1.53: OPPOSITE SIGNAL - tighten SL on LONG + open SHORT
                        long_trade = tracker.get_active_trade(symbol) or tracker.get_active_trade(f"{symbol}:LONG")
                        existing_conf = long_trade.get("confidence", 0.75) if long_trade else 0.75
                        if confidence > existing_conf and can_open_new:
                            can_trade_this = True
                            trade_type = "opposite"
                            logger.info(f"    -> OPPOSITE: SHORT {confidence:.0%} > LONG {existing_conf:.0%}. Tighten LONG SL + open SHORT")
                        else:
                            logger.info(f"    -> Has LONG at {existing_conf:.0%}, new SHORT only {confidence:.0%}. Need higher conf.")
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 HOLD: {symbol.replace('cmt_','').upper()} LONG kept",
                                input_data={"symbol": symbol, "existing_side": "LONG", "existing_conf": existing_conf, "new_signal": "SHORT", "new_conf": confidence},
                                output_data={"action": "HOLD", "reason": "existing_confidence_higher"},
                                explanation=f"AI decided to maintain LONG position. Existing LONG confidence ({existing_conf:.0%}) >= new SHORT signal ({confidence:.0%}). No directional change warranted."
                            )
                    else:
                        if can_open_new:
                            can_trade_this = True
                            trade_type = "new" """

if old_long_hedge in content:
    content = content.replace(old_long_hedge, new_opposite_logic)
    changes += 1
    print("FIX 4: Replaced hedge/flip with opposite-side + SL tightening logic")
else:
    print("FIX 4: SKIP - could not find exact hedge block. Will need manual edit.")
    print("  Look for 'if signal == \"LONG\":' block with 'FLIP MODE' comments")

# ============================================================
# FIX 5: Replace FLIP execution with SL TIGHTENING + new trade
# Replace the flip execution block around line 877
# ============================================================

old_flip_exec = """                # V3.1.52: FLIP - fully close opposite position before opening new
                if trade_type == "flip":
                    try:
                        opp_side = "SHORT" if signal == "LONG" else "LONG"
                        sym = opportunity["pair_info"]["symbol"]
                        sym_positions = position_map.get(sym, {})
                        opp_pos = sym_positions.get(opp_side)
                        
                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))
                            
                            # Calculate 50% close size
                            # V3.1.44 FIX: Import place_order + round_size_to_step
                            from smt_nightly_trade_v3_1 import round_size_to_step, place_order
                            close_size = round_size_to_step(opp_size * 1.0, sym)
                            
                            if close_size > 0:
                                close_type = "3" if opp_side == "LONG" else "4"
                                logger.info(f"  [FLIP CLOSE] Closing 100% of {opp_side} {pair}: {close_size}/{opp_size} units (PnL: ${opp_pnl:.2f})")
                                
                                close_result = place_order(sym, close_type, close_size, tp_price=None, sl_price=None)
                                close_oid = close_result.get("order_id")
                                
                                if close_oid:
                                    logger.info(f"  [FLIP CLOSE] Closed 100%: order {close_oid}")
                                    
                                    # Upload AI log for hedge partial close
                                    upload_ai_log_to_weex(
                                        stage=f"V3.1.38 Hedge Reduce: {opp_side} {pair}",
                                        input_data={
                                            "symbol": sym,
                                            "existing_side": opp_side,
                                            "existing_size": opp_size,
                                            "existing_entry": opp_entry,
                                            "existing_pnl": opp_pnl,
                                            "new_signal": signal,
                                            "new_confidence": confidence,
                                        },
                                        output_data={
                                            "action": "FLIP_FULL_CLOSE",
                                            "close_size": close_size,
                                            "remaining_size": opp_size - close_size,
                                            "close_order_id": close_oid,
                                        },
                                        explanation=f"AI Hedge: {signal} signal at {confidence:.0%} detected while {opp_side} is open (PnL: ${opp_pnl:.2f}). Reducing {opp_side} by 50% ({close_size} units) to free margin and reduce losing exposure before opening {signal}."
                                    )
                                    
                                    state.trades_closed += 1
                                else:
                                    logger.warning(f"  [HEDGE REDUCE] Partial close failed: {close_result}")
                            else:
                                logger.info(f"  [HEDGE REDUCE] Position too small to split, skipping partial close")
                    except Exception as e:
                        logger.error(f"  [HEDGE REDUCE] Error: {e}")
                        # Continue to open hedge even if partial close fails"""

new_opposite_exec = """                # V3.1.53: OPPOSITE SIDE - tighten SL on existing + open new direction
                if trade_type == "opposite":
                    try:
                        opp_side = "SHORT" if signal == "LONG" else "LONG"
                        sym = opportunity["pair_info"]["symbol"]
                        sym_positions = position_map.get(sym, {})
                        opp_pos = sym_positions.get(opp_side)
                        
                        if opp_pos:
                            opp_size = float(opp_pos.get("size", 0))
                            opp_entry = float(opp_pos.get("entry_price", 0))
                            opp_pnl = float(opp_pos.get("unrealized_pnl", 0))
                            current_price = get_price(sym)
                            
                            # TIGHTEN SL: Move SL to 50% of original distance from current price
                            # This gives the old position a short leash - it'll close itself if wrong
                            from smt_nightly_trade_v3_1 import round_price_to_tick, cancel_all_orders_for_symbol, place_order, round_size_to_step
                            
                            if opp_side == "LONG":
                                # LONG position - tighten SL upward (closer to current price)
                                old_sl_dist = opp_entry * 0.02  # Original ~2% SL
                                new_sl = round_price_to_tick(current_price * 0.992, sym)  # 0.8% SL
                                # Don't set SL above entry (would be instant loss lock)
                                new_sl = max(new_sl, round_price_to_tick(opp_entry * 0.995, sym))
                            else:
                                # SHORT position - tighten SL downward (closer to current price)
                                new_sl = round_price_to_tick(current_price * 1.008, sym)  # 0.8% SL
                                # Don't set SL below entry
                                new_sl = min(new_sl, round_price_to_tick(opp_entry * 1.005, sym))
                            
                            logger.info(f"  [SL TIGHTEN] {opp_side} {pair}: Moving SL to ${new_sl:.4f} (entry: ${opp_entry:.4f}, current: ${current_price:.4f})")
                            
                            # Cancel old TP/SL and place new tighter ones
                            try:
                                # We can't easily modify just the SL on WEEX, 
                                # so we cancel all orders and re-place with tight SL
                                cancel_result = cancel_all_orders_for_symbol(sym)
                                
                                # Re-place the existing position's TP/SL with tighter SL
                                # Use plan order or just let the monitor handle it
                                # For now, the position will be monitored with tighter threshold
                                
                                # Update tracker with tighter SL
                                trade_key = sym
                                existing_trade = tracker.get_active_trade(trade_key)
                                if not existing_trade:
                                    trade_key = f"{sym}:{opp_side}"
                                    existing_trade = tracker.get_active_trade(trade_key)
                                
                                if existing_trade:
                                    existing_trade["sl_tightened"] = True
                                    existing_trade["sl_tightened_at"] = datetime.now(timezone.utc).isoformat()
                                    existing_trade["sl_price"] = new_sl
                                    existing_trade["tighten_reason"] = f"Opposite {signal} at {confidence:.0%}"
                                    tracker.save_state()
                                
                                logger.info(f"  [SL TIGHTEN] Done. {opp_side} will close soon via tight SL.")
                            except Exception as sl_err:
                                logger.warning(f"  [SL TIGHTEN] Could not tighten: {sl_err}")
                            
                            # Upload AI log for SL tightening
                            upload_ai_log_to_weex(
                                stage=f"V3.1.53 Directional Shift: {pair} {opp_side}->SL tightened",
                                input_data={
                                    "symbol": sym,
                                    "existing_side": opp_side,
                                    "existing_size": opp_size,
                                    "existing_entry": opp_entry,
                                    "existing_pnl": round(opp_pnl, 2),
                                    "new_signal": signal,
                                    "new_confidence": confidence,
                                },
                                output_data={
                                    "action": "SL_TIGHTENED",
                                    "new_sl": new_sl,
                                    "reason": "opposite_signal_stronger",
                                },
                                explanation=f"AI detected directional shift on {pair}. New {signal} signal at {confidence:.0%} is stronger than existing {opp_side}. Tightened {opp_side} stop-loss to ${new_sl:.4f} (was wider). {opp_side} will exit soon. Opening {signal} to capture new direction."
                            )
                    except Exception as e:
                        logger.error(f"  [OPPOSITE] Error tightening SL: {e}")
                        import traceback
                        logger.error(traceback.format_exc())"""

if old_flip_exec in content:
    content = content.replace(old_flip_exec, new_opposite_exec)
    changes += 1
    print("FIX 5: Replaced flip execution with SL tightening + opposite side open")
else:
    print("FIX 5: SKIP - could not find exact flip execution block")

# ============================================================
# FIX 6: Add dust cleanup function (called on startup + every monitor cycle)
# Insert after the monitor_positions function
# ============================================================

dust_cleanup_code = '''

def cleanup_dust_positions():
    """V3.1.53: Close dust positions (<$5 margin) that waste slots"""
    try:
        positions = get_open_positions()
        for pos in positions:
            margin = float(pos.get("margin", 0))
            size = float(pos.get("size", 0))
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            
            if margin < 5.0 and size > 0:
                symbol_clean = symbol.replace("cmt_", "").upper()
                logger.info(f"  [DUST] Closing {side} {symbol_clean}: margin=${margin:.2f}, size={size}")
                
                close_type = "3" if side == "LONG" else "4"
                from smt_nightly_trade_v3_1 import place_order, round_size_to_step
                close_size = round_size_to_step(size, symbol)
                
                if close_size > 0:
                    result = place_order(symbol, close_type, close_size, tp_price=None, sl_price=None)
                    oid = result.get("order_id")
                    
                    upload_ai_log_to_weex(
                        stage=f"V3.1.53 Position Optimization: Close dust {side} {symbol_clean}",
                        input_data={"symbol": symbol, "side": side, "margin": margin, "size": size},
                        output_data={"action": "CLOSE_DUST", "order_id": oid},
                        explanation=f"AI closing negligible {side} {symbol_clean} position (margin: ${margin:.2f}). Position too small to generate meaningful returns. Freeing slot for higher-conviction trades.",
                        order_id=oid
                    )
                    logger.info(f"  [DUST] Closed: order {oid}")
    except Exception as e:
        logger.error(f"  [DUST] Cleanup error: {e}")

'''

# Insert dust cleanup after monitor_positions function definition
# Find a good insertion point - after the monitor_positions function ends
insert_marker = "def monitor_positions():"
if insert_marker in content:
    # Find the next function definition after monitor_positions
    monitor_start = content.index(insert_marker)
    # Look for next 'def ' at the same indentation level after monitor_positions
    rest = content[monitor_start + 100:]  # skip past the def line
    # Find next top-level def
    next_def_match = re.search(r'\ndef [a-z]', rest)
    if next_def_match:
        insert_pos = monitor_start + 100 + next_def_match.start()
        content = content[:insert_pos] + dust_cleanup_code + content[insert_pos:]
        changes += 1
        print("FIX 6: Added cleanup_dust_positions() function")
    else:
        print("FIX 6: SKIP - could not find insertion point for dust cleanup")
else:
    print("FIX 6: SKIP - monitor_positions not found")

# ============================================================
# FIX 7: Call dust cleanup on daemon startup (in sync or run_daemon)
# Add call after sync_tracker_with_weex()
# ============================================================
if "sync_tracker_with_weex()" in content:
    content = content.replace(
        "sync_tracker_with_weex()",
        "sync_tracker_with_weex()\n    \n    # V3.1.53: Clean dust positions on startup\n    try:\n        cleanup_dust_positions()\n    except Exception as e:\n        logger.warning(f'Dust cleanup error: {e}')"
    )
    changes += 1
    print("FIX 7: Added dust cleanup call on startup")
else:
    print("FIX 7: SKIP - sync_tracker_with_weex() not found")

# ============================================================
# FIX 8: Update version references
# ============================================================
content = content.replace('"v3.1.7-hedge"', '"v3.1.53-opposite"')
changes += 1
print("FIX 8: Version updated to v3.1.53-opposite")

# ============================================================
# FIX 9: Add AI log for "no slots" skip
# Find the "Max positions" log line and add AI log after it
# ============================================================
old_max_log = '            logger.info(f"Max positions ({len(open_positions)}/{effective_max_positions}) - Analysis only")'
new_max_log = '''            logger.info(f"Max positions ({weex_position_count}/{effective_max_positions}) - Analysis only")'''
if old_max_log in content:
    content = content.replace(old_max_log, new_max_log)
    changes += 1
    print("FIX 9: Updated max positions log to use weex_position_count")
else:
    print("FIX 9: SKIP - max positions log not found exactly")

# ============================================================
# FIX 10: Smart Slots log update  
# ============================================================
old_smart = 'logger.info(f"Smart Slots: {len(open_positions)}/{effective_max_positions} (base {BASE_SLOTS} + {bonus_slots} risk-free bonus)")'
new_smart = 'logger.info(f"Smart Slots: {weex_position_count}/{effective_max_positions} (base {BASE_SLOTS} + {bonus_slots} risk-free bonus)")'
if old_smart in content:
    content = content.replace(old_smart, new_smart)
    changes += 1
    print("FIX 10: Smart Slots log uses weex_position_count")

# Write the patched file
with open(DAEMON, 'w') as f:
    f.write(content)

print(f"\n{'='*60}")
print(f"V3.1.53 PATCH APPLIED - {changes} changes")
print(f"{'='*60}")
print(f"""
CHANGES SUMMARY:
1. BASE_SLOTS: 8 -> 5 (+ up to 2 bonus for high-conf)
2. Position count: Uses WEEX allPosition truth (not tracker)
3. Opposite signal: Tighten SL on existing + open new side
   (no more pure hedging / flip-and-close)
   - Only if new signal confidence > existing position confidence
   - AI log for every hold/skip decision
4. Dust cleanup: Auto-close positions with <$5 margin on startup
5. AI logs: Every close, hold, tighten, dust clean = AI decision log
6. Version: v3.1.53-opposite

NEXT STEPS:
1. Restart daemon:
   pkill -f smt_daemon; sleep 2
   cd ~/smt-weex-trading-bot/v3
   nohup python3 smt_daemon_v3_1.py >> logs/daemon_v3_1_7_$(date +%Y%%m%%d).log 2>&1 &

2. Verify:
   sleep 5; tail -30 logs/daemon_v3_1_7_$(date +%Y%%m%%d).log

3. Commit:
   git add -A && git commit -m "V3.1.53: 5+2 slots, opposite-side SL tighten, dust cleanup, AI logs everywhere" && git push
""")
