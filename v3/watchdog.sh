#!/bin/bash
while true; do
    if ! pgrep -f "smt_daemon_v3_1.py" > /dev/null; then
        echo "$(date): Daemon died, restarting..."
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py >> daemon.log 2>&1 &
    fi
    sleep 30
done
