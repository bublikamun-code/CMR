#!/bin/bash
# scripts/deploy.sh — деплой с автоматическим бэкапом
set -e

APP_DIR="/var/www/h212005/data/www/cmr-svetvdome.online"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Деплой CRM Снабжение и Продажи ==="
echo ""

# 1. Автоматический бэкап
echo "[1/5] Создание бэкапа..."
bash "$SCRIPT_DIR/backup.sh"
echo ""

# 2. Git commit
echo "[2/5] Git commit..."
cd "$APP_DIR"
if [ -d .git ]; then
    git add -A 2>/dev/null || true
    VERSION=$(python -c "from version import __version__; print(__version__)" 2>/dev/null || echo "unknown")
    git commit -m "deploy v$VERSION — $(date +%Y-%m-%d\ %H:%M)" --allow-empty 2>/dev/null || true
    echo "  Коммит создан: v$VERSION"
else
    echo "  Git не инициализирован, пропускаем."
fi
echo ""

# 3. Установка зависимостей
echo "[3/5] Проверка зависимостей..."
if [ -f "$APP_DIR/requirements.txt" ] && [ -d "$APP_DIR/.venv" ]; then
    source "$APP_DIR/.venv/bin/activate"
    pip install -q -r "$APP_DIR/requirements.txt" 2>/dev/null || true
    echo "  Зависимости обновлены."
else
    echo "  .venv или requirements.txt не найдены, пропускаем."
fi
echo ""

# 4. Миграции БД
echo "[4/5] Проверка миграций..."
if [ -f "$APP_DIR/migrate_v2.py" ]; then
    cd "$APP_DIR"
    python migrate_v2.py 2>/dev/null || true
    echo "  Миграции выполнены."
else
    echo "  Миграций нет, пропускаем."
fi
echo ""

# 5. Перезапуск
echo "[5/5] Перезапуск сервиса..."
pm2 restart crm 2>/dev/null || pm2 start ecosystem.config.js --cwd "$APP_DIR" 2>/dev/null || true
echo ""

# 6. Проверка здоровья
sleep 3
HEALTH=$(curl -s http://localhost:20008/health 2>/dev/null || echo '{"status":"error"}')
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo "=== Деплой успешен! CRM работает ==="
    echo "Проверьте: http://87-232-64-12.nip.io"
else
    echo "=== ВНИМАНИЕ: CRM может не работать ==="
    echo "Проверьте логи: pm2 logs crm"
    echo ""
    read -p "Откатить к последнему бэкапу? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        bash "$SCRIPT_DIR/rollback.sh" latest
    fi
fi
