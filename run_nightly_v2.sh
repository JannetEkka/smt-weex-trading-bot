#!/bin/bash
# SMT Nightly Trade V2 Runner
# Run manually or via cron at 11 PM IST (5:30 PM UTC)

cd ~/smt-weex-trading-bot
source ~/.bashrc

echo "=========================================="
echo "SMT Nightly Trade V2"
echo "Started: $(date)"
echo "=========================================="

python3 smt_nightly_trade_v2.py 2>&1 | tee -a logs/nightly_v2_$(date +%Y%m%d).log

echo "=========================================="
echo "Completed: $(date)"
echo "=========================================="
