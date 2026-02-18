# v2/ — Production Working Directory (V3.2.1+)

Run the bot from this folder. All scripts are self-contained here.

## Directory Structure

```
v2/
├── smt_daemon_v2_1.py          ← CURRENT DAEMON (run this)
├── smt_daemon_v3_1.py          ← Shim only — routes nightly's cross-imports to current daemon
├── smt_nightly_trade_v3_1.py   ← Core trading logic (keep this exact name — daemon imports it)
├── rl_data_collector.py        ← RL training data collector
├── cryptoracle_client.py       ← Cryptoracle API client
├── close_all_positions.py      ← Emergency close script
├── requirements.txt            ← pip dependencies
├── trade_state_v3_1_7.json     ← Live state (active trades, cooldowns) — NOT in git
├── logs/                       ← Daemon log files — NOT in git
├── rl_training_data/           ← RL JSONL files — NOT in git
└── backup/                     ← Previous version snapshots
    ├── smt_daemon_v2_1.py.bak
    └── smt_nightly_trade_v3_1_v2_1.py.bak
```

## Versioning Scheme

| Current | When new version is made |
|---------|--------------------------|
| `smt_daemon_v2_N.py` | → `backup/smt_daemon_v2_N.py.bak` |
| `smt_nightly_trade_v3_1.py` | → `backup/smt_nightly_trade_v3_1_v2_N.py.bak` |
| New daemon | → `smt_daemon_v2_(N+1).py` |
| New nightly | → overwrite `smt_nightly_trade_v3_1.py` (same name) |
| Shim update | → change `from smt_daemon_v2_N import *` to `v2_(N+1)` |

## On Each Version Bump (e.g. V3.2.2 → v2_2)

```bash
cd ~/smt-weex-trading-bot/v2

# 1. Backup current versions
cp smt_daemon_v2_1.py backup/smt_daemon_v2_1.py.bak
cp smt_nightly_trade_v3_1.py backup/smt_nightly_trade_v3_1_v2_1.py.bak

# 2. Copy new versions from v3/
cp ../v3/smt_daemon_v3_1.py smt_daemon_v2_2.py
cp ../v3/smt_nightly_trade_v3_1.py smt_nightly_trade_v3_1.py

# 3. Update shim to point to new daemon
echo "from smt_daemon_v2_2 import *  # noqa: F401,F403" > smt_daemon_v3_1.py
# (keep the comment header — edit the file rather than overwriting if you want to keep it)
```

## Start / Stop / Restart

```bash
cd ~/smt-weex-trading-bot/v2

# First-time switch from v3/ — copy live state
cp ../v3/trade_state_v3_1_7.json .

# Kill existing daemon (wherever it's running)
ps aux | grep smt_daemon | grep -v grep | awk '{print $2}' | xargs kill

# Start daemon from v2/
nohup python3 smt_daemon_v2_1.py >> logs/daemon.log 2>&1 &
echo "Daemon PID: $!"

# Tail logs
tail -f logs/daemon.log

# Emergency close all positions
python3 close_all_positions.py
```

## Cross-Import Notes

`smt_nightly_trade_v3_1.py` has lazy imports like:
```python
from smt_daemon_v3_1 import get_trade_history_summary
```
The shim `smt_daemon_v3_1.py` re-exports everything from the real daemon (`smt_daemon_v2_1.py`).
This means the nightly never needs editing — only the shim's one import line changes on version bump.
