"""
SMT Nightly Trade V3 - Competition-Ready Pipeline
==================================================
Full automated trading pipeline for WEEX AI Wars hackathon.

Features:
- Real-time whale discovery (ETH/BTC)
- Multi-pair trading (8 pairs)
- Smart position sizing based on balance & confidence
- Competition-aware prompts with TP/SL calculations
- Auto AI log upload to WEEX
- BigQuery storage with timestamped tables
- Test mode for development

Run: python3 smt_nightly_trade_v3.py
Test: python3 smt_nightly_trade_v3.py --test
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import pickle
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

# ============================================================
# CONFIGURATION
# ============================================================

# Test Mode - set via --test flag or environment
TEST_MODE = "--test" in sys.argv or os.getenv("SMT_TEST_MODE", "false").lower() == "true"
SIMULATED_BALANCE = 1000.0  # Used when balance is 0 or in test mode

# Etherscan V2 API
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY', 'W7GTUDUM9BMBQPJUZXXMDBJH4JDPUQS9UR')
ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = 1

# WEEX API
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

# Google Cloud
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')
GCS_BUCKET = os.getenv('GCS_BUCKET', 'smt-weex-2025-models')

# Competition Settings
COMPETITION_START = datetime(2026, 1, 12, tzinfo=timezone.utc)
COMPETITION_END = datetime(2026, 2, 2, tzinfo=timezone.utc)
STARTING_BALANCE = 1000.0

# Trading Parameters
MAX_LEVERAGE = 20
MAX_OPEN_POSITIONS = 5
MAX_SINGLE_POSITION_PCT = 0.25  # Max 25% of balance per trade
MAX_TOTAL_EXPOSURE_PCT = 0.60  # Max 60% of balance across all positions

# Trading Pairs
TRADING_PAIRS = {
    "ETH": {"symbol": "cmt_ethusdt", "tier": 1, "has_whale_data": True},
    "BTC": {"symbol": "cmt_btcusdt", "tier": 1, "has_whale_data": True},  # Uses ETH whale correlation
    "SOL": {"symbol": "cmt_solusdt", "tier": 2, "has_whale_data": False},
    "DOGE": {"symbol": "cmt_dogeusdt", "tier": 2, "has_whale_data": False},
    "XRP": {"symbol": "cmt_xrpusdt", "tier": 2, "has_whale_data": False},
    "ADA": {"symbol": "cmt_adausdt", "tier": 2, "has_whale_data": False},
    "BNB": {"symbol": "cmt_bnbusdt", "tier": 2, "has_whale_data": False},
    "LTC": {"symbol": "cmt_ltcusdt", "tier": 2, "has_whale_data": False},
}

# Whale Discovery Parameters
MIN_TX_VALUE_ETH = 100.0
LOOKBACK_HOURS = 6
HISTORY_DAYS = 90
MAX_WHALES_TO_ANALYZE = 10

# Pipeline Version
PIPELINE_VERSION = "SMT-v3.0-Competition"
MODEL_NAME = "CatBoost-Gemini-v3.0"

# Known CEX addresses
CEX_ADDRESSES = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    "0x3cc936b795a188f0e246cbb2d74c5bd190aecf18": "MEXC",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
}

# Cache for contract info (stepSize, minOrderSize, etc.)
CONTRACT_INFO_CACHE = {}


def get_contract_info(symbol: str) -> Dict:
    """Get contract info including stepSize from WEEX"""
    global CONTRACT_INFO_CACHE
    
    if symbol in CONTRACT_INFO_CACHE:
        return CONTRACT_INFO_CACHE[symbol]
    
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/contracts?symbol={symbol}", timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) > 0:
            info = data[0]
            contract_info = {
                "symbol": symbol,
                "size_increment": info.get("size_increment", "3"),  # Decimal places
                "tick_size": info.get("tick_size", "2"),  # Price decimal places
                "min_order_size": float(info.get("minOrderSize", "0.001")),
                "max_order_size": float(info.get("maxOrderSize", "100000")),
                "max_leverage": int(info.get("maxLeverage", 100)),
            }
            CONTRACT_INFO_CACHE[symbol] = contract_info
            return contract_info
    except Exception as e:
        print(f"  [WEEX] Error getting contract info: {e}")
    
    # Default fallback
    return {"size_increment": "3", "tick_size": "2", "min_order_size": 0.001}


def round_size_to_step(size: float, symbol: str) -> float:
    """Round size to match WEEX stepSize requirement"""
    contract_info = get_contract_info(symbol)
    
    # size_increment is the number of decimal places allowed
    # e.g., "3" means 0.001 stepSize, "0" means 1, "-1" means 10
    increment = int(contract_info.get("size_increment", "3"))
    
    if increment >= 0:
        # Round to N decimal places
        return round(size, increment)
    else:
        # Round to nearest 10, 100, etc.
        step = 10 ** abs(increment)
        return round(size / step) * step

# Signal mapping for whale classification
SIGNAL_MAP = {
    "CEX_Wallet": {"inflow": ("BEARISH", 0.75), "outflow": ("BULLISH", 0.85)},
    "Large_Holder": {"inflow": ("BULLISH", 0.65), "outflow": ("BEARISH", 0.70)},
    "Staker": {"inflow": ("BULLISH", 0.60), "outflow": ("BEARISH", 0.70)},
    "DeFi_Trader": {"inflow": ("NEUTRAL", 0.40), "outflow": ("NEUTRAL", 0.40)},
    "Miner": {"inflow": ("NEUTRAL", 0.30), "outflow": ("BEARISH", 0.80)},
    "Exploiter": {"inflow": ("AVOID", 0.90), "outflow": ("AVOID", 0.90)},
}

# 32 Features for CatBoost
FEATURE_COLUMNS = [
    'total_txs', 'incoming_count', 'outgoing_count',
    'incoming_volume_eth', 'outgoing_volume_eth', 'net_flow_eth',
    'avg_tx_value_eth', 'max_tx_value_eth', 'min_tx_value_eth',
    'std_tx_value_eth', 'median_tx_value_eth',
    'unique_counterparties', 'unique_incoming', 'unique_outgoing',
    'activity_span_days', 'avg_time_between_tx_hours', 'tx_per_day',
    'erc20_tx_count', 'erc20_ratio', 'unique_tokens',
    'nft_tx_count', 'nft_ratio',
    'internal_tx_count', 'internal_ratio',
    'defi_interactions', 'cex_interactions',
    'large_tx_count', 'large_tx_ratio',
    'gas_avg', 'gas_max', 'gas_total',
    'balance_eth_log'
]


# ============================================================
# WEEX API HELPERS
# ============================================================

def weex_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def weex_headers(method: str, path: str, body: str = "") -> Dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": weex_sign(ts, method, path, body),
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE,
        "Content-Type": "application/json"
    }


def get_price(symbol: str) -> float:
    """Get current price from WEEX"""
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        return float(r.json().get("last", 0))
    except:
        return 0.0


def get_balance() -> float:
    """Get USDT balance from WEEX. Returns SIMULATED_BALANCE if 0 or test mode."""
    try:
        endpoint = "/capi/v2/account/accounts"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        balance = 0.0
        if "collateral" in data and len(data["collateral"]) > 0:
            balance = float(data["collateral"][0].get("amount", 0))
        
        # Use simulated balance if actual is 0 or in test mode
        if balance == 0 or TEST_MODE:
            print(f"  [Balance] Actual: {balance}, Using simulated: {SIMULATED_BALANCE}")
            return SIMULATED_BALANCE
        return balance
    except Exception as e:
        print(f"  [WEEX] Error getting balance: {e}")
        return SIMULATED_BALANCE


def get_open_positions() -> List[Dict]:
    """Get all open positions from WEEX"""
    try:
        endpoint = "/capi/v2/account/position/allPosition"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        positions = []
        if isinstance(data, list):
            for pos in data:
                if float(pos.get("size", 0)) > 0:
                    positions.append({
                        "symbol": pos.get("symbol"),
                        "side": "LONG" if pos.get("side") == "long" else "SHORT",
                        "size": float(pos.get("size", 0)),
                        "entry_price": float(pos.get("avgCost", 0)),
                        "unrealized_pnl": float(pos.get("unrealizedPL", 0)),
                        "margin": float(pos.get("margin", 0)),
                    })
        return positions
    except Exception as e:
        print(f"  [WEEX] Error getting positions: {e}")
        return []


def set_leverage(symbol: str, leverage: int) -> Dict:
    endpoint = "/capi/v2/account/leverage"
    body = json.dumps({"symbol": symbol, "leverage": leverage})
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()


def place_order(symbol: str, side: str, size: float, tp_price: float = None, sl_price: float = None) -> Dict:
    """
    Place market order on WEEX with optional TP/SL
    side: "1"=OpenLong, "2"=OpenShort, "3"=CloseLong, "4"=CloseShort
    """
    endpoint = "/capi/v2/order/placeOrder"
    # Round size to match WEEX stepSize requirement
    rounded_size = round_size_to_step(size, symbol)
    
    order = {
        "symbol": symbol,
        "client_oid": f"smt_{int(time.time()*1000)}",
        "size": str(rounded_size),
        "type": side,
        "order_type": "0",
        "match_price": "1"
    }
    
    # Add TP/SL if provided
    if tp_price:
        order["presetTakeProfitPrice"] = str(round(tp_price, 2))
    if sl_price:
        order["presetStopLossPrice"] = str(round(sl_price, 2))
    
    body = json.dumps(order)
    
    if TEST_MODE:
        print(f"  [TEST MODE] Would place order: {order}")
        return {"order_id": f"test_{int(time.time())}", "test_mode": True}
    
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()


def upload_ai_log_to_weex(stage: str, input_data: Dict, output_data: Dict, explanation: str, order_id: int = None) -> Dict:
    """Upload AI decision log to WEEX API"""
    endpoint = "/capi/v2/order/uploadAiLog"
    
    payload = {
        "stage": stage,
        "model": MODEL_NAME,
        "input": input_data,
        "output": output_data,
        "explanation": explanation[:500]
    }
    
    if order_id:
        payload["orderId"] = order_id
    
    body = json.dumps(payload)
    
    if TEST_MODE:
        print(f"  [TEST MODE] Would upload AI log: {stage}")
        return {"test_mode": True}
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        result = r.json()
        print(f"  [WEEX AI LOG] Uploaded: {stage}")
        return result
    except Exception as e:
        print(f"  [WEEX AI LOG] Error: {e}")
        return {"error": str(e)}


# ============================================================
# COMPETITION HELPERS
# ============================================================

def get_competition_status(balance: float) -> Dict:
    """Calculate competition timeline and strategy mode"""
    now = datetime.now(timezone.utc)
    
    if now < COMPETITION_START:
        days_until_start = (COMPETITION_START - now).days
        days_left = (COMPETITION_END - COMPETITION_START).days
        phase = "PRE_COMPETITION"
    elif now > COMPETITION_END:
        days_left = 0
        phase = "ENDED"
    else:
        days_left = (COMPETITION_END - now).days
        phase = "ACTIVE"
    
    # Calculate P&L
    pnl = balance - STARTING_BALANCE
    pnl_pct = ((balance / STARTING_BALANCE) - 1) * 100
    
    # Determine strategy mode
    if balance > STARTING_BALANCE * 1.20:  # Up 20%+
        strategy_mode = "CONSERVATIVE"
        risk_level = "LOW"
    elif balance < STARTING_BALANCE * 0.90:  # Down 10%+
        strategy_mode = "AGGRESSIVE"
        risk_level = "HIGH"
    else:
        strategy_mode = "MODERATE"
        risk_level = "MEDIUM"
    
    # Adjust for time remaining
    if days_left <= 3:
        max_hold_hours = 6
        tp_target = 2.0
        sl_limit = 1.5
    elif days_left <= 7:
        max_hold_hours = 24
        tp_target = 3.0
        sl_limit = 2.0
    elif days_left <= 15:
        max_hold_hours = 72
        tp_target = 4.0
        sl_limit = 2.0
    else:
        max_hold_hours = 120
        tp_target = 5.0
        sl_limit = 2.5
    
    return {
        "phase": phase,
        "days_left": days_left,
        "balance": balance,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "strategy_mode": strategy_mode,
        "risk_level": risk_level,
        "max_hold_hours": max_hold_hours,
        "default_tp_pct": tp_target,
        "default_sl_pct": sl_limit,
    }


def calculate_position_size(balance: float, confidence: float, tier: int, competition_status: Dict) -> float:
    """Calculate position size based on confidence and competition status"""
    
    # Base percentages by tier
    if tier == 1:  # ETH/BTC with whale data
        if confidence >= 0.85:
            base_pct = 0.20
        elif confidence >= 0.75:
            base_pct = 0.15
        elif confidence >= 0.65:
            base_pct = 0.10
        else:
            base_pct = 0.05
    else:  # Grounding-only pairs
        if confidence >= 0.85:
            base_pct = 0.10
        elif confidence >= 0.75:
            base_pct = 0.07
        else:
            base_pct = 0.05
    
    # Adjust for strategy mode
    if competition_status["strategy_mode"] == "AGGRESSIVE":
        base_pct *= 1.5
    elif competition_status["strategy_mode"] == "CONSERVATIVE":
        base_pct *= 0.7
    
    # Calculate size
    size = balance * base_pct
    
    # Apply limits
    max_size = balance * MAX_SINGLE_POSITION_PCT
    size = min(size, max_size)
    
    return round(size, 2)
# ============================================================
# ETHERSCAN API - WHALE DISCOVERY
# ============================================================

def fetch_recent_large_transactions(min_value_eth: float = 100.0, hours: int = 6) -> List[Dict]:
    """Fetch recent large ETH transactions"""
    print(f"\n[WHALE DISCOVERY] Fetching large txs (>{min_value_eth} ETH, last {hours}h)...")
    
    large_txs = []
    
    try:
        # Get latest block
        params = {"chainid": CHAIN_ID, "module": "proxy", "action": "eth_blockNumber", "apikey": ETHERSCAN_API_KEY}
        r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
        latest_block = int(r.json().get("result", "0x0"), 16)
        print(f"  Latest block: {latest_block}")
        
        blocks_per_hour = 300
        start_block = latest_block - (blocks_per_hour * hours)
    except Exception as e:
        print(f"  Error getting latest block: {e}")
        return []
    
    # Fetch from known CEX addresses
    for addr in list(CEX_ADDRESSES.keys())[:5]:
        time.sleep(0.25)
        try:
            params = {
                "chainid": CHAIN_ID, "module": "account", "action": "txlist",
                "address": addr, "startblock": start_block, "endblock": latest_block,
                "page": 1, "offset": 100, "sort": "desc", "apikey": ETHERSCAN_API_KEY
            }
            r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
            data = r.json()
            
            if data.get("status") == "1":
                for tx in data.get("result", []):
                    value_eth = int(tx.get("value", 0)) / 1e18
                    if value_eth >= min_value_eth:
                        large_txs.append({
                            "hash": tx.get("hash"),
                            "from": tx.get("from", "").lower(),
                            "to": tx.get("to", "").lower(),
                            "value_eth": value_eth,
                            "timestamp": int(tx.get("timeStamp", 0)),
                            "block": int(tx.get("blockNumber", 0)),
                        })
        except Exception as e:
            print(f"  Error fetching txs for {addr[:10]}...: {e}")
    
    print(f"  Found {len(large_txs)} large transactions")
    return large_txs


def discover_whales_from_transactions(transactions: List[Dict]) -> List[str]:
    """Extract unique whale addresses from large transactions"""
    whale_addresses = set()
    
    for tx in transactions:
        if tx["from"] and tx["from"] not in CEX_ADDRESSES:
            whale_addresses.add(tx["from"])
        if tx["to"] and tx["to"] not in CEX_ADDRESSES:
            whale_addresses.add(tx["to"])
    
    # Also include CEX addresses
    for cex_addr in CEX_ADDRESSES.keys():
        whale_addresses.add(cex_addr)
    
    whales = list(whale_addresses)[:MAX_WHALES_TO_ANALYZE]
    print(f"  Discovered {len(whales)} unique whale addresses")
    return whales


def fetch_whale_transaction_history(address: str, days: int = 90) -> List[Dict]:
    """Fetch full transaction history for a whale"""
    all_txs = []
    blocks_per_day = 7200
    
    try:
        params = {"chainid": CHAIN_ID, "module": "proxy", "action": "eth_blockNumber", "apikey": ETHERSCAN_API_KEY}
        r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
        latest_block = int(r.json().get("result", "0x0"), 16)
        start_block = max(0, latest_block - (blocks_per_day * days))
    except:
        start_block, latest_block = 0, 99999999
    
    # Normal transactions
    time.sleep(0.25)
    try:
        params = {
            "chainid": CHAIN_ID, "module": "account", "action": "txlist",
            "address": address, "startblock": start_block, "endblock": latest_block,
            "page": 1, "offset": 1000, "sort": "desc", "apikey": ETHERSCAN_API_KEY
        }
        r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "1":
            for tx in data.get("result", []):
                all_txs.append({
                    "hash": tx.get("hash"), "from": tx.get("from", "").lower(),
                    "to": tx.get("to", "").lower(), "value": int(tx.get("value", 0)),
                    "timestamp": int(tx.get("timeStamp", 0)),
                    "gas_used": int(tx.get("gasUsed", 0)), "tx_type": "normal", "token_symbol": "ETH",
                })
    except Exception as e:
        print(f"    Error fetching normal txs: {e}")
    
    # ERC-20 transactions
    time.sleep(0.25)
    try:
        params = {
            "chainid": CHAIN_ID, "module": "account", "action": "tokentx",
            "address": address, "startblock": start_block, "endblock": latest_block,
            "page": 1, "offset": 500, "sort": "desc", "apikey": ETHERSCAN_API_KEY
        }
        r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "1":
            for tx in data.get("result", []):
                decimals = int(tx.get("tokenDecimal", 18))
                all_txs.append({
                    "hash": tx.get("hash"), "from": tx.get("from", "").lower(),
                    "to": tx.get("to", "").lower(),
                    "value": int(tx.get("value", 0)) // (10 ** decimals) if decimals > 0 else 0,
                    "timestamp": int(tx.get("timeStamp", 0)),
                    "gas_used": int(tx.get("gasUsed", 0)), "tx_type": "erc20",
                    "token_symbol": tx.get("tokenSymbol", ""),
                })
    except Exception as e:
        print(f"    Error fetching ERC-20 txs: {e}")
    
    # Internal transactions
    time.sleep(0.25)
    try:
        params = {
            "chainid": CHAIN_ID, "module": "account", "action": "txlistinternal",
            "address": address, "startblock": start_block, "endblock": latest_block,
            "page": 1, "offset": 500, "sort": "desc", "apikey": ETHERSCAN_API_KEY
        }
        r = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "1":
            for tx in data.get("result", []):
                all_txs.append({
                    "hash": tx.get("hash"), "from": tx.get("from", "").lower(),
                    "to": tx.get("to", "").lower(), "value": int(tx.get("value", 0)),
                    "timestamp": int(tx.get("timeStamp", 0)),
                    "gas_used": 0, "tx_type": "internal", "token_symbol": "ETH",
                })
    except Exception as e:
        print(f"    Error fetching internal txs: {e}")
    
    return all_txs


# ============================================================
# FEATURE EXTRACTION
# ============================================================

def extract_features(address: str, transactions: List[Dict]) -> Dict:
    """Extract 32 features from transaction history for CatBoost"""
    address = address.lower()
    
    if not transactions:
        return {col: 0 for col in FEATURE_COLUMNS}
    
    incoming = [tx for tx in transactions if tx["to"] == address]
    outgoing = [tx for tx in transactions if tx["from"] == address]
    
    all_values_eth = [tx["value"] / 1e18 for tx in transactions if tx["value"] > 0]
    incoming_values = [tx["value"] / 1e18 for tx in incoming if tx["value"] > 0]
    outgoing_values = [tx["value"] / 1e18 for tx in outgoing if tx["value"] > 0]
    
    timestamps = sorted([tx["timestamp"] for tx in transactions if tx["timestamp"] > 0])
    
    erc20_txs = [tx for tx in transactions if tx["tx_type"] == "erc20"]
    internal_txs = [tx for tx in transactions if tx["tx_type"] == "internal"]
    nft_txs = [tx for tx in transactions if tx["tx_type"] in ("erc721", "erc1155")]
    
    counterparties, incoming_cp, outgoing_cp = set(), set(), set()
    for tx in transactions:
        if tx["to"] == address and tx["from"]:
            counterparties.add(tx["from"])
            incoming_cp.add(tx["from"])
        elif tx["from"] == address and tx["to"]:
            counterparties.add(tx["to"])
            outgoing_cp.add(tx["to"])
    
    tokens = set(tx.get("token_symbol", "") for tx in transactions if tx.get("token_symbol"))
    
    defi_protocols = {"0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"}
    defi_count = sum(1 for tx in transactions if tx["to"] in defi_protocols or tx["from"] in defi_protocols)
    cex_count = sum(1 for tx in transactions if tx["to"] in CEX_ADDRESSES or tx["from"] in CEX_ADDRESSES)
    
    large_txs = [tx for tx in transactions if tx["value"] / 1e18 > 10]
    gas_values = [tx["gas_used"] for tx in transactions if tx["gas_used"] > 0]
    
    if len(timestamps) >= 2:
        activity_span_days = (timestamps[-1] - timestamps[0]) / 86400
        time_diffs = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        avg_time_between = np.mean(time_diffs) / 3600 if time_diffs else 0
    else:
        activity_span_days, avg_time_between = 0, 0
    
    tx_per_day = len(transactions) / max(activity_span_days, 1)
    
    return {
        "total_txs": len(transactions),
        "incoming_count": len(incoming),
        "outgoing_count": len(outgoing),
        "incoming_volume_eth": sum(incoming_values) if incoming_values else 0,
        "outgoing_volume_eth": sum(outgoing_values) if outgoing_values else 0,
        "net_flow_eth": sum(incoming_values) - sum(outgoing_values) if incoming_values or outgoing_values else 0,
        "avg_tx_value_eth": np.mean(all_values_eth) if all_values_eth else 0,
        "max_tx_value_eth": max(all_values_eth) if all_values_eth else 0,
        "min_tx_value_eth": min(all_values_eth) if all_values_eth else 0,
        "std_tx_value_eth": np.std(all_values_eth) if len(all_values_eth) > 1 else 0,
        "median_tx_value_eth": np.median(all_values_eth) if all_values_eth else 0,
        "unique_counterparties": len(counterparties),
        "unique_incoming": len(incoming_cp),
        "unique_outgoing": len(outgoing_cp),
        "activity_span_days": activity_span_days,
        "avg_time_between_tx_hours": avg_time_between,
        "tx_per_day": tx_per_day,
        "erc20_tx_count": len(erc20_txs),
        "erc20_ratio": len(erc20_txs) / len(transactions) if transactions else 0,
        "unique_tokens": len(tokens),
        "nft_tx_count": len(nft_txs),
        "nft_ratio": len(nft_txs) / len(transactions) if transactions else 0,
        "internal_tx_count": len(internal_txs),
        "internal_ratio": len(internal_txs) / len(transactions) if transactions else 0,
        "defi_interactions": defi_count,
        "cex_interactions": cex_count,
        "large_tx_count": len(large_txs),
        "large_tx_ratio": len(large_txs) / len(transactions) if transactions else 0,
        "gas_avg": np.mean(gas_values) if gas_values else 0,
        "gas_max": max(gas_values) if gas_values else 0,
        "gas_total": sum(gas_values) if gas_values else 0,
        "balance_eth_log": 0,
    }


# ============================================================
# BIGQUERY STORAGE
# ============================================================

def save_to_bigquery(table_type: str, data: List[Dict], run_timestamp: str) -> str:
    """Save data to BigQuery with timestamped table"""
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=PROJECT_ID)
        
        if table_type == "transactions":
            table_id = f"{PROJECT_ID}.raw_data.whale_transactions_{run_timestamp}"
            schema = [
                bigquery.SchemaField("address", "STRING"),
                bigquery.SchemaField("hash", "STRING"),
                bigquery.SchemaField("from_address", "STRING"),
                bigquery.SchemaField("to_address", "STRING"),
                bigquery.SchemaField("value_eth", "FLOAT"),
                bigquery.SchemaField("timestamp", "INTEGER"),
                bigquery.SchemaField("tx_type", "STRING"),
                bigquery.SchemaField("token_symbol", "STRING"),
                bigquery.SchemaField("inserted_at", "STRING"),
            ]
        else:  # features
            table_id = f"{PROJECT_ID}.processed_data.whale_features_{run_timestamp}"
            schema = [
                bigquery.SchemaField("address", "STRING"),
                bigquery.SchemaField("classification", "STRING"),
                bigquery.SchemaField("confidence", "FLOAT"),
                bigquery.SchemaField("inserted_at", "STRING"),
            ] + [bigquery.SchemaField(col, "FLOAT") for col in FEATURE_COLUMNS]
        
        df = pd.DataFrame(data)
        job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        
        print(f"  [BigQuery] Saved {len(data)} rows to {table_id}")
        return table_id
    except Exception as e:
        print(f"  [BigQuery] Error: {e}")
        return ""


# ============================================================
# CATBOOST CLASSIFIER
# ============================================================

class WhaleClassifier:
    def __init__(self):
        self.model = None
        self.label_encoder = None
        self.loaded = False
    
    def load_from_gcs(self):
        try:
            from google.cloud import storage
            from catboost import CatBoostClassifier
            
            print("  [Model] Loading CatBoost from GCS...")
            storage_client = storage.Client(project=PROJECT_ID)
            bucket = storage_client.bucket(GCS_BUCKET)
            
            os.makedirs('/tmp/smt_model', exist_ok=True)
            
            bucket.blob('models/production/catboost_whale_classifier_production.cbm').download_to_filename('/tmp/smt_model/model.cbm')
            bucket.blob('models/production/label_encoder_production.pkl').download_to_filename('/tmp/smt_model/encoder.pkl')
            
            self.model = CatBoostClassifier()
            self.model.load_model('/tmp/smt_model/model.cbm')
            
            with open('/tmp/smt_model/encoder.pkl', 'rb') as f:
                self.label_encoder = pickle.load(f)
            
            self.loaded = True
            print(f"  [Model] Loaded. Classes: {list(self.label_encoder.classes_)}")
        except Exception as e:
            print(f"  [Model] Error: {e}, using rule-based fallback")
            self.loaded = False
    
    def classify(self, features: Dict) -> Tuple[str, float]:
        if not self.loaded:
            return self._rule_based(features)
        
        try:
            feature_vector = np.array([[features.get(col, 0) for col in FEATURE_COLUMNS]])
            prediction = self.model.predict(feature_vector)
            probabilities = self.model.predict_proba(feature_vector)
            pred_idx = int(prediction[0])
            return self.label_encoder.inverse_transform([pred_idx])[0], float(probabilities[0][pred_idx])
        except Exception as e:
            print(f"  [Model] Prediction error: {e}")
            return self._rule_based(features)
    
    def _rule_based(self, features: Dict) -> Tuple[str, float]:
        if features.get("cex_interactions", 0) > 10:
            return "CEX_Wallet", 0.70
        elif features.get("defi_interactions", 0) > 20:
            return "DeFi_Trader", 0.65
        elif features.get("large_tx_ratio", 0) > 0.5:
            return "Large_Holder", 0.60
        else:
            return "Large_Holder", 0.50


def analyze_whale_flow(address: str, recent_txs: List[Dict]) -> Dict:
    """Analyze recent flow direction"""
    address = address.lower()
    inflow, outflow, cex_in, cex_out = 0.0, 0.0, 0.0, 0.0
    
    for tx in recent_txs:
        value = tx.get("value", 0) / 1e18
        if tx["to"] == address:
            inflow += value
            if tx["from"] in CEX_ADDRESSES:
                cex_in += value
        elif tx["from"] == address:
            outflow += value
            if tx["to"] in CEX_ADDRESSES:
                cex_out += value
    
    net = inflow - outflow
    direction = "mixed" if abs(net) < MIN_TX_VALUE_ETH else ("inflow" if net > 0 else "outflow")
    
    return {
        "inflow_eth": inflow, "outflow_eth": outflow, "net_flow_eth": net,
        "direction": direction, "cex_inflow": cex_in, "cex_outflow": cex_out,
        "has_cex_interaction": cex_in > 0 or cex_out > 0,
    }


def generate_whale_signal(category: str, confidence: float, flow: Dict) -> Dict:
    """Generate signal from whale classification + flow"""
    if flow["direction"] == "mixed":
        return {"signal": "NEUTRAL", "confidence": 0.0, "reasoning": "Mixed flow"}
    
    signal_info = SIGNAL_MAP.get(category, {"inflow": ("NEUTRAL", 0.3), "outflow": ("NEUTRAL", 0.3)})
    base_signal, weight = signal_info.get(flow["direction"], ("NEUTRAL", 0.3))
    
    if flow["has_cex_interaction"]:
        weight = min(0.95, weight + 0.10)
    
    final_conf = confidence * weight
    trade_signal = {"BULLISH": "LONG", "BEARISH": "SHORT", "AVOID": "SKIP"}.get(base_signal, "NEUTRAL")
    
    reasoning = f"{category} showing {flow['direction']} of {abs(flow['net_flow_eth']):.1f} ETH. "
    if flow["has_cex_interaction"]:
        reasoning += f"CEX interaction: in={flow['cex_inflow']:.1f}, out={flow['cex_outflow']:.1f} ETH."
    
    return {"signal": trade_signal, "confidence": final_conf, "base_signal": base_signal, "reasoning": reasoning}
# ============================================================
# GEMINI TRADING PROMPTS
# ============================================================

def build_trading_prompt(pair: str, pair_info: Dict, balance: float, competition_status: Dict,
                         open_positions: List[Dict], whale_data: Dict = None) -> str:
    """Build comprehensive trading prompt for Gemini"""
    
    token = pair.upper()
    symbol = pair_info["symbol"]
    tier = pair_info["tier"]
    
    # Get current price
    current_price = get_price(symbol)
    
    # Calculate position sizes
    conservative_size = balance * 0.05
    moderate_size = balance * 0.10
    aggressive_size = balance * 0.20
    
    # Calculate available capital
    total_in_positions = sum(p.get('margin', 0) for p in open_positions)
    available = balance - total_in_positions
    
    prompt = f"""You are an AI trading assistant for the WEEX AI Wars futures trading competition.

