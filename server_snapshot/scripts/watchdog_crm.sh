#!/bin/bash
# Watchdog для PM2-managed CRM. Запускается cron каждые 5 минут.
# Liveness проверяет процесс, readiness — зависимости перед фоновой работой.

APP_DIR="/var/www/h212005/data/www/cmr-svetvdome.online"
LOG_DIR="/var/www/h212005/data/backups"
LOG_FILE="$LOG_DIR/watchdog.log"
# Must match PORT in ecosystem.config.js. The previous version of this script
# used 8000, which on this shared host belongs to an unrelated application, so
# every sync request returned 404 and email sync never ran.
APP_PORT="${APP_PORT:-20008}"
mkdir -p "$LOG_DIR"

# Source nvm to make pm2 available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

pm2_status() {
    pm2 jlist 2>/dev/null | python3 -c "
import sys, json
try:
    apps = json.load(sys.stdin)
    crm = next((a for a in apps if a.get('name') == 'crm'), None)
    print(crm['pm2_env']['status'] if crm else 'stopped')
except Exception:
    print('stopped')
"
}

health_code() {
    local code
    code=$(curl -sS -m 10 -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:$APP_PORT/$1" 2>/dev/null || true)
    [ -n "$code" ] || code="000"
    printf "%s" "$code"
}

PM2_STATUS=$(pm2_status)
LIVE_CODE=$(health_code "health/live")

# FIX 2026-09-04 (аудит отказоустойчивости): после перезагрузки сервера
# демон PM2 мёртв, и «pm2 restart crm» на пустом демоне приложение не
# поднимал — CRM молча лежала до ручного вмешательства. Сначала resurrect,
# при неудаче — start из ecosystem.config.js.
if [ "$PM2_STATUS" != "online" ]; then
    echo "$(date '+%F %T') CRM stopped, restoring via PM2" >> "$LOG_FILE"
    if pm2 resurrect 2>>"$LOG_FILE"; then
        if [ "$(pm2_status)" != "online" ]; then
            echo "$(date '+%F %T') PM2 resurrect completed without online CRM, starting from config" >> "$LOG_FILE"
            cd "$APP_DIR" && pm2 start ecosystem.config.js 2>&1 | head -5 >> "$LOG_FILE"
        fi
    else
        cd "$APP_DIR" && pm2 start ecosystem.config.js 2>&1 | head -5 >> "$LOG_FILE"
    fi
elif [ "$LIVE_CODE" != "200" ]; then
    # Наружу процесс жив, но liveness не отвечает: это process failure, а не
    # проблема БД/uploads, поэтому проверяем readiness только после рестарта.
    echo "$(date '+%F %T') CRM process liveness failed, restarting" >> "$LOG_FILE"
    pm2 restart crm --update-env >> "$LOG_FILE" 2>&1
fi

sleep 6
PM2_STATUS=$(pm2_status)
LIVE_CODE=$(health_code "health/live")
READY_CODE=$(health_code "health/ready")
echo "$(date '+%F %T') PM2=$PM2_STATUS live=$LIVE_CODE ready=$READY_CODE" >> "$LOG_FILE"

# Trigger scheduled email sync for all tenants.
# Requires .cron_token to exist on the server; it is generated on first backend
# boot by auth.py. Pass it via X-Cron-Token header.
if [ "$PM2_STATUS" = "online" ] && [ "$LIVE_CODE" = "200" ] && [ "$READY_CODE" = "200" ] && [ -f "$APP_DIR/.cron_token" ]; then
    CRON_TOKEN=$(cat "$APP_DIR/.cron_token")
    curl -s -X POST \
        -H "X-Cron-Token: $CRON_TOKEN" \
        -m 120 \
        -w '\n' \
        "http://127.0.0.1:$APP_PORT/email-parser/sync-all" \
        >> "$LOG_FILE" 2>&1 || echo "$(date '+%F %T') email sync request failed" >> "$LOG_FILE"
elif [ "$PM2_STATUS" = "online" ] && [ "$READY_CODE" != "200" ]; then
    echo "$(date '+%F %T') skipping email sync: application not ready" >> "$LOG_FILE"
elif [ "$PM2_STATUS" = "online" ]; then
    echo "$(date '+%F %T') skipping email sync: .cron_token missing" >> "$LOG_FILE"
fi

# Keep the log from growing without bound; cron appends to it every 5 minutes.
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 2000000 ]; then
    tail -c 500000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
