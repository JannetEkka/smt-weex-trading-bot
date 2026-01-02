"""
SMT Nightly Trade - Full Pipeline
==================================
Runs once per night at 11 PM IST via Cloud Scheduler

Pipeline:
1. Fetch recent ETH whale transactions (Etherscan V2)
2. Classify whale behavior (CatBoost)
3. Generate trading signal based on category + action
4. Validate with Gemini + Google Search grounding
5. Execute 10 USDT trade on WEEX (cmt_ethusdt)
6. Save AI decision logs for review
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY', 'W7GTUDUM9BMBQPJUZXXMDBJH4JDPUQS9UR')
ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = 1

WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')

TRADING_PAIR = "cmt_ethusdt"
TRADE_SIZE_USD = 10.0
MAX_LEVERAGE = 20
MIN_TX_VALUE_ETH = 100.0
LOOKBACK_HOURS = 6

# ============================================================
# ETH WHALE ADDRESSES
# ============================================================

ETH_WHALES = [
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "category": "CEX_Wallet", "sub_label": "Binance 8", "balance_eth": 538622},
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "category": "CEX_Wallet", "sub_label": "Binance 14", "balance_eth": 400000},
    {"address": "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "category": "CEX_Wallet", "sub_label": "Binance 15", "balance_eth": 350000},
    {"address": "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "category": "CEX_Wallet", "sub_label": "Binance Hot", "balance_eth": 200000},
    {"address": "0x3cc936b795a188f0e246cbb2d74c5bd190aecf18", "category": "CEX_Wallet", "sub_label": "MEXC", "balance_eth": 150000},
    {"address": "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "category": "Staker", "sub_label": "Lido stETH", "balance_eth": 9000000},
    {"address": "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "category": "Large_Holder", "sub_label": "Binance Cold", "balance_eth": 1000000},
    {"address": "0x1111111254eeb25477b68fb85ed929f73a960582", "category": "DeFi_Trader", "sub_label": "1inch Router", "balance_eth": 50000},
]

CEX_ADDRESSES = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance",
    "0x3cc936b795a188f0e246cbb2d74c5bd190aecf18": "MEXC",
}

SIGNAL_MAP = {
    "CEX_Wallet": {"inflow": {"signal": "BEARISH", "weight": 0.75}, "outflow": {"signal": "BULLISH", "weight": 0.85}},
    "Staker": {"inflow": {"signal": "BULLISH", "weight": 0.6}, "outflow": {"signal": "BEARISH", "weight": 0.7}},
    "Large_Holder": {"inflow": {"signal": "BULLISH", "weight": 0.65}, "outflow": {"signal": "BEARISH", "weight": 0.7}},
    "DeFi_Trader": {"inflow": {"signal": "NEUTRAL", "weight": 0.4}, "outflow": {"signal": "NEUTRAL", "weight": 0.4}},
    "Miner": {"inflow": {"signal": "NEUTRAL", "weight": 0.3}, "outflow": {"signal": "BEARISH", "weight": 0.8}},
}

# ============================================================
# ETHERSCAN API
# ============================================================

def fetch_whale_transactions(whale_address: str) -> List[Dict]:
    params = {
        "chainid": CHAIN_ID, "module": "account", "action": "txlist",
        "address": whale_address, "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": 100, "sort": "desc", "apikey": ETHERSCAN_API_KEY
    }
    try:
        response = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=15)
        data = response.json()
        if data.get("status") != "1":
            return []
        txs = data.get("result", [])
        cutoff = int((datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp())
        recent_txs = []
        for tx in txs:
            tx_ts = int(tx.get("timeStamp", 0))
            if tx_ts >= cutoff:
                value_eth = int(tx.get("value", 0)) / 1e18
                if value_eth >= MIN_TX_VALUE_ETH:
                    recent_txs.append({
                        "hash": tx.get("hash"), "from": tx.get("from", "").lower(),
                        "to": tx.get("to", "").lower(), "value_eth": value_eth, "timestamp": tx_ts,
                    })
        return recent_txs
    except Exception as e:
        print(f"Error fetching txs: {e}")
        return []

def analyze_whale_flow(whale: Dict, txs: List[Dict]) -> Dict:
    whale_addr = whale["address"].lower()
    inflow_eth, outflow_eth, inflow_count, outflow_count = 0.0, 0.0, 0, 0
    cex_interactions = []
    for tx in txs:
        if tx["to"] == whale_addr:
            inflow_eth += tx["value_eth"]
            inflow_count += 1
            if tx["from"] in CEX_ADDRESSES:
                cex_interactions.append(("inflow_from_cex", tx["value_eth"]))
        elif tx["from"] == whale_addr:
            outflow_eth += tx["value_eth"]
            outflow_count += 1
            if tx["to"] in CEX_ADDRESSES:
                cex_interactions.append(("outflow_to_cex", tx["value_eth"]))
    net_flow = inflow_eth - outflow_eth
    if abs(net_flow) < MIN_TX_VALUE_ETH:
        direction = "mixed"
    elif net_flow > 0:
        direction = "inflow"
    else:
        direction = "outflow"
    return {
        "whale": whale, "inflow_eth": inflow_eth, "outflow_eth": outflow_eth,
        "net_flow": net_flow, "net_direction": direction,
        "cex_interactions": cex_interactions, "total_txs": len(txs),
    }

# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_signal(flow_data: Dict) -> Dict:
    whale = flow_data["whale"]
    category = whale["category"]
    direction = flow_data["net_direction"]
    cat_signals = SIGNAL_MAP.get(category, {"inflow": {"signal": "NEUTRAL", "weight": 0.3}, "outflow": {"signal": "NEUTRAL", "weight": 0.3}})
    dir_signal = cat_signals.get(direction, {"signal": "NEUTRAL", "weight": 0.3})
    base_signal = dir_signal["signal"]
    base_weight = dir_signal["weight"]
    tx_count = flow_data["total_txs"]
    confidence_boost = 0.10 if tx_count >= 5 else 0.05 if tx_count >= 3 else 0.0
    if flow_data["cex_interactions"]:
        confidence_boost += 0.10
    confidence = min(0.95, base_weight + confidence_boost)
    if base_signal == "BULLISH":
        trade_signal = "LONG"
    elif base_signal == "BEARISH":
        trade_signal = "SHORT"
    else:
        trade_signal = "NEUTRAL"
    reasoning = f"{category} ({whale['sub_label']}) shows {direction} of {abs(flow_data['net_flow']):.2f} ETH. Signal: {base_signal}."
    return {"signal": trade_signal, "base": base_signal, "confidence": confidence, "reasoning": reasoning, "whale": whale, "flow": flow_data}

# ============================================================
# GEMINI VALIDATION
# ============================================================

def validate_with_gemini(signal_data: Dict, eth_price: float) -> Dict:
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        client = genai.Client()
        print("  [Gemini] Searching ETH market news...")
        grounding_config = GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], temperature=0.2)
        news_response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Latest Ethereum ETH news today. Any major whale movements or market events?",
            config=grounding_config
        )
        news_text = news_response.text[:500] if news_response.text else "No news"
        print(f"  [Gemini] News: {news_text[:150]}...")
        whale = signal_data["whale"]
        flow = signal_data["flow"]
        analysis_prompt = f"""Analyze this ETH whale activity:
