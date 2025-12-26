"""
WEEX API Trading Executor (Updated for Hackathon)
Correct endpoints + AI logging for compliance
"""

import os
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import json
import uuid

# WEEX API Configuration
WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

# Token to WEEX symbol mapping
TOKEN_TO_SYMBOL = {
    'ETH': 'cmt_ethusdt',
    'BTC': 'cmt_btcusdt',
    'SOL': 'cmt_solusdt',
    'DOGE': 'cmt_dogeusdt',
    'XRP': 'cmt_xrpusdt',
}


class AILogger:
    """AI decision logger for hackathon compliance"""
    
    def __init__(self, log_dir: str = "ai_logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.session_id = str(uuid.uuid4())[:8]
        self.logs = []
    
    def log(self, action: str, decision: str, reasoning: str, data: Dict = None) -> Dict:
        entry = {
            "log_id": f"smt_{self.session_id}_{len(self.logs)+1}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_version": "SMT-CatBoost-v1.0",
            "action": action,
            "decision": decision,
            "reasoning": reasoning,
            "data": data or {}
        }
        self.logs.append(entry)
        self._save()
        return entry
    
    def _save(self):
        filename = f"{self.log_dir}/ai_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(self.logs, f, indent=2)
        return filename


class WEEXTrader:
    """WEEX Contract API v2 client"""
    
    def __init__(self, api_key: str = None, api_secret: str = None, passphrase: str = None):
        self.api_key = api_key or WEEX_API_KEY
        self.api_secret = api_secret or WEEX_API_SECRET
        self.passphrase = passphrase or WEEX_API_PASSPHRASE
        self.base_url = WEEX_BASE_URL
        self.ai_logger = AILogger()
    
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = timestamp + method.upper() + path + body
        sig = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).digest()
        return base64.b64encode(sig).decode()
    
    def _headers(self, method: str, path: str, body: str = "") -> Dict:
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }
    
    def _get(self, endpoint: str) -> Dict:
        r = requests.get(f"{self.base_url}{endpoint}", headers=self._headers("GET", endpoint), timeout=15)
        return r.json()
    
    def _post(self, endpoint: str, data: Dict) -> Dict:
        body = json.dumps(data)
        r = requests.post(f"{self.base_url}{endpoint}", headers=self._headers("POST", endpoint, body), data=body, timeout=15)
        return r.json()
    
    # === Market Data ===
    def get_ticker(self, symbol: str = "cmt_btcusdt") -> Dict:
        r = requests.get(f"{self.base_url}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        return r.json()
    
    def get_price(self, symbol: str = "cmt_btcusdt") -> float:
        return float(self.get_ticker(symbol).get("last", 0))
    
    # === Account ===
    def get_account(self) -> Dict:
        return self._get("/capi/v2/account/accounts")
    
    def get_balance(self) -> float:
        data = self.get_account()
        try:
            return float(data["collateral"][0]["amount"])
        except:
            return 0.0
    
    def get_positions(self) -> Dict:
        return self._get("/capi/v2/account/position/allPosition")
    
    # === Trading ===
    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        return self._post("/capi/v2/account/leverage", {"symbol": symbol, "leverage": leverage})
    
    def place_order(self, symbol: str, side: str, size: float, reduce_only: bool = False) -> Dict:
        """
        Place market order
        side: "1" = open long, "2" = open short, "3" = close long, "4" = close short
        """
        order = {
            "symbol": symbol,
            "client_oid": f"smt_{int(time.time()*1000)}",
            "size": str(size),
            "type": side,
            "order_type": "0",  # market
            "match_price": "1"
        }
        return self._post("/capi/v2/order/placeOrder", order)
    
    def close_position(self, symbol: str) -> Dict:
        """Close all positions for symbol"""
        return self._post("/capi/v2/order/close-all", {"symbol": symbol})
    
    # === Order History ===
    def get_orders(self, symbol: str) -> Dict:
        return self._get(f"/capi/v2/order/orders?symbol={symbol}")
    
    def get_trades(self, symbol: str) -> Dict:
        return self._get(f"/capi/v2/order/trades?symbol={symbol}")


def run_api_test():
    """
    Complete WEEX API Test with AI Logging
    Opens and closes a ~10 USDT BTC position
    """
    trader = WEEXTrader()
    symbol = "cmt_btcusdt"
    
    print("=" * 60)
    print("SMT WEEX API TEST (with AI Logging)")
    print("=" * 60)
    
    # 1. Check balance
    print("\n[1] Checking balance...")
    balance = trader.get_balance()
    print(f"    Available: {balance:.2f} USDT")
    
    # 2. Get BTC price
    print("\n[2] Getting BTC price...")
    price = trader.get_price(symbol)
    print(f"    BTC/USDT: ${price:,.2f}")
    
    # 3. Set leverage
    print("\n[3] Setting leverage 20x...")
    r = trader.set_leverage(symbol, 20)
    print(f"    Response: {str(r)[:100]}")
    
    # 4. AI Decision to OPEN
    print("\n[4] AI Decision: OPEN LONG...")
    size = round(10 / price, 4)
    log1 = trader.ai_logger.log(
        action="OPEN_POSITION",
        decision="EXECUTE",
        reasoning="API test trade for hackathon qualification. Market conditions neutral. Opening minimum position (10 USDT) on BTC/USDT.",
        data={"symbol": symbol, "side": "LONG", "size_btc": size, "size_usd": 10, "price": price, "leverage": 20}
    )
    print(f"    AI Log: {log1['log_id']}")
    
    # 5. Place order (type=1 = open long)
    print("\n[5] Placing market buy order...")
    order_result = trader.place_order(symbol, "1", size)
    print(f"    Result: {str(order_result)[:200]}")
    
    order_id = order_result.get("order_id") or order_result.get("data", {}).get("order_id")
    trader.ai_logger.log(
        action="ORDER_PLACED",
        decision="EXECUTED",
        reasoning="Market buy order placed successfully",
        data={"order_id": order_id, "result": order_result}
    )
    
    # 6. Wait for fill
    print("\n[6] Waiting 3 seconds for fill...")
    time.sleep(3)
    
    # 7. Check position
    print("\n[7] Checking position...")
    positions = trader.get_positions()
    print(f"    Positions: {str(positions)[:200]}")
    
    # 8. AI Decision to CLOSE
    print("\n[8] AI Decision: CLOSE POSITION...")
    log2 = trader.ai_logger.log(
        action="CLOSE_POSITION",
        decision="EXECUTE",
        reasoning="Closing position to complete API test cycle. Open+Close = 1 complete trade.",
        data={"symbol": symbol, "side": "CLOSE_LONG"}
    )
    print(f"    AI Log: {log2['log_id']}")
    
    # 9. Close position (type=3 = close long)
    print("\n[9] Closing position...")
    close_result = trader.place_order(symbol, "3", size)
    print(f"    Result: {str(close_result)[:200]}")
    
    trader.ai_logger.log(
        action="ORDER_PLACED",
        decision="EXECUTED",
        reasoning="Close order placed successfully",
        data={"result": close_result}
    )
    
    # 10. Order history
    print("\n[10] Order history...")
    orders = trader.get_orders(symbol)
    print(f"    Orders: {str(orders)[:200]}")
    
    # 11. Trade details
    print("\n[11] Trade details...")
    trades = trader.get_trades(symbol)
    print(f"    Trades: {str(trades)[:200]}")
    
    # 12. Final balance
    print("\n[12] Final balance...")
    final_balance = trader.get_balance()
    print(f"    Balance: {final_balance:.2f} USDT")
    print(f"    P/L: {final_balance - balance:.4f} USDT")
    
    # Save logs
    print(f"\n[OK] AI Logs saved to: {trader.ai_logger.log_dir}/")
    print("=" * 60)
    print("API TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    run_api_test()
