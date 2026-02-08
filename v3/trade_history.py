#!/usr/bin/env python3
"""
Display trade history from daemon logs and tracker state
"""
import json
import re
from datetime import datetime

print("="*80)
print("SMT TRADE HISTORY")
print("="*80)

# Read daemon log
with open('daemon.log', 'r') as f:
    log_lines = f.readlines()

# Parse trades
opened_trades = []
closed_trades = []

for line in log_lines:
    # Opened trades
    if "EXECUTING" in line and ("LONG" in line or "SHORT" in line):
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*EXECUTING (\w+) (LONG|SHORT).*- (\d+)%', line)
        if match:
            opened_trades.append({
                'time': match.group(1),
                'pair': match.group(2),
                'side': match.group(3),
                'confidence': match.group(4) + '%'
            })
    
    # Closed trades
    if "CLOSED via TP/SL" in line:
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*(\w+) CLOSED via TP/SL', line)
        if match:
            time = match.group(1)
            pair = match.group(2).replace('cmt_', '').replace('usdt', '').upper()
            
            # Look for PnL in next few lines
            idx = log_lines.index(line)
            pnl = "N/A"
            reason = "TP/SL"
            for i in range(idx, min(idx+5, len(log_lines))):
                if "PnL:" in log_lines[i]:
                    pnl_match = re.search(r'PnL: \$([+-]?\d+\.?\d*) \(([+-]?\d+\.?\d*)%\)', log_lines[i])
                    if pnl_match:
                        pnl = f"${pnl_match.group(1)} ({pnl_match.group(2)}%)"
                        break
            
            closed_trades.append({
                'time': time,
                'pair': pair,
                'pnl': pnl,
                'reason': reason
            })

# Display opened trades
print(f"\n📈 TRADES OPENED: {len(opened_trades)}")
print("-"*80)
if opened_trades:
    print(f"{'Time':<20} {'Pair':<8} {'Side':<6} {'Confidence':<12}")
    print("-"*80)
    for trade in opened_trades:
        print(f"{trade['time']:<20} {trade['pair']:<8} {trade['side']:<6} {trade['confidence']:<12}")
else:
    print("No trades opened")

# Display closed trades
print(f"\n📉 TRADES CLOSED: {len(closed_trades)}")
print("-"*80)
if closed_trades:
    print(f"{'Time':<20} {'Pair':<8} {'P&L':<20} {'Reason':<15}")
    print("-"*80)
    for trade in closed_trades:
        print(f"{trade['time']:<20} {trade['pair']:<8} {trade['pnl']:<20} {trade['reason']:<15}")
else:
    print("No trades closed")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Total Opened: {len(opened_trades)}")
print(f"Total Closed: {len(closed_trades)}")
print(f"Currently Open: {len(opened_trades) - len(closed_trades)}")

# Calculate total PnL from closed trades
total_pnl = 0
for trade in closed_trades:
    if trade['pnl'] != "N/A":
        pnl_match = re.search(r'\$([+-]?\d+\.?\d*)', trade['pnl'])
        if pnl_match:
            total_pnl += float(pnl_match.group(1))

print(f"Total Realized P&L: ${total_pnl:.2f}")
print("="*80)
