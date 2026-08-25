#!/bin/bash
# Резервное копирование CRM. Пишет ВНЕ www (не доступно из веба).
# Запуск: bash ~/backup_crm.sh   |   крон: 0 3 * * *
APP="/var/www/h212005/data/www/cmr-svetvdome.online"
DST="$HOME/backups"
KEEP=7
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$DST"
log() { echo "$(date '+%F %T') $*"; }

log "=== backup start $TS ==="

# 1) База: sqlite3 .backup — консистентный снимок на живой БД (cp в WAL-режиме даёт битый файл)
if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "$APP/crm_app.db" ".backup '$DST/db_$TS.sqlite'"; then
        gzip -f "$DST/db_$TS.sqlite"
        log "OK db -> db_$TS.sqlite.gz ($(du -h "$DST/db_$TS.sqlite.gz" | cut -f1))"
    else
        log "FAIL: sqlite3 .backup"
    fi
else
    log "FAIL: sqlite3 not found"
fi

# 2) Базы тенантов, если есть
if [ -d "$APP/tenants" ]; then
    tar czf "$DST/tenants_$TS.tar.gz" -C "$APP" tenants 2>/dev/null && log "OK tenants"
fi

# 3) Код (без мусора, venv, баз и загрузок)
tar czf "$DST/code_$TS.tar.gz" -C "$APP" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.bak_*' --exclude='*.backup_*' \
    --exclude='.venv' --exclude='backups' --exclude='logs' \
    --exclude='uploads' --exclude='__pycache__' --exclude='.git' \
    . 2>/dev/null && log "OK code -> code_$TS.tar.gz ($(du -h "$DST/code_$TS.tar.gz" | cut -f1))"

# 4) Загруженные файлы — раз в неделю (воскресенье), они тяжёлые
if [ "$(date +%u)" = "7" ] && [ -d "$APP/uploads" ]; then
    tar czf "$DST/uploads_$TS.tar.gz" -C "$APP" uploads 2>/dev/null && log "OK uploads (weekly)"
fi

# 5) Ротация — держим KEEP последних каждого вида
for pat in db_ code_ tenants_ uploads_; do
    ls -t "$DST"/${pat}* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
done

# 6) Чекпоинт WAL, чтобы журнал не разрастался (у вас был 4 МБ)
sqlite3 "$APP/crm_app.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 && log "OK wal checkpoint"

log "=== done. Место: $(du -sh "$DST" | cut -f1), файлов: $(ls -1 "$DST" | wc -l) ==="
