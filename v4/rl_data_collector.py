#!/usr/bin/env python3
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
            f.write(json.dumps(entry, default=str) + "\n")
    
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
