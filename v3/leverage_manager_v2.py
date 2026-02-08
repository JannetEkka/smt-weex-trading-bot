"""
Smart Leverage Manager V2 - FINALS READY
Even more conservative for competition safety
"""
import math

class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 5
        self.MAX_LEVERAGE = 12  # Reduced from 15x
        self.MAX_POSITION_PCT = 0.20  # Max 20% balance per position (was 25%)
        self.MIN_LIQUIDATION_DISTANCE = 15  # Must be 15% away minimum
        
    def calculate_safe_leverage(self, pair_tier: int, volatility: float, regime: str) -> int:
        """Dynamic leverage - more conservative"""
        tier_leverage = {
            1: 10,  # BTC, ETH, BNB, LTC (was 12)
            2: 8,   # SOL (was 10)
            3: 6    # DOGE, XRP, ADA (was 8)
        }
        
        base = tier_leverage.get(pair_tier, 8)
        
        # Reduce in high volatility
        if volatility > 3.0:
            base -= 3
        elif volatility > 2.0:
            base -= 2
        
        # Reduce in uncertain regimes
        if regime == "NEUTRAL":
            base -= 2
        elif regime == "BEARISH":
            base -= 1  # Also reduce slightly in bearish
        
        return max(self.MIN_LEVERAGE, min(base, self.MAX_LEVERAGE))
    
    def calculate_position_size(self, balance: float, leverage: int, 
                                price: float, max_positions: int = 5) -> float:
        """Conservative position sizing"""
        max_margin = balance * self.MAX_POSITION_PCT
        adjusted_margin = max_margin / math.sqrt(max_positions)
        position_value = adjusted_margin * leverage
        position_size = position_value / price
        return position_size
    
    def check_liquidation_distance(self, entry_price: float, current_price: float,
                                   side: str, leverage: int) -> dict:
        """Calculate distance to liquidation"""
        liq_pct = 90 / leverage
        
        if side == "LONG":
            liq_price = entry_price * (1 - liq_pct/100)
            distance_pct = ((current_price - liq_price) / current_price) * 100
        else:
            liq_price = entry_price * (1 + liq_pct/100)
            distance_pct = ((liq_price - current_price) / current_price) * 100
        
        return {
            "liquidation_price": liq_price,
            "distance_pct": distance_pct,
            "safe": distance_pct >= self.MIN_LIQUIDATION_DISTANCE
        }

# Test with finals settings
if __name__ == "__main__":
    manager = LeverageManager()
    
    print("FINALS-READY LEVERAGE SYSTEM")
    print("="*60)
    
    # BTC in current market
    lev = manager.calculate_safe_leverage(pair_tier=1, volatility=1.5, regime="BULLISH")
    print(f"BTC (Tier 1, BULLISH): {lev}x leverage")
    
    balance = 5743
    btc_price = 71000
    
    size = manager.calculate_position_size(balance, lev, btc_price)
    margin = (size * btc_price) / lev
    
    print(f"\nSafe position (Balance: ${balance}):")
    print(f"  Size: {size:.4f} BTC")
    print(f"  Margin: ${margin:.2f} ({margin/balance*100:.1f}% of balance)")
    
    # Check safety
    liq = manager.check_liquidation_distance(71000, 70000, "LONG", lev)
    print(f"\nLiquidation safety:")
    print(f"  Liq price: ${liq['liquidation_price']:.2f}")
    print(f"  Distance: {liq['distance_pct']:.1f}%")
    print(f"  SAFE: {'✅ YES' if liq['safe'] else '❌ NO'}")
    
    # Compare all tiers
    print(f"\n{'Pair':<10} {'Regime':<10} {'Volatility':<12} {'Leverage':<10}")
    print("-"*60)
    for pair, tier in [("BTC", 1), ("SOL", 2), ("DOGE", 3)]:
        for regime in ["BULLISH", "NEUTRAL", "BEARISH"]:
            for vol in [1.5, 3.5]:
                lev = manager.calculate_safe_leverage(tier, vol, regime)
                vol_label = "Low" if vol < 2 else "High"
                print(f"{pair:<10} {regime:<10} {vol_label:<12} {lev}x")
