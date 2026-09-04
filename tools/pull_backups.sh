#!/usr/bin/env bash
#
# Офсайт-копия бэкапов CRM: забирает дампы БД (и опционально uploads)
# с прода на этот компьютер. Единственная копия бэкапов НЕ должна жить
# на одном диске с продом — см. docs/audits/AUDIT-2026-09-04.md, P0-2.
#
# Запуск:
#   tools/pull_backups.sh                # только дампы БД (лёгкие)
#   tools/pull_backups.sh --with-uploads # + свежий архив uploads (~300 МБ)
#
# Совет: раз в неделю (напр., понедельник 09:00). На macOS автоматизируется
# через launchd или `crontab -e`: 0 9 * * 1 /bin/bash /полный/путь/tools/pull_backups.sh
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/crm_svetvdome_deploy}"
SSH_USER="${SSH_USER:-h212005}"
SSH_HOST="${SSH_HOST:-87.232.64.12}"
REMOTE_BAK="${REMOTE_BAK:-/var/www/h212005/data/backups}"
DEST="${DEST:-$HOME/crm-offsite}"
KEEP=8
WITH_UPLOADS=false
[ "${1:-}" = "--with-uploads" ] && WITH_UPLOADS=true

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
mkdir -p "$DEST"

echo "==> Дампы БД: $REMOTE_BAK/db_*.sqlite.gz -> $DEST/"
rsync -avz --human-readable \
    -e "ssh ${SSH_OPTS[*]}" \
    --include='db_*.sqlite.gz' --exclude='*' \
    "$SSH_USER@$SSH_HOST:$REMOTE_BAK/" "$DEST/"

# Офсайт-архив uploads: тяжёлый (~300 МБ), тянем только свежий
if $WITH_UPLOADS; then
    LATEST_UPLOAD=$(ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" \
        "ls -t $REMOTE_BAK/uploads_*.tar.gz 2>/dev/null | head -1")
    if [ -n "${LATEST_UPLOAD:-}" ]; then
        echo "==> Свежий uploads: $LATEST_UPLOAD"
        rsync -avz --human-readable -e "ssh ${SSH_OPTS[*]}" \
            "$SSH_USER@$SSH_HOST:$LATEST_UPLOAD" "$DEST/"
    fi
fi

# Ротация: оставляем последние KEEP дампов, старые удаляем
ls -t "$DEST"/db_*.sqlite.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    echo "==> ротация: удаляю $old"
    rm -f "$old"
done

# Контроль: свежий дамп должен открываться (sqlite3 есть на macOS по умолчанию? — нет,
# поэтому проверка только если бинарник найден)
LATEST_LOCAL=$(ls -t "$DEST"/db_*.sqlite.gz 2>/dev/null | head -1)
if [ -n "$LATEST_LOCAL" ] && command -v sqlite3 >/dev/null 2>&1; then
    TMP=$(mktemp -d)
    gunzip -c "$LATEST_LOCAL" > "$TMP/check.sqlite"
    RESULT=$(sqlite3 "$TMP/check.sqlite" "PRAGMA integrity_check;" 2>&1 || echo "FAIL")
    rm -rf "$TMP"
    echo "==> integrity $LATEST_LOCAL: $RESULT"
fi

echo "==> Готово. Копий локально: $(ls "$DEST"/db_*.sqlite.gz 2>/dev/null | wc -l | tr -d ' ')"
