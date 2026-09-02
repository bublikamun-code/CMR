#!/bin/bash
# Резервное копирование CRM. Пишет ВНЕ www (не доступно из веба).
# Запуск: bash ~/backup_crm.sh   |   крон: 0 3 * * *
#
# FIX 2026-08-29: данные переехали из веб-корна в /var/www/h212005/data/crm_data;
# добавлен PRAGMA integrity_check снимка; uploads бэкапятся ЕЖЕДНЕВНО (раньше
# раз в неделю — сбой до воскресенья терял до 6 дней вложений); при ошибке БД
# скрипт падает с ненулевым кодом (крон пишет в backup.log).
APP="/var/www/h212005/data/www/cmr-svetvdome.online"
DATA="/var/www/h212005/data/crm_data"
DST="$HOME/backups"
KEEP=14
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$DST"
log() { echo "$(date '+%F %T') $*"; }

log "=== backup start $TS ==="

# 1) База: sqlite3 .backup — консистентный снимок на живой БД (cp в WAL-режиме даёт битый файл)
if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "$DATA/crm_app.db" ".backup '$DST/db_$TS.sqlite'"; then
        CHECK=$(sqlite3 "$DST/db_$TS.sqlite" "PRAGMA integrity_check;")
        if [ "$CHECK" != "ok" ]; then
            log "FAIL: integrity_check = $CHECK"
            rm -f "$DST/db_$TS.sqlite"
            exit 1
        fi
        gzip -f "$DST/db_$TS.sqlite"
        log "OK db -> db_$TS.sqlite.gz ($(du -h "$DST/db_$TS.sqlite.gz" | cut -f1), integrity ok)"
    else
        log "FAIL: sqlite3 .backup"
        exit 1
    fi
else
    log "FAIL: sqlite3 not found"
    exit 1
fi

# 2) Загруженные файлы — ежедневно (417 файлов, ~6 МБ, дешевле потери)
if [ -d "$DATA/uploads" ]; then
    tar czf "$DST/uploads_$TS.tar.gz" -C "$DATA" uploads 2>/dev/null && log "OK uploads -> uploads_$TS.tar.gz ($(du -h "$DST/uploads_$TS.tar.gz" | cut -f1))"
fi

# 3) Код (без мусора, venv, баз и загрузок)
tar czf "$DST/code_$TS.tar.gz" -C "$APP" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.bak_*' --exclude='*.backup_*' --exclude='uploads_migrated_bak_*' \
    --exclude='.venv' --exclude='backups' --exclude='logs' \
    --exclude='uploads' --exclude='__pycache__' --exclude='.git' \
    . 2>/dev/null && log "OK code -> code_$TS.tar.gz ($(du -h "$DST/code_$TS.tar.gz" | cut -f1))"

# 4) Чувствительное: настройки почты и ключи данных каталога
if [ -d "$DATA" ]; then
    tar czf "$DST/crmdata_secret_$TS.tar.gz" -C "$DATA" \
        --exclude='uploads' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
        --exclude='backups_legacy' . 2>/dev/null && chmod 600 "$DST/crmdata_secret_$TS.tar.gz" && log "OK crmdata secrets (600)"
fi

# 5) Ротация — держим KEEP последних каждого вида
for pat in db_ code_ uploads_ crmdata_secret_; do
    ls -t "$DST"/${pat}* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
done

# 6) Чекпоинт WAL, чтобы журнал не разрастался
sqlite3 "$DATA/crm_app.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 && log "OK wal checkpoint"

log "=== done. Место: $(du -sh "$DST" | cut -f1), файлов: $(ls -1 "$DST" | wc -l) ==="
