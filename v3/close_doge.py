from smt_nightly_trade_v3_1 import (
    WEEX_BASE_URL, WEEX_API_KEY, WEEX_API_SECRET, WEEX_API_PASSPHRASE,
    close_position_manually, upload_ai_log_to_weex, cancel_all_orders_for_symbol
)

symbol = "cmt_dogeusdt"
side = "SHORT"
size = 13000.0

print("Closing DOGE SHORT position...")

# Cancel existing TP/SL orders first
print("Cancelling TP/SL orders...")
cancel_result = cancel_all_orders_for_symbol(symbol)
print(f"Cancel result: {cancel_result}")

# Close the position
print(f"Closing {size} DOGE...")
close_result = close_position_manually(symbol, side, size)
print(f"Close result: {close_result}")

order_id = close_result.get("order_id")

# Upload AI log as regime exit
print("Uploading AI log...")
upload_ai_log_to_weex(
    stage="V3.1.20 Regime Exit: SHORT DOGE",
    input_data={
        "symbol": symbol,
        "side": side,
        "size": size,
        "unrealized_pnl": -15.34,
        "market_regime": "NEUTRAL",
        "btc_24h_change": 0.1,
        "analysis": "FLOW showing taker buy pressure 1.62, bid wall forming"
    },
    output_data={
        "action": "CLOSE",
        "ai_decision": "REGIME_EXIT",
        "reason": "SHORT losing in strengthening market with buy pressure"
    },
    explanation="AI Regime Exit: DOGE SHORT closed due to building buy-side pressure (taker ratio 1.62) and bid wall forming (depth ratio 1.18). Cutting position to preserve capital for better setups.",
    order_id=order_id
)

print("Done! DOGE SHORT closed.")
