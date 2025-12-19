"""
SMT WEEX Trading Bot - Main Orchestrator
Monitors whale transactions and executes validated trading signals

Pipeline:
1. Monitor whale transactions (Etherscan V2 API)
2. Classify whale behavior (CatBoost model)
3. Create trading signals
4. Validate with Gemini (Google Search grounding)
5. Execute on WEEX
"""

import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import requests

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.classifier import WhaleClassifier
from src.signal_validator import GeminiSignalValidator, create_trading_signal
from src.weex_trader import WEEXTrader, SignalProcessor

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('smt_bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    'etherscan_api_key': os.getenv('ETHERSCAN_API_KEY'),
    'etherscan_base_url': 'https://api.etherscan.io/v2/api',
    'chain_id': 1,
    'poll_interval_seconds': 60,
    'min_tx_value_eth': 10,
    'auto_execute': False,  # Set True for live trading
    'max_position_usd': 100,
    'leverage': 1,
}

# Known CEX addresses for flow detection
CEX_ADDRESSES = {
    '0x28c6c06298d514db089934071355e5743bf21d60': 'Binance',
    '0x21a31ee1afc51d94c2efccaa2092ad1028285549': 'Binance',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d': 'Binance',
    '0x56eddb7aa87536c09ccc2793473599fd21a8b17f': 'Binance',
    '0xf977814e90da44bfa03b6295a0616a897441acec': 'Binance',
    '0x3cc936b795a188f0e246cbb2d74c5bd190aecf18': 'MEXC',
}