WHALE: {whale['category']} ({whale['sub_label']}) - {flow['net_direction']} of {abs(flow['net_flow']):.2f} ETH
SIGNAL: {signal_data['signal']} at {signal_data['confidence']:.0%} confidence
ETH PRICE: ${eth_price:,.2f}
NEWS: {news_text}
Should we EXECUTE, WAIT, or SKIP? Respond in JSON: {{"decision": "EXECUTE|WAIT|SKIP", "signal": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "explanation"}}"""
        json_config = GenerateContentConfig(temperature=0.1, response_mime_type="application/json")
        analysis_response = client.models.generate_content(model="gemini-2.5-flash", contents=analysis_prompt, config=json_config)
        result = json.loads(analysis_response.text)
        result["grounding"] = True
        result["news"] = news_text
        return result
    except Exception as e:
        print(f"  [Gemini] Error: {e}")
        return {"decision": "EXECUTE" if signal_data["confidence"] >= 0.7 else "WAIT", "signal": signal_data["signal"], "confidence": signal_data["confidence"], "reasoning": f"Gemini unavailable. {signal_data['reasoning']}", "grounding": False}

# ============================================================
# WEEX TRADING
# ============================================================

def weex_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()

def weex_headers(method: str, path: str, body: str = "") -> Dict:
    ts = str(int(time.time() * 1000))
    return {"ACCESS-KEY": WEEX_API_KEY, "ACCESS-SIGN": weex_sign(ts, method, path, body), "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE, "Content-Type": "application/json"}

def get_eth_price() -> float:
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={TRADING_PAIR}", timeout=10)
        return float(r.json().get("last", 0))
    except:
        return 0.0

def get_balance() -> float:
    try:
        endpoint = "/capi/v2/account/accounts"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        return float(r.json().get("collateral", [{}])[0].get("amount", 0))
    except:
        return 0.0

def set_leverage(symbol: str, leverage: int) -> Dict:
    endpoint = "/capi/v2/account/leverage"
    body = json.dumps({"symbol": symbol, "leverage": leverage})
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()

def place_order(symbol: str, side: str, size: float) -> Dict:
    endpoint = "/capi/v2/order/placeOrder"
    order = {"symbol": symbol, "client_oid": f"smt_{int(time.time()*1000)}", "size": str(size), "type": side, "order_type": "0", "match_price": "1"}
    body = json.dumps(order)
    r = requests.post(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("POST", endpoint, body), data=body, timeout=15)
    return r.json()

# ============================================================
# MAIN PIPELINE
# ============================================================

def run_nightly_trade():
    print("=" * 70)
    print("SMT NIGHTLY TRADE - Full Pipeline")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    
    ai_log = {"run_id": f"smt_nightly_{datetime.now().strftime('%Y%m%d_%H%M%S')}", "timestamp": datetime.now(timezone.utc).isoformat(), "pipeline_version": "SMT-ETH-v1.0", "trading_pair": TRADING_PAIR, "steps": []}
    
    def log_step(step: str, data: Dict):
        ai_log["steps"].append({"step": step, "timestamp": datetime.now(timezone.utc).isoformat(), "data": data})
        print(f"\n[{step}]")
        for k, v in data.items():
            print(f"  {k}: {v}")
    
    eth_price = get_eth_price()
    balance = get_balance()
    log_step("MARKET_CHECK", {"eth_price": eth_price, "balance_usdt": balance})
    
    if eth_price == 0:
        log_step("ERROR", {"message": "Could not get ETH price"})
        save_log(ai_log)
        return
    
    print("\n[FETCHING WHALE TRANSACTIONS]")
    best_signal = None
    best_confidence = 0
    
    for whale in ETH_WHALES:
        print(f"  Checking {whale['sub_label']}...")
        time.sleep(0.3)
        txs = fetch_whale_transactions(whale["address"])
        if not txs:
            continue
        flow = analyze_whale_flow(whale, txs)
        if flow["net_direction"] == "mixed":
            continue
        signal = generate_signal(flow)
        if signal["signal"] != "NEUTRAL" and signal["confidence"] > best_confidence:
            best_signal = signal
            best_confidence = signal["confidence"]
            print(f"    -> Found signal: {signal['signal']} ({signal['confidence']:.0%})")
    
    if not best_signal:
        log_step("NO_SIGNAL", {"message": "No significant whale activity detected"})
        ai_log["final_decision"] = "NO_TRADE"
        save_log(ai_log)
        return
    
    log_step("SIGNAL_GENERATED", {"whale": best_signal["whale"]["sub_label"], "signal": best_signal["signal"], "confidence": f"{best_signal['confidence']:.0%}", "reasoning": best_signal["reasoning"]})
    
    print("\n[GEMINI VALIDATION]")
    validation = validate_with_gemini(best_signal, eth_price)
    log_step("GEMINI_VALIDATION", {"decision": validation["decision"], "signal": validation["signal"], "reasoning": validation["reasoning"][:200]})
    
    if validation["decision"] != "EXECUTE":
        log_step("TRADE_SKIPPED", {"reason": validation["reasoning"]})
        ai_log["final_decision"] = "NO_TRADE"
        save_log(ai_log)
        return
    
    set_leverage(TRADING_PAIR, MAX_LEVERAGE)
    size = round(TRADE_SIZE_USD / eth_price, 4)
    trade_signal = validation["signal"]
    order_type = "1" if trade_signal == "LONG" else "2"
    close_type = "3" if trade_signal == "LONG" else "4"
    
    log_step("OPENING_POSITION", {"signal": trade_signal, "size_eth": size, "size_usd": TRADE_SIZE_USD})
    open_result = place_order(TRADING_PAIR, order_type, size)
    log_step("ORDER_RESULT", {"result": str(open_result)[:200]})
    
    time.sleep(3)
    close_result = place_order(TRADING_PAIR, close_type, size)
    log_step("CLOSE_RESULT", {"result": str(close_result)[:200]})
    
    time.sleep(2)
    final_balance = get_balance()
    pnl = final_balance - balance
    log_step("TRADE_COMPLETE", {"initial_balance": balance, "final_balance": final_balance, "pnl": pnl})
    
    ai_log["final_decision"] = "EXECUTED"
    ai_log["pnl"] = pnl
    save_log(ai_log)
    
    print("\n" + "=" * 70)
    print(f"NIGHTLY TRADE COMPLETE - PnL: {pnl:.4f} USDT")
    print("=" * 70)

def save_log(log_data: Dict):
    os.makedirs("ai_logs", exist_ok=True)
    filename = f"ai_logs/nightly_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w") as f:
        json.dump(log_data, f, indent=2, default=str)
    print(f"\n[LOG SAVED] {filename}")

if __name__ == "__main__":
    run_nightly_trade()
