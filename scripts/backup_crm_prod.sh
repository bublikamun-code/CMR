#!/bin/bash
# Локальный скрипт резервного копирования CRM вне веб-корня.
set -euo pipefail
umask 077
APP="${APP:-/var/www/h212005/data/www/cmr-svetvdome.online}"
DATA="${DATA:-/var/www/h212005/data/crm_data}"
DST="${DST:-$HOME/backups}"
KEEP="${KEEP-14}"
if [[ ! "$KEEP" =~ ^[1-9][0-9]{0,8}$ ]]; then
    echo 'FAIL: KEEP must be a positive integer (1..999999999).' >&2
    exit 1
fi
log() { echo "$(date '+%F %T') $*"; }
# -readonly дополнительно закрывает гонку удаления источника перед sqlite3.
[[ -f "$DATA/crm_app.db" ]] || { log "FAIL: missing source $DATA/crm_app.db"; exit 1; }
command -v sqlite3 >/dev/null || { log 'FAIL: sqlite3 not found'; exit 1; }
mkdir -p "$DST"
STAGE="$(mktemp -d "$DST/.crm-backup.XXXXXXXXXX")"
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$STAGE" && "$STAGE" == "$DST"/.crm-backup.* ]]; then
        rm -rf -- "$STAGE" || status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
TS="$(date +%Y%m%d_%H%M%S)_${STAGE##*.}"
log "=== backup start $TS ==="
# Относительный путь .backup не требует SQL-экранирования пути DST.
(cd "$STAGE" && sqlite3 -readonly "$DATA/crm_app.db" '.backup db.sqlite')
# Снимок в WAL-режиме нельзя открыть -readonly без -shm (ошибка 14); это наша
# приватная копия, integrity_check ничего не пишет — открываем как обычно.
CHECK=$(sqlite3 "$STAGE/db.sqlite" 'PRAGMA integrity_check;')
[[ "$CHECK" == ok ]] || { log "FAIL: integrity_check = $CHECK"; exit 1; }
gzip -f "$STAGE/db.sqlite"
mv "$STAGE/db.sqlite.gz" "$STAGE/db_$TS.sqlite.gz"

# Все имеющиеся части обязательны; ошибки tar/gzip видны и останавливают запуск.
if [[ -d "$DATA/uploads" ]]; then
    tar czf "$STAGE/uploads_$TS.tar.gz" -C "$DATA" uploads
fi
tar czf "$STAGE/code_$TS.tar.gz" -C "$APP" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.bak_*' --exclude='*.backup_*' --exclude='uploads_migrated_bak_*' \
    --exclude='.venv' --exclude='backups' --exclude='logs' \
    --exclude='uploads' --exclude='__pycache__' --exclude='.git' .
tar czf "$STAGE/crmdata_secret_$TS.tar.gz" -C "$DATA" \
    --exclude='uploads' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='backups_legacy' .

# Публикуем только после успеха всех обязательных частей.
for file in "$STAGE"/*.gz; do
    chmod 600 "$file"
    mv -- "$file" "$DST/"
done
# Ротация только завершённых архивов; ошибки удаления не скрываются.
shopt -s nullglob
for prefix in db code uploads crmdata_secret; do
    group=()
    for file in "$DST"/"${prefix}"_*; do
        name=${file##*/}
        suffix=${name#"${prefix}_"}
        if [[ "$prefix" == db ]]; then
            [[ "$suffix" =~ ^[0-9]{8}_[0-9]{6}(_[a-zA-Z0-9]+)?\.sqlite\.gz$ ]] || continue
        else
            [[ "$suffix" =~ ^[0-9]{8}_[0-9]{6}(_[a-zA-Z0-9]+)?\.tar\.gz$ ]] || continue
        fi
        group+=("$file")
    done
    if ((${#group[@]} > KEEP)); then
        ls -1t -- "${group[@]}" | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
            rm -- "$old"
        done
    fi
done
# Обслуживание WAL не влияет на уже проверенный снимок.
if ! sqlite3 "$DATA/crm_app.db" 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null; then
    log 'WARN: WAL checkpoint failed (backup is complete)'
fi
log '=== backup complete ==='
