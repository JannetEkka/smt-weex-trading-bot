#!/usr/bin/env python3
"""
Real-time trade monitor for SMT daemon
Tracks all opens, closes, and PnL
"""
import time
import json
from datetime import datetime
from smt_nightly_trade_v3_1 import get_open_positions, get_balance

class TradeMonitor:
    def __init__(self):
        self.last_positions = {}
        self.trade_history = []
        self.session_start_balance = get_balance()
        self.session_pnl = 0
        
    def check_positions(self):
        """Check for position changes"""
        current_positions = {}
        positions = get_open_positions()
        
        # Build current state
        for pos in positions:
            symbol = pos['symbol']
            current_positions[symbol] = {
                'side': pos['side'],
                'size': pos['size'],
                'entry': pos['entry_price'],
                'upnl': pos['unrealized_pnl']
            }
        
        # Detect new positions
        for symbol, pos in current_positions.items():
            if symbol not in self.last_positions:
                self.log_open(symbol, pos)
        
        # Detect closed positions
        for symbol, pos in self.last_positions.items():
            if symbol not in current_positions:
                self.log_close(symbol, pos)
        
        self.last_positions = current_positions
    
    def log_open(self, symbol, pos):
        """Log position opened"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"[{timestamp}] 🟢 OPENED {symbol} {pos['side']}"
        msg += f" | Entry: ${pos['entry']:,.2f} | Size: {pos['size']}"
        print(msg)
        
        self.trade_history.append({
            'time': timestamp,
            'action': 'OPEN',
            'symbol': symbol,
            'side': pos['side'],
            'entry': pos['entry'],
            'size': pos['size']
        })
    
    def log_close(self, symbol, pos):
        """Log position closed"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get final PnL from balance change
        current_balance = get_balance()
        
        msg = f"[{timestamp}] 🔴 CLOSED {symbol} {pos['side']}"
        msg += f" | Entry: ${pos['entry']:,.2f} | Last UPnL: ${pos['upnl']:.2f}"
        print(msg)
        
        self.trade_history.append({
            'time': timestamp,
            'action': 'CLOSE',
            'symbol': symbol,
            'side': pos['side'],
            'entry': pos['entry'],
            'upnl': pos['upnl']
        })
        
        self.session_pnl += pos['upnl']
    
    def print_status(self):
        """Print current status"""
        current_balance = get_balance()
        positions = get_open_positions()
        
        print("\n" + "="*60)
        print(f"MONITOR STATUS - {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        print(f"Balance: ${current_balance:,.2f}")
        print(f"Session Start: ${self.session_start_balance:,.2f}")
        print(f"Session P&L: ${current_balance - self.session_start_balance:,.2f}")
        print(f"Open Positions: {len(positions)}")
        
        if positions:
            total_upnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions)
            print(f"Total UPnL: ${total_upnl:.2f}")
            for pos in positions:
                symbol = pos['symbol'].replace('cmt_', '').replace('usdt', '').upper()
                side = pos['side']
                upnl = float(pos['unrealized_pnl'])
                print(f"  {symbol} {side}: ${upnl:+.2f}")
        
        print(f"Trades This Session: {len([t for t in self.trade_history if t['action'] == 'OPEN'])}")
        print("="*60 + "\n")
    
    def run(self, interval=10):
        """Run monitor loop"""
        print("🚀 SMT Trade Monitor Started")
        print(f"Session Start Balance: ${self.session_start_balance:,.2f}\n")
        
        try:
            while True:
                self.check_positions()
                self.print_status()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n📊 FINAL SESSION SUMMARY")
            print("="*60)
            current_balance = get_balance()
            print(f"Starting Balance: ${self.session_start_balance:,.2f}")
            print(f"Ending Balance: ${current_balance:,.2f}")
            print(f"Total P&L: ${current_balance - self.session_start_balance:,.2f}")
            print(f"Total Trades: {len([t for t in self.trade_history if t['action'] == 'OPEN'])}")
            print("\nTrade History:")
            for trade in self.trade_history:
                print(f"  {trade['time']} - {trade['action']} {trade['symbol']} {trade.get('side', '')}")
            print("="*60)

if __name__ == "__main__":
    monitor = TradeMonitor()
    monitor.run(interval=10)  # Check every 10 seconds
