#!/bin/bash
# Резервное копирование CRM. Пишет ВНЕ www (не доступно из веба),
# ВСЕ артефакты шифруются AES-256-CBC (PBKDF2) — №3 аудита 06.09.
# Запуск: крон 0 3 * * * (см. crontab -l)
#
# ВОССТАНОВЛЕНИЕ (нужен файл ключа $BK_KEY — его копия хранится ВНЕ сервера,
# без ключа зашифрованные бэкапы не восстановить):
#   openssl enc -d -aes-256-cbc -pbkdf2 -in db_TS.sqlite.gz.enc \
#               -out db_TS.sqlite.gz -pass file:"$BK_KEY"
#   gunzip db_TS.sqlite.gz && sqlite3 restore.sqlite "PRAGMA integrity_check;"
#   (далее — остановить приложение, заменить CRM_DATA/crm_app.db, запустить)
APP="/var/www/h212005/data/www/cmr-svetvdome.online"
DATA="/var/www/h212005/data/crm_data"
DST="$HOME/backups"
KEEP=14
BK_DIR="/var/www/h212005/data/keys"
BK_KEY="$BK_DIR/.backup_key"
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$DST"
log() { echo "$(date '+%F %T') $*"; }

# Ключ шифрования бэкапов: генерируется один раз, права 600.
# КОПИЮ ключа хранить вне сервера (парольный менеджер/офлайн) — при потере
# и сервера, и ключа восстановить данные будет невозможно.
umask 077
mkdir -p "$BK_DIR" && chmod 700 "$BK_DIR"
if [ ! -s "$BK_KEY" ]; then
    openssl rand -base64 48 > "$BK_KEY" && chmod 600 "$BK_KEY" && log "создан новый ключ шифрования: $BK_KEY"
fi

# Шифрование артефакта: на выходе только .enc, открытая копия удаляется.
enc() {
    openssl enc -aes-256-cbc -pbkdf2 -salt -in "$1" -out "$1.enc" -pass file:"$BK_KEY" \
        && rm -f "$1" && chmod 600 "$1.enc"
}

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
        enc "$DST/db_$TS.sqlite.gz" \
            && log "OK db -> db_$TS.sqlite.gz.enc ($(du -h "$DST/db_$TS.sqlite.gz.enc" | cut -f1), integrity ok)" \
            || { log "FAIL: шифрование db"; exit 1; }
    else
        log "FAIL: sqlite3 .backup"
        exit 1
    fi
else
    log "FAIL: sqlite3 not found"
    exit 1
fi

# 1b) Тенант-базы — FIX 2026-09-03 (аудит): раньше бэкапилась только
# основная БД, данные тенантов при аварии терялись. Тот же .backup +
# integrity_check, архив с правами 600.
if [ -d "$DATA/tenants" ]; then
    TDIR="$DST/.tenants_$TS"
    mkdir -p "$TDIR"
    for tdb in "$DATA"/tenants/*.db; do
        [ -f "$tdb" ] || continue
        name=$(basename "$tdb")
        if sqlite3 "$tdb" ".backup '$TDIR/$name'"; then
            CHECK=$(sqlite3 "$TDIR/$name" "PRAGMA integrity_check;")
            if [ "$CHECK" != "ok" ]; then
                log "FAIL: tenants/$name integrity_check = $CHECK"
                rm -f "$TDIR/$name"
            fi
        else
            log "FAIL: sqlite3 .backup tenants/$name"
        fi
    done
    if [ -n "$(ls -A "$TDIR" 2>/dev/null)" ]; then
        tar czf "$DST/tenants_$TS.tar.gz" -C "$TDIR" . \
            && enc "$DST/tenants_$TS.tar.gz" \
            && log "OK tenants -> tenants_$TS.tar.gz.enc ($(du -h "$DST/tenants_$TS.tar.gz.enc" | cut -f1))"
    fi
    rm -rf "$TDIR"
fi

# 2) Загруженные файлы — ежедневно (417 файлов, ~6 МБ, дешевле потери)
if [ -d "$DATA/uploads" ]; then
    tar czf "$DST/uploads_$TS.tar.gz" -C "$DATA" uploads 2>/dev/null \
        && enc "$DST/uploads_$TS.tar.gz" \
        && log "OK uploads -> uploads_$TS.tar.gz.enc ($(du -h "$DST/uploads_$TS.tar.gz.enc" | cut -f1))"
fi

# 3) Код (без мусора, venv, баз и загрузок)
tar czf "$DST/code_$TS.tar.gz" -C "$APP" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.bak_*' --exclude='*.backup_*' --exclude='uploads_migrated_bak_*' \
    --exclude='.venv' --exclude='backups' --exclude='logs' \
    --exclude='uploads' --exclude='__pycache__' --exclude='.git' \
    . 2>/dev/null \
    && enc "$DST/code_$TS.tar.gz" \
    && log "OK code -> code_$TS.tar.gz.enc ($(du -h "$DST/code_$TS.tar.gz.enc" | cut -f1))"

# 4) Чувствительное: настройки почты и ключи данных каталога
if [ -d "$DATA" ]; then
    tar czf "$DST/crmdata_secret_$TS.tar.gz" -C "$DATA" \
        --exclude='uploads' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
        --exclude='backups_legacy' . 2>/dev/null \
        && enc "$DST/crmdata_secret_$TS.tar.gz" \
        && log "OK crmdata secrets (.enc, 600)"
fi

# 5) Ротация — держим KEEP последних каждого вида
for pat in db_ code_ uploads_ crmdata_secret_ tenants_; do
    ls -t "$DST"/${pat}* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
done

# 6) Чекпоинт WAL, чтобы журнал не разрастался
sqlite3 "$DATA/crm_app.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 && log "OK wal checkpoint"

log "=== done. Место: $(du -sh "$DST" | cut -f1), файлов: $(ls -1 "$DST" | wc -l) ==="
