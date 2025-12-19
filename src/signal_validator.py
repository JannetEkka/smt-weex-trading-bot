"""
Gemini Signal Validator with Google Search Grounding
Validates whale trading signals against real-time market context
"""

import os
import json
from datetime import datetime
from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

# Environment setup for Vertex AI
os.environ.setdefault('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')
os.environ.setdefault('GOOGLE_CLOUD_LOCATION', 'us-central1')
os.environ.setdefault('GOOGLE_GENAI_USE_VERTEXAI', 'True')

# Signal mapping from whale classification
SIGNAL_MAP = {
    'Miner': {'sell': 'BEARISH', 'buy': 'NEUTRAL'},
    'Staker': {'unstake': 'BEARISH', 'stake': 'NEUTRAL'},
    'CEX_Wallet': {'inflow': 'BEARISH', 'outflow': 'BULLISH'},
    'Large_Holder': {'sell': 'BEARISH', 'buy': 'BULLISH'},
    'DeFi_Trader': {'high_volume': 'VOLATILITY', 'normal': 'NEUTRAL'},
    'Exploiter': {'any': 'AVOID'}
}


class GeminiSignalValidator:
    """Validates trading signals using Gemini 2.5 Flash with grounding"""
    
    def __init__(self):
        self.client = genai.Client()
        self.model = "gemini-2.5-flash"
        
        # Grounding config with Google Search
        self.grounding_config = GenerateContentConfig(
            tools=[
                Tool(google_search=GoogleSearch())
            ],
            temperature=0.1,  # Low for deterministic output
            response_mime_type="application/json"
        )
    
    def validate_signal(
        self,
        whale_address: str,
        classification: str,
        classification_confidence: float,
        action: str,  # 'sell', 'buy', 'stake', 'unstake', etc.
        token: str,
        amount_eth: float,
        destination: str = None
    ) -> dict:
        """
        Validate a whale trading signal against market context
        
        Returns:
            dict with decision (EXECUTE/WAIT/SKIP), confidence, reasoning
        """
        
        # Get base signal from classification
        base_signal = SIGNAL_MAP.get(classification, {}).get(action, 'NEUTRAL')
        
        # Skip Exploiter signals
        if classification == 'Exploiter':
            return {
                'decision': 'SKIP',
                'signal': 'AVOID',
                'confidence': 0.95,
                'reasoning': 'Exploiter wallet detected - avoiding any related tokens',
                'validated_at': datetime.utcnow().isoformat()
            }
        
        # Build prompt for Gemini
        prompt = f"""
You are a crypto trading signal validator. Analyze this whale activity and validate the trading signal.

WHALE ACTIVITY:
- Address: {whale_address[:10]}...{whale_address[-6:]}
- Classification: {classification} (confidence: {classification_confidence:.0%})
- Action: {action.upper()} {amount_eth:.2f} ETH worth of {token}
- Destination: {destination or 'Unknown'}
- Base Signal: {base_signal}

TASK:
1. Search for current {token} price, 24h volume, and recent news
2. Check if there are any major events affecting {token} today
3. Validate if the whale's action aligns with market sentiment

RESPOND IN JSON FORMAT:
{{
    "decision": "EXECUTE" or "WAIT" or "SKIP",
    "signal": "LONG" or "SHORT" or "NEUTRAL",
    "confidence": 0.0 to 1.0,
    "token_price_usd": current price,
    "market_sentiment": "BULLISH" or "BEARISH" or "NEUTRAL",
    "reasoning": "brief explanation",
    "risk_level": "LOW" or "MEDIUM" or "HIGH",
    "suggested_position_size_pct": 1 to 100
}}

Rules:
- If classification confidence < 60%, set decision to WAIT
- If conflicting signals found, set decision to SKIP
- For CEX inflow > 1000 ETH, high confidence SHORT signal
- For Miner sells during bull market, lower confidence
"""
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.grounding_config
            )
            
            # Parse JSON response
            result = json.loads(response.text)
            result['whale_address'] = whale_address
            result['classification'] = classification
            result['base_signal'] = base_signal
            result['validated_at'] = datetime.utcnow().isoformat()
            
            return result
            
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return {
                'decision': 'WAIT',
                'signal': base_signal,
                'confidence': 0.5,
                'reasoning': f'Validation inconclusive, using base signal: {base_signal}',
                'risk_level': 'HIGH',
                'validated_at': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'decision': 'SKIP',
                'signal': 'ERROR',
                'confidence': 0.0,
                'reasoning': f'Validation error: {str(e)}',
                'validated_at': datetime.utcnow().isoformat()
            }
    
    def validate_batch(self, signals: list) -> list:
        """Validate multiple signals"""
        results = []
        for signal in signals:
            result = self.validate_signal(**signal)
            results.append(result)
        return results
    
    def get_market_context(self, token: str) -> dict:
        """Get current market context for a token using Gemini grounding"""
        
        prompt = f"""
Search for current market data for {token} cryptocurrency.

Return JSON with:
{{
    "token": "{token}",
    "price_usd": current price,
    "price_change_24h_pct": percentage,
    "volume_24h_usd": volume,
    "market_cap_usd": market cap,
    "trending_news": ["headline1", "headline2"],
    "overall_sentiment": "BULLISH" or "BEARISH" or "NEUTRAL"
}}
"""
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.grounding_config
            )
            return json.loads(response.text)
        except:
            return {"token": token, "error": "Failed to fetch market context"}


def create_trading_signal(
    whale_address: str,
    classification: str,
    classification_confidence: float,
    tx_type: str,
    value_eth: float,
    token: str = "ETH",
    counterparty: str = None
) -> dict:
    """
    Create a trading signal from whale transaction
    
    Args:
        whale_address: Whale wallet address
        classification: CatBoost model classification
        classification_confidence: Model confidence score
        tx_type: 'incoming' or 'outgoing'
        value_eth: Transaction value in ETH
        token: Token symbol
        counterparty: Destination/source address
    
    Returns:
        Trading signal dict ready for validation
    """
    
    # Determine action based on classification and tx direction
    if classification == 'CEX_Wallet':
        action = 'inflow' if tx_type == 'incoming' else 'outflow'
    elif classification == 'Staker':
        action = 'unstake' if tx_type == 'outgoing' else 'stake'
    elif classification == 'Miner':
        action = 'sell' if tx_type == 'outgoing' else 'receive'
    else:
        action = 'sell' if tx_type == 'outgoing' else 'buy'
    
    return {
        'whale_address': whale_address,
        'classification': classification,
        'classification_confidence': classification_confidence,
        'action': action,
        'token': token,
        'amount_eth': value_eth,
        'destination': counterparty
    }


# Example usage
if __name__ == "__main__":
    validator = GeminiSignalValidator()
    
    # Example: Miner selling 500 ETH
    signal = create_trading_signal(
        whale_address="0x1234567890abcdef1234567890abcdef12345678",
        classification="Miner",
        classification_confidence=0.82,
        tx_type="outgoing",
        value_eth=500.0,
        token="ETH",
        counterparty="0xbinance..."
    )
    
    print("Signal to validate:")
    print(json.dumps(signal, indent=2))
    
    result = validator.validate_signal(**signal)
    print("\nValidation result:")
    print(json.dumps(result, indent=2))