#!/bin/bash
# scripts/backup.sh — создание полного бэкапа перед изменениями
# FIX 2026-09-03: корень сайта переехал в cmr-svetvdome.online, БД — в /var/www/h212005/data/crm_data;
# снимок БД делается через sqlite3 .backup (простой cp в WAL-режиме даёт битый файл).
set -e

APP_DIR="/var/www/h212005/data/www/cmr-svetvdome.online"
DATA_DIR="/var/www/h212005/data/crm_data"
BACKUP_DIR="/var/www/h212005/data/crm_backups_predeploy"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="backup_$TIMESTAMP"

mkdir -p "$BACKUP_DIR/$BACKUP_NAME"

# 1. Бэкап БД — консистентный снимок живой базы; при неудаче деплой должен прерваться
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DATA_DIR/crm_app.db" ".backup '$BACKUP_DIR/$BACKUP_NAME/crm_app.db'"
else
    echo "ОШИБКА: sqlite3 не найден — бэкап БД невозможен, деплой прерван" >&2
    exit 1
fi

# 2. Бэкап всех .py файлов
cp "$APP_DIR"/*.py "$BACKUP_DIR/$BACKUP_NAME/" 2>/dev/null || true

# 3. Бэкап всех .js файлов из js/
mkdir -p "$BACKUP_DIR/$BACKUP_NAME/js"
cp "$APP_DIR"/js/*.js "$BACKUP_DIR/$BACKUP_NAME/js/" 2>/dev/null || true

# 4. Бэкап CSS
mkdir -p "$BACKUP_DIR/$BACKUP_NAME/css"
cp "$APP_DIR"/css/*.css "$BACKUP_DIR/$BACKUP_NAME/css/" 2>/dev/null || true

# 5. Бэкап HTML
cp "$APP_DIR"/*.html "$BACKUP_DIR/$BACKUP_NAME/" 2>/dev/null || true

# 6. Бэкап конфигов
cp "$APP_DIR"/ecosystem.config.js "$BACKUP_DIR/$BACKUP_NAME/" 2>/dev/null || true
cp "$APP_DIR"/requirements.txt "$BACKUP_DIR/$BACKUP_NAME/" 2>/dev/null || true

# 7. Бэкап routers
mkdir -p "$BACKUP_DIR/$BACKUP_NAME/routers"
cp "$APP_DIR"/routers/*.py "$BACKUP_DIR/$BACKUP_NAME/routers/" 2>/dev/null || true

# 8. Git snapshot
cd "$APP_DIR"
if [ -d .git ]; then
    git add -A 2>/dev/null || true
    git commit -m "pre-update backup: $TIMESTAMP" --allow-empty 2>/dev/null || true
fi

# 9. Очистка старых бэкапов (оставить последние 10)
cd "$BACKUP_DIR"
ls -dt backup_* 2>/dev/null | tail -n +11 | xargs rm -rf 2>/dev/null || true

echo "Бэкап создан: $BACKUP_DIR/$BACKUP_NAME"
echo "Размер: $(du -sh "$BACKUP_DIR/$BACKUP_NAME" 2>/dev/null | cut -f1)"
echo "Файлов: $(find "$BACKUP_DIR/$BACKUP_NAME" -type f 2>/dev/null | wc -l | tr -d ' ')"
