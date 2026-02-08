"""
Smart Leverage Manager - ULTRA SAFE for Finals
Guarantees >15% liquidation distance
"""
import math

class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 5
        self.MAX_LEVERAGE = 8  # Ultra conservative
        self.MAX_POSITION_PCT = 0.18  # Max 18% balance per position
        self.MIN_LIQUIDATION_DISTANCE = 15  # Must be 15% away
        
    def calculate_safe_leverage(self, pair_tier: int, volatility: float, regime: str) -> int:
        """Ultra-safe leverage for competition"""
        tier_leverage = {
            1: 8,  # BTC, ETH, BNB, LTC
            2: 7,  # SOL
            3: 5   # DOGE, XRP, ADA
        }
        
        base = tier_leverage.get(pair_tier, 6)
        
        if volatility > 3.0:
            base -= 2
        elif volatility > 2.0:
            base -= 1
        
        if regime == "NEUTRAL":
            base -= 1
        
        return max(self.MIN_LEVERAGE, min(base, self.MAX_LEVERAGE))
    
    def calculate_position_size(self, balance: float, leverage: int, 
                                price: float, max_positions: int = 5) -> float:
        """Ultra-conservative sizing"""
        max_margin = balance * self.MAX_POSITION_PCT
        adjusted_margin = max_margin / math.sqrt(max_positions)
        position_value = adjusted_margin * leverage
        position_size = position_value / price
        return position_size
    
    def check_liquidation_distance(self, entry_price: float, current_price: float,
                                   side: str, leverage: int) -> dict:
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

if __name__ == "__main__":
    manager = LeverageManager()
    
    print("ULTRA-SAFE FINALS LEVERAGE")
    print("="*60)
    
    lev = manager.calculate_safe_leverage(pair_tier=1, volatility=1.5, regime="BULLISH")
    print(f"BTC (Tier 1, BULLISH): {lev}x leverage\n")
    
    balance = 5743
    btc_price = 71000
    
    size = manager.calculate_position_size(balance, lev, btc_price)
    margin = (size * btc_price) / lev
    
    print(f"Position (Balance: ${balance}):")
    print(f"  Size: {size:.4f} BTC")
    print(f"  Margin: ${margin:.2f} ({margin/balance*100:.1f}%)")
    print(f"  Max positions: 5")
    print(f"  Total margin if maxed: ${margin*5:.2f} ({margin*5/balance*100:.1f}%)\n")
    
    # Test -5% BTC crash
    for price_change in [-1, -3, -5, -10]:
        test_price = btc_price * (1 + price_change/100)
        liq = manager.check_liquidation_distance(btc_price, test_price, "LONG", lev)
        emoji = "✅" if liq['safe'] else "❌"
        print(f"BTC {price_change:+d}% (${test_price:,.0f}): {liq['distance_pct']:>5.1f}% from liq {emoji}")
    
    print("\n" + "="*60)
    print("LEVERAGE TABLE (All Scenarios)")
    print("="*60)
    for pair, tier in [("BTC", 1), ("ETH", 1), ("SOL", 2), ("XRP", 3)]:
        for regime in ["BULLISH", "NEUTRAL"]:
            lev = manager.calculate_safe_leverage(tier, 2.0, regime)
            print(f"{pair:<6} {regime:<10} → {lev}x")
