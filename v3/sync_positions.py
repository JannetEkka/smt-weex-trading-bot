#!/usr/bin/env python3
"""Sync trade_state with actual WEEX positions"""

import json
from datetime import datetime, timezone
from smt_nightly_trade_v3_1 import get_open_positions, get_tier_for_symbol, get_tier_config

STATE_FILE = "trade_state_v3_1_4.json"

def sync():
    # Load current state
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
    except:
        state = {"active": {}, "closed": [], "cooldowns": {}}
    
    # Get actual WEEX positions
    positions = get_open_positions()
    
    print(f"=== WEEX Positions: {len(positions)} ===")
    for p in positions:
        symbol = p['symbol']
        print(f"  {symbol}: {p['side']} @ {p['entry_price']:.4f}, PnL: ${p['unrealized_pnl']:.2f}")
    
    print(f"\n=== TradeTracker Active: {len(state['active'])} ===")
    for symbol, trade in state['active'].items():
        print(f"  {symbol}: {trade.get('side')} opened {trade.get('opened_at')}")
    
    # Find mismatches
    weex_symbols = {p['symbol'] for p in positions}
    tracker_symbols = set(state['active'].keys())
    
    missing_in_tracker = weex_symbols - tracker_symbols
    orphan_in_tracker = tracker_symbols - weex_symbols
    
    if missing_in_tracker:
        print(f"\n!!! MISSING in tracker (won't be monitored!): {missing_in_tracker}")
        print("Adding them now...")
        
        for p in positions:
            if p['symbol'] in missing_in_tracker:
                tier = get_tier_for_symbol(p['symbol'])
                tier_config = get_tier_config(tier)
                state['active'][p['symbol']] = {
                    "opened_at": datetime.now(timezone.utc).isoformat(),  # Approximate
                    "side": p['side'],
                    "entry_price": p['entry_price'],
                    "tier": tier,
                    "max_hold_hours": tier_config['max_hold_hours'],
                    "synced": True,  # Flag that this was auto-synced
                }
                print(f"  Added {p['symbol']} (Tier {tier})")
    
    if orphan_in_tracker:
        print(f"\n!!! ORPHAN in tracker (position closed but tracker didn't know): {orphan_in_tracker}")
        print("Removing them...")
        for symbol in orphan_in_tracker:
            del state['active'][symbol]
            print(f"  Removed {symbol}")
    
    # Save updated state
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)
    
    print(f"\nState synced! {len(state['active'])} active trades.")

if __name__ == "__main__":
    sync()
