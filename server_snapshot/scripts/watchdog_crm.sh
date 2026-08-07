#!/bin/bash
# Поднимает CRM, если процесс упал. Крон: */5 * * * *
APP="/var/www/h212005/data/www/cmr-svetvdome.online"
LOG="/var/www/h212005/data/backups/watchdog.log"
mkdir -p "$(dirname "$LOG")" "$APP/logs"

if pgrep -u "$(id -u)" -f "$APP/server.py" > /dev/null 2>&1; then
    :
else
    echo "$(date '+%F %T') CRM НЕ РАБОТАЕТ — запускаю" >> "$LOG"
    cd "$APP" || exit 1
    nohup "$APP/.venv/bin/python" "$APP/server.py" >> "$APP/logs/crm.log" 2>&1 &
    sleep 6

    PID=$(pgrep -u "$(id -u)" -f "$APP/server.py" | head -1)
    if [ -n "$PID" ]; then
        echo "$(date '+%F %T') запущен, pid=$PID" >> "$LOG"
    else
        echo "$(date '+%F %T') ЗАПУСК НЕ УДАЛСЯ:" >> "$LOG"
        tail -15 "$APP/logs/crm.log" >> "$LOG"
    fi
fi

# Автосинхронизация почты для всех тенантов
if pgrep -u "$(id -u)" -f "$APP/server.py" > /dev/null 2>&1; then
    curl -s -X POST "http://127.0.0.1:8000/email-parser/sync-all" >> "$LOG" 2>&1 || true
fi
