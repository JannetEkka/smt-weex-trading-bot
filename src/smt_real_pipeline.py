"""
SMT REAL PIPELINE v2 - With Transaction Storage
================================================
1. Load whale list (known addresses)
2. Fetch FRESH transactions from Etherscan V2 API
3. SAVE transactions to:
   - Local: data/whale_txs_{address}_{datetime}.csv
   - BigQuery: smt-weex-2025.raw_data.whale_txs_{address}_{datetime}
4. Find whale with most recent significant TX
5. Generate signal based on TX direction
6. Validate with Gemini grounding
7. Save AI log (NO TRADING YET)
"""

import os
import sys
import json
import time
import csv
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Configuration
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY', 'W7GTUDUM9BMBQPJUZXXMDBJH4JDPUQS9UR')
ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
WEEX_BASE_URL = "https://api-contract.weex.com"
MIN_TX_VALUE_ETH = 50.0
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')

# Signal mapping
SIGNAL_MAP = {
    'Miner': {'outflow': 'BEARISH', 'inflow': 'NEUTRAL'},
    'Staker': {'outflow': 'BEARISH', 'inflow': 'NEUTRAL'},
    'CEX_Wallet': {'inflow': 'BEARISH', 'outflow': 'BULLISH'},
    'DeFi_Trader': {'outflow': 'BEARISH', 'inflow': 'BULLISH'},
    'Institutional': {'outflow': 'BEARISH', 'inflow': 'BULLISH'},
}

