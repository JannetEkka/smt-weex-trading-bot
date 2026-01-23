#!/usr/bin/env python3
"""
Inspect the analysis JSON files to see what data we actually have
"""

import os
import json
import glob

def main():
    # Find analysis files
    json_files = sorted(glob.glob("logs/v3_*.json"))
    
    if not json_files:
        json_files = sorted(glob.glob("ai_logs/v3_*.json"))
    
    if not json_files:
        print("No analysis JSON files found!")
        return
    
    print(f"Found {len(json_files)} analysis files")
    print("=" * 60)
    
    # Show structure of most recent file
    latest = json_files[-1]
    print(f"\nLatest file: {latest}")
    print("-" * 60)
    
    with open(latest, 'r') as f:
        data = json.load(f)
    
    print(json.dumps(data, indent=2)[:3000])
    
    print("\n" + "=" * 60)
    print("CHECKING FOR PERSONA VOTE DATA")
    print("=" * 60)
    
    # Sample 5 files to check structure
    sample_files = json_files[-5:]
    
    has_personas = 0
    has_trades = 0
    has_confidence = 0
    
    for f in sample_files:
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            if 'personas' in data:
                has_personas += 1
            if 'trades' in data:
                has_trades += 1
                # Check if trades have confidence
                trades = data.get('trades', {})
                if isinstance(trades, dict):
                    for pair, trade_data in trades.items():
                        if isinstance(trade_data, dict) and 'confidence' in trade_data:
                            has_confidence += 1
                            break
        except Exception as e:
            print(f"Error reading {f}: {e}")
    
    print(f"\nFiles with 'personas' key: {has_personas}/{len(sample_files)}")
    print(f"Files with 'trades' key: {has_trades}/{len(sample_files)}")
    print(f"Files with confidence scores: {has_confidence}/{len(sample_files)}")
    
    # Try to extract actual persona structure
    print("\n" + "=" * 60)
    print("PERSONA DATA STRUCTURE (if exists)")
    print("=" * 60)
    
    for f in json_files[-3:]:
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            if 'personas' in data:
                print(f"\n{os.path.basename(f)}:")
                personas = data['personas']
                if isinstance(personas, dict):
                    for name, value in list(personas.items())[:2]:
                        print(f"  {name}: {str(value)[:200]}")
        except:
            pass
    
    # Check trades structure
    print("\n" + "=" * 60)
    print("TRADES DATA STRUCTURE (if exists)")
    print("=" * 60)
    
    for f in json_files[-3:]:
        try:
            with open(f, 'r') as file:
                data = json.load(file)
            
            if 'trades' in data:
                print(f"\n{os.path.basename(f)}:")
                trades = data['trades']
                if isinstance(trades, dict):
                    for pair, trade_data in list(trades.items())[:2]:
                        print(f"  {pair}: {str(trade_data)[:300]}")
                elif isinstance(trades, list):
                    for t in trades[:2]:
                        print(f"  {str(t)[:300]}")
        except:
            pass


if __name__ == "__main__":
    main()