====== ACCOUNT STATUS ======
Current Balance: {balance:.2f} USDT
Available (not in positions): {available:.2f} USDT
Open Positions: {len(open_positions)}
Total P&L So Far: {competition_status['pnl']:.2f} USDT ({competition_status['pnl_pct']:+.1f}%)

====== COMPETITION STATUS ======
Phase: {competition_status['phase']}
Days Remaining: {competition_status['days_left']}
Strategy Mode: {competition_status['strategy_mode']}
Risk Level: {competition_status['risk_level']}
Goal: Finish top 2 in group by final balance

====== TRADING PAIR ======
Token: {token}
Symbol: {symbol}
Current Price: ${current_price:,.4f}
Signal Source: {"Whale Intelligence + Grounding" if tier == 1 else "Market Grounding Only"}

====== POSITION SIZING (at {MAX_LEVERAGE}x leverage) ======
Conservative (5%): {conservative_size:.0f} USDT margin = {conservative_size * MAX_LEVERAGE:.0f} USDT exposure
Moderate (10%): {moderate_size:.0f} USDT margin = {moderate_size * MAX_LEVERAGE:.0f} USDT exposure
Aggressive (20%): {aggressive_size:.0f} USDT margin = {aggressive_size * MAX_LEVERAGE:.0f} USDT exposure

