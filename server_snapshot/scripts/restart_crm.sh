#!/bin/bash
# Корректный перезапуск CRM (без pm2 — его на хостинге нет).
APP="/var/www/h212005/data/www/cmr-svetvdome.online"
mkdir -p "$APP/logs"

echo "$(date '+%F %T') остановка..."
pkill -u "$(id -u)" -f "$APP/server.py" 2>/dev/null
sleep 3
pkill -9 -u "$(id -u)" -f "$APP/server.py" 2>/dev/null
sleep 1

cd "$APP" || exit 1
nohup "$APP/.venv/bin/python" "$APP/server.py" >> "$APP/logs/crm.log" 2>&1 &
sleep 6

PID=$(pgrep -u "$(id -u)" -f "$APP/server.py" | head -1)
if [ -z "$PID" ]; then
    echo "$(date '+%F %T') ОШИБКА ЗАПУСКА:"
    tail -30 "$APP/logs/crm.log"
    exit 1
fi
echo "$(date '+%F %T') OK, pid=$PID"

# Порт берём У САМОГО ПРОЦЕССА, а не из лога: в логе копятся строки
# со всех прошлых запусков, и grep|tail хватал чужой порт (8000),
# где отвечает другое приложение -> ложный "health: HTTP 404".
PORT=$(ss -lptnH 2>/dev/null | grep -o "pid=$PID," -B0 >/dev/null 2>&1; \
       ss -lptn 2>/dev/null | awk -v p="pid=$PID," '$0 ~ p {split($4,a,":"); print a[length(a)]; exit}')

if [ -z "$PORT" ]; then
    # запасной путь: последняя строка лога ПОСЛЕ текущего старта
    PORT=$(tail -40 "$APP/logs/crm.log" | grep -oE "(Starting CRM on|Uvicorn running on) https?://[0-9.]+:[0-9]+" | tail -1 | sed 's/.*://')
fi
[ -z "$PORT" ] && PORT="${APPS_PORT:-20008}"
echo "порт: $PORT"

CODE=$(curl -sS -m 10 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" 2>/dev/null)
if [ "$CODE" != "200" ]; then
    # перебираем реально слушающие порты этого процесса
    for TRY in $(ss -lptn 2>/dev/null | awk -v p="pid=$PID," '$0 ~ p {split($4,a,":"); print a[length(a)]}'); do
        C=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$TRY/health" 2>/dev/null)
        [ "$C" = "200" ] && PORT="$TRY" && CODE="200" && echo "порт уточнён: $PORT" && break
    done
fi
echo "health: HTTP $CODE"
[ "$CODE" = "200" ] || { echo "ВНИМАНИЕ: /health не ответил. Последние строки лога:"; tail -15 "$APP/logs/crm.log"; }
curl -sS -m 10 "http://127.0.0.1:$PORT/api/version" 2>/dev/null; echo
