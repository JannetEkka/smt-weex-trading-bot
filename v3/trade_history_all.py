#!/usr/bin/env python3
import glob
import re

print("="*80)
print("SMT TRADE HISTORY (ALL LOGS)")
print("="*80)

# Read all daemon logs
log_files = glob.glob('daemon*.log') + glob.glob('logs/daemon*.log')
all_lines = []

for log_file in sorted(log_files):
    try:
        with open(log_file, 'r') as f:
            all_lines.extend(f.readlines())
    except:
        pass

opened_trades = []
closed_trades = []

for line in all_lines:
    if "EXECUTING" in line and ("LONG" in line or "SHORT" in line):
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*EXECUTING (\w+) (LONG|SHORT).*- (\d+)%', line)
        if match:
            opened_trades.append({
                'time': match.group(1),
                'pair': match.group(2),
                'side': match.group(3),
                'confidence': match.group(4) + '%'
            })
    
    if "CLOSED via TP/SL" in line:
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*(\w+) CLOSED', line)
        if match:
            time = match.group(1)
            pair = match.group(2).replace('cmt_', '').replace('usdt', '').upper()
            
            idx = all_lines.index(line)
            pnl = "N/A"
            for i in range(idx, min(idx+5, len(all_lines))):
                if "PnL:" in all_lines[i]:
                    pnl_match = re.search(r'PnL: \$([+-]?\d+\.?\d*) \(([+-]?\d+\.?\d*)%\)', all_lines[i])
                    if pnl_match:
                        pnl = f"${pnl_match.group(1)} ({pnl_match.group(2)}%)"
                        break
            
            closed_trades.append({'time': time, 'pair': pair, 'pnl': pnl})

print(f"\n📈 TRADES OPENED: {len(opened_trades)}")
print("-"*80)
for trade in opened_trades[-20:]:  # Last 20
    print(f"{trade['time']} | {trade['pair']:<6} {trade['side']:<6} | Confidence: {trade['confidence']}")

print(f"\n📉 TRADES CLOSED: {len(closed_trades)}")
print("-"*80)
for trade in closed_trades[-20:]:  # Last 20
    print(f"{trade['time']} | {trade['pair']:<6} | P&L: {trade['pnl']}")

total_pnl = sum(float(re.search(r'\$([+-]?\d+\.?\d*)', t['pnl']).group(1)) 
                for t in closed_trades if t['pnl'] != "N/A" and re.search(r'\$([+-]?\d+\.?\d*)', t['pnl']))
print(f"\nTotal Realized P&L: ${total_pnl:.2f}")
print("="*80)
