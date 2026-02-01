#!/usr/bin/env python3
"""
RL Outcome Filler V2
Matches closed trades to RL decision entries and fills outcomes.
Run periodically or after trades close.
"""

import json
import os
import glob
from datetime import datetime, timezone, timedelta

RL_DATA_DIR = "rl_training_data"
TRADE_STATE_FILE = "trade_state_v3_1_7.json"

# Match window: RL decision within X minutes BEFORE trade opened
MATCH_WINDOW_MINUTES = 10


def parse_ts(ts_str):
    """Parse ISO timestamp to datetime."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace('Z', '+00:00')
        return datetime.fromisoformat(ts_str)
    except:
        return None


def symbol_from_key(key):
    """Extract symbol from trade_state key (e.g., cmt_btcusdt)."""
    return key.lower()


def load_rl_entries():
    """Load all RL entries as dict keyed by id."""
    entries = {}
    for fpath in glob.glob(f"{RL_DATA_DIR}/exp_*.jsonl"):
        with open(fpath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries[entry['id']] = {
                        'entry': entry,
                        'file': fpath
                    }
                except:
                    continue
    return entries


def find_matching_rl_entry(symbol, side, trade_opened_ts, rl_entries):
    """
    Find RL entry that matches this trade.
    Match criteria:
    - Same symbol
    - Same action (LONG/SHORT)
    - RL decision timestamp within MATCH_WINDOW_MINUTES before trade opened
    """
    best_match = None
    best_diff = timedelta(minutes=MATCH_WINDOW_MINUTES + 1)
    
    for entry_id, data in rl_entries.items():
        entry = data['entry']
        
        # Check symbol
        if entry.get('symbol') != symbol:
            continue
        
        # Check action matches side
        if entry.get('action') != side:
            continue
        
        # Check timing - RL decision should be BEFORE trade, within window
        rl_ts = parse_ts(entry.get('ts'))
        if not rl_ts:
            continue
        
        diff = trade_opened_ts - rl_ts
        
        # RL decision should be 0-10 minutes before trade opened
        if timedelta(0) <= diff <= timedelta(minutes=MATCH_WINDOW_MINUTES):
            if diff < best_diff:
                best_diff = diff
                best_match = data
    
    return best_match


def calculate_outcome(trade):
    """Calculate outcome metrics from closed trade."""
    close_data = trade.get('close_data', {})
    position_usdt = trade.get('position_usdt', 0)
    pnl_usd = close_data.get('pnl', 0)
    
    # Calculate PnL %
    if position_usdt > 0 and pnl_usd:
        pnl_pct = (pnl_usd / position_usdt) * 100
    else:
        pnl_pct = 0
    
    # Calculate hold time
    opened = parse_ts(trade.get('opened_at'))
    closed = parse_ts(trade.get('closed_at'))
    hold_hours = (closed - opened).total_seconds() / 3600 if opened and closed else 0
    
    reason = close_data.get('reason', 'unknown')
    
    return {
        "traded": True,
        "pnl_usd": round(pnl_usd, 2) if pnl_usd else 0,
        "pnl_pct": round(pnl_pct, 4),
        "reason": reason,
        "hold_hours": round(hold_hours, 2),
        "peak_pnl_pct": trade.get('peak_pnl_pct', 0),
        "hit_tp": reason == 'tp_sl_hit' and (pnl_usd or 0) > 0,
        "hit_sl": reason == 'tp_sl_hit' and (pnl_usd or 0) < 0,
        "win": (pnl_usd or 0) > 0
    }


def update_rl_file(filepath, entry_id, outcome):
    """Update specific entry in JSONL file with outcome."""
    lines = []
    updated = False
    
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get('id') == entry_id:
                    entry['outcome'] = outcome
                    updated = True
                lines.append(json.dumps(entry))
            except:
                lines.append(line)
    
    if updated:
        with open(filepath, 'w') as f:
            f.write('\n'.join(lines) + '\n')
    
    return updated


def main():
    print("RL Outcome Filler V2")
    print("=" * 40)
    
    # Load trade state
    with open(TRADE_STATE_FILE, 'r') as f:
        trade_state = json.load(f)
    
    closed_trades = trade_state.get('closed', [])
    print(f"Closed trades: {len(closed_trades)}")
    
    # Load RL entries
    rl_entries = load_rl_entries()
    print(f"RL entries: {len(rl_entries)}")
    
    # RL logging started Feb 1, 2026
    rl_start = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    
    # Filter closed trades to those after RL logging started
    recent_closed = []
    for trade in closed_trades:
        closed_ts = parse_ts(trade.get('closed_at'))
        if closed_ts and closed_ts >= rl_start:
            recent_closed.append(trade)
    
    print(f"Closed after RL start: {len(recent_closed)}")
    
    if not recent_closed:
        print("\nNo closed trades since RL logging started.")
        print("Active positions will be matched when they close.")
        
        # Show active positions
        active = trade_state.get('active', {})
        print(f"\nActive positions ({len(active)}):")
        for sym, pos in active.items():
            print(f"  {sym}: {pos.get('side')} @ {pos.get('entry_price')}")
        return
    
    # Match and fill
    matched = 0
    already_filled = 0
    no_match = 0
    
    for trade in recent_closed:
        # We need to figure out the symbol - it's the key in trade_state
        # But closed trades don't have it directly stored
        # Let's check if we stored it anywhere
        
        # Try to match by order_id pattern or other means
        # For now, skip if we can't determine symbol
        # TODO: Store symbol when closing trades
        
        trade_opened = parse_ts(trade.get('opened_at'))
        trade_side = trade.get('side')
        
        if not trade_opened or not trade_side:
            continue
        
        # Try each symbol to find match
        found_match = False
        for entry_id, data in rl_entries.items():
            entry = data['entry']
            
            # Skip if already has outcome
            if entry.get('outcome') is not None:
                continue
            
            # Check action matches
            if entry.get('action') != trade_side:
                continue
            
            # Check timing
            rl_ts = parse_ts(entry.get('ts'))
            if not rl_ts:
                continue
            
            diff = trade_opened - rl_ts
            if timedelta(0) <= diff <= timedelta(minutes=MATCH_WINDOW_MINUTES):
                # Found match!
                outcome = calculate_outcome(trade)
                symbol = entry.get('symbol')
                
                print(f"\nMatch found:")
                print(f"  RL: {entry_id} @ {entry.get('ts')[:19]}")
                print(f"  Trade: {trade_side} opened {trade.get('opened_at')[:19]}")
                print(f"  Outcome: {'WIN' if outcome['win'] else 'LOSS'} {outcome['pnl_usd']:+.2f} USD ({outcome['pnl_pct']:+.2f}%)")
                
                if update_rl_file(data['file'], entry_id, outcome):
                    matched += 1
                    found_match = True
                    break
        
        if not found_match:
            no_match += 1
    
    print(f"\n" + "=" * 40)
    print(f"Results:")
    print(f"  Matched & filled: {matched}")
    print(f"  No RL match: {no_match}")
    
    # Summary of unfilled entries
    unfilled = sum(1 for d in rl_entries.values() if d['entry'].get('outcome') is None)
    print(f"  RL entries still unfilled: {unfilled}")


if __name__ == "__main__":
    main()
