import json

# Clear the stale active trades
new_state = {
    "active": {
        "cmt_btcusdt": {"side": "SHORT", "size": 0.01, "entry_price": 93182, "opened_at": "2026-01-19"},
        "cmt_ethusdt": {"side": "SHORT", "size": 0.77, "entry_price": 3205, "opened_at": "2026-01-19"},
        "cmt_solusdt": {"side": "SHORT", "size": 10.5, "entry_price": 133.75, "opened_at": "2026-01-19"},
        "cmt_xrpusdt": {"side": "SHORT", "size": 840, "entry_price": 2.02, "opened_at": "2026-01-19"},
        "cmt_bnbusdt": {"side": "SHORT", "size": 3.0, "entry_price": 922.65, "opened_at": "2026-01-19"},
    },
    "closed": []
}

with open("trade_state_v3_1.json", "w") as f:
    json.dump(new_state, f, indent=2)

with open("active_trades.json", "w") as f:
    json.dump({"active": new_state["active"], "closed": []}, f, indent=2)

print("Trade state synced to actual WEEX positions (5 SHORTs)")