def get_known_whales() -> List[Dict]:
    """Top whales from our dataset"""
    return [
        {"address": "0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503", "category": "DeFi_Trader", "sub_label": "SushiSwap", "balance_eth": 554999},
        {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "category": "CEX_Wallet", "sub_label": "Binance 8", "balance_eth": 538622},
        {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "category": "CEX_Wallet", "sub_label": "Binance 14", "balance_eth": 159563},
        {"address": "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "category": "CEX_Wallet", "sub_label": "Binance 15", "balance_eth": 24971},
        {"address": "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "category": "CEX_Wallet", "sub_label": "Binance 16", "balance_eth": 15215},
        {"address": "0xdc24316b9ae028f1497c275eb9192a3ea0f67022", "category": "Staker", "sub_label": "Lido", "balance_eth": 21323},
        {"address": "0x889edc2edab5f40e902b864ad4d7ade8e412f9b1", "category": "Staker", "sub_label": "Lido", "balance_eth": 31744},
        {"address": "0x7f3a1e45f67e92c880e573b43379d71ee089db54", "category": "Miner", "sub_label": "Genesis", "balance_eth": 41499},
        {"address": "0xebec795c9c8bbd61ffc14a6662944748f299cacf", "category": "Miner", "sub_label": "Proposer", "balance_eth": 28079},
        {"address": "0x742d35cc6634c0532925a3b844bc454e4438f44e", "category": "Institutional", "sub_label": "Finance", "balance_eth": 15082},
    ]

def fetch_transactions(address: str, limit: int = 20) -> List[Dict]:
    """Fetch recent transactions from Etherscan V2"""
    params = {
        'chainid': '1',
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'page': 1,
        'offset': limit,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    try:
        response = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
        data = response.json()
        if data.get('status') == '1':
            return data.get('result', [])
        return []
    except Exception as e:
        print(f"    Etherscan error: {e}")
        return []

def save_transactions_local(address: str, txs: List[Dict], category: str) -> str:
    """Save transactions to local CSV file"""
    os.makedirs('data', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    short_addr = address[:8]
    filename = f"data/whale_txs_{short_addr}_{timestamp}.csv"
    if not txs:
        return None
    rows = []
    for tx in txs:
        value_eth = int(tx.get('value', '0')) / 1e18
        is_outflow = tx.get('from', '').lower() == address.lower()
        rows.append({
            'whale_address': address,
            'category': category,
            'tx_hash': tx.get('hash'),
            'block_number': tx.get('blockNumber'),
            'timestamp': tx.get('timeStamp'),
            'datetime': datetime.fromtimestamp(int(tx.get('timeStamp', 0))).isoformat(),
            'from_address': tx.get('from'),
            'to_address': tx.get('to'),
            'value_wei': tx.get('value'),
            'value_eth': value_eth,
            'direction': 'outflow' if is_outflow else 'inflow',
            'gas': tx.get('gas'),
            'gas_price': tx.get('gasPrice'),
            'gas_used': tx.get('gasUsed'),
            'is_error': tx.get('isError'),
            'function_name': tx.get('functionName', ''),
            'fetched_at': datetime.now(timezone.utc).isoformat()
        })
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"    Saved {len(rows)} txs to {filename}")
    return filename

def save_transactions_bigquery(address: str, txs: List[Dict], category: str) -> str:
    """Save transactions to BigQuery table"""
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=PROJECT_ID)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        short_addr = address[2:10]
        table_name = f"whale_txs_{short_addr}_{timestamp}"
        table_id = f"{PROJECT_ID}.raw_data.{table_name}"
        rows = []
        for tx in txs:
            value_eth = int(tx.get('value', '0')) / 1e18
            is_outflow = tx.get('from', '').lower() == address.lower()
            tx_timestamp = int(tx.get('timeStamp', 0))
            rows.append({
                'whale_address': address,
                'category': category,
                'tx_hash': tx.get('hash'),
                'block_number': int(tx.get('blockNumber', 0)),
                'tx_timestamp': tx_timestamp,
                'tx_datetime': datetime.fromtimestamp(tx_timestamp).isoformat(),
                'from_address': tx.get('from'),
                'to_address': tx.get('to'),
                'value_wei': tx.get('value'),
                'value_eth': value_eth,
                'direction': 'outflow' if is_outflow else 'inflow',
                'gas': int(tx.get('gas', 0)),
                'gas_price': tx.get('gasPrice'),
                'gas_used': int(tx.get('gasUsed', 0)),
                'is_error': tx.get('isError') == '1',
                'function_name': tx.get('functionName', '')[:100] if tx.get('functionName') else '',
                'fetched_at': datetime.now(timezone.utc).isoformat()
            })
        schema = [
            bigquery.SchemaField("whale_address", "STRING"),
            bigquery.SchemaField("category", "STRING"),
            bigquery.SchemaField("tx_hash", "STRING"),
            bigquery.SchemaField("block_number", "INTEGER"),
            bigquery.SchemaField("tx_timestamp", "INTEGER"),
            bigquery.SchemaField("tx_datetime", "STRING"),
            bigquery.SchemaField("from_address", "STRING"),
            bigquery.SchemaField("to_address", "STRING"),
            bigquery.SchemaField("value_wei", "STRING"),
            bigquery.SchemaField("value_eth", "FLOAT"),
            bigquery.SchemaField("direction", "STRING"),
            bigquery.SchemaField("gas", "INTEGER"),
            bigquery.SchemaField("gas_price", "STRING"),
            bigquery.SchemaField("gas_used", "INTEGER"),
            bigquery.SchemaField("is_error", "BOOLEAN"),
            bigquery.SchemaField("function_name", "STRING"),
            bigquery.SchemaField("fetched_at", "STRING"),
        ]
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table, exists_ok=True)
        errors = client.insert_rows_json(table_id, rows)
        if errors:
            print(f"    BigQuery errors: {errors}")
        else:
            print(f"    Saved {len(rows)} txs to BigQuery: {table_name}")
        return table_name
    except Exception as e:
        print(f"    BigQuery error: {e}")
        return None

def find_whale_activity(whales: List[Dict], min_value: float = MIN_TX_VALUE_ETH) -> Optional[Dict]:
    """Find whale with most recent significant TX and save all transactions"""
    print(f"\n[SCANNING] {len(whales)} whales (min {min_value} ETH)...")
    best = None
    best_ts = 0
    all_saved_files = []
    all_saved_tables = []
    for i, w in enumerate(whales):
        time.sleep(0.25)
        addr = w['address']
        print(f"\n  [{i+1}/{len(whales)}] {addr[:16]}... ({w['category']})")
        txs = fetch_transactions(addr, limit=20)
        if not txs:
            print("    No transactions found")
            continue
        print(f"    Fetched {len(txs)} transactions")
        local_file = save_transactions_local(addr, txs, w['category'])
        if local_file:
            all_saved_files.append(local_file)
        bq_table = save_transactions_bigquery(addr, txs, w['category'])
        if bq_table:
            all_saved_tables.append(bq_table)
        for tx in txs:
            val = int(tx.get('value', '0')) / 1e18
            ts = int(tx.get('timeStamp', 0))
            if val >= min_value and ts > best_ts:
                is_out = tx.get('from', '').lower() == addr.lower()
                best = {
                    'address': addr,
                    'category': w['category'],
                    'sub_label': w.get('sub_label', ''),
                    'balance_eth': w.get('balance_eth', 0),
                    'tx_hash': tx.get('hash'),
                    'tx_time': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'),
                    'tx_value': val,
                    'is_outflow': is_out,
                    'direction': 'OUTFLOW' if is_out else 'INFLOW',
                }
                best_ts = ts
                print(f"    [SIGNIFICANT] {val:.1f} ETH {'OUT' if is_out else 'IN'}")
    print(f"\n[STORAGE SUMMARY]")
    print(f"  Local files: {len(all_saved_files)}")
    print(f"  BigQuery tables: {len(all_saved_tables)}")
    return best

def generate_signal(whale: Dict) -> Dict:
    """Generate trading signal"""
    cat = whale['category']
    direction = 'outflow' if whale['is_outflow'] else 'inflow'
    base = SIGNAL_MAP.get(cat, {}).get(direction, 'NEUTRAL')
    if base == 'BULLISH':
        signal = 'LONG'
    elif base == 'BEARISH':
        signal = 'SHORT'
    else:
        signal = 'NEUTRAL'
    confidence = 0.75 if whale['tx_value'] > 100 else 0.65
    return {
        'signal': signal,
        'base': base,
        'confidence': confidence,
        'reasoning': f"{cat} {direction} of {whale['tx_value']:.1f} ETH = {base}"
    }

def get_weex_prices() -> Dict:
    """Get BTC/ETH prices from WEEX"""
    try:
        btc = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol=cmt_btcusdt", timeout=10).json()
        eth = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol=cmt_ethusdt", timeout=10).json()
        return {
            'btc_price': float(btc.get('last', 0)),
            'btc_change': float(btc.get('priceChangePercent', 0)) * 100,
            'eth_price': float(eth.get('last', 0)),
            'eth_change': float(eth.get('priceChangePercent', 0)) * 100,
        }
    except Exception as e:
        print(f"WEEX error: {e}")
        return {'btc_price': 0, 'btc_change': 0, 'eth_price': 0, 'eth_change': 0}

def validate_with_gemini(whale: Dict, signal: Dict, market: Dict) -> Dict:
    """Validate with Gemini grounding"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        client = genai.Client()
        print("\n    [Gemini] Searching ETH news...")
        grounding_config = GenerateContentConfig(
            tools=[Tool(google_search=GoogleSearch())],
            temperature=0.2
        )
        news = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Latest Ethereum ETH crypto news today in 2 sentences",
            config=grounding_config
        )
        print(f"    [Gemini] News: {news.text[:150]}...")
        print("    [Gemini] Analyzing signal...")
        json_config = GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )
        prompt = f"""Analyze whale activity:
- Category: {whale['category']} ({whale.get('sub_label','')})
- Action: {whale['direction']} of {whale['tx_value']:.1f} ETH  
- Signal: {signal['base']}
- ETH: ${market['eth_price']:,.0f} ({market['eth_change']:+.1f}%)
- News: {news.text[:200]}

JSON response: {{"decision":"EXECUTE or WAIT","signal":"LONG or SHORT","confidence":0.0-1.0,"reasoning":"brief"}}"""
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=json_config
        )
        import re
        match = re.search(r'\{[\s\S]*\}', resp.text)
        if match:
            result = json.loads(match.group())
            result['grounding'] = True
            result['news'] = news.text[:200]
            return result
    except Exception as e:
        print(f"    [Gemini] Error: {e}")
    return {
        'decision': 'EXECUTE' if signal['signal'] != 'NEUTRAL' else 'WAIT',
        'signal': signal['signal'],
        'confidence': signal['confidence'],
        'reasoning': signal['reasoning'],
        'grounding': False
    }

def run_pipeline():
    """Main pipeline"""
    print("=" * 70)
    print("SMT AI TRADING PIPELINE v2")
    print("With Transaction Storage (Local + BigQuery)")
    print("=" * 70)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Project: {PROJECT_ID}")
    print("\n" + "-" * 70)
    print("[STEP 1] WHALE LIST")
    print("-" * 70)
    whales = get_known_whales()
    print(f"Using {len(whales)} known whales")
    print("\n" + "-" * 70)
    print("[STEP 2] ETHERSCAN V2 - FETCH & SAVE TRANSACTIONS")
    print("-" * 70)
    whale = find_whale_activity(whales, MIN_TX_VALUE_ETH)
    if not whale:
        print("\nNo significant whale activity found!")
        return None
    print(f"\n[BEST WHALE] {whale['category']} ({whale['sub_label']})")
    print(f"  Address: {whale['address']}")
    print(f"  TX: {whale['tx_value']:.1f} ETH {whale['direction']}")
    print(f"  Time: {whale['tx_time']}")
    print("\n" + "-" * 70)
    print("[STEP 3] GENERATE SIGNAL")
    print("-" * 70)
    signal = generate_signal(whale)
    print(f"Signal: {signal['signal']} ({signal['base']})")
    print(f"Confidence: {signal['confidence']:.0%}")
    print(f"Reasoning: {signal['reasoning']}")
    print("\n" + "-" * 70)
    print("[STEP 4] WEEX MARKET DATA")
    print("-" * 70)
    market = get_weex_prices()
    print(f"BTC: ${market['btc_price']:,.0f} ({market['btc_change']:+.1f}%)")
    print(f"ETH: ${market['eth_price']:,.0f} ({market['eth_change']:+.1f}%)")
    print("\n" + "-" * 70)
    print("[STEP 5] GEMINI VALIDATION")
    print("-" * 70)
    validation = validate_with_gemini(whale, signal, market)
    print(f"\nFinal Decision: {validation['decision']}")
    print(f"Final Signal: {validation['signal']}")
    print(f"Confidence: {validation.get('confidence', 'N/A')}")
    print(f"Grounding Used: {validation.get('grounding', False)}")
    print(f"Reasoning: {validation['reasoning']}")
    print("\n" + "=" * 70)
    print("[STEP 6] SAVE AI LOG")
    print("=" * 70)
    os.makedirs('ai_logs', exist_ok=True)
    log = {
        'whale': whale,
        'signal': signal,
        'market': market,
        'validation': validation,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    log_file = f"ai_logs/pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_file, 'w') as f:
        json.dump(log, f, indent=2)
    print(f"AI log saved: {log_file}")
    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE - NO TRADING EXECUTED")
    print("=" * 70)
    return log

if __name__ == "__main__":
    run_pipeline()
