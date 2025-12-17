"""
Fetch transactions for filtered whale addresses
Input: data/top_whales_filtered.csv
Output: data/whale_transactions.csv
"""

import pandas as pd
import requests
import time
import os
from datetime import datetime

# Config
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://api.etherscan.io/v2/api"
RATE_LIMIT_DELAY = 0.25
LOOKBACK_DAYS = 90

def fetch_transactions(address: str, tx_type: str = "txlist") -> list:
    """Fetch transactions for an address. tx_type: txlist, txlistinternal, tokentx"""
    params = {
        'chainid': '1',
        'module': 'account',
        'action': tx_type,
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'page': 1,
        'offset': 1000,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        data = response.json()
        
        if data.get('status') == '1':
            return data.get('result', [])
        return []
    except Exception as e:
        print(f"Error fetching {tx_type} for {address}: {e}")
        return []

def main():
    input_file = "data/top_whales_filtered.csv"
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found. Run 02_filter_top_whales.py first.")
        return
    
    df = pd.read_csv(input_file)
    addresses = df['address'].str.lower().unique().tolist()
    print(f"Fetching transactions for {len(addresses)} whales...")
    
    # Calculate cutoff timestamp
    cutoff_ts = int((datetime.now().timestamp()) - (LOOKBACK_DAYS * 86400))
    
    all_txs = []
    
    for i, addr in enumerate(addresses):
        print(f"[{i+1}/{len(addresses)}] {addr[:10]}...", end=" ")
        
        # Fetch normal txs
        txs = fetch_transactions(addr, "txlist")
        time.sleep(RATE_LIMIT_DELAY)
        
        # Fetch token txs
        token_txs = fetch_transactions(addr, "tokentx")
        time.sleep(RATE_LIMIT_DELAY)
        
        # Filter by lookback period and add wallet address
        for tx in txs + token_txs:
            if int(tx.get('timeStamp', 0)) >= cutoff_ts:
                tx['wallet_address'] = addr
                all_txs.append(tx)
        
        print(f"{len(txs)} normal, {len(token_txs)} token txs")
    
    # Save
    if all_txs:
        tx_df = pd.DataFrame(all_txs)
        output_file = f"data/whale_transactions_{datetime.now().strftime('%Y%m%d')}.csv"
        tx_df.to_csv(output_file, index=False)
        print(f"\nSaved {len(tx_df)} transactions to {output_file}")
    else:
        print("No transactions found")

if __name__ == "__main__":
    main()
