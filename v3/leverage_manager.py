"""
Smart Leverage Manager V3.1.59 - Confidence-Tiered
Leverage scales with signal confidence, NOT just tier.
High confidence (90%+) gets more leverage. Low confidence stays conservative.
Safety: SL always triggers well before liquidation distance.

Liquidation distances:
  18x = ~5.0% (SL at 2.5% = 2.5% buffer)
  15x = ~6.0% (SL at 2.5% = 3.5% buffer)
  12x = ~7.5% (SL at 2.0% = 5.5% buffer)
  10x = ~9.0% (SL at 2.0% = 7.0% buffer)
"""

# V3.1.59: Confidence-tiered leverage matrix
# Key: (tier, confidence_bracket) -> leverage
# Confidence brackets: "ultra" (90%+), "high" (80-89%), "normal" (<80%)
LEVERAGE_MATRIX = {
    (1, "ultra"):  18,  # T1 Blue Chip, 90%+ confidence
    (1, "high"):   15,  # T1 Blue Chip, 80-89%
    (1, "normal"): 12,  # T1 Blue Chip, <80%
    (2, "ultra"):  15,  # T2 Mid Cap, 90%+
    (2, "high"):   12,  # T2 Mid Cap, 80-89%
    (2, "normal"): 10,  # T2 Mid Cap, <80%
    (3, "ultra"):  12,  # T3 Small Cap, 90%+
    (3, "high"):   10,  # T3 Small Cap, 80-89%
    (3, "normal"):  8,  # T3 Small Cap, <80%
}


class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 8
        self.MAX_LEVERAGE = 18  # V3.1.59: Up from 15, but only for ultra-conf
        self.MAX_POSITION_PCT = 0.35  # V3.1.59: Up from 0.20
        self.MIN_LIQUIDATION_DISTANCE = 4  # 4% min buffer above SL

    def calculate_safe_leverage(self, pair_tier: int, volatility: float = 2.0,
                                 regime: str = "NEUTRAL", confidence: float = 0.75) -> int:
        """V3.1.59: Confidence-tiered leverage selection."""

        # Determine confidence bracket
        if confidence >= 0.90:
            bracket = "ultra"
        elif confidence >= 0.80:
            bracket = "high"
        else:
            bracket = "normal"

        base = LEVERAGE_MATRIX.get((pair_tier, bracket), 10)

        # Reduce in high volatility
        if volatility > 4.0:
            base -= 2
        elif volatility > 3.0:
            base -= 1

        # Reduce in uncertain regime (only for non-ultra)
        if regime == "NEUTRAL" and bracket != "ultra":
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


def get_safe_leverage(tier: int, volatility: float = 2.0, regime: str = "NEUTRAL",
                      confidence: float = 0.75) -> int:
    return _manager.calculate_safe_leverage(tier, volatility, regime, confidence)
