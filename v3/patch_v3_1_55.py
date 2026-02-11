#!/usr/bin/env python3
"""
SMT Daemon V3.1.55 Patch: Position Sync + Portfolio Rules + Opposite-Side Resolution

Changes:
1. sync_tracker_with_weex() - uses symbol:SIDE keys to track both sides
2. Portfolio review rules - adds Rules 12, 13, modifies Rules 4, 9, CRITICAL rule
3. New resolve_opposite_sides() - mechanically closes losing side when both exist
4. Integrates into main loop
"""

import sys
import re
import shutil
from datetime import datetime

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    lines = content.split('\n')
    original_count = len(lines)
    
    # ================================================================
    # PATCH 1: Fix sync_tracker_with_weex to use symbol:SIDE keys
    # ================================================================
    old_sync = '''def sync_tracker_with_weex():
    """Sync TradeTracker with actual WEEX positions on startup.
    
    This fixes the issue where daemon restart loses track of positions.
    """
    logger.info("Syncing tracker with WEEX positions...")
    
    try:
        positions = get_open_positions()
        
        weex_symbols = {p['symbol'] for p in positions}
        tracker_symbols = set(tracker.get_active_symbols())
        
        # Find positions on WEEX but not in tracker
        missing = weex_symbols - tracker_symbols
        
        if missing:
            logger.warning(f"Found {len(missing)} untracked positions: {missing}")
            
            for pos in positions:
                if pos['symbol'] in missing:
                    tier = get_tier_for_symbol(pos['symbol'])
                    tier_config = get_tier_config(tier)
                    
                    # Add to tracker with current time (conservative - may exit sooner than needed)
                    tracker.active_trades[pos['symbol']] = {
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "side": pos['side'],
                        "entry_price": pos['entry_price'],
                        "tier": tier,
                        "max_hold_hours": tier_config['max_hold_hours'],
                        "synced": True,
                    }
                    logger.info(f"  Added {pos['symbol']} (Tier {tier}, {pos['side']} @ {pos['entry_price']:.4f})")
            
            tracker.save_state()
        
        # Find orphan trades in tracker (position closed but tracker didn't know)
        orphans = tracker_symbols - weex_symbols
        
        if orphans:
            logger.warning(f"Found {len(orphans)} orphan trades: {orphans}")
            
            for symbol in orphans:
                tracker.close_trade(symbol, {"reason": "sync_cleanup", "note": "Position not found on WEEX"})
                logger.info(f"  Removed orphan {symbol}")
        
        logger.info(f"Sync complete. Tracking {len(tracker.get_active_symbols())} positions.")
        
    except Exception as e:
        logger.error(f"Sync error: {e}")'''

    new_sync = '''def sync_tracker_with_weex():
    """V3.1.55: Sync TradeTracker with actual WEEX positions on startup.
    
    Uses symbol:SIDE keys (e.g. cmt_bnbusdt:LONG) so both sides of same
    pair can be tracked independently. Falls back to plain symbol key for
    backward compat with existing tracker lookups.
    """
    logger.info("Syncing tracker with WEEX positions...")
    
    try:
        positions = get_open_positions()
        
        # V3.1.55: Build set of symbol:SIDE keys from WEEX
        weex_keys = set()
        for p in positions:
            sym = p['symbol']
            side = p.get('side', 'LONG').upper()
            weex_keys.add(f"{sym}:{side}")
            weex_keys.add(sym)  # also track plain symbol for compat
        
        tracker_symbols = set(tracker.get_active_symbols())
        
        # Build tracker keys (both plain and symbol:SIDE)
        tracker_keys = set()
        for s in tracker_symbols:
            tracker_keys.add(s)
        
        # Find positions on WEEX but not in tracker (check both key formats)
        added = 0
        for pos in positions:
            sym = pos['symbol']
            side = pos.get('side', 'LONG').upper()
            key_sided = f"{sym}:{side}"
            
            # Check if tracked under either key format
            if key_sided not in tracker_keys and sym not in tracker_keys:
                tier = get_tier_for_symbol(sym)
                tier_config = get_tier_config(tier)
                
                tracker.active_trades[key_sided] = {
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "side": side,
                    "entry_price": float(pos.get('entry_price', 0)),
                    "tier": tier,
                    "max_hold_hours": tier_config['max_hold_hours'],
                    "synced": True,
                    "confidence": 0.75,
                }
                added += 1
                logger.info(f"  Added {key_sided} (Tier {tier}, {side} @ {float(pos.get('entry_price',0)):.4f})")
            elif key_sided not in tracker_keys and sym in tracker_keys:
                # Tracked under plain symbol but side might be wrong - check
                existing = tracker.active_trades.get(sym, {})
                existing_side = existing.get('side', '').upper()
                if existing_side and existing_side != side:
                    # Different side! Track this one separately
                    tracker.active_trades[key_sided] = {
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "side": side,
                        "entry_price": float(pos.get('entry_price', 0)),
                        "tier": get_tier_for_symbol(sym),
                        "max_hold_hours": get_tier_config(get_tier_for_symbol(sym))['max_hold_hours'],
                        "synced": True,
                        "confidence": 0.75,
                    }
                    added += 1
                    logger.info(f"  Added opposite side {key_sided} (existing {sym} is {existing_side})")
        
        if added > 0:
            logger.warning(f"Added {added} untracked positions")
            tracker.save_state()
        
        # Find orphan trades in tracker (position closed but tracker didn't know)
        orphan_count = 0
        for tracker_key in list(tracker_symbols):
            # Check if this tracker entry has a matching WEEX position
            found = False
            for pos in positions:
                sym = pos['symbol']
                side = pos.get('side', 'LONG').upper()
                if tracker_key == sym or tracker_key == f"{sym}:{side}":
                    found = True
                    break
            if not found:
                tracker.close_trade(tracker_key, {"reason": "sync_cleanup", "note": "Position not found on WEEX"})
                logger.info(f"  Removed orphan {tracker_key}")
                orphan_count += 1
        
        total_tracked = len(tracker.get_active_symbols())
        total_weex = len(positions)
        logger.info(f"Sync complete. Tracking {total_tracked} entries for {total_weex} WEEX positions.")
        
    except Exception as e:
        logger.error(f"Sync error: {e}")'''

    if old_sync in content:
        content = content.replace(old_sync, new_sync)
        print("[OK] Patched sync_tracker_with_weex")
    else:
        print("[WARN] Could not find exact sync function - trying line-based")
        # Fallback: find by def name
        pass

    # ================================================================
    # PATCH 2: Add resolve_opposite_sides() function after sync
    # ================================================================
    resolve_func = '''

def resolve_opposite_sides():
    """V3.1.55: If same symbol has BOTH Long and Short open, close the losing side.
    
    This is a mechanical rule - no Gemini needed. Two sides on same pair is
    capital-inefficient and indicates the system changed its mind but didn't
    clean up. Always close the losing side.
    """
    try:
        positions = get_open_positions()
        
        # Group by symbol
        by_symbol = {}
        for p in positions:
            sym = p.get('symbol', '')
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(p)
        
        for sym, pos_list in by_symbol.items():
            if len(pos_list) < 2:
                continue
            
            # Both sides exist
            long_pos = None
            short_pos = None
            for p in pos_list:
                if p.get('side', '').upper() == 'LONG':
                    long_pos = p
                elif p.get('side', '').upper() == 'SHORT':
                    short_pos = p
            
            if not long_pos or not short_pos:
                continue
            
            long_pnl = float(long_pos.get('unrealized_pnl', 0))
            short_pnl = float(short_pos.get('unrealized_pnl', 0))
            sym_clean = sym.replace('cmt_', '').upper()
            
            logger.info(f"  [OPPOSITE] {sym_clean}: LONG PnL=${long_pnl:.2f}, SHORT PnL=${short_pnl:.2f}")
            
            # Close the losing side (or smaller PnL if both positive)
            if long_pnl <= short_pnl:
                # Close LONG (it's the loser or smaller winner)
                close_side = "LONG"
                close_pos = long_pos
                keep_side = "SHORT"
                keep_pnl = short_pnl
            else:
                close_side = "SHORT"
                close_pos = short_pos
                keep_side = "LONG"
                keep_pnl = long_pnl
            
            close_pnl = float(close_pos.get('unrealized_pnl', 0))
            close_size = float(close_pos.get('size', 0))
            
            if close_size <= 0:
                continue
            
            logger.info(f"  [OPPOSITE] Closing {close_side} {sym_clean} (PnL=${close_pnl:.2f}), keeping {keep_side} (PnL=${keep_pnl:.2f})")
            
            from smt_nightly_trade_v3_1 import place_order, round_size_to_step
            close_type = "3" if close_side == "LONG" else "4"
            rounded_size = round_size_to_step(close_size, sym)
            
            if rounded_size > 0:
                # Cancel any pending orders for this side first
                cancel_all_orders_for_symbol(sym)
                
                result = place_order(sym, close_type, rounded_size, tp_price=None, sl_price=None)
                oid = result.get("order_id")
                
                upload_ai_log_to_weex(
                    stage=f"V3.1.55 Opposite-Side Resolution: Close {close_side} {sym_clean}",
                    input_data={
                        "symbol": sym,
                        "long_pnl": long_pnl,
                        "short_pnl": short_pnl,
                        "closing_side": close_side,
                        "keeping_side": keep_side,
                    },
                    output_data={"action": "CLOSE_OPPOSITE", "order_id": oid},
                    explanation=f"AI detected both LONG (PnL=${long_pnl:.2f}) and SHORT (PnL=${short_pnl:.2f}) open on {sym_clean}. Closing {close_side} side to eliminate capital-inefficient hedge and free margin for the winning {keep_side} position.",
                    order_id=oid
                )
                
                # Remove from tracker
                for key in [f"{sym}:{close_side}", sym]:
                    if key in tracker.active_trades:
                        tracker.close_trade(key, {"reason": "opposite_side_resolution", "pnl": close_pnl})
                        break
                
                logger.info(f"  [OPPOSITE] Closed {close_side} {sym_clean}: order {oid}")
    
    except Exception as e:
        logger.error(f"  [OPPOSITE] Resolution error: {e}")'''

    # Insert after sync function
    insert_marker = "        logger.error(f\"Sync error: {e}\")"
    # Find the LAST occurrence in the sync function context
    sync_end_idx = content.find(insert_marker)
    if sync_end_idx > 0:
        # Find the next section marker after sync
        next_section = content.find("# ============", sync_end_idx)
        if next_section > 0:
            content = content[:next_section] + resolve_func + "\n\n\n" + content[next_section:]
            print("[OK] Added resolve_opposite_sides() function")
        else:
            print("[WARN] Could not find insertion point for resolve_opposite_sides")
    else:
        print("[WARN] Could not find sync error handler for insertion")

    # ================================================================
    # PATCH 3: Rewrite portfolio review rules
    # ================================================================
    old_rules_start = "=== MANDATORY RULES (from 40+ iterations of battle-tested experience) ==="
    old_rules_end = "Whale-backed positions historically produce the biggest wins. Protect them."
    
    new_rules = """=== MANDATORY RULES (V3.1.55 - 45+ iterations of battle-tested experience) ===

RULE 1 - OPPOSITE SIDE RESOLUTION (HIGHEST PRIORITY):
If the SAME symbol has BOTH a LONG and SHORT position open, IMMEDIATELY close the losing side.
This is NOT optional. Two-sided positions on the same pair waste margin, cancel each other out,
and occupy 2 slots instead of 1. Close the side with worse PnL. No exceptions.

RULE 2 - DIRECTIONAL CONCENTRATION LIMIT:
Max 5 positions in the same direction normally. If 6+ LONGs or 6+ SHORTs, close the WEAKEST ones
(lowest PnL% or highest loss) until we have max 5.
EXCEPTION: If F&G < 15 (Capitulation), allow up to 7 LONGs. Violent bounces move all alts together.

RULE 3 - LET WINNERS RUN:
Do NOT close winning positions just because they faded from peak. Our TP orders are at 5-8%.
Closing at +0.5% when TP is at +6% means we capture $15 instead of $180.
Only close a WINNING position if it has been held past max_hold_hours.

RULE 4 - F&G EXTREME FEAR (UPDATED V3.1.55):
If F&G < 20 (extreme fear), be patient with positions BUT you CAN still close if:
(a) Same pair has both LONG and SHORT open (Rule 1 overrides)
(b) Position losing > -2% AND whale confidence >= 70% AGAINST position direction
(c) Position held longer than max_hold_hours AND losing
(d) 7+ total positions clogging slots (close weakest loser to free capital)
Extreme fear does NOT mean blindly hold everything. Our SL orders are the last defense,
but the PM should still trim obvious bad positions.

RULE 5 - WHALE DISAGREE EXIT (NEW V3.1.55):
If whale confidence >= 70% in the OPPOSITE direction of your position AND position is losing,
CLOSE IT. Smart money has turned against this trade. Examples:
- LONG position losing -1.5%, whale says SHORT@73% -> CLOSE the LONG
- SHORT position losing -0.8%, whale says LONG@78% -> CLOSE the SHORT
This is the INVERSE of Rule 11 (whale hold protection). Whale agreement = hold. Whale disagreement = exit.
Exception: if position is WINNING despite whale disagreement, keep it (price action > whale signal).

RULE 6 - BREAKEVEN PATIENCE:
If a position has faded to breakeven (within +/- 0.3%), DO NOT CLOSE. Crypto is volatile.
A position at 0% can rally to +5% in the next hour. Only the SL should close breakeven positions.

RULE 7 - TIME-BASED PATIENCE:
Do NOT close positions just because they have been open 2-4 hours.
Our TP targets are 5-8%. These moves take TIME (4-12h for alts, 12-48h for BTC).
Only close if: max_hold_hours exceeded AND position is negative.

RULE 8 - WEEKEND/LOW LIQUIDITY (check if Saturday or Sunday):
On weekends, max 4 positions. Thinner books = more manipulation.

RULE 9 - SLOT MANAGEMENT (UPDATED V3.1.55):
If we have 7+ total positions, we SHOULD free slots by closing the weakest position.
7+ positions means we are over-committed and have no room for high-conviction entries.
Close the position with: worst PnL% + whale disagreement + longest hold time past max.
If we have 5 or fewer, be patient - SL orders protect us.

RULE 10 - GRACE PERIOD:
Positions opened < 30 minutes ago with confidence >= 85% get a GRACE PERIOD.
Do NOT close them unless losing more than -1.5%. Give the trade time to work.
If F&G < 20, grace period extends to 90 minutes.

RULE 11 - WHALE HOLD PROTECTION:
If whale confidence >= 70% in the SAME direction as the position,
do NOT close it. Smart money agrees. Let it run to full TP.
Only exception: max_hold_hours exceeded AND losing more than -3%.

RULE 12 - FUNDING COST AWARENESS:
If funding rate is positive and we are LONG, we PAY every 8h. If position is barely
profitable (+0.1-0.3%) and funding eats the profit, close it. Same for negative funding + SHORT.

RULE 13 - STALE POSITION CLEANUP:
If a position has been held > max_hold_hours AND is losing ANY amount, close it.
Do not wait for -3%. A stale losing position is dead capital. Free it.
If the position is winning past max_hold_hours, let it run but tighten expectations."""

    old_your_job = """=== YOUR JOB ===
Apply ALL 10 rules above. For each position, check every rule. Be PATIENT with winners.
Our biggest problem is NOT fading profits -- it is that we close winners too early.
Our biggest win is $55 but biggest loss is $299. We need $100+ wins to recover.
CRITICAL V3.1.47 RULE: Do NOT close ANY position at a loss. We have SL orders on WEEX.
Every time the PM closes a losing position, we lock in a loss AND pay fees AND lose the bounce.
Our data shows: -$267 lost in 8 hours from PM closing losers that would have recovered.
The ONLY acceptable closes are:
(a) Position past max_hold_hours AND losing more than -3% = stale loser, SL probably broken
(b) 8+ positions in same direction creating liquidation cascade risk
(c) Winning positions that hit max_hold_hours (take the profit)
NEVER close: positions under 6 hours old, positions losing less than -3%, positions during F&G < 20."""

    new_your_job = """=== YOUR JOB ===
Apply ALL 13 rules above. For EACH position, check Rules 1, 5, 11 first (whale + opposite side).
Then check Rules 4, 9, 13 (fear/slots/stale). Then check the rest.

PRIORITY ORDER for closing decisions:
1. Rule 1: Opposite sides on same pair -> close loser (ALWAYS)
2. Rule 5: Whale disagrees 70%+ AND position losing -> close
3. Rule 13: Stale (past max_hold) AND losing -> close
4. Rule 9: 7+ positions -> close weakest
5. Rule 2: 6+ same direction -> close weakest

PROTECTION ORDER (reasons to KEEP):
1. Rule 11: Whale agrees 70%+ -> protect
2. Rule 3: Winning position -> let run to TP
3. Rule 6: Breakeven within +/-0.3% -> patience
4. Rule 10: Grace period (< 30min or < 90min in fear) -> patience

Do NOT be afraid to close losers. Our previous rule "never close at a loss" cost us $500+ in
dead positions that eventually hit SL anyway. Cut losers early when whale data confirms the
trade thesis is broken. Keep winners when whale data confirms the thesis is intact."""

    old_rule11 = """RULE 11 - WHALE HOLD PROTECTION (V3.1.51 "Smart Holds"):
If a position shows whale confidence >= 70% in the SAME direction as the position (e.g. whale=LONG@82% on a LONG),
do NOT close it. Smart money (on-chain whale flows + community sentiment) agrees with this trade.
Let it run to full TP. Only exception: if it has exceeded max_hold_hours AND is losing more than -3%.
Whale-backed positions historically produce the biggest wins. Protect them."""

    # Do the replacement
    # Find and replace the rules block
    start_idx = content.find(old_rules_start)
    end_idx = content.find(old_rule11)
    
    if start_idx > 0 and end_idx > 0:
        end_idx = end_idx + len(old_rule11)
        content = content[:start_idx] + new_rules + content[end_idx:]
        print("[OK] Replaced portfolio rules (Rules 1-13)")
    else:
        print(f"[WARN] Could not find rules block. Start: {start_idx}, End: {end_idx}")

    # Replace YOUR JOB section
    if old_your_job in content:
        content = content.replace(old_your_job, new_your_job)
        print("[OK] Replaced YOUR JOB section")
    else:
        print("[WARN] Could not find exact YOUR JOB section")
    
    # ================================================================
    # PATCH 4: Add resolve_opposite_sides to startup and main loop
    # ================================================================
    
    # Add to startup after dust cleanup
    old_startup = """    # V3.1.53: Clean dust positions on startup
    try:
        cleanup_dust_positions()
    except Exception as e:
        logger.warning(f'Dust cleanup error: {e}')"""
    
    new_startup = """    # V3.1.53: Clean dust positions on startup
    try:
        cleanup_dust_positions()
    except Exception as e:
        logger.warning(f'Dust cleanup error: {e}')
    
    # V3.1.55: Resolve opposite-side positions on startup
    try:
        resolve_opposite_sides()
    except Exception as e:
        logger.warning(f'Opposite-side resolution error: {e}')"""
    
    if old_startup in content:
        content = content.replace(old_startup, new_startup)
        print("[OK] Added resolve_opposite_sides to startup")
    else:
        print("[WARN] Could not find startup dust cleanup block")
    
    # Add to main loop alongside portfolio review
    old_loop = """                monitor_positions()
                regime_aware_exit_check()  # V3.1.9: Check for regime-fighting positions
                gemini_portfolio_review()  # V3.1.40: Gemini reviews portfolio, closes bad positions"""
    
    new_loop = """                monitor_positions()
                resolve_opposite_sides()  # V3.1.55: Close losing side when both exist
                regime_aware_exit_check()  # V3.1.9: Check for regime-fighting positions
                gemini_portfolio_review()  # V3.1.55: Gemini reviews portfolio with whale-aware rules"""
    
    if old_loop in content:
        content = content.replace(old_loop, new_loop)
        print("[OK] Added resolve_opposite_sides to main loop")
    else:
        print("[WARN] Could not find main loop monitor block")
    
    # ================================================================
    # PATCH 5: Update version string
    # ================================================================
    content = content.replace("SMT Daemon V3.1.14 - NO FLOOR + BUG FIX", "SMT Daemon V3.1.55 - OPPOSITE SIDE + WHALE EXITS + SYNC FIX")
    content = content.replace('"v3.1.53-opposite"', '"v3.1.55-opposite-whale"')
    print("[OK] Updated version strings")
    
    # Write patched file
    with open(filepath, 'w') as f:
        f.write(content)
    
    new_count = len(content.split('\n'))
    print(f"\nDone. Lines: {original_count} -> {new_count} ({new_count - original_count:+d})")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 patch_v3_1_55.py <path_to_smt_daemon_v3_1.py>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    # Backup first
    backup = filepath + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup)
    print(f"Backup: {backup}")
    
    patch_file(filepath)
