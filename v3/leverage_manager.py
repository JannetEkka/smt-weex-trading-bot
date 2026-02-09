"""
Smart Leverage Manager V2 - Ultra-safe for finals
5-6x leverage, 15%+ liquidation buffer
"""

class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 5
        self.MAX_LEVERAGE = 6
        self.MAX_POSITION_PCT = 0.18
        self.MIN_LIQUIDATION_DISTANCE = 15

    def calculate_safe_leverage(self, pair_tier: int, volatility: float = 2.0, regime: str = "NEUTRAL") -> int:
        tier_leverage = {
            1: 6,   # BTC, ETH, BNB, LTC
            2: 5,   # SOL
            3: 5    # DOGE, XRP, ADA
        }
        base = tier_leverage.get(pair_tier, 5)

        if volatility > 3.0:
            base -= 1
        if regime == "NEUTRAL":
            base -= 1

        return max(self.MIN_LEVERAGE, min(base, self.MAX_LEVERAGE))

    def check_liquidation_distance(self, entry_price: float, current_price: float,
                                   side: str, leverage: int) -> dict:
        liq_pct = 90 / leverage
        if side == "LONG":
            liq_price = entry_price * (1 - liq_pct / 100)
            distance_pct = ((current_price - liq_price) / current_price) * 100
        else:
            liq_price = entry_price * (1 + liq_pct / 100)
            distance_pct = ((liq_price - current_price) / current_price) * 100
        return {
            "liquidation_price": liq_price,
            "distance_pct": distance_pct,
            "safe": distance_pct > self.MIN_LIQUIDATION_DISTANCE
        }

# Singleton
_manager = LeverageManager()

def get_safe_leverage(tier: int, volatility: float = 2.0, regime: str = "NEUTRAL") -> int:
    return _manager.calculate_safe_leverage(tier, volatility, regime)