====== RISK/REWARD CALCULATOR ======
Example with {moderate_size:.0f} USDT position at {MAX_LEVERAGE}x:
- SL at -1.5% price: -{moderate_size * 0.015 * MAX_LEVERAGE:.1f} USDT loss ({(moderate_size * 0.015 * MAX_LEVERAGE / balance) * 100:.1f}% of balance)
- SL at -2.0% price: -{moderate_size * 0.02 * MAX_LEVERAGE:.1f} USDT loss ({(moderate_size * 0.02 * MAX_LEVERAGE / balance) * 100:.1f}% of balance)
- TP at +3.0% price: +{moderate_size * 0.03 * MAX_LEVERAGE:.1f} USDT profit ({(moderate_size * 0.03 * MAX_LEVERAGE / balance) * 100:.1f}% of balance)
- TP at +5.0% price: +{moderate_size * 0.05 * MAX_LEVERAGE:.1f} USDT profit ({(moderate_size * 0.05 * MAX_LEVERAGE / balance) * 100:.1f}% of balance)

====== TIME CONSTRAINTS ======
Max Hold Time: {competition_status['max_hold_hours']} hours
Default TP Target: {competition_status['default_tp_pct']}%
Default SL Limit: {competition_status['default_sl_pct']}%
"""

    # Add whale data if available (Tier 1)
    if whale_data and tier == 1:
        prompt += f"""
