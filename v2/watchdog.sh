#!/bin/bash
# V2 WATCHDOG - 15min timeout (matches v3 watchdog logic)
TIMEOUT=900  # 15 minutes without log activity = hung
LOG_FILE=~/smt-weex-trading-bot/v2/logs/daemon.log

while true; do
    sleep 30  # Check every 30 seconds

    DAEMON_COUNT=$(pgrep -f "smt_daemon_v2_1.py" 2>/dev/null | wc -l)

    # Kill duplicates
    if [ "$DAEMON_COUNT" -gt 1 ]; then
        echo "$(date) | WATCHDOG: $DAEMON_COUNT daemons. Killing ALL." >> ~/smt-weex-trading-bot/v2/logs/watchdog.log
        pkill -9 -f "smt_daemon_v2_1.py"
        sleep 5
        cd ~/smt-weex-trading-bot/v2
        nohup python3 smt_daemon_v2_1.py >> logs/daemon.log 2>&1 &
        sleep 60
        continue
    fi

    # Restart if dead
    if [ "$DAEMON_COUNT" -eq 0 ]; then
        echo "$(date) | WATCHDOG: No daemon. Starting." >> ~/smt-weex-trading-bot/v2/logs/watchdog.log
        cd ~/smt-weex-trading-bot/v2
        nohup python3 smt_daemon_v2_1.py >> logs/daemon.log 2>&1 &
        sleep 60
        continue
    fi

    # Detect hangs - check log freshness
    if [ -f "$LOG_FILE" ]; then
        LAST_MOD=$(stat -c %Y "$LOG_FILE")
        NOW=$(date +%s)
        DIFF=$((NOW - LAST_MOD))

        if [ $DIFF -gt $TIMEOUT ]; then
            echo "$(date) | WATCHDOG: Stale ${DIFF}s (>${TIMEOUT}s). SIGKILL + restart." >> ~/smt-weex-trading-bot/v2/logs/watchdog.log
            pkill -9 -f "smt_daemon_v2_1.py"
            sleep 5
            cd ~/smt-weex-trading-bot/v2
            nohup python3 smt_daemon_v2_1.py >> logs/daemon.log 2>&1 &
            sleep 60
        fi
    fi
done
