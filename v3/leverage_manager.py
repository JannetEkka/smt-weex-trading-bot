"""
Smart Leverage Manager V3.1.75 - FLAT 20x ALL TIERS
User wants 20x leverage on ALL trades, ALL tiers, ALL conditions.
No reductions for volatility or regime. 20x flat.

Liquidation distance at 20x = ~4.5% (SL at 1.5-1.8% = 2.7-3.0% buffer)
SL always triggers well before liquidation.
"""

# V3.1.75: FLAT 20x - no tiering, no reductions, user mandate
LEVERAGE_MATRIX = {
    (1, "ultra"):  20,
    (1, "high"):   20,
    (1, "normal"): 20,
    (2, "ultra"):  20,
    (2, "high"):   20,
    (2, "normal"): 20,
    (3, "ultra"):  20,
    (3, "high"):   20,
    (3, "normal"): 20,
}


class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 20  # V3.1.75: Floor is 20x
        self.MAX_LEVERAGE = 20  # V3.1.75: Cap is 20x
        self.MAX_POSITION_PCT = 0.50  # V3.1.75: Match nightly config
        self.MIN_LIQUIDATION_DISTANCE = 2.5  # 2.5% min buffer above SL

    def calculate_safe_leverage(self, pair_tier: int, volatility: float = 2.0,
                                 regime: str = "NEUTRAL", confidence: float = 0.75) -> int:
        """V3.1.75: FLAT 20x leverage - no reductions."""
        # Always return 20x regardless of tier, volatility, regime, or confidence
        return 20

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


def get_safe_leverage(tier: int, volatility: float = 2.0, regime: str = "NEUTRAL",
                      confidence: float = 0.75) -> int:
    return _manager.calculate_safe_leverage(tier, volatility, regime, confidence)
