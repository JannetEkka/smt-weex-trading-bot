"""
Extract ML features from whale transactions
Input: data/whale_transactions_*.csv + data/top_whales_filtered.csv
Output: data/whale_features.csv
"""

import pandas as pd
import numpy as np
import glob
from datetime import datetime
import os

def extract_features(tx_df: pd.DataFrame, wallet_address: str) -> dict:
    """Extract features for a single whale wallet"""
    
    wallet_txs = tx_df[tx_df['wallet_address'] == wallet_address].copy()
    
    if len(wallet_txs) == 0:
        return None
    
    # Convert timestamps
    wallet_txs['timestamp'] = pd.to_datetime(wallet_txs['timeStamp'].astype(int), unit='s')
    wallet_txs['value_eth'] = wallet_txs['value'].astype(float) / 1e18
    
    # Basic counts
    total_txs = len(wallet_txs)
    
    # Direction analysis
    outgoing = wallet_txs[wallet_txs['from'].str.lower() == wallet_address]
    incoming = wallet_txs[wallet_txs['to'].str.lower() == wallet_address]
    
    outgoing_count = len(outgoing)
    incoming_count = len(incoming)
    outgoing_volume = outgoing['value_eth'].sum()
    incoming_volume = incoming['value_eth'].sum()
    
    # Time-based features
    if len(wallet_txs) > 1:
        time_diffs = wallet_txs['timestamp'].diff().dropna().dt.total_seconds()
        avg_time_between_tx = time_diffs.mean() / 3600  # hours
        std_time_between_tx = time_diffs.std() / 3600
    else:
        avg_time_between_tx = 0
        std_time_between_tx = 0
    
    # Gas analysis
    if 'gasUsed' in wallet_txs.columns:
        wallet_txs['gasUsed'] = pd.to_numeric(wallet_txs['gasUsed'], errors='coerce').fillna(0)
        avg_gas = wallet_txs['gasUsed'].mean()
    else:
        avg_gas = 0
    
    # Unique counterparties
    all_counterparties = set()
    all_counterparties.update(wallet_txs['from'].str.lower().unique())
    all_counterparties.update(wallet_txs['to'].str.lower().unique())
    all_counterparties.discard(wallet_address)
    unique_counterparties = len(all_counterparties)
    
    # Token diversity (if token txs present)
    if 'tokenSymbol' in wallet_txs.columns:
        unique_tokens = wallet_txs['tokenSymbol'].nunique()
    else:
        unique_tokens = 0
    
    # Net flow
    net_flow = incoming_volume - outgoing_volume
    
    return {
        'address': wallet_address,
        'total_txs': total_txs,
        'outgoing_count': outgoing_count,
        'incoming_count': incoming_count,
        'outgoing_volume_eth': round(outgoing_volume, 4),
        'incoming_volume_eth': round(incoming_volume, 4),
        'net_flow_eth': round(net_flow, 4),
        'avg_time_between_tx_hours': round(avg_time_between_tx, 2),
        'std_time_between_tx_hours': round(std_time_between_tx, 2),
        'avg_gas_used': round(avg_gas, 0),
        'unique_counterparties': unique_counterparties,
        'unique_tokens': unique_tokens,
        'tx_ratio_out_in': round(outgoing_count / max(incoming_count, 1), 4),
        'volume_ratio_out_in': round(outgoing_volume / max(incoming_volume, 0.0001), 4)
    }

def main():
    # Load transactions
    tx_files = glob.glob("data/whale_transactions_*.csv")
    if not tx_files:
        print("Error: No transaction files found. Run 03_fetch_transactions.py first.")
        return
    
    latest_tx_file = max(tx_files)
    print(f"Loading {latest_tx_file}")
    tx_df = pd.read_csv(latest_tx_file, low_memory=False)
    tx_df['wallet_address'] = tx_df['wallet_address'].str.lower()
    
    # Load whale info for labels
    whale_df = pd.read_csv("data/top_whales_filtered.csv")
    whale_df['address'] = whale_df['address'].str.lower()
    
    # Extract features for each whale
    wallets = tx_df['wallet_address'].unique()
    print(f"Extracting features for {len(wallets)} wallets...")
    
    features_list = []
    for i, wallet in enumerate(wallets):
        if i % 50 == 0:
            print(f"Processing {i}/{len(wallets)}...")
        
        features = extract_features(tx_df, wallet)
        if features:
            features_list.append(features)
    
    # Create features dataframe
    features_df = pd.DataFrame(features_list)
    
    # Merge with whale info (category, balance)
    features_df = features_df.merge(
        whale_df[['address', 'category', 'balance_eth']], 
        on='address', 
        how='left'
    )
    
    # Save
    output_file = f"data/whale_features_{datetime.now().strftime('%Y%m%d')}.csv"
    features_df.to_csv(output_file, index=False)
    print(f"\nSaved {len(features_df)} whale features to {output_file}")
    
    # Summary
    print("\n=== Feature Summary ===")
    print(features_df.describe())

if __name__ == "__main__":
    main()
