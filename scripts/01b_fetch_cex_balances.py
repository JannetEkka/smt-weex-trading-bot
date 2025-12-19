"""
Fetch balances for CEX wallet addresses and merge with main whale data
Input: cex_wallet_labels.xlsx
Output: Updates whale_balances CSV with new CEX data
"""

import pandas as pd
import requests
import time
import os
import re
import glob
from typing import List, Dict
from datetime import datetime

# Config
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://api.etherscan.io/v2/api"
BATCH_SIZE = 20
RATE_LIMIT_DELAY = 0.25

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
            print(f"API Error: {data.get('message', 'Unknown error')}")
            return {}
    except Exception as e:
        print(f"Request failed: {e}")
        return {}

def main():
    # Load new CEX wallet file
    cex_file = "data/cex_wallet_labels.xlsx"
    if not os.path.exists(cex_file):
        print(f"Error: {cex_file} not found")
        return
    
    xl = pd.ExcelFile(cex_file)
    df_cex = pd.read_excel(xl, sheet_name='Sheet1')
    print(f"CEX columns: {df_cex.columns.tolist()}")
    
    # Extract all addresses with their sub-labels
    cex_records = []
    for col in df_cex.columns:
        addresses_in_col = df_cex[col].dropna().tolist()
        for addr in addresses_in_col:
            clean = clean_address(addr)
            if clean:
                cex_records.append({
                    'address': clean,
                    'category': 'CEX_Wallet',
                    'sub_label': col
                })
    
    # Dedupe
    cex_df = pd.DataFrame(cex_records)
    cex_df = cex_df.drop_duplicates(subset=['address'], keep='first')
    print(f"Total unique CEX addresses: {len(cex_df)}")
    
    # Fetch balances
    all_addresses = cex_df['address'].unique().tolist()
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
    
    # Add balances
    cex_df['balance_eth'] = cex_df['address'].map(all_balances)
    cex_df['balance_eth'] = cex_df['balance_eth'].fillna(0)
    cex_df = cex_df.sort_values('balance_eth', ascending=False)
    
    # Print CEX summary
    print(f"\n=== CEX Wallet Summary ===")
    print(f"Total: {len(cex_df)} addresses")
    print(f"With balance > 0: {len(cex_df[cex_df['balance_eth'] > 0])}")
    print(f"With balance > 100 ETH: {len(cex_df[cex_df['balance_eth'] > 100])}")
    print(f"With balance > 1000 ETH: {len(cex_df[cex_df['balance_eth'] > 1000])}")
    print(f"Top balance: {cex_df['balance_eth'].max():,.2f} ETH")
    print(f"Total ETH: {cex_df['balance_eth'].sum():,.2f} ETH")
    
    # Top 10 CEX wallets
    print(f"\n=== Top 10 CEX Wallets ===")
    for _, row in cex_df.head(10).iterrows():
        print(f"{row['address'][:10]}... | {row['balance_eth']:,.2f} ETH | {row['sub_label']}")
    
    # Load existing whale balances and replace CEX_Wallet entries
    balance_files = glob.glob("data/whale_balances_*.csv")
    if balance_files:
        latest_file = max(balance_files)
        print(f"\nLoading existing data from {latest_file}")
        main_df = pd.read_csv(latest_file)
        
        # Remove old CEX_Wallet entries
        main_df = main_df[main_df['category'] != 'CEX_Wallet']
        print(f"After removing old CEX_Wallet: {len(main_df)} rows")
        
        # Append new CEX data
        combined_df = pd.concat([main_df, cex_df], ignore_index=True)
        combined_df = combined_df.sort_values(['category', 'balance_eth'], ascending=[True, False])
        
        # Save combined
        output_file = f"data/whale_balances_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        combined_df.to_csv(output_file, index=False)
        print(f"\nSaved combined data to {output_file}")
        print(f"Total rows: {len(combined_df)}")
        
        # Final summary
        print("\n=== Final Balance Summary by Category ===")
        for cat in ['Miner', 'Exploiter', 'DeFi_Trader', 'CEX_Wallet', 'Staker', 'Institutional']:
            cat_df = combined_df[combined_df['category'] == cat]
            if len(cat_df) == 0:
                continue
            total = cat_df['balance_eth'].sum()
            count = len(cat_df)
            non_zero = len(cat_df[cat_df['balance_eth'] > 0])
            top_balance = cat_df['balance_eth'].max()
            print(f"{cat}: {count} addresses, {non_zero} with balance, Top: {top_balance:,.2f} ETH, Total: {total:,.2f} ETH")
    else:
        # Just save CEX data
        output_file = f"data/cex_balances_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        cex_df.to_csv(output_file, index=False)
        print(f"\nSaved CEX data to {output_file}")

if __name__ == "__main__":
    main()