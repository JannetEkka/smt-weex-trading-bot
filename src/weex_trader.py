"""
WEEX API Trading Executor
Executes validated trading signals on WEEX exchange
"""

import os
import time
import hmac
import hashlib
import requests
from datetime import datetime
from typing import Optional
import json

# WEEX API Configuration
WEEX_API_KEY = os.getenv('WEEX_API_KEY', '')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', '')
WEEX_BASE_URL = os.getenv('WEEX_BASE_URL', 'https://api.weex.com')

# Trading pairs mapping
TOKEN_TO_PAIR = {
    'ETH': 'ETHUSDT',
    'BTC': 'BTCUSDT',
    'SOL': 'SOLUSDT',
    'DOGE': 'DOGEUSDT',
    'XRP': 'XRPUSDT',
    'ADA': 'ADAUSDT',
    'BNB': 'BNBUSDT',
    'LTC': 'LTCUSDT',
}


class WEEXTrader:
    """WEEX exchange trading client"""
    
    def __init__(
        self,
        api_key: str = WEEX_API_KEY,
        api_secret: str = WEEX_API_SECRET,
        passphrase: str = WEEX_API_PASSPHRASE,
        base_url: str = WEEX_BASE_URL,
        testnet: bool = True
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url
        self.testnet = testnet
        
        if not all([api_key, api_secret]):
            print("Warning: WEEX API credentials not set")
    
    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        """Generate HMAC signature for WEEX API"""
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_headers(self, method: str, path: str, body: str = '') -> dict:
        """Get authenticated headers for WEEX API"""
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, path, body)
        
        return {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': signature,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
    
    def _request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        """Make authenticated request to WEEX API"""
        url = self.base_url + path
        body = json.dumps(data) if data else ''
        headers = self._get_headers(method, path, body)
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, data=body, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            return {'error': str(e), 'success': False}
    
    def get_account_balance(self) -> dict:
        """Get account balance"""
        return self._request('GET', '/api/v1/account/balance')
    
    def get_ticker(self, symbol: str) -> dict:
        """Get current ticker price"""
        return self._request('GET', f'/api/v1/market/ticker', params={'symbol': symbol})
    
    def get_open_positions(self) -> dict:
        """Get open positions"""
        return self._request('GET', '/api/v1/position/list')
    
    def place_order(
        self,
        symbol: str,
        side: str,  # 'buy' or 'sell'
        order_type: str,  # 'limit' or 'market'
        quantity: float,
        price: float = None,
        leverage: int = 1,
        reduce_only: bool = False
    ) -> dict:
        """
        Place a trading order
        
        Args:
            symbol: Trading pair (e.g., 'ETHUSDT')
            side: 'buy' for long, 'sell' for short
            order_type: 'limit' or 'market'
            quantity: Order quantity
            price: Limit price (required for limit orders)
            leverage: Leverage multiplier
            reduce_only: Close position only
        
        Returns:
            Order response dict
        """
        data = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'quantity': str(quantity),
            'leverage': str(leverage),
            'reduceOnly': reduce_only
        }
        
        if order_type == 'limit' and price:
            data['price'] = str(price)
        
        return self._request('POST', '/api/v1/order/place', data=data)
    
    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order"""
        return self._request('DELETE', f'/api/v1/order/cancel', 
                           data={'orderId': order_id, 'symbol': symbol})
    
    def execute_signal(
        self,
        validated_signal: dict,
        max_position_usd: float = 100.0,
        leverage: int = 1
    ) -> dict:
        """
        Execute a validated trading signal
        
        Args:
            validated_signal: Signal dict from GeminiSignalValidator
            max_position_usd: Maximum position size in USD
            leverage: Trading leverage
        
        Returns:
            Execution result dict
        """
        # Check if signal should be executed
        if validated_signal.get('decision') != 'EXECUTE':
            return {
                'executed': False,
                'reason': f"Signal decision was {validated_signal.get('decision')}",
                'signal': validated_signal
            }
        
        # Get trading parameters
        signal_type = validated_signal.get('signal')  # LONG or SHORT
        confidence = validated_signal.get('confidence', 0)
        token = validated_signal.get('token', 'ETH')
        position_pct = validated_signal.get('suggested_position_size_pct', 10)
        
        # Map token to trading pair
        symbol = TOKEN_TO_PAIR.get(token, 'ETHUSDT')
        
        # Calculate position size
        position_size_usd = max_position_usd * (position_pct / 100) * confidence
        
        # Get current price
        ticker = self.get_ticker(symbol)
        if 'error' in ticker:
            return {'executed': False, 'reason': f"Failed to get price: {ticker['error']}"}
        
        current_price = float(ticker.get('data', {}).get('lastPrice', 0))
        if current_price == 0:
            return {'executed': False, 'reason': 'Could not fetch current price'}
        
        # Calculate quantity
        quantity = position_size_usd / current_price
        
        # Determine order side
        side = 'buy' if signal_type == 'LONG' else 'sell'
        
        # Place market order
        order_result = self.place_order(
            symbol=symbol,
            side=side,
            order_type='market',
            quantity=round(quantity, 6),
            leverage=leverage
        )
        
        return {
            'executed': True,
            'order': order_result,
            'details': {
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'position_usd': position_size_usd,
                'price': current_price,
                'leverage': leverage,
                'signal_confidence': confidence
            },
            'timestamp': datetime.utcnow().isoformat()
        }


class SignalProcessor:
    """Process whale signals end-to-end"""
    
    def __init__(self, validator, trader):
        self.validator = validator
        self.trader = trader
        self.signal_history = []
    
    def process_whale_transaction(
        self,
        whale_address: str,
        classification: str,
        classification_confidence: float,
        tx_type: str,
        value_eth: float,
        token: str = "ETH",
        counterparty: str = None,
        auto_execute: bool = False,
        max_position_usd: float = 100.0
    ) -> dict:
        """
        Full pipeline: Create signal -> Validate -> Execute
        
        Args:
            whale_address: Whale wallet address
            classification: CatBoost classification
            classification_confidence: Model confidence
            tx_type: 'incoming' or 'outgoing'
            value_eth: Transaction value
            token: Token symbol
            counterparty: Counterparty address
            auto_execute: Whether to auto-execute validated signals
            max_position_usd: Max position size
        
        Returns:
            Complete processing result
        """
        from signal_validator import create_trading_signal
        
        # Step 1: Create signal
        signal = create_trading_signal(
            whale_address=whale_address,
            classification=classification,
            classification_confidence=classification_confidence,
            tx_type=tx_type,
            value_eth=value_eth,
            token=token,
            counterparty=counterparty
        )
        
        # Step 2: Validate with Gemini
        validated = self.validator.validate_signal(**signal)
        
        result = {
            'signal': signal,
            'validation': validated,
            'execution': None
        }
        
        # Step 3: Execute if auto_execute and decision is EXECUTE
        if auto_execute and validated.get('decision') == 'EXECUTE':
            validated['token'] = token  # Ensure token is in validated signal
            execution = self.trader.execute_signal(
                validated_signal=validated,
                max_position_usd=max_position_usd
            )
            result['execution'] = execution
        
        # Store in history
        self.signal_history.append(result)
        
        return result


# Example usage
if __name__ == "__main__":
    # Initialize trader
    trader = WEEXTrader(testnet=True)
    
    # Test connection
    print("Testing WEEX API connection...")
    balance = trader.get_account_balance()
    print(f"Account balance: {json.dumps(balance, indent=2)}")
    
    # Example: Execute a validated signal
    validated_signal = {
        'decision': 'EXECUTE',
        'signal': 'SHORT',
        'confidence': 0.75,
        'token': 'ETH',
        'suggested_position_size_pct': 20,
        'reasoning': 'Miner selling 500 ETH to Binance'
    }
    
    print("\nExample signal execution (dry run):")
    print(json.dumps(validated_signal, indent=2))
    
    # Note: Uncomment to actually execute
    # result = trader.execute_signal(validated_signal, max_position_usd=100)
    # print(f"Execution result: {json.dumps(result, indent=2)}")