"""
SMT SIGNAL PIPELINE v2 - BTC Whale Detection
=============================================
Improved logic based on 2025 market research:

1. Uses BTC-specific whale addresses (not ETH)
2. Fetches BTC transactions via BlockCypher API
3. Requires SUSTAINED flow (multiple TXs) not just one
4. Filters out internal transfers
5. Validates with Gemini grounding

API: BlockCypher (1000 requests/day free)
Output: ai_logs/signal_latest.json
"""

import os
import sys
import json
import time
import csv
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

BLOCKCYPHER_API_KEY = os.getenv('BLOCKCYPHER_API_KEY', '')  # Optional for basic requests
BLOCKCYPHER_BASE_URL = "https://api.blockcypher.com/v1/btc/main"
WEEX_BASE_URL = "https://api-contract.weex.com"
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')

TRADING_PAIR = "cmt_btcusdt"
MIN_TX_VALUE_BTC = 10.0  # Minimum BTC per transaction to consider
MIN_SUSTAINED_TXS = 2   # Require at least 2 TXs in same direction
LOOKBACK_HOURS = 6      # Look at last 6 hours of activity (more relevant)

# ============================================================
# KNOWN BTC WHALE ADDRESSES (LABELED)
# ============================================================

BTC_WHALES = [
    {
        "address": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",
        "category": "CEX_Wallet",
        "sub_label": "Binance Cold 1",
        "balance_btc": 248597
    },
    {
        "address": "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97",
        "category": "CEX_Wallet", 
        "sub_label": "Bitfinex Cold",
        "balance_btc": 178010
    },
    {
        "address": "bc1ql49ydapnjafl5t2cp9zqpjwe6pdgmxy98859v2",
        "category": "CEX_Wallet",
        "sub_label": "Robinhood",
        "balance_btc": 118300
    },
    {
        "address": "39884E3j6KZj82FK4vcCrkUvWYL5MQaS3v",
        "category": "CEX_Wallet",
        "sub_label": "Binance Cold 2",
        "balance_btc": 115177
    },
    {
        "address": "bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt",
        "category": "Government",
        "sub_label": "FBI Bitfinex Recovery",
        "balance_btc": 94643
    },
    # Hot wallets (more active, better for flow detection)
    {
        "address": "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s",
        "category": "CEX_Wallet",
        "sub_label": "Binance Hot",
        "balance_btc": 50000
    },
    {
        "address": "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h",
        "category": "CEX_Wallet",
        "sub_label": "Binance Hot 2",
        "balance_btc": 45000
    },
]

# Known CEX internal addresses (for filtering internal transfers)
CEX_INTERNAL_ADDRESSES = {
    "binance": ["34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "39884E3j6KZj82FK4vcCrkUvWYL5MQaS3v", 
                "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s", "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h"],
    "bitfinex": ["bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97"],
    "robinhood": ["bc1ql49ydapnjafl5t2cp9zqpjwe6pdgmxy98859v2"],
}

# Signal mapping - 2025 updated logic
SIGNAL_MAP = {
    'CEX_Wallet': {
        'inflow': {'signal': 'BEARISH', 'weight': 0.6},   # Reduced weight - may be noise
        'outflow': {'signal': 'BULLISH', 'weight': 0.7},  # Outflow more reliable
    },
    'Government': {
        'inflow': {'signal': 'NEUTRAL', 'weight': 0.3},
        'outflow': {'signal': 'BEARISH', 'weight': 0.8},  # Gov selling = very bearish
    },
    'Whale': {
        'inflow': {'signal': 'BULLISH', 'weight': 0.6},
        'outflow': {'signal': 'BEARISH', 'weight': 0.6},
    },
}


# ============================================================
# BLOCKCYPHER API FUNCTIONS
# ============================================================

def blockcypher_get(endpoint: str) -> Dict:
    """Make BlockCypher API request"""
    url = f"{BLOCKCYPHER_BASE_URL}{endpoint}"
    if BLOCKCYPHER_API_KEY:
        url += f"{'&' if '?' in url else '?'}token={BLOCKCYPHER_API_KEY}"
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"    BlockCypher error: {response.status_code}")
            return {}
    except Exception as e:
        print(f"    BlockCypher error: {e}")
        return {}


