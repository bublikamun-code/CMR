#!/bin/bash
# scripts/rollback.sh — откат к указанному бэкапу или к stable-версии
# FIX 2026-09-03: корень сайта — cmr-svetvdome.online, БД — в /var/www/h212005/data/crm_data
set -e

APP_DIR="/var/www/h212005/data/www/cmr-svetvdome.online"
DATA_DIR="/var/www/h212005/data/crm_data"
BACKUP_DIR="$APP_DIR/backups"

usage() {
    echo "Использование:"
    echo "  $0                    — список доступных бэкапов"
    echo "  $0 <backup_name>      — откат к конкретному бэкапу"
    echo "  $0 stable             — откат к git stable-версии"
    echo "  $0 latest             — откат к последнему бэкапу"
    exit 1
}

# Если нет аргументов — показать список
if [ $# -eq 0 ]; then
    echo "=== Доступные бэкапы ==="
    echo ""
    for dir in $(ls -dt "$BACKUP_DIR"/backup_* 2>/dev/null); do
        name=$(basename "$dir")
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        files=$(find "$dir" -type f 2>/dev/null | wc -l | tr -d ' ')
        echo "  $name  ($size, $files файлов)"
    done
    echo ""
    if [ -d "$APP_DIR/.git" ]; then
        echo "=== Git-теги ==="
        cd "$APP_DIR" && git tag -l 2>/dev/null
    fi
    exit 0
fi

TARGET=$1

# Остановка сервиса
echo "Остановка CRM..."
pm2 stop crm 2>/dev/null || true

if [ "$TARGET" = "stable" ]; then
    echo "Откат к stable-версии (git tag v2.1.0-stable)..."
    cd "$APP_DIR"
    if git rev-parse v2.1.0-stable >/dev/null 2>&1; then
        git checkout v2.1.0-stable -- .
        echo "Файлы восстановлены из git."
    else
        echo "Тег v2.1.0-stable не найден!"
        echo "Доступные теги:"
        git tag -l
        pm2 start crm 2>/dev/null || true
        exit 1
    fi
elif [ "$TARGET" = "latest" ]; then
    LATEST=$(ls -dt "$BACKUP_DIR"/backup_* 2>/dev/null | head -1)
    if [ -z "$LATEST" ]; then
        echo "Нет доступных бэкапов!"
        exit 1
    fi
    TARGET=$(basename "$LATEST")
    echo "Откат к последнему бэкапу: $TARGET"
else
    if [ ! -d "$BACKUP_DIR/$TARGET" ]; then
        echo "Бэкап '$TARGET' не найден!"
        echo "Доступные:"
        for d in $(ls -dt "$BACKUP_DIR"/backup_* 2>/dev/null); do
            echo "  $(basename "$d")"
        done
        pm2 start crm 2>/dev/null || true
        exit 1
    fi
    echo "Откат к бэкапу: $TARGET"
fi

# Восстановление файлов
if [ "$TARGET" != "stable" ]; then
    # Восстановление Python файлов
    cp "$BACKUP_DIR/$TARGET/"*.py "$APP_DIR/" 2>/dev/null || true

    # Восстановление JS файлов
    mkdir -p "$APP_DIR/js"
    cp "$BACKUP_DIR/$TARGET/js/"*.js "$APP_DIR/js/" 2>/dev/null || true

    # Восстановление CSS
    mkdir -p "$APP_DIR/css"
    cp "$BACKUP_DIR/$TARGET/css/"*.css "$APP_DIR/css/" 2>/dev/null || true

    # Восстановление HTML
    cp "$BACKUP_DIR/$TARGET/"*.html "$APP_DIR/" 2>/dev/null || true

    # Восстановление routers
    mkdir -p "$APP_DIR/routers"
    cp "$BACKUP_DIR/$TARGET/routers/"*.py "$APP_DIR/routers/" 2>/dev/null || true

    # Восстановление конфигов
    cp "$BACKUP_DIR/$TARGET/ecosystem.config.js" "$APP_DIR/" 2>/dev/null || true
    cp "$BACKUP_DIR/$TARGET/requirements.txt" "$APP_DIR/" 2>/dev/null || true

    # Восстановление БД (pm2 уже остановлен выше; старые -wal/-shm удаляем,
    # чтобы чужой журнал не испортил восстановленный файл)
    echo ""
    read -p "Восстановить базу данных? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -f "$DATA_DIR/crm_app.db-wal" "$DATA_DIR/crm_app.db-shm"
        cp "$BACKUP_DIR/$TARGET/crm_app.db" "$DATA_DIR/crm_app.db"
        echo "БД восстановлена"
    fi
fi

# Перезапуск
echo "Запуск CRM..."
pm2 start ecosystem.config.js --cwd "$APP_DIR" 2>/dev/null || pm2 restart crm 2>/dev/null || true

echo ""
echo "Откат завершён!"
echo "Проверьте: http://87-232-64-12.nip.io"