====== WHALE INTELLIGENCE (High Confidence Signal) ======
Whale Address: {whale_data.get('address', 'N/A')[:10]}...{whale_data.get('address', 'N/A')[-6:]}
Classification: {whale_data.get('category', 'Unknown')}
Classification Confidence: {whale_data.get('class_confidence', 0):.0%}
Flow Direction: {whale_data.get('flow', {}).get('direction', 'unknown')}
Net Flow: {whale_data.get('flow', {}).get('net_flow_eth', 0):.2f} ETH
CEX Interaction: {"Yes" if whale_data.get('flow', {}).get('has_cex_interaction') else "No"}
Whale Signal: {whale_data.get('signal', {}).get('signal', 'N/A')} ({whale_data.get('signal', {}).get('confidence', 0):.0%})
Reasoning: {whale_data.get('signal', {}).get('reasoning', 'N/A')}
"""

    prompt += f"""
====== YOUR TASK ======
1. Search for latest {token} news and market data:
   - Whale movements and large transactions
   - Exchange inflows/outflows
   - Upcoming events (unlocks, upgrades, ETFs, partnerships)
   - Current market sentiment
   - Any major catalysts in next {min(competition_status['days_left'], 5)} days

2. Analyze risk/reward considering:
   - Competition: {competition_status['days_left']} days left, we {"need gains" if balance < STARTING_BALANCE else "should protect our lead" if balance > STARTING_BALANCE * 1.2 else "should grow steadily"}
   - Max hold: {competition_status['max_hold_hours']} hours
   - Current strategy: {competition_status['strategy_mode']}