def get_address_info(address: str) -> Dict:
    """Get address balance and basic info"""
    # BlockCypher returns balance in satoshis
    data = blockcypher_get(f"/addrs/{address}/balance")
    if data:
        return {
            'address': address,
            'balance_btc': data.get('final_balance', 0) / 1e8,
            'total_received': data.get('total_received', 0) / 1e8,
            'total_sent': data.get('total_sent', 0) / 1e8,
            'n_tx': data.get('n_tx', 0)
        }
    return {}


def get_address_transactions(address: str, limit: int = 50) -> List[Dict]:
    """Get recent transactions for an address"""
    data = blockcypher_get(f"/addrs/{address}/full?limit={limit}")
    
    if not data or 'txs' not in data:
        return []
    
    transactions = []
    for tx in data.get('txs', []):
        try:
            # Parse transaction
            tx_time = tx.get('confirmed')
            if tx_time:
                # Parse ISO format
                try:
                    tx_datetime = datetime.fromisoformat(tx_time.replace('Z', '+00:00'))
                except:
                    tx_datetime = datetime.now(timezone.utc)
            else:
                tx_datetime = datetime.now(timezone.utc)
            
            # Calculate total input/output for this address
            address_input = 0
            address_output = 0
            other_addresses = []
            
            for inp in tx.get('inputs', []):
                addrs = inp.get('addresses') or []
                for addr in addrs:
                    if addr == address:
                        address_input += inp.get('output_value', 0)
                    else:
                        other_addresses.append(addr)
            
            for out in tx.get('outputs', []):
                addrs = out.get('addresses') or []
                for addr in addrs:
                    if addr == address:
                        address_output += out.get('value', 0)
                    else:
                        other_addresses.append(addr)
            
            # Determine direction
            if address_input > address_output:
                direction = 'outflow'
                value_satoshi = address_input - address_output
            else:
                direction = 'inflow'
                value_satoshi = address_output - address_input
            
            value_btc = value_satoshi / 1e8
            
            transactions.append({
                'hash': tx.get('hash'),
                'time': tx_datetime.isoformat(),
                'timestamp': tx_datetime.timestamp(),
                'direction': direction,
                'value_btc': value_btc,
                'confirmations': tx.get('confirmations', 0),
                'other_addresses': list(set(other_addresses))[:5]  # Keep first 5
            })
        except Exception as e:
            # Skip malformed transactions
            continue
    
    return transactions


def is_internal_transfer(whale_address: str, other_addresses: List[str]) -> bool:
    """Check if transaction is internal CEX transfer (noise)"""
    # Find which CEX this whale belongs to
    whale_cex = None
    for cex, addresses in CEX_INTERNAL_ADDRESSES.items():
        if whale_address in addresses:
            whale_cex = cex
            break
    
    if not whale_cex:
        return False
    
    # Check if other addresses belong to same CEX
    cex_addresses = CEX_INTERNAL_ADDRESSES.get(whale_cex, [])
    for addr in other_addresses:
        if addr in cex_addresses:
            return True
    
    return False


# ============================================================
# SUSTAINED FLOW DETECTION
# ============================================================

