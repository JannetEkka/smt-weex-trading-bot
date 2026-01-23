#!/usr/bin/env python3
"""
Patch to add RL Data Collection to smt_daemon_v3_1.py
Run: python3 patch_rl_collector.py

This does TWO things:
1. Copies rl_data_collector.py to the v3 folder
2. Patches smt_daemon_v3_1.py to use it
"""

import os
import shutil

DAEMON_FILE = "smt_daemon_v3_1.py"

# The RL Data Collector module (will be created as separate file)
RL_COLLECTOR_CODE = '''#!/usr/bin/env python3
"""
SMT RL Data Collector V1.0 - Collects training data for RL
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict
import glob


class RLDataCollector:
    """Collects (state, action, reward) tuples for RL training."""
    
    def __init__(self, data_dir: str = "rl_training_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.pending_trades = {}
        self.current_file = None
        self._init_daily_file()
    
    def _init_daily_file(self):
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        self.current_file = f"{self.data_dir}/exp_{date_str}.jsonl"
    
    def log_decision(
        self,
        symbol: str,
        action: str,
        confidence: float,
        persona_votes: Dict,
        market_state: Dict,
        portfolio_state: Dict,
        tier: int = 2,
    ) -> str:
        """Log a decision with full state vector."""
        exp_id = f"{symbol}_{int(datetime.now(timezone.utc).timestamp())}"
        
        state = self._build_state(persona_votes, market_state, portfolio_state, tier)
        
        entry = {
            "id": exp_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "tier": tier,
            "state": state,
            "raw_personas": persona_votes,
            "raw_market": market_state,
            "outcome": None,
        }
        
        if action in ("LONG", "SHORT"):
            self.pending_trades[symbol] = entry
        
        self._save(entry)
        return exp_id
    
    def log_outcome(self, symbol: str, pnl: float, hours: float, reason: str, 
                    max_dd: float = 0, peak: float = 0):
        """Log outcome when trade closes."""
        if symbol not in self.pending_trades:
            return
        
        entry = self.pending_trades.pop(symbol)
        reward = self._calc_reward(pnl, hours, max_dd, peak)
        
        entry["outcome"] = {
            "pnl": pnl,
            "hours": hours,
            "reason": reason,
            "reward": reward,
        }
        
        self._save(entry)
    
    def _build_state(self, personas: Dict, market: Dict, portfolio: Dict, tier: int) -> Dict:
        def sig2num(s):
            return 1.0 if s == "LONG" else (-1.0 if s == "SHORT" else 0.0)
        
        def norm(v, lo, hi):
            return max(-1, min(1, 2 * (v - lo) / (hi - lo) - 1)) if hi != lo else 0
        
        w = personas.get("WHALE", {})
        s = personas.get("SENTIMENT", {})
        f = personas.get("FLOW", {})
        t = personas.get("TECHNICAL", {})
        
        return {
            "whale_sig": sig2num(w.get("signal", "WAIT")),
            "whale_conf": w.get("confidence", 0.5),
            "sent_sig": sig2num(s.get("signal", "WAIT")),
            "sent_conf": s.get("confidence", 0.5),
            "flow_sig": sig2num(f.get("signal", "WAIT")),
            "flow_conf": f.get("confidence", 0.5),
            "tech_sig": sig2num(t.get("signal", "WAIT")),
            "tech_conf": t.get("confidence", 0.5),
            "btc_24h": norm(market.get("btc_24h", 0), -10, 10),
            "btc_4h": norm(market.get("btc_4h", 0), -5, 5),
            "regime": market.get("regime", "NEUTRAL"),
            "n_pos": portfolio.get("num_positions", 0),
            "long_exp": portfolio.get("long_exposure", 0),
            "short_exp": portfolio.get("short_exposure", 0),
            "upnl_pct": norm(portfolio.get("upnl_pct", 0), -20, 20),
            "tier": tier,
        }
    
    def _calc_reward(self, pnl: float, hours: float, max_dd: float, peak: float) -> float:
        base = pnl / 50.0
        dd_penalty = max_dd * 0.3
        hold_penalty = max(0, (hours - 24) / 100)
        loser_penalty = 0.5 if (peak > 2.0 and pnl < 0) else 0
        return max(-5, min(5, base - dd_penalty - hold_penalty - loser_penalty))
    
    def _save(self, entry: Dict):
        self._init_daily_file()
        with open(self.current_file, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\\n")
    
    def stats(self) -> Dict:
        files = glob.glob(f"{self.data_dir}/exp_*.jsonl")
        total = 0
        outcomes = 0
        for fp in files:
            with open(fp) as f:
                for line in f:
                    total += 1
                    if json.loads(line).get("outcome"):
                        outcomes += 1
        return {"files": len(files), "decisions": total, "outcomes": outcomes}
'''