3. Make a trading decision

====== RESPONSE FORMAT (JSON only) ======
{{
    "decision": "LONG" | "SHORT" | "WAIT",
    "confidence": 0.0-1.0,
    "recommended_position_usdt": number ({conservative_size:.0f} to {aggressive_size:.0f}),
    "take_profit_percent": number (e.g., 3.0),
    "stop_loss_percent": number (e.g., 2.0),
    "expected_profit_usdt": number,
    "max_loss_usdt": number,
    "hold_time_hours": number,
    "entry_price": {current_price:.4f},
    "tp_price": number,
    "sl_price": number,
    "key_catalyst": "main reason for trade",
    "risk_factors": ["risk1", "risk2"],
    "market_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
    "reasoning": "2-3 sentence explanation"
}}
"""
    
    return prompt


def validate_with_gemini(pair: str, pair_info: Dict, balance: float, competition_status: Dict,
                         open_positions: List[Dict], whale_data: Dict = None) -> Dict:
    """Get trading decision from Gemini with grounding"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        
        client = genai.Client()
        
        # Build the comprehensive prompt
        prompt = build_trading_prompt(pair, pair_info, balance, competition_status, open_positions, whale_data)
        
        # First: Search for market context
        print(f"  [Gemini] Searching {pair.upper()} market data...")
        grounding_config = GenerateContentConfig(
            tools=[Tool(google_search=GoogleSearch())],
            temperature=0.3
        )
        
        search_query = f"Latest {pair.upper()} cryptocurrency news price prediction whale movements today"
        search_response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=search_query,
            config=grounding_config
        )
        
        market_context = search_response.text[:1000] if search_response.text else "No market data found"
        print(f"  [Gemini] Found market context: {market_context[:100]}...")
        
        # Add market context to prompt
        full_prompt = prompt + f"""
====== CURRENT MARKET CONTEXT (from search) ======
{market_context}

Now analyze and respond with JSON only:
"""
        
        # Second: Get trading decision
        json_config = GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )
        
        decision_response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
            config=json_config
        )
        
        result = json.loads(decision_response.text)
        result["grounding"] = True
        result["market_context"] = market_context[:500]
        
        return result
        
    except Exception as e:
        print(f"  [Gemini] Error: {e}")
        
        # Fallback: use whale signal if available
        if whale_data and whale_data.get("signal", {}).get("confidence", 0) >= 0.70:
            signal = whale_data["signal"]
            return {
                "decision": signal["signal"],
                "confidence": signal["confidence"],
                "recommended_position_usdt": balance * 0.10,
                "take_profit_percent": competition_status["default_tp_pct"],
                "stop_loss_percent": competition_status["default_sl_pct"],
                "hold_time_hours": competition_status["max_hold_hours"],
                "reasoning": f"Gemini unavailable. Using whale signal: {signal['reasoning']}",
                "grounding": False,
            }
        
        return {
            "decision": "WAIT",
            "confidence": 0.0,
            "reasoning": f"Gemini unavailable and no strong whale signal. Error: {str(e)}",
            "grounding": False,
        }


# ============================================================
# TRADE EXECUTION
# ============================================================

