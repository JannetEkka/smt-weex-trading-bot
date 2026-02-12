#!/bin/bash
TIMEOUT=1200

while true; do
    sleep 60

    DAEMON_COUNT=$(pgrep -fc "smt_daemon_v3_1.py" 2>/dev/null || echo 0)

    if [ "$DAEMON_COUNT" -gt 1 ]; then
        echo "$(date) | WATCHDOG: $DAEMON_COUNT daemons. Killing ALL." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        pkill -9 -f "smt_daemon_v3_1.py"
        sleep 5
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 60
        continue
    fi

    if [ "$DAEMON_COUNT" -eq 0 ]; then
        echo "$(date) | WATCHDOG: No daemon. Starting." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 60
        continue
    fi

    LOG_FILE=~/smt-weex-trading-bot/v3/logs/daemon_v3_1_7_$(date +%Y%m%d).log
    if [ -f "$LOG_FILE" ]; then
        LAST_MOD=$(stat -c %Y "$LOG_FILE")
        NOW=$(date +%s)
        DIFF=$((NOW - LAST_MOD))

        if [ $DIFF -gt $TIMEOUT ]; then
            echo "$(date) | WATCHDOG: Stale ${DIFF}s. KILL then restart." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
            pkill -9 -f "smt_daemon_v3_1.py"
            sleep 5
            cd ~/smt-weex-trading-bot/v3
            nohup python3 smt_daemon_v3_1.py --force >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
            sleep 60
        fi
    fi
done
