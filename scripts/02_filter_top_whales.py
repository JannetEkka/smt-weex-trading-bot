"""
Filter top N whales per category by ETH balance
Input: whale_balances_*.csv (from 01_fetch_balances.py)
Output: top_whales_filtered.csv
"""

import pandas as pd
import glob
import os

# Config
TOP_N_PER_CATEGORY = 200
MIN_BALANCE_ETH = 10

def main():
    # Find latest balance file
    balance_files = glob.glob("whale_balances_*.csv")
    if not balance_files:
        balance_files = glob.glob("data/whale_balances_*.csv")
    
    if not balance_files:
        print("Error: No whale_balances_*.csv file found")
        return
    
    latest_file = max(balance_files)
    print(f"Loading {latest_file}")
    
    df = pd.read_csv(latest_file)
    print(f"Total addresses: {len(df)}")
    
    # Filter minimum balance
    df = df[df['balance_eth'] >= MIN_BALANCE_ETH]
    print(f"After min balance filter ({MIN_BALANCE_ETH} ETH): {len(df)}")
    
    # Get top N per category
    if 'category' in df.columns:
        filtered = df.groupby('category').apply(
            lambda x: x.nlargest(TOP_N_PER_CATEGORY, 'balance_eth')
        ).reset_index(drop=True)
    else:
        filtered = df.nlargest(TOP_N_PER_CATEGORY * 6, 'balance_eth')
    
    print(f"After top {TOP_N_PER_CATEGORY} per category: {len(filtered)}")
    
    # Save
    output_file = "data/top_whales_filtered.csv"
    os.makedirs("data", exist_ok=True)
    filtered.to_csv(output_file, index=False)
    print(f"Saved to {output_file}")
    
    # Summary
    if 'category' in filtered.columns:
        print("\n=== Filtered Whales by Category ===")
        for cat in filtered['category'].unique():
            cat_df = filtered[filtered['category'] == cat]
            print(f"{cat}: {len(cat_df)} whales, Total: {cat_df['balance_eth'].sum():,.0f} ETH")

if __name__ == "__main__":
    main()
