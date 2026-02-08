"""
Smart Leverage Manager - Prevents liquidation and optimizes position sizing
"""
import math

class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 5
        self.MAX_LEVERAGE = 15  # Reduced from 20x
        self.MAX_POSITION_PCT = 0.25  # Max 25% balance per position
        self.LIQUIDATION_BUFFER = 0.15  # Stay 15% away from liquidation
        
    def calculate_safe_leverage(self, pair_tier: int, volatility: float, regime: str) -> int:
        """
        Dynamic leverage based on:
        - Tier (1=stable, 3=volatile)
        - Market volatility
        - Current regime
        """
        # Base leverage by tier
        tier_leverage = {
            1: 12,  # BTC, ETH, BNB, LTC - most stable
            2: 10,  # SOL - mid volatility
            3: 8    # DOGE, XRP, ADA - high volatility
        }
        
        base = tier_leverage.get(pair_tier, 10)
        
        # Reduce in high volatility
        if volatility > 3.0:  # >3% daily moves
            base -= 3
        elif volatility > 2.0:
            base -= 2
        
        # Reduce in uncertain regimes
        if regime == "NEUTRAL":
            base -= 2
        
        return max(self.MIN_LEVERAGE, min(base, self.MAX_LEVERAGE))
    
    def calculate_position_size(self, balance: float, leverage: int, 
                                price: float, max_positions: int = 5) -> float:
        """
        Calculate safe position size that:
        1. Uses max 25% of balance
        2. Allows for max_positions simultaneously
        3. Keeps liquidation risk low
        """
        # Max margin per position
        max_margin = balance * self.MAX_POSITION_PCT
        
        # Adjust for concurrent positions
        adjusted_margin = max_margin / math.sqrt(max_positions)
        
        # Position size in units
        position_value = adjusted_margin * leverage
        position_size = position_value / price
        
        return position_size
    
    def check_liquidation_distance(self, entry_price: float, current_price: float,
                                   side: str, leverage: int) -> dict:
        """
        Calculate distance to liquidation
        """
        # Liquidation happens at ~90% margin loss
        liq_pct = 90 / leverage
        
        if side == "LONG":
            liq_price = entry_price * (1 - liq_pct/100)
            distance_pct = ((current_price - liq_price) / current_price) * 100
        else:  # SHORT
            liq_price = entry_price * (1 + liq_pct/100)
            distance_pct = ((liq_price - current_price) / current_price) * 100
        
        return {
            "liquidation_price": liq_price,
            "distance_pct": distance_pct,
            "safe": distance_pct > 10  # >10% away is safe
        }

# Example usage
if __name__ == "__main__":
    manager = LeverageManager()
    
    # Test scenarios
    print("SMART LEVERAGE EXAMPLES")
    print("="*60)
    
    # Tier 1 (BTC) in BULLISH, low volatility
    lev = manager.calculate_safe_leverage(pair_tier=1, volatility=1.5, regime="BULLISH")
    print(f"BTC (Tier 1, low vol, BULLISH): {lev}x leverage")
    
    # Tier 3 (DOGE) in NEUTRAL, high volatility  
    lev = manager.calculate_safe_leverage(pair_tier=3, volatility=4.0, regime="NEUTRAL")
    print(f"DOGE (Tier 3, high vol, NEUTRAL): {lev}x leverage")
    
    # Position sizing
    balance = 5743
    btc_price = 71000
    lev = 12
    
    size = manager.calculate_position_size(balance, lev, btc_price, max_positions=5)
    margin = (size * btc_price) / lev
    
    print(f"\nPosition sizing (Balance: ${balance}):")
    print(f"  BTC @ ${btc_price} with {lev}x")
    print(f"  Size: {size:.4f} BTC")
    print(f"  Margin: ${margin:.2f} ({margin/balance*100:.1f}% of balance)")
    
    # Liquidation check
    liq = manager.check_liquidation_distance(71000, 70000, "LONG", 12)
    print(f"\nLiquidation safety:")
    print(f"  Liq price: ${liq['liquidation_price']:.2f}")
    print(f"  Distance: {liq['distance_pct']:.1f}%")
    print(f"  Safe: {liq['safe']}")