# Code to ADD to daemon imports
IMPORT_PATCH = '''
# V3.1.23: RL Data Collection
try:
    from rl_data_collector import RLDataCollector
    rl_collector = RLDataCollector()
    RL_ENABLED = True
except ImportError:
    rl_collector = None
    RL_ENABLED = False
'''

# Code to ADD after each trade decision (inside signal_check loop)
DECISION_LOG_CODE = '''
                # V3.1.23: Log decision for RL training
                if RL_ENABLED and rl_collector:
                    try:
                        persona_dict = {}
                        for v in decision.get("persona_votes", []):
                            persona_dict[v.get("persona", "?")] = {
                                "signal": v.get("signal", "WAIT"),
                                "confidence": v.get("confidence", 0.5)
                            }
                        
                        rl_collector.log_decision(
                            symbol=symbol,
                            action=signal,
                            confidence=confidence,
                            persona_votes=persona_dict,
                            market_state={
                                "btc_24h": regime.get("change_24h", 0) if regime else 0,
                                "btc_4h": regime.get("change_4h", 0) if regime else 0,
                                "regime": regime.get("regime", "NEUTRAL") if regime else "NEUTRAL",
                            },
                            portfolio_state={
                                "num_positions": len(open_positions),
                                "long_exposure": sum(1 for p in open_positions if p.get("side") == "LONG") / 8,
                                "short_exposure": sum(1 for p in open_positions if p.get("side") == "SHORT") / 8,
                                "upnl_pct": sum(float(p.get("unrealized_pnl", 0)) for p in open_positions) / max(balance, 1) * 100,
                            },
                            tier=tier,
                        )
                    except Exception as e:
                        logger.debug(f"RL log error: {e}")
'''


def main():
    print("=" * 60)
    print("SMT RL Data Collection Patch")
    print("=" * 60)
    
    # Step 1: Create rl_data_collector.py
    print("\n[1/3] Creating rl_data_collector.py...")
    with open("rl_data_collector.py", "w") as f:
        f.write(RL_COLLECTOR_CODE)
    print("      Created rl_data_collector.py")
    
    # Step 2: Read daemon
    print("\n[2/3] Reading smt_daemon_v3_1.py...")
    if not os.path.exists(DAEMON_FILE):
        print(f"      ERROR: {DAEMON_FILE} not found!")
        return False
    
    with open(DAEMON_FILE, "r") as f:
        content = f.read()
    
    # Check if already patched
    if "RL_ENABLED" in content:
        print("      Already patched for RL collection!")
        return True
    
    # Step 3: Apply patches
    print("\n[3/3] Patching daemon...")
    
    # Add import after other imports
    import_marker = "from typing import Dict, List, Optional"
    if import_marker in content:
        content = content.replace(
            import_marker,
            import_marker + IMPORT_PATCH
        )
        print("      Added RL collector import")
    else:
        print("      WARNING: Could not find import location")
    
    # Add decision logging after ai_log["analyses"].append
    log_marker = '''ai_log["analyses"].append({
                    "pair": pair,
                    "tier": tier,
                    "decision": signal,
                    "confidence": confidence,
                    "has_long": has_long,
                    "has_short": has_short,
                    "trade_type": trade_type,
                })'''
    
    if log_marker in content:
        content = content.replace(
            log_marker,
            log_marker + DECISION_LOG_CODE
        )
        print("      Added RL decision logging")
    else:
        print("      WARNING: Could not find decision log location")
        print("      You may need to manually add RL logging")
    
    # Update version in header
    content = content.replace(
        "V3.1.23 - REGIME-ALIGNED TRADING",
        "V3.1.24 - REGIME-ALIGNED + RL DATA COLLECTION"
    )
    
    # Write back
    with open(DAEMON_FILE, "w") as f:
        f.write(content)
    
    print("\n" + "=" * 60)
    print("PATCH COMPLETE!")
    print("=" * 60)
    print("""
Files created/modified:
  - rl_data_collector.py (NEW)
  - smt_daemon_v3_1.py (PATCHED)

New data will be saved to:
  - rl_training_data/exp_YYYYMMDD.jsonl

To verify:
  python3 -c "from rl_data_collector import RLDataCollector; print('OK')"

To check collected data:
  ls -la rl_training_data/
  head rl_training_data/exp_*.jsonl

Restart daemon:
  pkill -f smt_daemon
  nohup python3 smt_daemon_v3_1.py > daemon.log 2>&1 &
  
Commit:
  git add -A && git commit -m "V3.1.24: Add RL data collection" && git push
""")
    
    return True


if __name__ == "__main__":
    main()
