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

# Токен фоновых задач. Приоритет источников ДОЛЖЕН совпадать с тем, как их
# видит приложение: server.py читает .pm2.env через setdefault, auth.py сначала
# смотрит окружение и только потом падает в файл.
#
#   1) переменная окружения — так же, как в приложении;
#   2) CRM_CRON_TOKEN в .pm2.env — источник боевых переменных на сервере;
#   3) файл .cron_token — то, что auth.py генерирует при первом старте.
#
# FIX 2026-09-30 (аудит фоновых задач): раньше брался ТОЛЬКО пункт 3. Сценарий
# потери почты: оператор прописывает CRM_CRON_TOKEN в .pm2.env (так советует
# .env.example), а .cron_token на сервере лежит со старым значением. Приложение
# проверяет .pm2.env, watchdog шлёт из файла — каждый запуск 403, синк мёртв
# неделями, и в лог падает одна строка «email sync request failed».
#
# Пункт 2 добавлен после разбора: cron не наследует окружение PM2, поэтому
# одной проверки переменной окружения было бы недостаточно — токен лежит
# именно в файле, из которого приложение читает переменные при старте.
cron_token() {
    if [ -n "${CRM_CRON_TOKEN:-}" ]; then
        printf '%s' "$CRM_CRON_TOKEN"
        return 0
    fi
    if [ -f "$APP_DIR/.pm2.env" ]; then
        # Разбор ДОЛЖЕН совпадать с server.py построчно: он делит строку по
        # первому "=" и делает .strip(), НЕ срезая кавычки и хвостовой
        # комментарий. Своя «умная» очистка здесь создала бы новый рассинхрон
        # ровно того вида, который эта правка и устраняет. Токены генерируются
        # как secrets.token_hex(32), то есть всегда чистый hex — но полагаться
        # на это при разборе конфигурации нельзя.
        local from_file="" line key value
        while IFS= read -r line || [ -n "$line" ]; do
            line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            case "$line" in
                ""|\#*) continue ;;
                *=*) ;;
                *) continue ;;
            esac
            key=${line%%=*}
            value=${line#*=}
            key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            value=$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            if [ "$key" = "CRM_CRON_TOKEN" ]; then
                # os.environ.setdefault: последнее присваивание побеждает.
                from_file=$value
            fi
        done < "$APP_DIR/.pm2.env"
        if [ -n "$from_file" ]; then
            printf '%s' "$from_file"
            return 0
        fi
    fi
    if [ -f "$APP_DIR/.cron_token" ]; then
        tr -d '\r\n' < "$APP_DIR/.cron_token"
        return 0
    fi
    printf ''
}

if [ "$PM2_STATUS" = "online" ] && [ "$LIVE_CODE" = "200" ] && [ "$READY_CODE" = "200" ]; then
    CRON_TOKEN=$(cron_token)
    if [ -z "$CRON_TOKEN" ]; then
        echo "$(date '+%F %T') skipping email sync: no cron token in env, .pm2.env or .cron_token" >> "$LOG_FILE"
    else
        curl -s -X POST \
            -H "X-Cron-Token: $CRON_TOKEN" \
            -m 120 \
            -w '\n' \
            "http://127.0.0.1:$APP_PORT/email-parser/sync-all" \
            >> "$LOG_FILE" 2>&1 || echo "$(date '+%F %T') email sync request failed" >> "$LOG_FILE"
    fi
elif [ "$PM2_STATUS" = "online" ] && [ "$READY_CODE" != "200" ]; then
    echo "$(date '+%F %T') skipping email sync: application not ready" >> "$LOG_FILE"
fi

# Keep the log from growing without bound; cron appends to it every 5 minutes.
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 2000000 ]; then
    tail -c 500000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