def execute_trade(pair_info: Dict, decision: Dict, balance: float) -> Dict:
    """Execute trade based on Gemini decision"""
    
    symbol = pair_info["symbol"]
    signal = decision["decision"]
    
    if signal not in ("LONG", "SHORT"):
        return {"executed": False, "reason": "No trade signal"}
    
    # Get current price
    current_price = get_price(symbol)
    if current_price == 0:
        return {"executed": False, "reason": "Could not get price"}
    
    # Position size
    position_usdt = decision.get("recommended_position_usdt", balance * 0.10)
    position_usdt = min(position_usdt, balance * MAX_SINGLE_POSITION_PCT)  # Cap at max
    
    # Calculate size in asset units and round to WEEX stepSize
    raw_size = position_usdt / current_price
    size = round_size_to_step(raw_size, symbol)
    
    # Ensure minimum order size
    contract_info = get_contract_info(symbol)
    min_size = contract_info.get("min_order_size", 0.001)
    if size < min_size:
        size = min_size
    
    print(f"  [Trade] Raw size: {raw_size:.6f}, Rounded: {size}")
    
    # Calculate TP/SL prices
    tp_pct = decision.get("take_profit_percent", 3.0) / 100
    sl_pct = decision.get("stop_loss_percent", 2.0) / 100
    
    if signal == "LONG":
        order_type = "1"  # Open Long
        tp_price = current_price * (1 + tp_pct)
        sl_price = current_price * (1 - sl_pct)
    else:  # SHORT
        order_type = "2"  # Open Short
        tp_price = current_price * (1 - tp_pct)
        sl_price = current_price * (1 + sl_pct)
    
    # Set leverage
    print(f"  [Trade] Setting leverage to {MAX_LEVERAGE}x...")
    set_leverage(symbol, MAX_LEVERAGE)
    
    # Place order
    print(f"  [Trade] Placing {signal} order: {size:.4f} @ ${current_price:.2f}")
    print(f"  [Trade] TP: ${tp_price:.2f} ({tp_pct*100:.1f}%), SL: ${sl_price:.2f} ({sl_pct*100:.1f}%)")
    
    result = place_order(symbol, order_type, size, tp_price, sl_price)
    
    order_id = result.get("order_id")
    
    return {
        "executed": True,
        "order_id": order_id,
        "symbol": symbol,
        "signal": signal,
        "size": size,
        "position_usdt": position_usdt,
        "entry_price": current_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "tp_pct": tp_pct * 100,
        "sl_pct": sl_pct * 100,
        "leverage": MAX_LEVERAGE,
        "result": result,
    }


# ============================================================
# LOCAL LOGGING
# ============================================================

def save_local_log(log_data: Dict, run_timestamp: str) -> str:
    """Save AI decision log locally"""
    os.makedirs("ai_logs", exist_ok=True)
    filename = f"ai_logs/v3_{run_timestamp}.json"
    with open(filename, "w") as f:
        json.dump(log_data, f, indent=2, default=str)
    print(f"\n[LOCAL LOG] Saved to {filename}")
    return filename
# ============================================================
# POSITION MONITORING & ORDER MANAGEMENT
# ============================================================

def get_pending_orders(symbol: str = None) -> List[Dict]:
    """Get all pending/open orders"""
    try:
        endpoint = "/capi/v2/order/current"
        if symbol:
            endpoint += f"?symbol={symbol}"
        
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        orders = []
        if isinstance(data, list):
            for order in data:
                if order.get("status") in ("pending", "open", "untriggered"):
                    orders.append({
                        "order_id": order.get("order_id"),
                        "client_oid": order.get("client_oid"),
                        "symbol": order.get("symbol"),
                        "type": order.get("type"),
                        "size": order.get("size"),
                        "price": order.get("price"),
                        "status": order.get("status"),
                        "tp_price": order.get("presetTakeProfitPrice"),
                        "sl_price": order.get("presetStopLossPrice"),
                    })
        return orders
    except Exception as e:
        print(f"  [WEEX] Error getting pending orders: {e}")
        return []


def get_pending_plan_orders(symbol: str = None) -> List[Dict]:
    """Get all pending trigger/plan orders (TP/SL)"""
    try:
        endpoint = "/capi/v2/order/currentPlan"
        if symbol:
            endpoint += f"?symbol={symbol}"
        
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        orders = []
        if isinstance(data, list):
            for order in data:
                orders.append({
                    "order_id": order.get("order_id"),
                    "client_oid": order.get("client_oid"),
                    "symbol": order.get("symbol"),
                    "type": order.get("type"),
                    "size": order.get("size"),
                    "trigger_price": order.get("triggerPrice"),
                    "status": order.get("status"),
                })
        return orders
    except Exception as e:
        print(f"  [WEEX] Error getting plan orders: {e}")
        return []


def cancel_order(order_id: str) -> Dict:
    """Cancel a regular order"""
    endpoint = "/capi/v2/order/cancel_order"
    body = json.dumps({"orderId": order_id})
    
    if TEST_MODE:
        print(f"  [TEST MODE] Would cancel order: {order_id}")
        return {"result": True, "test_mode": True}
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        return r.json()
    except Exception as e:
        print(f"  [WEEX] Error canceling order {order_id}: {e}")
        return {"result": False, "error": str(e)}


def cancel_plan_order(order_id: str) -> Dict:
    """Cancel a trigger/plan order (TP/SL)"""
    endpoint = "/capi/v2/order/cancel_plan"
    body = json.dumps({"orderId": order_id})
    
    if TEST_MODE:
        print(f"  [TEST MODE] Would cancel plan order: {order_id}")
        return {"result": True, "test_mode": True}
    
    try:
        r = requests.post(
            f"{WEEX_BASE_URL}{endpoint}",
            headers=weex_headers("POST", endpoint, body),
            data=body,
            timeout=15
        )
        return r.json()
    except Exception as e:
        print(f"  [WEEX] Error canceling plan order {order_id}: {e}")
        return {"result": False, "error": str(e)}


def cancel_all_orders_for_symbol(symbol: str) -> Dict:
    """Cancel all pending orders (regular + plan) for a symbol"""
    results = {"regular_cancelled": [], "plan_cancelled": [], "errors": []}
    
    print(f"  [Cleanup] Canceling all orders for {symbol}...")
    
    # Cancel regular orders
    pending_orders = get_pending_orders(symbol)
    for order in pending_orders:
        result = cancel_order(order["order_id"])
        if result.get("result"):
            results["regular_cancelled"].append(order["order_id"])
            print(f"    Cancelled regular order: {order['order_id']}")
        else:
            results["errors"].append({"order_id": order["order_id"], "error": result.get("err_msg")})
    
    # Cancel plan/trigger orders (TP/SL)
    plan_orders = get_pending_plan_orders(symbol)
    for order in plan_orders:
        result = cancel_plan_order(order["order_id"])
        if result.get("result"):
            results["plan_cancelled"].append(order["order_id"])
            print(f"    Cancelled plan order: {order['order_id']}")
        else:
            results["errors"].append({"order_id": order["order_id"], "error": result.get("err_msg")})
    
    print(f"  [Cleanup] Done. Cancelled {len(results['regular_cancelled'])} regular, {len(results['plan_cancelled'])} plan orders")
    return results


def check_position_status(symbol: str) -> Dict:
    """Check if position is still open and its current status"""
    try:
        endpoint = f"/capi/v2/account/position/singlePosition?symbol={symbol}"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        
        if isinstance(data, dict) and float(data.get("size", 0)) > 0:
            return {
                "is_open": True,
                "symbol": symbol,
                "side": "LONG" if data.get("side") == "long" else "SHORT",
                "size": float(data.get("size", 0)),
                "entry_price": float(data.get("avgCost", 0)),
                "mark_price": float(data.get("markPrice", 0)),
                "unrealized_pnl": float(data.get("unrealizedPL", 0)),
                "margin": float(data.get("margin", 0)),
                "liquidation_price": float(data.get("liquidationPrice", 0)),
            }
        else:
            return {"is_open": False, "symbol": symbol}
    except Exception as e:
        print(f"  [WEEX] Error checking position: {e}")
        return {"is_open": False, "symbol": symbol, "error": str(e)}