def analyze_sustained_flow(transactions: List[Dict], min_value: float, lookback_hours: int) -> Dict:
    """
    Analyze for sustained directional flow (not just single TX)
    
    Returns flow summary with:
    - Total inflow/outflow in period
    - Number of significant TXs in each direction
    - Net direction
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    
    inflow_count = 0
    outflow_count = 0
    inflow_btc = 0.0
    outflow_btc = 0.0
    significant_txs = []
    
    for tx in transactions:
        # Parse timestamp
        try:
            tx_time = datetime.fromisoformat(tx['time'])
        except:
            continue
        
        # Skip if outside lookback window
        if tx_time.tzinfo is None:
            tx_time = tx_time.replace(tzinfo=timezone.utc)
        if tx_time < cutoff:
            continue
        
        value = tx['value_btc']
        
        # Skip small transactions
        if value < min_value:
            continue
        
        if tx['direction'] == 'inflow':
            inflow_count += 1
            inflow_btc += value
        else:
            outflow_count += 1
            outflow_btc += value
        
        significant_txs.append(tx)
    
    # Determine net direction
    if inflow_btc > outflow_btc * 1.2:  # 20% threshold
        net_direction = 'inflow'
        net_value = inflow_btc - outflow_btc
    elif outflow_btc > inflow_btc * 1.2:
        net_direction = 'outflow'
        net_value = outflow_btc - inflow_btc
    else:
        net_direction = 'mixed'
        net_value = abs(inflow_btc - outflow_btc)
    
    return {
        'inflow_count': inflow_count,
        'outflow_count': outflow_count,
        'inflow_btc': inflow_btc,
        'outflow_btc': outflow_btc,
        'net_direction': net_direction,
        'net_value': net_value,
        'total_significant_txs': inflow_count + outflow_count,
        'significant_txs': significant_txs,
        'lookback_hours': lookback_hours
    }


# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_signal(whale: Dict, flow: Dict) -> Dict:
    """
    Generate trading signal based on sustained flow
    
    2025 Logic Updates:
    - Requires sustained flow (multiple TXs)
    - Weights signals based on reliability
    - Accounts for BTC/altcoin decoupling
    """
    category = whale['category']
    direction = flow['net_direction']
    
    # Get signal mapping
    cat_signals = SIGNAL_MAP.get(category, SIGNAL_MAP.get('Whale'))
    dir_signal = cat_signals.get(direction, {'signal': 'NEUTRAL', 'weight': 0.3})
    
    base_signal = dir_signal['signal']
    base_weight = dir_signal['weight']
    
    # Adjust confidence based on sustained flow
    tx_count = flow['total_significant_txs']
    if tx_count >= 5:
        confidence_boost = 0.15
    elif tx_count >= 3:
        confidence_boost = 0.10
    elif tx_count >= 2:
        confidence_boost = 0.05
    else:
        confidence_boost = -0.15  # Single TX = lower confidence
    
    # Adjust for mixed signals
    if direction == 'mixed':
        base_signal = 'NEUTRAL'
        base_weight = 0.3
    
    confidence = min(0.95, max(0.4, base_weight + confidence_boost))
    
    # Convert to trade signal
    if base_signal == 'BULLISH':
        trade_signal = 'LONG'
    elif base_signal == 'BEARISH':
        trade_signal = 'SHORT'
    else:
        trade_signal = 'NEUTRAL'
    
    reasoning = (
        f"{whale['category']} ({whale['sub_label']}) shows {direction} of "
        f"{flow['net_value']:.2f} BTC over {flow['lookback_hours']}h "
        f"({flow['total_significant_txs']} significant TXs). "
        f"Net flow indicates {base_signal} sentiment."
    )
    
    # 2025 caveat
    if category == 'CEX_Wallet' and direction == 'inflow':
        reasoning += " Note: 2025 research shows only ~15% of CEX inflows are actual sells."
        confidence *= 0.8  # Reduce confidence for CEX inflow
    
    return {
        'signal': trade_signal,
        'base': base_signal,
        'confidence': confidence,
        'reasoning': reasoning,
        'sustained': tx_count >= MIN_SUSTAINED_TXS
    }


# ============================================================
# MARKET DATA
# ============================================================

def get_btc_price() -> float:
    """Get current BTC price from WEEX"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={TRADING_PAIR}"
        response = requests.get(url, timeout=10).json()
        return float(response.get('last', 0))
    except:
        return 0


# ============================================================
# GEMINI VALIDATION
# ============================================================

def validate_with_gemini(whale: Dict, flow: Dict, signal: Dict, btc_price: float) -> Dict:
    """Validate with Gemini + Google Search grounding"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        
        client = genai.Client()
        
        print("\n    [Gemini] Searching BTC market news...")
        grounding_config = GenerateContentConfig(
            tools=[Tool(google_search=GoogleSearch())],
            temperature=0.2
        )
        news = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Latest Bitcoin BTC news today. Any major whale movements, exchange flows, or ETF activity?",
            config=grounding_config
        )
        print(f"    [Gemini] News: {news.text[:150]}...")
        
        print("    [Gemini] Analyzing signal...")
        json_config = GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )
        
        prompt = f"""Analyze this BTC whale activity for a trade decision:

WHALE DETECTED:
- Category: {whale['category']} ({whale['sub_label']})
- Address: {whale['address'][:20]}...

SUSTAINED FLOW (last {flow['lookback_hours']} hours):
- Net Direction: {flow['net_direction']}
- Net Value: {flow['net_value']:.2f} BTC
- Significant TXs: {flow['total_significant_txs']}
- Inflow: {flow['inflow_btc']:.2f} BTC ({flow['inflow_count']} TXs)
- Outflow: {flow['outflow_btc']:.2f} BTC ({flow['outflow_count']} TXs)

GENERATED SIGNAL:
- Direction: {signal['signal']}
- Confidence: {signal['confidence']:.0%}
- Reasoning: {signal['reasoning']}

MARKET:
- BTC Price: ${btc_price:,.0f}

