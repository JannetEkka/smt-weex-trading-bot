"""
Smart Leverage Manager V3.1.75 - DISCIPLINE RESTORATION
Lower leverage = survive longer = compound more wins.
At 15x with 1.5% SL, loss per trade = 22.5% ROE (survivable).
At 20x with 1.5% SL, loss per trade = 30% ROE (account killer).

Liquidation distances:
  15x = ~6.0% (SL at 1.5% = 4.5% buffer)
  12x = ~7.5% (SL at 1.5% = 6.0% buffer)
  10x = ~9.0% (SL at 1.8% = 7.2% buffer)
"""

# V3.1.75: Conservative leverage - T1=15x max, T2=12x max, T3=10x max
LEVERAGE_MATRIX = {
    (1, "ultra"):  15,
    (1, "high"):   15,
    (1, "normal"): 12,
    (2, "ultra"):  12,
    (2, "high"):   12,
    (2, "normal"): 10,
    (3, "ultra"):  10,
    (3, "high"):   10,
    (3, "normal"): 8,
}


class LeverageManager:
    def __init__(self):
        self.MIN_LEVERAGE = 5
        self.MAX_LEVERAGE = 15  # V3.1.75: Hard cap at 15x (was 18x)
        self.MAX_POSITION_PCT = 0.20  # V3.1.75: 20% max (was 0.35)
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
