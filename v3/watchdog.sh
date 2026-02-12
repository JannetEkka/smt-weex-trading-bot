#!/bin/bash
# watchdog.sh - restarts daemon if no log activity for 5 minutes

LOG_FILE="logs/daemon_v3_1_7_$(date +%Y%m%d).log"
DAEMON_CMD="python3 smt_daemon_v3_1.py"
TIMEOUT=300  # 5 minutes

while true; do
    sleep 60
    
    # Check if daemon process exists
    if ! pgrep -f "smt_daemon_v3_1.py" > /dev/null; then
        echo "$(date) | WATCHDOG: Daemon not running. Restarting..." >> logs/watchdog.log
        cd ~/smt-weex-trading-bot/v3
        nohup $DAEMON_CMD >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 30
        continue
    fi
    
    # Check if log file was modified in last 5 minutes
    LOG_FILE="logs/daemon_v3_1_7_$(date +%Y%m%d).log"
    if [ -f "$LOG_FILE" ]; then
        LAST_MOD=$(stat -c %Y "$LOG_FILE")
        NOW=$(date +%s)
        DIFF=$((NOW - LAST_MOD))
        
        if [ $DIFF -gt $TIMEOUT ]; then
            echo "$(date) | WATCHDOG: Log stale for ${DIFF}s. Killing and restarting..." >> logs/watchdog.log
            pkill -f "smt_daemon_v3_1.py"
            sleep 5
            cd ~/smt-weex-trading-bot/v3
            nohup $DAEMON_CMD >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
            sleep 30
        fi
    fi
done
