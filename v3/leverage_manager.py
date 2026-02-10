"""
Smart Leverage Manager V3 - Competition-tuned
10-12x leverage with SL safety net (1.5% SL triggers well before liquidation)
Liquidation distance at 10x = ~9%, at 12x = ~7.5% -- SL at 1.5% gives 6%+ buffer
"""

class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 10  # V3.1.41: Recovery mode
        self.MAX_LEVERAGE = 15  # V3.1.41: Recovery mode (prelims used 20x)
        self.MAX_POSITION_PCT = 0.20  # 20% of balance per position
        self.MIN_LIQUIDATION_DISTANCE = 6  # 6% min buffer above SL

    def calculate_safe_leverage(self, pair_tier: int, volatility: float = 2.0, regime: str = "NEUTRAL") -> int:
        tier_leverage = {
            1: 15,  # BTC, ETH, BNB, LTC - prelims proved 20x safe with SL
            2: 12,  # SOL - mid vol
            3: 10   # DOGE, XRP, ADA - higher vol, keep conservative
        }
        base = tier_leverage.get(pair_tier, 10)

        # Reduce in high volatility
        if volatility > 4.0:
            base -= 2
        elif volatility > 3.0:
            base -= 1

        # Reduce in uncertain regime
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
