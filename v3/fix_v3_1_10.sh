#!/bin/bash
# V3.1.10 EMERGENCY FIX - Aggressive LONG exits
# Run this on VM: bash fix_v3_1_10.sh

cd ~/smt-weex-trading-bot/v3

# Stop the daemon first
pkill -f smt_daemon
sleep 2

# Backup current files
cp smt_daemon_v3_1.py smt_daemon_v3_1.py.backup_$(date +%Y%m%d_%H%M%S)
cp smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py.backup_$(date +%Y%m%d_%H%M%S)

echo "Applying V3.1.10 fixes..."

# FIX 1: Update daemon regime_aware_exit_check function
# Find and replace the regime exit logic with more aggressive version

python3 << 'PYTHON_SCRIPT'
import re

# Read daemon file
with open('smt_daemon_v3_1.py', 'r') as f:
    content = f.read()

# Update version in docstring
content = content.replace('SMT Trading Daemon V3.1.9', 'SMT Trading Daemon V3.1.10')
content = content.replace('SMT Daemon V3.1.9 - CRITICAL Regime Filter Fix', 'SMT Daemon V3.1.10 - AGGRESSIVE LONG EXIT')

# Find the regime_aware_exit_check function and replace the exit logic
old_exit_logic = '''            # LONG losing in BEARISH market
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -5:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -5:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.9: Even in NEUTRAL, cut LONGs losing >$8 if BTC is slightly negative
            elif regime["regime"] == "NEUTRAL" and side == "LONG" and pnl < -8 and regime["change_24h"] < 0:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} while BTC weak (24h: {regime['change_24h']:+.1f}%)"'''

new_exit_logic = '''            # V3.1.10: Calculate portfolio context
            total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in positions if p['side'] == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
            total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
            shorts_winning = total_short_gain > 20 and total_long_loss > 15
            
            # LONG losing in BEARISH market
            if regime["regime"] == "BEARISH" and side == "LONG" and pnl < -5:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} in BEARISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # SHORT losing in BULLISH market
            elif regime["regime"] == "BULLISH" and side == "SHORT" and pnl < -5:
                should_close = True
                reason = f"SHORT losing ${abs(pnl):.1f} in BULLISH market (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.9: Even in NEUTRAL, cut LONGs losing >$8 if BTC is slightly negative
            elif regime["regime"] == "NEUTRAL" and side == "LONG" and pnl < -8 and regime["change_24h"] < 0:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} while BTC weak (24h: {regime['change_24h']:+.1f}%)"
            
            # V3.1.10: HARD STOP - Any LONG losing > $12 gets cut regardless of regime
            elif side == "LONG" and pnl < -12:
                should_close = True
                reason = f"HARD STOP: LONG losing ${abs(pnl):.1f} (max $12 loss per position)"
            
            # V3.1.10: Cut LONGs when SHORTs clearly winning
            elif side == "LONG" and pnl < -8 and shorts_winning:
                should_close = True
                reason = f"LONG losing ${abs(pnl):.1f} while SHORTs winning +${total_short_gain:.1f}"'''

if old_exit_logic in content:
    content = content.replace(old_exit_logic, new_exit_logic)
    print("Updated daemon exit logic")
else:
    print("WARNING: Could not find exact exit logic pattern in daemon")
    print("Manual edit required")

# Add logging for LONG/SHORT balance after regime log
old_regime_log = '''logger.info(f"[REGIME] Market: {regime['regime']} | 24h: {regime['change_24h']:+.1f}% | 4h: {regime['change_4h']:+.1f}%")'''

new_regime_log = '''logger.info(f"[REGIME] Market: {regime['regime']} | 24h: {regime['change_24h']:+.1f}% | 4h: {regime['change_4h']:+.1f}%")
        
        # V3.1.10: Log position balance
        long_pnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'LONG')
        short_pnl = sum(float(p.get('unrealized_pnl', 0)) for p in positions if p['side'] == 'SHORT')
        if positions:
            logger.info(f"[REGIME] Position PnL - LONGs: ${long_pnl:+.1f} | SHORTs: ${short_pnl:+.1f}")'''