def monitor_position_until_close(symbol: str, max_wait_seconds: int = 300, check_interval: int = 10) -> Dict:
    """
    Monitor a position until it closes (TP/SL hit) or timeout.
    When closed, cancel any remaining orders.
    
    Args:
        symbol: Trading pair symbol
        max_wait_seconds: Maximum time to wait (default 5 minutes)
        check_interval: How often to check (default 10 seconds)
    
    Returns:
        Dict with final status and cleanup results
    """
    print(f"\n[MONITOR] Watching position {symbol} (max {max_wait_seconds}s)...")
    
    start_time = time.time()
    initial_status = check_position_status(symbol)
    
    if not initial_status.get("is_open"):
        print(f"  Position already closed")
        cleanup = cancel_all_orders_for_symbol(symbol)
        return {"status": "already_closed", "cleanup": cleanup}
    
    print(f"  Position open: {initial_status['side']} {initial_status['size']} @ ${initial_status['entry_price']:.2f}")
    print(f"  Current P&L: ${initial_status['unrealized_pnl']:.2f}")
    
    while time.time() - start_time < max_wait_seconds:
        time.sleep(check_interval)
        
        status = check_position_status(symbol)
        elapsed = int(time.time() - start_time)
        
        if not status.get("is_open"):
            print(f"\n  Position CLOSED after {elapsed}s!")
            
            # CRITICAL: Cancel remaining orders (the other side of TP/SL)
            cleanup = cancel_all_orders_for_symbol(symbol)
            
            return {
                "status": "closed",
                "elapsed_seconds": elapsed,
                "cleanup": cleanup,
            }
        else:
            pnl = status.get("unrealized_pnl", 0)
            mark = status.get("mark_price", 0)
            pnl_pct = (pnl / status.get("margin", 1)) * 100 if status.get("margin") else 0
            print(f"  [{elapsed}s] Still open. Mark: ${mark:.2f}, P&L: ${pnl:.2f} ({pnl_pct:+.1f}%)")
    
    # Timeout reached - position still open
    print(f"\n  Timeout reached ({max_wait_seconds}s). Position still open.")
    final_status = check_position_status(symbol)
    
    return {
        "status": "timeout",
        "elapsed_seconds": max_wait_seconds,
        "final_position": final_status,
    }


def close_position_manually(symbol: str, side: str, size: float) -> Dict:
    """
    Manually close a position and cleanup all orders.
    
    Args:
        symbol: Trading pair
        side: Current position side ("LONG" or "SHORT")
        size: Position size to close
    """
    print(f"\n[MANUAL CLOSE] Closing {side} position: {size} {symbol}")
    
    # Determine close order type
    if side == "LONG":
        close_type = "3"  # Close Long
    else:
        close_type = "4"  # Close Short
    
    # Place close order
    result = place_order(symbol, close_type, size)
    
    # Wait for close to complete
    time.sleep(2)
    
    # Cleanup any remaining orders
    cleanup = cancel_all_orders_for_symbol(symbol)
    
    return {
        "close_result": result,
        "cleanup": cleanup,
    }


# ============================================================
# TRADE TRACKING (for managing multiple positions)
# ============================================================

class TradeTracker:
    """Track active trades and their associated orders"""
    
    def __init__(self, log_file: str = "active_trades.json"):
        self.log_file = log_file
        self.trades = self._load()
    
    def _load(self) -> Dict:
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"active": {}, "closed": []}
    
    def _save(self):
        with open(self.log_file, 'w') as f:
            json.dump(self.trades, f, indent=2, default=str)
    
    def add_trade(self, symbol: str, trade_data: Dict):
        """Record a new trade"""
        self.trades["active"][symbol] = {
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "order_id": trade_data.get("order_id"),
            "side": trade_data.get("signal"),
            "size": trade_data.get("size"),
            "entry_price": trade_data.get("entry_price"),
            "tp_price": trade_data.get("tp_price"),
            "sl_price": trade_data.get("sl_price"),
            "position_usdt": trade_data.get("position_usdt"),
        }
        self._save()
        print(f"  [Tracker] Added trade: {symbol}")
    
    def close_trade(self, symbol: str, close_data: Dict = None):
        """Move trade from active to closed"""
        if symbol in self.trades["active"]:
            trade = self.trades["active"].pop(symbol)
            trade["closed_at"] = datetime.now(timezone.utc).isoformat()
            if close_data:
                trade["close_data"] = close_data
            self.trades["closed"].append(trade)
            self._save()
            print(f"  [Tracker] Closed trade: {symbol}")
    
    def get_active_symbols(self) -> List[str]:
        """Get list of symbols with active trades"""
        return list(self.trades["active"].keys())
    
    def get_active_trade(self, symbol: str) -> Optional[Dict]:
        """Get active trade for a symbol"""
        return self.trades["active"].get(symbol)
    
    def check_and_cleanup_all(self):
        """Check all active trades and cleanup closed ones"""
        print("\n[CLEANUP CHECK] Checking all active trades...")
        
        for symbol in list(self.trades["active"].keys()):
            status = check_position_status(symbol)
            
            if not status.get("is_open"):
                print(f"  {symbol}: Position closed, cleaning up...")
                cleanup = cancel_all_orders_for_symbol(symbol)
                self.close_trade(symbol, {"cleanup": cleanup})
            else:
                pnl = status.get("unrealized_pnl", 0)
                print(f"  {symbol}: Still open, P&L: ${pnl:.2f}")
        
        print(f"  Active trades: {len(self.trades['active'])}")
# ============================================================
# MAIN PIPELINE
# ============================================================

