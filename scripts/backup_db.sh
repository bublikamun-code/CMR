#!/bin/bash
# Консистентные SQLite-снимки из контейнера, включая WAL.
# KEEP=30 CONTAINER=crm-backend-prod bash scripts/backup_db.sh /mnt/nas/crm
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
DEST="${1:-${DST:-backups}}"
CONTAINER="${CONTAINER:-crm-backend}"
KEEP="${KEEP-14}"
# Ограничение длины исключает переполнение арифметики shell.
if [[ ! "$KEEP" =~ ^[1-9][0-9]{0,8}$ ]]; then
    echo 'ERROR: KEEP must be a positive integer (1..999999999).' >&2
    exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
    echo "ERROR: container '$CONTAINER' is not running." >&2
    exit 1
fi
mkdir -p "$DEST"
STAGE="$(mktemp -d "$DEST/.crm-backup.XXXXXXXXXX")"
REMOTE=""
cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$REMOTE" =~ ^/tmp/crm-backup\.[a-zA-Z0-9]+$ ]]; then
        docker exec "$CONTAINER" rm -rf -- "$REMOTE" || status=1
    fi
    if [[ -n "$STAGE" && "$STAGE" == "$DEST"/.crm-backup.* ]]; then
        rm -rf -- "$STAGE" || status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
REMOTE="$(docker exec "$CONTAINER" mktemp -d /tmp/crm-backup.XXXXXXXXXX)"
[[ "$REMOTE" =~ ^/tmp/crm-backup\.[a-zA-Z0-9]+$ ]] || exit 1
RUN="$(date +%Y%m%d_%H%M%S)_${REMOTE##*.}"
# Путь передаётся явно; никакие общие каталоги или wildcard не удаляются.
docker exec -i "$CONTAINER" python - "$RUN" "$REMOTE" <<'PY'
import os
from pathlib import Path
import sqlite3
import sys

run, out_dir = sys.argv[1:]
os.umask(0o077)
data = Path(os.environ.get('CRM_DATA_DIR', '/app/data'))
main = data / 'crm_app.db'
if not main.is_file():
    raise SystemExit(f'ERROR: missing source database: {main}')
targets = [(main, 'crm_app')]
tenants = data / 'tenants'
if tenants.is_dir():
    targets += [(p, 'tenant_' + p.stem) for p in sorted(tenants.glob('*.db'))]
for src, prefix in targets:
    if '\n' in prefix or '\r' in prefix:
        raise SystemExit('ERROR: unsupported database filename')
    dst = Path(out_dir) / f'{prefix}_{run}.db'
    source = sqlite3.connect(src.resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError(f'integrity_check failed: {src}')
    finally:
        target.close()
        source.close()
PY
# Копирование и сжатие полностью заканчиваются до публикации и ротации.
docker cp "$CONTAINER:$REMOTE/." "$STAGE/" >/dev/null
shopt -s nullglob
files=("$STAGE"/*.db)
((${#files[@]} > 0)) || exit 1
for file in "${files[@]}"; do
    chmod 600 "$file"
    gzip -f "$file"
done
for file in "$STAGE"/*.db.gz; do
    mv -- "$file" "$DEST/"
done
# Группировка по точному имени БД, а не по общему tenant_*.
# Распознаём старые timestamp-имена и новые имена с уникальным суффиксом.
# Непустой sentinel сохраняет совместимость с set -u в Bash 3.2.
prefixes=("")
for file in "$DEST"/*.db.gz; do
    name=${file##*/}
    if [[ "$name" =~ ^(crm_app|tenant_.+)_([0-9]{8}_[0-9]{6})(_[a-zA-Z0-9]+)?\.db\.gz$ ]]; then
        prefix=${BASH_REMATCH[1]}
        seen=false
        for item in "${prefixes[@]}"; do
            [[ "$item" != "$prefix" ]] || seen=true
        done
        if ! "$seen"; then prefixes+=("$prefix"); fi
    fi
done
for prefix in "${prefixes[@]}"; do
    [[ -n "$prefix" ]] || continue
    group=("")
    for file in "$DEST"/*.db.gz; do
        name=${file##*/}
        if [[ "$name" =~ ^(crm_app|tenant_.+)_([0-9]{8}_[0-9]{6})(_[a-zA-Z0-9]+)?\.db\.gz$ ]] && [[ "${BASH_REMATCH[1]}" == "$prefix" ]]; then
            group+=("$file")
        fi
    done
    if ((${#group[@]} - 1 > KEEP)); then
        ls -1t -- "${group[@]:1}" | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
            rm -- "$old"
        done
    fi
done
printf '%s\n' '==> Backup complete.' \
    'Восстановление: сначала распакуйте снимок в отдельный файл и выполните PRAGMA integrity_check.' \
    'Остановите все процессы, пишущие в БД (включая контейнер), и убедитесь, что соединений нет.' \
    'Сохраните старую БД вместе с её -wal/-shm для отката. Не копируйте снимок поверх живой БД.' \
    'Только после остановки замените БД, уберите старые WAL/SHM из рабочего пути, восстановите права и запустите сервис.'
