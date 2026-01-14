#!/usr/bin/env python3
"""
SMT Live Dashboard V1.0
=======================
Fetches live data from WEEX API and generates an HTML dashboard.

Run: python3 smt_live_dashboard.py
     python3 smt_live_dashboard.py --watch  (auto-refresh every 60s)

Output: smt_dashboard_live.html
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List

# ============================================================
# WEEX API CONFIG
# ============================================================

WEEX_API_KEY = os.getenv('WEEX_API_KEY', 'weex_cda1971e60e00a1f6ce7393c1fa2cf86')
WEEX_API_SECRET = os.getenv('WEEX_API_SECRET', '15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c')
WEEX_API_PASSPHRASE = os.getenv('WEEX_API_PASSPHRASE', 'weex8282888')
WEEX_BASE_URL = "https://api-contract.weex.com"

STARTING_BALANCE = 1000.0

# Tier config for display
TIER_CONFIG = {
    1: {"name": "STABLE", "pairs": ["BTC", "ETH", "BNB", "LTC"], "tp": 4.0, "sl": 2.0},
    2: {"name": "MID", "pairs": ["SOL"], "tp": 3.0, "sl": 1.75},
    3: {"name": "FAST", "pairs": ["DOGE", "XRP", "ADA"], "tp": 3.0, "sl": 2.0},
}

def get_tier(symbol: str) -> int:
    pair = symbol.replace("cmt_", "").replace("usdt", "").upper()
    for tier, config in TIER_CONFIG.items():
        if pair in config["pairs"]:
            return tier
    return 2

# ============================================================
# WEEX API HELPERS
# ============================================================

def weex_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    sig = hmac.new(WEEX_API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()

def weex_headers(method: str, path: str, body: str = "") -> Dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": weex_sign(ts, method, path, body),
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": WEEX_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

def get_balance() -> float:
    try:
        endpoint = "/capi/v2/account/assets"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        if isinstance(data, list):
            for asset in data:
                if asset.get("currency") == "USDT":
                    return float(asset.get("available", 0)) + float(asset.get("frozen", 0))
    except:
        pass
    return 0.0

def get_equity() -> float:
    """Get total equity including unrealized PnL"""
    try:
        endpoint = "/capi/v2/account/accounts"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        if isinstance(data, list):
            for acc in data:
                if acc.get("symbol") == "USDT":
                    return float(acc.get("equity", 0))
    except:
        pass
    return 0.0

def get_price(symbol: str) -> float:
    try:
        r = requests.get(f"{WEEX_BASE_URL}/capi/v2/market/ticker?symbol={symbol}", timeout=10)
        return float(r.json().get("last", 0))
    except:
        return 0.0

def get_open_positions() -> List[Dict]:
    try:
        endpoint = "/capi/v2/account/position/allPosition"
        r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
        data = r.json()
        positions = []
        if isinstance(data, list):
            for pos in data:
                size = float(pos.get("size", 0))
                if size > 0:
                    margin = float(pos.get("marginSize", 0))
                    open_value = float(pos.get("open_value", 0))
                    entry_price = open_value / size if size > 0 else 0
                    symbol = pos.get("symbol", "")
                    current_price = get_price(symbol)
                    
                    # Calculate PnL percentage
                    if entry_price > 0 and current_price > 0:
                        side = pos.get("side", "").upper()
                        if side == "LONG":
                            pnl_pct = ((current_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - current_price) / entry_price) * 100
                    else:
                        pnl_pct = 0
                    
                    # Fix: Handle leverage as float string
                    try:
                        leverage = int(float(pos.get("leverage", 20)))
                    except:
                        leverage = 20
                    
                    positions.append({
                        "symbol": symbol,
                        "side": pos.get("side", "").upper(),
                        "size": size,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "unrealized_pnl": float(pos.get("unrealizePnl", 0)),
                        "pnl_pct": pnl_pct,
                        "margin": margin,
                        "leverage": leverage,
                        "tier": get_tier(symbol),
                    })
        return positions
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return []

def get_recent_trades(limit: int = 20) -> List[Dict]:
    """Get recent closed trades"""
    trades = []
    symbols = ["cmt_btcusdt", "cmt_ethusdt", "cmt_solusdt", "cmt_dogeusdt", 
               "cmt_xrpusdt", "cmt_adausdt", "cmt_bnbusdt", "cmt_ltcusdt"]
    
    for symbol in symbols:
        try:
            endpoint = f"/capi/v2/order/trades?symbol={symbol}&limit=10"
            r = requests.get(f"{WEEX_BASE_URL}{endpoint}", headers=weex_headers("GET", endpoint), timeout=15)
            data = r.json()
            if isinstance(data, list):
                for trade in data[:5]:  # Last 5 per symbol
                    trades.append({
                        "symbol": symbol,
                        "side": trade.get("side", "").upper(),
                        "size": float(trade.get("size", 0)),
                        "price": float(trade.get("price", 0)),
                        "pnl": float(trade.get("pnl", 0)),
                        "fee": float(trade.get("fee", 0)),
                        "timestamp": trade.get("timestamp", ""),
                        "tier": get_tier(symbol),
                    })
            time.sleep(0.1)  # Rate limit
        except:
            continue
    
    # Sort by timestamp descending
    trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return trades[:limit]

def get_btc_trend() -> Dict:
    """Get BTC 4h trend for market context"""
    try:
        url = f"{WEEX_BASE_URL}/capi/v2/market/candles?symbol=cmt_btcusdt&granularity=4h&limit=6"
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if isinstance(data, list) and len(data) >= 2:
            closes = [float(c[4]) for c in data[:3]]
            change_pct = ((closes[0] - closes[1]) / closes[1]) * 100
            
            if change_pct > 1.0:
                trend = "UP"
                color = "#00ff88"
            elif change_pct < -1.0:
                trend = "DOWN"
                color = "#ff4757"
            else:
                trend = "NEUTRAL"
                color = "#ffa502"
            
            return {
                "trend": trend,
                "change_pct": change_pct,
                "color": color,
                "price": closes[0]
            }
    except:
        pass
    
    return {"trend": "UNKNOWN", "change_pct": 0, "color": "#888", "price": 0}

# ============================================================
# HTML GENERATOR
# ============================================================

def generate_html(balance: float, equity: float, positions: List[Dict], 
                  trades: List[Dict], btc_trend: Dict) -> str:
    
    total_upnl = sum(p["unrealized_pnl"] for p in positions)
    realized_pnl = balance - STARTING_BALANCE
    total_pnl = equity - STARTING_BALANCE if equity > 0 else realized_pnl + total_upnl
    
    # Win rate from recent trades
    winning_trades = len([t for t in trades if t.get("pnl", 0) > 0])
    total_trades = len([t for t in trades if t.get("pnl", 0) != 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    # Position cards HTML
    positions_html = ""
    for pos in sorted(positions, key=lambda x: x["unrealized_pnl"], reverse=True):
        symbol_clean = pos["symbol"].replace("cmt_", "").upper()
        side_class = pos["side"].lower()
        pnl_class = "positive" if pos["unrealized_pnl"] >= 0 else "negative"
        tier = pos["tier"]
        tier_config = TIER_CONFIG.get(tier, {})
        
        positions_html += f'''
            <div class="position-card {side_class}">
                <div class="position-header">
                    <div>
                        <span class="position-symbol">{symbol_clean}</span>
                        <span class="tier-badge tier-{tier}">T{tier} {tier_config.get("name", "")}</span>
                    </div>
                    <span class="position-side {side_class}">{pos["side"]}</span>
                </div>
                <div class="position-details">
                    <div class="detail-item">
                        <span class="label">Entry</span>
                        <span class="value">${pos["entry_price"]:.4f}</span>
                    </div>
                    <div class="detail-item">
                        <span class="label">Current</span>
                        <span class="value">${pos["current_price"]:.4f}</span>
                    </div>
                    <div class="detail-item">
                        <span class="label">Size</span>
                        <span class="value">{pos["size"]:.4f}</span>
                    </div>
                    <div class="detail-item">
                        <span class="label">Margin</span>
                        <span class="value">${pos["margin"]:.2f}</span>
                    </div>
                    <div class="detail-item">
                        <span class="label">PnL %</span>
                        <span class="value {pnl_class}">{pos["pnl_pct"]:+.2f}%</span>
                    </div>
                    <div class="detail-item pnl-large {pnl_class}">
                        ${pos["unrealized_pnl"]:+.2f}
                    </div>
                </div>
                <div class="position-targets">
                    <span class="tp">TP: {tier_config.get("tp", 0)}%</span>
                    <span class="sl">SL: {tier_config.get("sl", 0)}%</span>
                </div>
            </div>
        '''
    
    # Trades table HTML
    trades_html = ""
    for trade in trades[:15]:
        symbol_clean = trade["symbol"].replace("cmt_", "").upper()
        pnl_class = "positive" if trade.get("pnl", 0) >= 0 else "negative"
        
        # Parse timestamp
        try:
            ts = datetime.fromtimestamp(int(trade.get("timestamp", 0)) / 1000)
            time_str = ts.strftime("%m/%d %H:%M")
        except:
            time_str = "--"
        
        trades_html += f'''
            <tr>
                <td>{time_str}</td>
                <td>{symbol_clean} <span class="tier-badge tier-{trade["tier"]}">T{trade["tier"]}</span></td>
                <td><span class="position-side {trade["side"].lower()}">{trade["side"]}</span></td>
                <td>${trade["price"]:.4f}</td>
                <td>{trade["size"]:.4f}</td>
                <td class="trade-pnl {pnl_class}">${trade.get("pnl", 0):+.2f}</td>
            </tr>
        '''
    
    # Stats classes
    balance_class = "positive" if balance >= STARTING_BALANCE else "negative"
    total_pnl_class = "positive" if total_pnl >= 0 else "negative"
    upnl_class = "positive" if total_upnl >= 0 else "negative"
    winrate_class = "positive" if win_rate >= 50 else "negative"
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>SMT Live Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        
        .header h1 {{
            font-size: 2.5em;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 10px;
        }}
        
        .header .version {{ color: #888; font-size: 0.9em; }}
        .header .update-time {{ color: #666; font-size: 0.8em; margin-top: 5px; }}
        
        .btc-trend {{
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            margin-top: 10px;
            font-weight: bold;
            background: rgba(0,0,0,0.3);
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        
        .stat-card .label {{
            color: #888;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }}
        
        .stat-card .value {{ font-size: 1.8em; font-weight: bold; }}
        .stat-card .value.positive {{ color: #00ff88; }}
        .stat-card .value.negative {{ color: #ff4757; }}
        .stat-card .value.neutral {{ color: #ffa502; }}
        
        .section {{
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 25px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        
        .section h2 {{
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid rgba(255,255,255,0.1);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .section h2 .count {{
            background: #7b2cbf;
            padding: 3px 12px;
            border-radius: 20px;
            font-size: 0.7em;
        }}
        
        .positions-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 15px;
        }}
        
        .position-card {{
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            padding: 18px;
            border-left: 4px solid;
        }}
        
        .position-card.long {{ border-left-color: #00ff88; }}
        .position-card.short {{ border-left-color: #ff4757; }}
        
        .position-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}
        
        .position-symbol {{ font-size: 1.2em; font-weight: bold; }}
        
        .position-side {{
            padding: 3px 10px;
            border-radius: 15px;
            font-size: 0.75em;
            font-weight: bold;
        }}
        
        .position-side.long {{ background: rgba(0,255,136,0.2); color: #00ff88; }}
        .position-side.short {{ background: rgba(255,71,87,0.2); color: #ff4757; }}
        
        .position-details {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            font-size: 0.85em;
        }}
        
        .detail-item {{ display: flex; flex-direction: column; }}
        .detail-item .label {{ color: #666; font-size: 0.8em; }}
        .detail-item .value {{ font-weight: 500; }}
        
        .pnl-large {{ font-size: 1.4em; font-weight: bold; text-align: right; }}
        .pnl-large.positive {{ color: #00ff88; }}
        .pnl-large.negative {{ color: #ff4757; }}
        
        .position-targets {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid rgba(255,255,255,0.1);
            font-size: 0.8em;
            color: #888;
        }}
        
        .position-targets .tp {{ color: #00ff88; margin-right: 15px; }}
        .position-targets .sl {{ color: #ff4757; }}
        
        .tier-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.7em;
            margin-left: 8px;
        }}
        
        .tier-1 {{ background: #3498db; }}
        .tier-2 {{ background: #9b59b6; }}
        .tier-3 {{ background: #e67e22; }}
        
        table {{ width: 100%; border-collapse: collapse; }}
        
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        
        th {{
            color: #888;
            font-weight: 500;
            text-transform: uppercase;
            font-size: 0.75em;
            letter-spacing: 1px;
        }}
        
        tr:hover {{ background: rgba(255,255,255,0.03); }}
        
        .trade-pnl.positive {{ color: #00ff88; }}
        .trade-pnl.negative {{ color: #ff4757; }}
        
        .positive {{ color: #00ff88; }}
        .negative {{ color: #ff4757; }}
        
        .footer {{
            text-align: center;
            color: #666;
            font-size: 0.8em;
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Smart Money Tracker</h1>
        <div class="version">V3.1.4 Live Dashboard - WEEX AI Wars</div>
        <div class="btc-trend" style="color: {btc_trend['color']}">
            BTC: ${btc_trend['price']:,.2f} | 4H Trend: {btc_trend['trend']} ({btc_trend['change_pct']:+.2f}%)
        </div>
        <div class="update-time">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Auto-refresh: 60s</div>
    </div>
    
    <div class="stats-grid">
        <div class="stat-card">
            <div class="label">Starting Balance</div>
            <div class="value neutral">${STARTING_BALANCE:,.2f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Current Balance</div>
            <div class="value {balance_class}">${balance:,.2f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Equity</div>
            <div class="value {total_pnl_class}">${equity:,.2f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Total PnL</div>
            <div class="value {total_pnl_class}">${total_pnl:+,.2f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Unrealized PnL</div>
            <div class="value {upnl_class}">${total_upnl:+,.2f}</div>
        </div>
        <div class="stat-card">
            <div class="label">Open Positions</div>
            <div class="value neutral">{len(positions)}</div>
        </div>
        <div class="stat-card">
            <div class="label">Win Rate</div>
            <div class="value {winrate_class}">{win_rate:.0f}%</div>
        </div>
    </div>
    
    <div class="section">
        <h2>Open Positions <span class="count">{len(positions)}</span></h2>
        <div class="positions-grid">
            {positions_html if positions_html else '<p style="color:#888">No open positions</p>'}
        </div>
    </div>
    
    <div class="section">
        <h2>Recent Trades <span class="count">{len(trades)}</span></h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Pair</th>
                    <th>Side</th>
                    <th>Price</th>
                    <th>Size</th>
                    <th>PnL</th>
                </tr>
            </thead>
            <tbody>
                {trades_html if trades_html else '<tr><td colspan="6" style="color:#888">No recent trades</td></tr>'}
            </tbody>
        </table>
    </div>
    
    <div class="footer">
        SMT Trading Bot - WEEX AI Wars Hackathon 2026<br>
        Dashboard auto-refreshes every 60 seconds
    </div>
</body>
</html>'''
    
    return html

# ============================================================
# MAIN
# ============================================================

def main():
    watch_mode = "--watch" in sys.argv
    output_file = "smt_dashboard_live.html"
    
    print("=" * 50)
    print("SMT Live Dashboard Generator")
    print("=" * 50)
    
    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Fetching data from WEEX...")
        
        # Fetch all data
        balance = get_balance()
        equity = get_equity()
        positions = get_open_positions()
        trades = get_recent_trades(20)
        btc_trend = get_btc_trend()
        
        print(f"  Balance: ${balance:.2f}")
        print(f"  Equity: ${equity:.2f}")
        print(f"  Positions: {len(positions)}")
        print(f"  BTC Trend: {btc_trend['trend']} ({btc_trend['change_pct']:+.2f}%)")
        
        # Calculate totals
        total_upnl = sum(p["unrealized_pnl"] for p in positions)
        print(f"  Unrealized PnL: ${total_upnl:+.2f}")
        
        # Generate HTML
        html = generate_html(balance, equity, positions, trades, btc_trend)
        
        # Save to file
        with open(output_file, "w") as f:
            f.write(html)
        
        print(f"  Dashboard saved: {output_file}")
        
        if not watch_mode:
            print(f"\nOpen {output_file} in your browser to view.")
            print("Run with --watch for auto-refresh mode.")
            break
        
        print(f"  Waiting 60 seconds... (Ctrl+C to stop)")
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break

if __name__ == "__main__":
    main()