class SMTBot:
    """Smart Money Tracker Trading Bot"""
    
    def __init__(self, config: dict = CONFIG):
        self.config = config
        
        # Initialize components
        logger.info("Initializing SMT Bot components...")
        
        self.classifier = WhaleClassifier()
        logger.info(f"Classifier loaded: {len(self.classifier.watched_whales)} watched whales")
        
        self.validator = GeminiSignalValidator()
        logger.info("Gemini validator initialized")
        
        self.trader = WEEXTrader(testnet=True)
        logger.info("WEEX trader initialized")
        
        self.processor = SignalProcessor(self.validator, self.trader)
        
        # State
        self.last_block = None
        self.processed_txs = set()
        self.signals_today = []
        
    def fetch_recent_whale_txs(self, whale_address: str, limit: int = 10) -> list:
        """Fetch recent transactions for a whale"""
        params = {
            'chainid': self.config['chain_id'],
            'module': 'account',
            'action': 'txlist',
            'address': whale_address,
            'startblock': 0,
            'endblock': 99999999,
            'page': 1,
            'offset': limit,
            'sort': 'desc',
            'apikey': self.config['etherscan_api_key']
        }
        
        try:
            response = requests.get(
                self.config['etherscan_base_url'],
                params=params,
                timeout=10
            )
            data = response.json()
            
            if data.get('status') == '1':
                return data.get('result', [])
            return []
        except Exception as e:
            logger.error(f"Error fetching txs for {whale_address}: {e}")
            return []
    
    def detect_cex_flow(self, tx: dict, whale_address: str) -> Optional[str]:
        """Detect if transaction involves a CEX"""
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()
        whale_addr = whale_address.lower()
        
        # Whale sending to CEX = potential sell
        if from_addr == whale_addr and to_addr in CEX_ADDRESSES:
            return f"outflow_to_{CEX_ADDRESSES[to_addr]}"
        
        # CEX sending to whale = potential buy
        if from_addr in CEX_ADDRESSES and to_addr == whale_addr:
            return f"inflow_from_{CEX_ADDRESSES[from_addr]}"
        
        return None
    
    def process_transaction(self, tx: dict, whale_address: str) -> Optional[dict]:
        """Process a single whale transaction"""
        tx_hash = tx.get('hash')
        
        # Skip if already processed
        if tx_hash in self.processed_txs:
            return None
        
        self.processed_txs.add(tx_hash)
        
        # Parse transaction
        value_wei = int(tx.get('value', 0))
        value_eth = value_wei / 1e18
        
        # Skip small transactions
        if value_eth < self.config['min_tx_value_eth']:
            return None
        
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()
        whale_addr = whale_address.lower()
        
        # Determine direction
        tx_type = 'outgoing' if from_addr == whale_addr else 'incoming'
        counterparty = to_addr if tx_type == 'outgoing' else from_addr
        
        # Detect CEX flow
        cex_flow = self.detect_cex_flow(tx, whale_address)
        
        # Classify whale
        classification, confidence = self.classifier.classify(whale_address)
        
        logger.info(
            f"TX detected: {whale_address[:10]}... "
            f"[{classification}:{confidence:.0%}] "
            f"{tx_type} {value_eth:.2f} ETH"
            f"{f' ({cex_flow})' if cex_flow else ''}"
        )
        
        # Create and validate signal
        result = self.processor.process_whale_transaction(
            whale_address=whale_address,
            classification=classification,
            classification_confidence=confidence,
            tx_type=tx_type,
            value_eth=value_eth,
            token='ETH',
            counterparty=counterparty,
            auto_execute=self.config['auto_execute'],
            max_position_usd=self.config['max_position_usd']
        )
        
        # Log result
        decision = result['validation'].get('decision', 'UNKNOWN')
        signal = result['validation'].get('signal', 'UNKNOWN')
        
        logger.info(
            f"Signal: {signal} | Decision: {decision} | "
            f"Confidence: {result['validation'].get('confidence', 0):.0%}"
        )
        
        if result.get('execution', {}).get('executed'):
            logger.info(f"EXECUTED: {result['execution']['details']}")
        
        self.signals_today.append(result)
        return result
    
    def monitor_whales(self, whale_addresses: list):
        """Monitor a list of whale addresses for new transactions"""
        logger.info(f"Starting whale monitoring for {len(whale_addresses)} addresses...")
        
        while True:
            try:
                for whale in whale_addresses:
                    txs = self.fetch_recent_whale_txs(whale, limit=5)
                    
                    for tx in txs:
                        # Only process recent txs (last 10 minutes)
                        tx_time = int(tx.get('timeStamp', 0))
                        if tx_time > time.time() - 600:
                            self.process_transaction(tx, whale)
                    
                    time.sleep(0.25)  # Rate limit
                
                logger.info(
                    f"Cycle complete. Signals today: {len(self.signals_today)} | "
                    f"Sleeping {self.config['poll_interval_seconds']}s..."
                )
                time.sleep(self.config['poll_interval_seconds'])
                
            except KeyboardInterrupt:
                logger.info("Stopping whale monitor...")
                break
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(10)
    
    def run_single_analysis(self, whale_address: str) -> dict:
        """Run single whale analysis (for testing)"""
        logger.info(f"Analyzing whale: {whale_address}")
        
        # Get classification
        classification, confidence = self.classifier.classify(whale_address)
        logger.info(f"Classification: {classification} ({confidence:.0%})")
        
        # Fetch recent transactions
        txs = self.fetch_recent_whale_txs(whale_address, limit=10)
        logger.info(f"Found {len(txs)} recent transactions")
        
        results = []
        for tx in txs[:3]:  # Process top 3
            result = self.process_transaction(tx, whale_address)
            if result:
                results.append(result)
        
        return {
            'whale': whale_address,
            'classification': classification,
            'confidence': confidence,
            'signals': results
        }
    
    def get_daily_summary(self) -> dict:
        """Get summary of today's signals"""
        executed = [s for s in self.signals_today if s.get('execution', {}).get('executed')]
        
        return {
            'date': datetime.utcnow().date().isoformat(),
            'total_signals': len(self.signals_today),
            'executed_trades': len(executed),
            'decisions': {
                'EXECUTE': len([s for s in self.signals_today if s['validation'].get('decision') == 'EXECUTE']),
                'WAIT': len([s for s in self.signals_today if s['validation'].get('decision') == 'WAIT']),
                'SKIP': len([s for s in self.signals_today if s['validation'].get('decision') == 'SKIP']),
            }
        }


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='SMT WEEX Trading Bot')
    parser.add_argument('--mode', choices=['monitor', 'analyze', 'test'], default='test')
    parser.add_argument('--whale', type=str, help='Whale address to analyze')
    parser.add_argument('--auto-execute', action='store_true', help='Enable auto execution')
    args = parser.parse_args()
    
    # Update config
    if args.auto_execute:
        CONFIG['auto_execute'] = True
        logger.warning("AUTO-EXECUTE ENABLED - Real trades will be placed!")
    
    # Initialize bot
    bot = SMTBot(CONFIG)
    
    if args.mode == 'test':
        # Test mode - analyze a sample whale
        test_whale = args.whale or '0x28c6c06298d514db089934071355e5743bf21d60'  # Binance
        result = bot.run_single_analysis(test_whale)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.mode == 'analyze':
        if not args.whale:
            print("Error: --whale address required for analyze mode")
            sys.exit(1)
        result = bot.run_single_analysis(args.whale)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.mode == 'monitor':
        # Monitor top whales
        top_whales = bot.classifier.get_top_whales(n=50)
        bot.monitor_whales(top_whales)
    
    # Print summary
    print("\n" + "="*50)
    print("Daily Summary:")
    print(json.dumps(bot.get_daily_summary(), indent=2))


if __name__ == "__main__":
    main()