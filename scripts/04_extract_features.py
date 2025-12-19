"""
Extract ML features from whale transactions - IMPROVED VERSION
Input: data/whale_transactions_*.csv + data/top_whales_filtered.csv
Output: data/whale_features.csv
"""

import pandas as pd
import numpy as np
import glob
from datetime import datetime
import os

# Known DeFi protocol addresses (lowercase)
DEFI_PROTOCOLS = {
    # Uniswap
    '0x7a250d5630b4cf539739df2c5dacb4c659f2488d': 'uniswap_v2',
    '0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45': 'uniswap_v3',
    '0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b': 'uniswap_universal',
    # Aave
    '0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2': 'aave_v3',
    '0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9': 'aave_v2',
    # Lido
    '0xae7ab96520de3a18e5e111b5eaab095312d7fe84': 'lido_steth',
    '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0': 'lido_wsteth',
    # Curve
    '0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7': 'curve_3pool',
    '0xdc24316b9ae028f1497c275eb9192a3ea0f67022': 'curve_steth',
    # 1inch
    '0x1111111254eeb25477b68fb85ed929f73a960582': '1inch_v5',
    '0x111111125421ca6dc452d289314280a0f8842a65': '1inch_v6',
    # Compound
    '0xc3d688b66703497daa19211eedff47f25384cdc3': 'compound_v3',
    # MakerDAO
    '0x6b175474e89094c44da98b954eedeac495271d0f': 'dai',
    '0x83f20f44975d03b1b09e64809b757c47f942beea': 'sdai',
    # CEX deposit addresses (common)
    '0x28c6c06298d514db089934071355e5743bf21d60': 'binance',
    '0x21a31ee1afc51d94c2efccaa2092ad1028285549': 'binance',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d': 'binance',
    '0x56eddb7aa87536c09ccc2793473599fd21a8b17f': 'binance',
}

# Stablecoin symbols
STABLECOINS = {'USDT', 'USDC', 'DAI', 'BUSD', 'TUSD', 'USDP', 'FRAX', 'LUSD', 'sUSD', 'GUSD'}

