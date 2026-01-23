#!/usr/bin/env python3
"""
SMT Log Analyzer - Check available data for RL training
Run on VM: python3 check_rl_data.py
"""

import os
import json
import glob
from datetime import datetime
from collections import defaultdict

def check_trade_state():
    """Check the trade state JSON file"""
    print("=" * 60)
    print("1. TRADE STATE FILE (trade_state_v3_1_4.json)")
    print("=" * 60)
    
    state_files = glob.glob("trade_state*.json")
    
    if not state_files:
        print("  [NOT FOUND] No trade state files")
        return
    
    for f in state_files:
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            print(f"\n  File: {f}")
            print(f"  Active trades: {len(data.get('active_trades', {}))}")
            print(f"  Closed trades: {len(data.get('closed_trades', []))}")
            print(f"  Cooldowns: {len(data.get('cooldowns', {}))}")
            
            # Show closed trades sample
            closed = data.get('closed_trades', [])
            if closed:
                print(f"\n  Sample closed trade:")
                print(f"    {json.dumps(closed[-1], indent=4)[:500]}")
                
        except Exception as e:
            print(f"  [ERROR] {f}: {e}")


def check_ai_logs():
    """Check the AI log directory"""
    print("\n" + "=" * 60)
    print("2. AI LOGS DIRECTORY (ai_logs/)")
    print("=" * 60)
    
    if not os.path.exists("ai_logs"):
        print("  [NOT FOUND] ai_logs/ directory doesn't exist")
        return
    
    log_files = glob.glob("ai_logs/*.json")
    
    if not log_files:
        print("  [EMPTY] No JSON files in ai_logs/")
        return
    
    print(f"  Found {len(log_files)} log files")
    
    total_entries = 0
    for f in sorted(log_files)[-5:]:  # Show last 5
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            entries = len(data) if isinstance(data, list) else 1
            total_entries += entries
            print(f"    {os.path.basename(f)}: {entries} entries")
        except Exception as e:
            print(f"    {os.path.basename(f)}: ERROR - {e}")
    
    print(f"\n  Total entries (sampled): {total_entries}")


def check_daemon_logs():
    """Check the daemon log files"""
    print("\n" + "=" * 60)
    print("3. DAEMON LOGS (logs/)")
    print("=" * 60)
    
    if not os.path.exists("logs"):
        print("  [NOT FOUND] logs/ directory doesn't exist")
        return
    
    log_files = glob.glob("logs/daemon_*.log")
    json_files = glob.glob("logs/v3_*.json")
    
    print(f"  Daemon .log files: {len(log_files)}")
    print(f"  Analysis .json files: {len(json_files)}")
    
    if json_files:
        print(f"\n  Sample JSON analysis files:")
        for f in sorted(json_files)[-3:]:
            try:
                with open(f, 'r') as file:
                    data = json.load(file)
                print(f"    {os.path.basename(f)}")
                if isinstance(data, dict):
                    print(f"      Keys: {list(data.keys())[:5]}")
            except Exception as e:
                print(f"    {os.path.basename(f)}: ERROR")


def check_json_structure():
    """Analyze what data we have for RL"""
    print("\n" + "=" * 60)
    print("4. RL DATA AVAILABILITY ANALYSIS")
    print("=" * 60)
    
    # Check trade state for RL-usable data
    state_files = glob.glob("trade_state*.json")
    
    rl_ready_trades = 0
    missing_fields = defaultdict(int)
    
    required_fields = [
        "symbol", "side", "entry_price", "opened_at", 
        "confidence", "tier"
    ]
    
    nice_to_have = [
        "closed_at", "exit_price", "pnl", "reason",
        "whale_signal", "sentiment_signal", "flow_signal", "technical_signal"
    ]
    
    for f in state_files:
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            closed = data.get('closed_trades', [])
            
            for trade in closed:
                has_required = all(trade.get(field) for field in required_fields)
                if has_required:
                    rl_ready_trades += 1
                else:
                    for field in required_fields:
                        if not trade.get(field):
                            missing_fields[field] += 1
                
        except:
            pass
    
    print(f"\n  Trades with required fields: {rl_ready_trades}")
    
    if missing_fields:
        print(f"\n  Missing fields count:")
        for field, count in sorted(missing_fields.items(), key=lambda x: -x[1]):
            print(f"    {field}: missing in {count} trades")
    
    # Check JSON analysis logs for persona data
    json_files = glob.glob("logs/v3_*.json")
    
    persona_data_count = 0
    for f in json_files[-20:]:  # Sample last 20
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            # Look for persona votes
            if isinstance(data, dict):
                for pair, analysis in data.items():
                    if isinstance(analysis, dict) and 'decision' in analysis:
                        persona_data_count += 1
        except:
            pass
    
    print(f"\n  Analysis logs with persona data (sampled): {persona_data_count}")


def estimate_rl_readiness():
    """Estimate how much data we have for RL training"""
    print("\n" + "=" * 60)
    print("5. RL TRAINING READINESS ESTIMATE")
    print("=" * 60)
    
    # Count closed trades
    total_closed = 0
    for f in glob.glob("trade_state*.json"):
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            total_closed += len(data.get('closed_trades', []))
        except:
            pass
    
    # Count analysis cycles
    analysis_files = len(glob.glob("logs/v3_*.json"))
    
    print(f"\n  Closed trades: {total_closed}")
    print(f"  Analysis cycles logged: {analysis_files}")
    
    print(f"\n  RL Training Requirements:")
    print(f"    Minimum trades needed: 1,000")
    print(f"    Current trades: {total_closed}")
    print(f"    Gap: {max(0, 1000 - total_closed)} more trades needed")
    
    if total_closed >= 100:
        print(f"\n  [OK] Enough for initial experimentation!")
    elif total_closed >= 50:
        print(f"\n  [PARTIAL] Can start with limited training")
    else:
        print(f"\n  [INSUFFICIENT] Need more trading history")
    
    print(f"\n  Data we ARE logging (good for RL):")
    print(f"    - Trade entries with confidence scores")
    print(f"    - Trade exits with PnL")
    print(f"    - Market regime at decision time")
    print(f"    - Persona votes (if in analysis JSON)")
    
    print(f"\n  Data we're MISSING (need to add):")
    print(f"    - Full state vector at each decision")
    print(f"    - Persona scores as numbers (-1 to +1)")
    print(f"    - Reward calculation (risk-adjusted)")
    print(f"    - next_state after action")


def main():
    print("\n" + "=" * 60)
    print("SMT LOG ANALYZER FOR RL TRAINING")
    print("=" * 60)
    print(f"Run time: {datetime.now()}")
    print(f"Working dir: {os.getcwd()}")
    
    check_trade_state()
    check_ai_logs()
    check_daemon_logs()
    check_json_structure()
    estimate_rl_readiness()
    
    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print("""
1. If trade_state has closed_trades:
   - Extract (state, action, reward, next_state) tuples
   - Convert to RL training format

2. If analysis JSON has persona votes:
   - Extract numeric signals for state vector
   - Link to corresponding trade outcomes

3. Missing data? Add to daemon:
   - Save full state vector before each decision
   - Save outcome (reward) after position closes
    """)


if __name__ == "__main__":
    main()
