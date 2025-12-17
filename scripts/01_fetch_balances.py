"""
Fetch ETH balances for all labeled whale addresses using Etherscan V2 API
Outputs: addresses with balances, sorted by balance descending per category
"""

import pandas as pd
import requests
import time
import os
from typing import List, Dict
from datetime import datetime

# Config
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://api.etherscan.io/v2/api"
BATCH_SIZE = 20  # Max addresses per balancemulti call
RATE_LIMIT_DELAY = 0.25  # 250ms between calls (safe for free tier)

def fetch_balances_batch(addresses: List[str]) -> Dict[str, float]:
    """Fetch balances for up to 20 addresses in one call"""
    params = {
        'chainid': '1',
        'module': 'account',
        'action': 'balancemulti',
        'address': ','.join(addresses),
        'tag': 'latest',
        'apikey': ETHERSCAN_API_KEY
    }
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        data = response.json()
        
        if data.get('status') == '1':
            results = {}
            for item in data.get('result', []):
                addr = item['account'].lower()
                balance_wei = int(item['balance'])
                balance_eth = balance_wei / 1e18
                results[addr] = balance_eth
            return results
        else:
            print(f"API Error: {data.get('message', 'Unknown error')}")
            return {}
    except Exception as e:
        print(f"Request failed: {e}")
        return {}

def main():
    # Load labeled addresses from Excel
    input_file = "etherscan_all_labels_20251211.xlsx"
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found")
        return
    
    df = pd.read_excel(input_file)
    print(f"Loaded {len(df)} addresses from {input_file}")
    print(f"Columns: {list(df.columns)}")
    print(f"Categories: {df['category'].unique() if 'category' in df.columns else 'N/A'}")
    
    # Get all unique addresses
    address_col = 'address' if 'address' in df.columns else df.columns[0]
    all_addresses = df[address_col].str.lower().unique().tolist()
    print(f"\nTotal unique addresses: {len(all_addresses)}")
    
    # Fetch balances in batches
    all_balances = {}
    total_batches = (len(all_addresses) + BATCH_SIZE - 1) // BATCH_SIZE
    
    print(f"\nFetching balances in {total_batches} batches...")
    
    for i in range(0, len(all_addresses), BATCH_SIZE):
        batch = all_addresses[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        
        print(f"Batch {batch_num}/{total_batches} ({len(batch)} addresses)...", end=" ")
        
        balances = fetch_balances_batch(batch)
        all_balances.update(balances)
        
        print(f"Got {len(balances)} balances")
        time.sleep(RATE_LIMIT_DELAY)
    
    # Add balances to dataframe
    df['balance_eth'] = df[address_col].str.lower().map(all_balances)
    df['balance_eth'] = df['balance_eth'].fillna(0)
    
    # Sort by balance within each category
    if 'category' in df.columns:
        df = df.sort_values(['category', 'balance_eth'], ascending=[True, False])
    else:
        df = df.sort_values('balance_eth', ascending=False)
    
    # Save results
    output_file = f"whale_balances_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(output_file, index=False)
    print(f"\nSaved to {output_file}")
    
    # Print summary per category
    if 'category' in df.columns:
        print("\n=== Balance Summary by Category ===")
        for cat in df['category'].unique():
            cat_df = df[df['category'] == cat]
            total = cat_df['balance_eth'].sum()
            count = len(cat_df)
            non_zero = len(cat_df[cat_df['balance_eth'] > 0])
            top_balance = cat_df['balance_eth'].max()
            print(f"{cat}: {count} addresses, {non_zero} with balance, Top: {top_balance:,.2f} ETH, Total: {total:,.2f} ETH")
    
    # Print top 10 overall
    print("\n=== Top 10 Whales by Balance ===")
    top10 = df.nlargest(10, 'balance_eth')
    for _, row in top10.iterrows():
        cat = row.get('category', 'N/A')
        print(f"{row[address_col][:10]}... | {row['balance_eth']:,.2f} ETH | {cat}")

if __name__ == "__main__":
    main()