def extract_features(tx_df: pd.DataFrame, wallet_address: str) -> dict:
    """Extract comprehensive features for a single whale wallet"""
    
    wallet_txs = tx_df[tx_df['wallet_address'] == wallet_address].copy()
    
    if len(wallet_txs) == 0:
        return None
    
    # Convert timestamps and values
    wallet_txs['timestamp'] = pd.to_datetime(wallet_txs['timeStamp'].astype(int), unit='s')
    wallet_txs['value_eth'] = pd.to_numeric(wallet_txs['value'], errors='coerce').fillna(0) / 1e18
    wallet_txs['hour'] = wallet_txs['timestamp'].dt.hour
    
    # === BASIC COUNTS ===
    total_txs = len(wallet_txs)
    
    # === TX TYPE BREAKDOWN ===
    tx_type_counts = wallet_txs['tx_type'].value_counts().to_dict()
    normal_count = tx_type_counts.get('normal', 0)
    internal_count = tx_type_counts.get('internal', 0)
    erc20_count = tx_type_counts.get('erc20', 0)
    erc721_count = tx_type_counts.get('erc721', 0)
    erc1155_count = tx_type_counts.get('erc1155', 0)
    
    # TX type ratios
    erc20_ratio = erc20_count / max(total_txs, 1)
    nft_ratio = (erc721_count + erc1155_count) / max(total_txs, 1)
    internal_ratio = internal_count / max(total_txs, 1)
    
    # === DIRECTION ANALYSIS ===
    outgoing = wallet_txs[wallet_txs['from'].str.lower() == wallet_address]
    incoming = wallet_txs[wallet_txs['to'].str.lower() == wallet_address]
    
    outgoing_count = len(outgoing)
    incoming_count = len(incoming)
    outgoing_volume = outgoing['value_eth'].sum()
    incoming_volume = incoming['value_eth'].sum()
    net_flow = incoming_volume - outgoing_volume
    
    # === TIME-BASED FEATURES ===
    if len(wallet_txs) > 1:
        wallet_txs_sorted = wallet_txs.sort_values('timestamp')
        time_diffs = wallet_txs_sorted['timestamp'].diff().dropna().dt.total_seconds()
        avg_time_between_tx = time_diffs.mean() / 3600  # hours
        std_time_between_tx = time_diffs.std() / 3600
        
        # Activity span in days
        activity_span_days = (wallet_txs['timestamp'].max() - wallet_txs['timestamp'].min()).days
    else:
        avg_time_between_tx = 0
        std_time_between_tx = 0
        activity_span_days = 0
    
    # Transactions per day (activity intensity)
    tx_per_day = total_txs / max(activity_span_days, 1)
    
    # === HOUR DISTRIBUTION (trading patterns) ===
    hour_counts = wallet_txs['hour'].value_counts()
    
    # Business hours (9-17 UTC) vs off-hours ratio
    business_hours = hour_counts[(hour_counts.index >= 9) & (hour_counts.index <= 17)].sum()
    off_hours = hour_counts[(hour_counts.index < 9) | (hour_counts.index > 17)].sum()
    business_hour_ratio = business_hours / max(business_hours + off_hours, 1)
    
    # Peak hour concentration (how concentrated activity is)
    if len(hour_counts) > 0:
        peak_hour_pct = hour_counts.max() / total_txs
    else:
        peak_hour_pct = 0
    
    # === GAS ANALYSIS ===
    if 'gasUsed' in wallet_txs.columns:
        wallet_txs['gasUsed'] = pd.to_numeric(wallet_txs['gasUsed'], errors='coerce').fillna(0)
        avg_gas = wallet_txs['gasUsed'].mean()
        max_gas = wallet_txs['gasUsed'].max()
    else:
        avg_gas = 0
        max_gas = 0
    
    # === COUNTERPARTY ANALYSIS ===
    all_counterparties = set()
    if 'from' in wallet_txs.columns:
        all_counterparties.update(wallet_txs['from'].str.lower().dropna().unique())
    if 'to' in wallet_txs.columns:
        all_counterparties.update(wallet_txs['to'].str.lower().dropna().unique())
    all_counterparties.discard(wallet_address)
    all_counterparties.discard('')
    unique_counterparties = len(all_counterparties)
    
    # === DEFI PROTOCOL INTERACTIONS ===
    defi_interactions = 0
    defi_protocols_used = set()
    cex_interactions = 0
    
    for cp in all_counterparties:
        if cp in DEFI_PROTOCOLS:
            protocol = DEFI_PROTOCOLS[cp]
            if protocol in ['binance']:
                cex_interactions += 1
            else:
                defi_interactions += 1
                defi_protocols_used.add(protocol)
    
    unique_defi_protocols = len(defi_protocols_used)
    
    # === TOKEN ANALYSIS ===
    if 'tokenSymbol' in wallet_txs.columns:
        tokens = wallet_txs['tokenSymbol'].dropna()
        unique_tokens = tokens.nunique()
        
        # Stablecoin ratio
        stablecoin_txs = tokens[tokens.isin(STABLECOINS)].count()
        stablecoin_ratio = stablecoin_txs / max(len(tokens), 1)
    else:
        unique_tokens = 0
        stablecoin_ratio = 0
    
    # === VALUE DISTRIBUTION ===
    values = wallet_txs['value_eth']
    avg_tx_value = values.mean()
    max_tx_value = values.max()
    std_tx_value = values.std() if len(values) > 1 else 0
    
    # Large transaction ratio (>10 ETH)
    large_tx_count = len(values[values > 10])
    large_tx_ratio = large_tx_count / max(total_txs, 1)
    
    # === RATIOS ===
    tx_ratio_out_in = outgoing_count / max(incoming_count, 1)
    volume_ratio_out_in = outgoing_volume / max(incoming_volume, 0.0001)
    
    return {
        'address': wallet_address,
        
        # Basic counts
        'total_txs': total_txs,
        'outgoing_count': outgoing_count,
        'incoming_count': incoming_count,
        
        # TX type breakdown
        'normal_tx_count': normal_count,
        'internal_tx_count': internal_count,
        'erc20_tx_count': erc20_count,
        'erc721_tx_count': erc721_count,
        'erc1155_tx_count': erc1155_count,
        'erc20_ratio': round(erc20_ratio, 4),
        'nft_ratio': round(nft_ratio, 4),
        'internal_ratio': round(internal_ratio, 4),
        
        # Volume
        'outgoing_volume_eth': round(outgoing_volume, 4),
        'incoming_volume_eth': round(incoming_volume, 4),
        'net_flow_eth': round(net_flow, 4),
        'avg_tx_value_eth': round(avg_tx_value, 4),
        'max_tx_value_eth': round(max_tx_value, 4),
        'std_tx_value_eth': round(std_tx_value, 4),
        'large_tx_ratio': round(large_tx_ratio, 4),
        
        # Time patterns
        'avg_time_between_tx_hours': round(avg_time_between_tx, 2),
        'std_time_between_tx_hours': round(std_time_between_tx, 2),
        'activity_span_days': activity_span_days,
        'tx_per_day': round(tx_per_day, 4),
        'business_hour_ratio': round(business_hour_ratio, 4),
        'peak_hour_pct': round(peak_hour_pct, 4),
        
        # Gas
        'avg_gas_used': round(avg_gas, 0),
        'max_gas_used': round(max_gas, 0),
        
        # Counterparties & DeFi
        'unique_counterparties': unique_counterparties,
        'defi_interactions': defi_interactions,
        'unique_defi_protocols': unique_defi_protocols,
        'cex_interactions': cex_interactions,
        
        # Tokens
        'unique_tokens': unique_tokens,
        'stablecoin_ratio': round(stablecoin_ratio, 4),
        
        # Ratios
        'tx_ratio_out_in': round(tx_ratio_out_in, 4),
        'volume_ratio_out_in': round(volume_ratio_out_in, 4),
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
    
    print(f"Total transactions: {len(tx_df):,}")
    print(f"TX types: {tx_df['tx_type'].value_counts().to_dict()}")
    
    # Load whale info for labels
    whale_df = pd.read_csv("data/top_whales_filtered.csv")
    whale_df['address'] = whale_df['address'].str.lower()
    
    # Extract features for each whale
    wallets = tx_df['wallet_address'].unique()
    print(f"\nExtracting features for {len(wallets)} wallets...")
    
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
        whale_df[['address', 'category', 'sub_label', 'balance_eth']], 
        on='address', 
        how='left'
    )
    
    # Save
    output_file = f"data/whale_features_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    features_df.to_csv(output_file, index=False)
    print(f"\nSaved {len(features_df)} whale features to {output_file}")
    print(f"Total features: {len(features_df.columns)}")
    
    # Summary by category
    print("\n=== Features by Category ===")
    category_stats = features_df.groupby('category').agg({
        'total_txs': 'mean',
        'erc20_ratio': 'mean',
        'defi_interactions': 'mean',
        'net_flow_eth': 'mean',
        'unique_tokens': 'mean',
        'tx_per_day': 'mean'
    }).round(2)
    print(category_stats)
    
    # Feature columns
    print(f"\n=== Feature Columns ({len(features_df.columns)}) ===")
    print(features_df.columns.tolist())

if __name__ == "__main__":
    main()