def run_pipeline():
    """Main trading pipeline"""
    
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    print("=" * 70)
    print("SMT V3 - Competition-Ready Trading Pipeline")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"Run ID: {run_timestamp}")
    print(f"Test Mode: {TEST_MODE}")
    print("=" * 70)
    
    # Initialize log
    ai_log = {
        "run_id": f"smt_v3_{run_timestamp}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "test_mode": TEST_MODE,
        "stages": [],
        "trades": [],
    }
    
    def log_stage(stage: str, data: Dict):
        ai_log["stages"].append({
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data
        })
        print(f"\n[{stage}]")
        for k, v in list(data.items())[:6]:
            print(f"  {k}: {str(v)[:100]}")
    
    # ===== STAGE 1: Get Account Status =====
    balance = get_balance()
    open_positions = get_open_positions()
    competition_status = get_competition_status(balance)
    
    log_stage("ACCOUNT_STATUS", {
        "balance": balance,
        "open_positions": len(open_positions),
        "pnl": competition_status["pnl"],
        "pnl_pct": f"{competition_status['pnl_pct']:.1f}%",
        "days_left": competition_status["days_left"],
        "strategy_mode": competition_status["strategy_mode"],
    })
    
    # Check if we can trade
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        log_stage("SKIPPED", {"reason": f"Max positions reached ({MAX_OPEN_POSITIONS})"})
        ai_log["final_decision"] = "MAX_POSITIONS"
        save_local_log(ai_log, run_timestamp)
        return
    
    # ===== STAGE 2: Whale Discovery (Tier 1 pairs) =====
    whale_data = None
    
    print("\n" + "=" * 50)
    print("TIER 1: Whale Intelligence Pipeline (ETH/BTC)")
    print("=" * 50)
    
    large_txs = fetch_recent_large_transactions(MIN_TX_VALUE_ETH, LOOKBACK_HOURS)
    
    if large_txs:
        whale_addresses = discover_whales_from_transactions(large_txs)
        
        log_stage("WHALE_DISCOVERY", {
            "large_txs": len(large_txs),
            "whales_found": len(whale_addresses),
        })
        
        # Upload discovery log
        upload_ai_log_to_weex(
            stage="Whale Discovery",
            input_data={"lookback_hours": LOOKBACK_HOURS, "min_tx_eth": MIN_TX_VALUE_ETH},
            output_data={"whales_found": len(whale_addresses), "large_txs": len(large_txs)},
            explanation=f"Discovered {len(whale_addresses)} whales from {len(large_txs)} large transactions."
        )
        
        # ===== STAGE 3: Analyze Whales =====
        classifier = WhaleClassifier()
        classifier.load_from_gcs()
        
        best_signal = None
        best_whale_data = None
        
        for i, addr in enumerate(whale_addresses[:MAX_WHALES_TO_ANALYZE]):
            print(f"\n[WHALE {i+1}/{min(len(whale_addresses), MAX_WHALES_TO_ANALYZE)}] {addr[:12]}...")
            
            # Fetch history
            txs = fetch_whale_transaction_history(addr, HISTORY_DAYS)
            if not txs:
                continue
            
            # Extract features
            features = extract_features(addr, txs)
            
            # Classify
            category, class_conf = classifier.classify(features)
            print(f"  Classification: {category} ({class_conf:.0%})")
            
            # Analyze recent flow
            recent_txs = [tx for tx in txs if tx["timestamp"] > time.time() - (LOOKBACK_HOURS * 3600)]
            flow = analyze_whale_flow(addr, recent_txs)
            
            if flow["direction"] == "mixed":
                continue
            
            # Generate signal
            signal = generate_whale_signal(category, class_conf, flow)
            print(f"  Signal: {signal['signal']} ({signal['confidence']:.0%})")
            
            if signal["signal"] not in ("NEUTRAL", "SKIP") and signal["confidence"] > (best_signal["confidence"] if best_signal else 0):
                best_signal = signal
                best_whale_data = {
                    "address": addr,
                    "category": category,
                    "class_confidence": class_conf,
                    "flow": flow,
                    "signal": signal,
                    "features": features,
                }
        
        if best_whale_data:
            whale_data = best_whale_data
            log_stage("WHALE_SIGNAL", {
                "address": whale_data["address"][:12] + "...",
                "category": whale_data["category"],
                "signal": whale_data["signal"]["signal"],
                "confidence": f"{whale_data['signal']['confidence']:.0%}",
            })
            
            # Upload signal log
            upload_ai_log_to_weex(
                stage="Whale Signal Generation",
                input_data={
                    "whale_address": whale_data["address"],
                    "category": whale_data["category"],
                    "flow_direction": whale_data["flow"]["direction"],
                },
                output_data={
                    "signal": whale_data["signal"]["signal"],
                    "confidence": whale_data["signal"]["confidence"],
                },
                explanation=whale_data["signal"]["reasoning"]
            )
    else:
        log_stage("NO_WHALE_DATA", {"reason": "No large transactions found"})
    
    # ===== STAGE 4: Evaluate All Pairs =====
    print("\n" + "=" * 50)
    print("EVALUATING ALL TRADING PAIRS")
    print("=" * 50)
    
    trade_opportunities = []
    
    for pair, pair_info in TRADING_PAIRS.items():
        print(f"\n[{pair}] Tier {pair_info['tier']} - Getting Gemini analysis...")
        
        # Use whale data for ETH/BTC
        pair_whale_data = whale_data if pair in ("ETH", "BTC") and whale_data else None
        
        # Get Gemini decision
        decision = validate_with_gemini(
            pair=pair,
            pair_info=pair_info,
            balance=balance,
            competition_status=competition_status,
            open_positions=open_positions,
            whale_data=pair_whale_data
        )
        
        print(f"  Decision: {decision.get('decision')} ({decision.get('confidence', 0):.0%})")
        print(f"  Reasoning: {decision.get('reasoning', 'N/A')[:80]}...")
        
        if decision.get("decision") in ("LONG", "SHORT") and decision.get("confidence", 0) >= 0.60:
            trade_opportunities.append({
                "pair": pair,
                "pair_info": pair_info,
                "decision": decision,
                "whale_data": pair_whale_data,
            })
        
        # Upload analysis log
        upload_ai_log_to_weex(
            stage=f"Market Analysis - {pair}",
            input_data={
                "pair": pair,
                "tier": pair_info["tier"],
                "balance": balance,
                "has_whale_data": pair_whale_data is not None,
            },
            output_data={
                "decision": decision.get("decision"),
                "confidence": decision.get("confidence", 0),
                "recommended_size": decision.get("recommended_position_usdt", 0),
            },
            explanation=decision.get("reasoning", "Analysis complete")[:500]
        )
        
        time.sleep(3)  # Rate limit between pairs - Gemini needs more time
    
    # ===== STAGE 5: Execute Best Trade =====
    if not trade_opportunities:
        log_stage("NO_TRADES", {"reason": "No opportunities met confidence threshold"})
        ai_log["final_decision"] = "NO_TRADE"
        save_local_log(ai_log, run_timestamp)
        return
    
    # Sort by confidence and pick best
    trade_opportunities.sort(key=lambda x: x["decision"]["confidence"], reverse=True)
    best_trade = trade_opportunities[0]
    
    log_stage("BEST_OPPORTUNITY", {
        "pair": best_trade["pair"],
        "signal": best_trade["decision"]["decision"],
        "confidence": f"{best_trade['decision']['confidence']:.0%}",
        "position_size": best_trade["decision"].get("recommended_position_usdt", 0),
        "tp": f"{best_trade['decision'].get('take_profit_percent', 0)}%",
        "sl": f"{best_trade['decision'].get('stop_loss_percent', 0)}%",
    })
    
    # Execute
    print("\n" + "=" * 50)
    print(f"EXECUTING TRADE: {best_trade['pair']}")
    print("=" * 50)
    
    trade_result = execute_trade(
        pair_info=best_trade["pair_info"],
        decision=best_trade["decision"],
        balance=balance
    )
    
    if trade_result["executed"]:
        log_stage("TRADE_EXECUTED", {
            "order_id": trade_result.get("order_id"),
            "symbol": trade_result["symbol"],
            "signal": trade_result["signal"],
            "size": trade_result["size"],
            "entry_price": trade_result["entry_price"],
            "tp_price": trade_result["tp_price"],
            "sl_price": trade_result["sl_price"],
        })
        
        # Upload execution log
        upload_ai_log_to_weex(
            stage="Trade Execution",
            input_data={
                "pair": best_trade["pair"],
                "signal": trade_result["signal"],
                "entry_price": trade_result["entry_price"],
            },
            output_data={
                "order_id": trade_result.get("order_id"),
                "size": trade_result["size"],
                "position_usdt": trade_result["position_usdt"],
                "tp_price": trade_result["tp_price"],
                "sl_price": trade_result["sl_price"],
            },
            explanation=f"Executed {trade_result['signal']} on {best_trade['pair']} at ${trade_result['entry_price']:.2f}. "
                       f"Position: {trade_result['position_usdt']:.0f} USDT at {MAX_LEVERAGE}x. "
                       f"TP: ${trade_result['tp_price']:.2f} (+{trade_result['tp_pct']:.1f}%), "
                       f"SL: ${trade_result['sl_price']:.2f} (-{trade_result['sl_pct']:.1f}%).",
            order_id=int(trade_result["order_id"]) if trade_result.get("order_id") and str(trade_result["order_id"]).isdigit() else None
        )
        
        ai_log["trades"].append(trade_result)
        ai_log["final_decision"] = "EXECUTED"
    else:
        log_stage("TRADE_FAILED", {"reason": trade_result.get("reason", "Unknown")})
        ai_log["final_decision"] = "FAILED"
    
    # Save local log
    save_local_log(ai_log, run_timestamp)
    
    print("\n" + "=" * 70)
    print(f"PIPELINE COMPLETE")
    print(f"Final Decision: {ai_log['final_decision']}")
    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_pipeline()
