#!/bin/bash
# Watchdog for PM2-managed CRM. Runs from cron every 5 minutes.
# Restarts the PM2 app when it has stopped. Triggers email sync when running.

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

# Check if PM2 process is online
PM2_STATUS=$(pm2 jlist 2>/dev/null | python3 -c "
import sys, json
try:
    apps = json.load(sys.stdin)
    crm = next((a for a in apps if a.get('name') == 'crm'), None)
    print(crm['pm2_env']['status'] if crm else 'stopped')
except:
    print('stopped')
")

if [ "$PM2_STATUS" != "online" ]; then
    echo "$(date '+%F %T') CRM stopped, restarting via PM2" >> "$LOG_FILE"
    pm2 restart crm 2>&1 | head -5 >> "$LOG_FILE"
    sleep 6
    pm2 list >> "$LOG_FILE" 2>&1
fi

# Trigger scheduled email sync for all tenants.
# Requires .cron_token to exist on the server; it is generated on first backend
# boot by auth.py. Pass it via X-Cron-Token header.
if [ "$PM2_STATUS" = "online" ] && [ -f "$APP_DIR/.cron_token" ]; then
    CRON_TOKEN=$(cat "$APP_DIR/.cron_token")
    curl -s -X POST \
        -H "X-Cron-Token: $CRON_TOKEN" \
        -m 120 \
        -w '\n' \
        "http://127.0.0.1:$APP_PORT/email-parser/sync-all" \
        >> "$LOG_FILE" 2>&1 || echo "$(date '+%F %T') email sync request failed" >> "$LOG_FILE"
elif [ "$PM2_STATUS" = "online" ]; then
    echo "$(date '+%F %T') skipping email sync: .cron_token missing" >> "$LOG_FILE"
fi

# Keep the log from growing without bound; cron appends to it every 5 minutes.
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 2000000 ]; then
    tail -c 500000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
