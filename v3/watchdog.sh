#!/bin/bash
# watchdog.sh - restarts daemon if no log activity for 5 minutes

TIMEOUT=300

while true; do
    sleep 60

    DAEMON_COUNT=$(pgrep -fc "smt_daemon_v3_1.py")

    # If multiple daemons, kill all and restart one
    if [ "$DAEMON_COUNT" -gt 1 ]; then
        echo "$(date) | WATCHDOG: $DAEMON_COUNT daemons found. Killing all and restarting one..." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        pkill -f "smt_daemon_v3_1.py"
        sleep 5
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 30
        continue
    fi

    # If no daemon, start one
    if [ "$DAEMON_COUNT" -eq 0 ]; then
        echo "$(date) | WATCHDOG: Daemon not running. Starting..." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
        cd ~/smt-weex-trading-bot/v3
        nohup python3 smt_daemon_v3_1.py >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
        sleep 30
        continue
    fi

    # If daemon exists but log is stale, kill and restart
    LOG_FILE=~/smt-weex-trading-bot/v3/logs/daemon_v3_1_7_$(date +%Y%m%d).log
    if [ -f "$LOG_FILE" ]; then
        LAST_MOD=$(stat -c %Y "$LOG_FILE")
        NOW=$(date +%s)
        DIFF=$((NOW - LAST_MOD))

        if [ $DIFF -gt $TIMEOUT ]; then
            echo "$(date) | WATCHDOG: Log stale for ${DIFF}s. Killing and restarting..." >> ~/smt-weex-trading-bot/v3/logs/watchdog.log
            pkill -f "smt_daemon_v3_1.py"
            sleep 5
            cd ~/smt-weex-trading-bot/v3
            nohup python3 smt_daemon_v3_1.py >> logs/daemon_v3_1_7_$(date +%Y%m%d).log 2>&1 &
            sleep 30
        fi
    fi
done
