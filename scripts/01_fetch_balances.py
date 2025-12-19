"""
Fetch ETH balances for all labeled whale addresses using Etherscan V2 API
Outputs: addresses with balances, sorted by balance descending per category
"""

import pandas as pd
import requests
import time
import os
import re
from typing import List, Dict
from datetime import datetime

# Config
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://api.etherscan.io/v2/api"
BATCH_SIZE = 20  # Max addresses per balancemulti call
RATE_LIMIT_DELAY = 0.25  # 250ms between calls (safe for free tier)

# Categories to process (skip All_Labels sheet)
CATEGORIES = ['Miner', 'Exploiter', 'DeFi_Trader', 'CEX_Wallet', 'Staker', 'Institutional']

def clean_address(addr) -> str:
    """Extract clean address from string"""
    if pd.isna(addr):
        return None
    addr_str = str(addr).strip()
    match = re.match(r'(0x[a-fA-F0-9]{40})', addr_str)
    if match:
        return match.group(1).lower()
    return None

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
            print(f"API Error: {data.get('message', 'Unknown error')} - {data.get('result', '')}")
            return {}
    except Exception as e:
        print(f"Request failed: {e}")
        return {}

def main():
    input_file = "data/etherscan_all_labels_20251211.xlsx"
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found")
        return
    
    xl = pd.ExcelFile(input_file)
    print(f"Found sheets: {xl.sheet_names}")
    
    # Collect all addresses with their categories
    all_records = []
    
    for category in CATEGORIES:
        if category not in xl.sheet_names:
            print(f"Warning: Sheet '{category}' not found, skipping")
            continue
        
        df = pd.read_excel(xl, sheet_name=category)
        print(f"\n--- {category} ---")
        print(f"Columns: {df.columns.tolist()}")
        
        # Each column contains addresses for a sub-label
        for col in df.columns:
            addresses_in_col = df[col].dropna().tolist()
            for addr in addresses_in_col:
                clean = clean_address(addr)
                if clean:
                    all_records.append({
                        'address': clean,
                        'category': category,
                        'sub_label': col
                    })
        
        print(f"Extracted addresses from {category}: {len([r for r in all_records if r['category'] == category])}")
    
    # Create dataframe and dedupe
    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=['address'], keep='first')
    print(f"\nTotal unique addresses: {len(df)}")
    
    # Get all unique addresses
    all_addresses = df['address'].unique().tolist()
    
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
    df['balance_eth'] = df['address'].map(all_balances)
    df['balance_eth'] = df['balance_eth'].fillna(0)
    
    # Sort by balance within each category
    df = df.sort_values(['category', 'balance_eth'], ascending=[True, False])
    
    # Save results
    output_file = f"data/whale_balances_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(output_file, index=False)
    print(f"\nSaved to {output_file}")
    
    # Print summary per category
    print("\n=== Balance Summary by Category ===")
    for cat in CATEGORIES:
        cat_df = df[df['category'] == cat]
        if len(cat_df) == 0:
            continue
        total = cat_df['balance_eth'].sum()
        count = len(cat_df)
        non_zero = len(cat_df[cat_df['balance_eth'] > 0])
        top_balance = cat_df['balance_eth'].max()
        print(f"{cat}: {count} addresses, {non_zero} with balance, Top: {top_balance:,.2f} ETH, Total: {total:,.2f} ETH")
    
    # Print top 10 overall
    print("\n=== Top 10 Whales by Balance ===")
    top10 = df.nlargest(10, 'balance_eth')
    for _, row in top10.iterrows():
        print(f"{row['address'][:10]}... | {row['balance_eth']:,.2f} ETH | {row['category']} | {row['sub_label']}")

if __name__ == "__main__":
    main()