if old_regime_log in content:
    content = content.replace(old_regime_log, new_regime_log)
    print("Added position PnL logging")

with open('smt_daemon_v3_1.py', 'w') as f:
    f.write(content)

print("Daemon updated!")

# Now update the trade script
with open('smt_nightly_trade_v3_1.py', 'r') as f:
    content = f.read()

# Update version
content = content.replace('SMT Nightly Trade V3.1.9', 'SMT Nightly Trade V3.1.10')

# Find the LONG blocking logic and add portfolio check
old_long_block = '''        # Block LONGs in bearish regime (ANY negative 24h = bearish for safety)
        if decision == "LONG":
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH regime (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)
            # V3.1.8: Also block if 24h is negative even if not "BEARISH" threshold
            if regime["change_24h"] < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)'''

new_long_block = '''        # Block LONGs in bearish regime (ANY negative 24h = bearish for safety)
        if decision == "LONG":
            if regime["regime"] == "BEARISH":
                return self._wait_decision(f"BLOCKED: LONG in BEARISH regime (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)
            # V3.1.8: Also block if 24h is negative even if not "BEARISH" threshold
            if regime["change_24h"] < -0.5:
                return self._wait_decision(f"BLOCKED: LONG while BTC dropping (24h: {regime['change_24h']:+.1f}%)", persona_votes, vote_summary)
            
            # V3.1.10: Block new LONGs if existing LONGs bleeding
            if hasattr(self, '_open_positions') and self._open_positions:
                total_long_loss = sum(abs(float(p.get('unrealized_pnl', 0))) for p in self._open_positions if p.get('side') == 'LONG' and float(p.get('unrealized_pnl', 0)) < 0)
                total_short_gain = sum(float(p.get('unrealized_pnl', 0)) for p in self._open_positions if p.get('side') == 'SHORT' and float(p.get('unrealized_pnl', 0)) > 0)
                
                if total_long_loss > 15:
                    return self._wait_decision(f"BLOCKED: Existing LONGs losing ${total_long_loss:.1f}", persona_votes, vote_summary)
                if total_short_gain > 20 and total_long_loss > 8:
                    return self._wait_decision(f"BLOCKED: SHORTs +${total_short_gain:.1f} outperforming LONGs -${total_long_loss:.1f}", persona_votes, vote_summary)'''

if old_long_block in content:
    content = content.replace(old_long_block, new_long_block)
    print("Updated LONG blocking logic in trade script")
else:
    print("WARNING: Could not find exact LONG block pattern")

# Add _open_positions setter in analyze method
old_analyze_start = '''        symbol = pair_info["symbol"]
        price = pair_info["price"]
        
        print(f"\\n{'='*50}")'''

new_analyze_start = '''        symbol = pair_info["symbol"]
        price = pair_info["price"]
        
        # V3.1.10: Pass positions to judge for portfolio-aware decisions
        self.judge._open_positions = open_positions or []
        
        print(f"\\n{'='*50}")'''

if old_analyze_start in content:
    content = content.replace(old_analyze_start, new_analyze_start)
    print("Added position passing to judge")

with open('smt_nightly_trade_v3_1.py', 'w') as f:
    f.write(content)

print("Trade script updated!")
print("\nV3.1.10 changes applied:")
print("1. Hard stop: Cut ANY LONG losing > $12")
print("2. Cut LONGs > $8 loss when SHORTs winning")
print("3. Block new LONGs if existing LONGs losing > $15")
print("4. Block new LONGs if SHORTs outperforming")
print("5. Added position PnL logging")
PYTHON_SCRIPT

echo ""
echo "Restarting daemon..."
nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
sleep 3

echo ""
echo "Checking daemon status..."
ps aux | grep smt_daemon | grep -v grep

echo ""
echo "Recent logs:"
tail -20 daemon.log

echo ""
echo "DONE! V3.1.10 is now running."
echo "Monitor with: tail -f daemon.log | grep -i 'regime\|exit\|blocked'"