NEWS CONTEXT:
{news.text[:400]}

2025 CONSIDERATIONS:
- BTC has decoupled from altcoins (correlation dropped to 0.64)
- Only ~15% of CEX inflows are actual sell signals
- ETF flows now dominate market sentiment

Should we execute this {signal['signal']} trade?

Respond JSON: {{"decision": "EXECUTE" or "WAIT", "signal": "LONG" or "SHORT", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""

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
            result['news'] = news.text[:400]
            return result
            
    except Exception as e:
        print(f"    [Gemini] Error: {e}")
    
    # Fallback
    return {
        'decision': 'EXECUTE' if signal['signal'] != 'NEUTRAL' and signal['sustained'] else 'WAIT',
        'signal': signal['signal'],
        'confidence': signal['confidence'],
        'reasoning': signal['reasoning'],
        'grounding': False
    }


# ============================================================
# LOCAL STORAGE
# ============================================================

def save_transactions_local(whale: Dict, transactions: List[Dict]) -> str:
    """Save transactions to local CSV"""
    os.makedirs('data', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    short_addr = whale['address'][:8]
    filename = f"data/btc_whale_txs_{short_addr}_{timestamp}.csv"
    
    if not transactions:
        return None
    
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['hash', 'time', 'direction', 'value_btc', 'confirmations'])
        writer.writeheader()
        for tx in transactions:
            writer.writerow({
                'hash': tx['hash'],
                'time': tx['time'],
                'direction': tx['direction'],
                'value_btc': tx['value_btc'],
                'confirmations': tx['confirmations']
            })
    
    print(f"    Saved {len(transactions)} TXs to {filename}")
    return filename


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_signal_pipeline():
    """Main signal generation pipeline"""
    
    print("=" * 70)
    print("SMT SIGNAL PIPELINE v2 - BTC Whale Detection")
    print("With Sustained Flow Analysis (2025 Logic)")
    print("=" * 70)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {TRADING_PAIR}")
    print(f"Min TX Value: {MIN_TX_VALUE_BTC} BTC")
    print(f"Lookback: {LOOKBACK_HOURS} hours")
    print(f"Required Sustained TXs: {MIN_SUSTAINED_TXS}")
    
    # ========== STEP 1: Get BTC Price ==========
    print("\n" + "-" * 70)
    print("[STEP 1] GET MARKET DATA")
    print("-" * 70)
    
    btc_price = get_btc_price()
    print(f"BTC Price: ${btc_price:,.2f}")
    
    # ========== STEP 2: Scan BTC Whales ==========
    print("\n" + "-" * 70)
    print("[STEP 2] SCAN BTC WHALE ADDRESSES (BlockCypher)")
    print("-" * 70)
    
    best_whale = None
    best_flow = None
    best_score = 0
    
    for i, whale in enumerate(BTC_WHALES):
        print(f"\n  [{i+1}/{len(BTC_WHALES)}] {whale['address'][:20]}... ({whale['sub_label']})")
        
        # Rate limit
        time.sleep(0.5)
        
        # Get transactions
        txs = get_address_transactions(whale['address'], limit=50)
        
        if not txs:
            print("    No transactions found")
            continue
        
        print(f"    Fetched {len(txs)} transactions")
        
        # Save locally
        save_transactions_local(whale, txs)
        
        # Filter internal transfers
        filtered_txs = []
        for tx in txs:
            if not is_internal_transfer(whale['address'], tx.get('other_addresses', [])):
                filtered_txs.append(tx)
        
        if len(filtered_txs) < len(txs):
            print(f"    Filtered {len(txs) - len(filtered_txs)} internal transfers")
        
        # Analyze sustained flow
        flow = analyze_sustained_flow(filtered_txs, MIN_TX_VALUE_BTC, LOOKBACK_HOURS)
        
        print(f"    Flow: {flow['net_direction']} ({flow['net_value']:.2f} BTC)")
        print(f"    Significant TXs: {flow['total_significant_txs']} (in:{flow['inflow_count']}, out:{flow['outflow_count']})")
        
        # Score this whale
        score = flow['net_value'] * flow['total_significant_txs']
        if flow['net_direction'] != 'mixed' and score > best_score:
            best_whale = whale
            best_flow = flow
            best_score = score
            print(f"    [BEST CANDIDATE] Score: {score:.2f}")
    
    if not best_whale or not best_flow:
        print("\n[WARNING] No significant sustained whale activity found!")
        print("This could mean:")
        print("  - Market is quiet")
        print("  - Whales are not moving")
        print("  - Need to lower thresholds")
        
        # Save empty signal
        signal_data = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'pipeline_version': 'SMT-Signal-v2-BTC',
            'trading_pair': TRADING_PAIR,
            'btc_price': btc_price,
            'whale': None,
            'flow': None,
            'signal': {'signal': 'NEUTRAL', 'confidence': 0},
            'validation': {'decision': 'WAIT', 'reasoning': 'No sustained whale activity detected'},
            'ready_to_trade': False
        }
        
        os.makedirs('ai_logs', exist_ok=True)
        with open('ai_logs/signal_latest.json', 'w') as f:
            json.dump(signal_data, f, indent=2)
        
        return signal_data
    
    # ========== STEP 3: Generate Signal ==========
    print("\n" + "-" * 70)
    print("[STEP 3] GENERATE TRADING SIGNAL")
    print("-" * 70)
    
    print(f"\n[BEST WHALE]")
    print(f"  Category: {best_whale['category']} ({best_whale['sub_label']})")
    print(f"  Address: {best_whale['address']}")
    print(f"  Flow: {best_flow['net_direction']} of {best_flow['net_value']:.2f} BTC")
    print(f"  TXs: {best_flow['total_significant_txs']} significant in last {LOOKBACK_HOURS}h")
    
    signal = generate_signal(best_whale, best_flow)
    
    print(f"\nSignal: {signal['signal']}")
    print(f"Base: {signal['base']}")
    print(f"Confidence: {signal['confidence']:.0%}")
    print(f"Sustained: {signal['sustained']}")
    print(f"Reasoning: {signal['reasoning']}")
    
    # ========== STEP 4: Gemini Validation ==========
    print("\n" + "-" * 70)
    print("[STEP 4] GEMINI VALIDATION (with Google Search)")
    print("-" * 70)
    
    validation = validate_with_gemini(best_whale, best_flow, signal, btc_price)
    
    print(f"\nFinal Decision: {validation['decision']}")
    print(f"Final Signal: {validation['signal']}")
    print(f"Confidence: {validation.get('confidence', 'N/A')}")
    print(f"Grounding: {validation.get('grounding', False)}")
    print(f"Reasoning: {validation['reasoning']}")
    
    # ========== STEP 5: Save Signal ==========
    print("\n" + "-" * 70)
    print("[STEP 5] SAVE SIGNAL FILE")
    print("-" * 70)
    
    os.makedirs('ai_logs', exist_ok=True)
    
    signal_data = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'pipeline_version': 'SMT-Signal-v2-BTC',
        'trading_pair': TRADING_PAIR,
        'btc_price': btc_price,
        'whale': best_whale,
        'flow': {
            'net_direction': best_flow['net_direction'],
            'net_value': best_flow['net_value'],
            'inflow_btc': best_flow['inflow_btc'],
            'outflow_btc': best_flow['outflow_btc'],
            'inflow_count': best_flow['inflow_count'],
            'outflow_count': best_flow['outflow_count'],
            'total_significant_txs': best_flow['total_significant_txs'],
            'lookback_hours': best_flow['lookback_hours']
        },
        'signal': signal,
        'validation': validation,
        'ready_to_trade': validation['decision'] == 'EXECUTE' and signal['sustained']
    }
    
    # Save with timestamp
    signal_file = f"ai_logs/signal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(signal_file, 'w') as f:
        json.dump(signal_data, f, indent=2)
    
    # Save as latest
    with open('ai_logs/signal_latest.json', 'w') as f:
        json.dump(signal_data, f, indent=2)
    
    print(f"Signal saved: {signal_file}")
    print(f"Latest: ai_logs/signal_latest.json")
    
    # ========== Summary ==========
    print("\n" + "=" * 70)
    print("SIGNAL GENERATION COMPLETE")
    print("=" * 70)
    
    if signal_data['ready_to_trade']:
        print(f"\nREADY TO TRADE: {validation['signal']} BTC")
        print(f"Confidence: {validation.get('confidence', signal['confidence']):.0%}")
        print(f"\nTo execute, run:")
        print(f"  python3 src/smt_execute_trade.py")
    else:
        print(f"\nDECISION: WAIT")
        if not signal['sustained']:
            print("Reason: Flow not sustained (need more TXs in same direction)")
        else:
            print(f"Reason: {validation['reasoning']}")
    
    return signal_data


if __name__ == "__main__":
    run_signal_pipeline()
