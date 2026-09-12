#!/bin/bash
# scripts/check.sh — CI-гейты (Фаза 4). Один вход: локально и в CI.
#
# Зачем: линтер НЕ ловит импорт несуществующего имени из другого модуля
# (проверено экспериментально 2026-09-11: ruff --select F отвечает
# «All checks passed» на таком коде) — именно так в origin/main прожили
# cap_list и ensure_registry_remainder. Поэтому первый гейт — реальный
# импорт приложения, дальше по нарастающей.
#
# Гейты:
#   1. import main — приложение собирается, все роутеры и имена на месте
#   2. pytest на профиле боевого DDL (tests/fixtures/prod_schema.sql):
#      characterization-тесты, деньги, метатесты паритета схем, smoke
#      через TestClient (/health, /, /admin, /api/version → 200; без
#      токена → 401/403), PRAGMA foreign_key_check после серий удалений
#   3. pytest на профиле models.py (второй профиль схемы)
#   4. stamp_assets.py --check — кэш-бастеры соответствуют содержимому
#   5. frontend-бандл свеж (Vite-пилот): npm ci → vite build →
#      git diff бандла; расхождение = забыли пересобрать и закоммитить
#      js/nakladnye.bundle.js (node на сервере нет — бандл коммитим)
#   6. ruff check — закреплённый набор правил (ruff.toml)
#   7. чистый venv: pip install -r requirements.txt + хэширование пароля
#      на фиксированном bcrypt (страховка Фазы 0: расфиксация bcrypt
#      молча ломает вход)
#   8. скриншот-регрессия tools/visual-check (4 ширины + тёмная тема):
#      сид-БД → сервер на 8799 → pixelmatch против baselines/.
#      Только локально: эталоны отрендерены на macOS, шрифты/антиалиасинг
#      платформенные — Ubuntu-раннер CI дал бы ложные расхождения.
#
# Запуск:  bash scripts/check.sh
# Выход 0 — все гейты зелёные; первый же красный гейт останавливает прогон.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=".venv/bin/python"

step() { echo ""; echo "=== [$1] $2 ==="; }

step 1/8 "import main"
$PY -c "import main; print('  роутов:', len(main.app.routes))"

step 2/8 "pytest — профиль боевого DDL (prod)"
$PY -m pytest -q

step 3/8 "pytest — профиль models.py"
CRM_TEST_SCHEMA=models $PY -m pytest -q

step 4/8 "кэш-бастеры ассетов"
$PY tools/stamp_assets.py --check

step 5/8 "frontend-бандл свеж"
npm ci --no-audit --no-fund
npm run build
git diff --exit-code -- js/nakladnye.bundle.js

step 6/8 "ruff"
.venv/bin/ruff check .

step 7/8 "чистый venv: requirements.txt + bcrypt"
VENV_DIR=$(mktemp -d /tmp/crm-gate-venv.XXXXXX)
trap 'rm -rf "$VENV_DIR"' EXIT
python3.12 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q -r requirements.txt
"$VENV_DIR/bin/python" - <<'EOF'
from passlib.context import CryptContext
ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
h = ctx.hash("gate-check")
assert ctx.verify("gate-check", h)
import bcrypt
print("  bcrypt", bcrypt.__version__, "— хэширование и проверка пароля ок")
EOF

step 8/8 "скриншот-регрессия (4 ширины + тёмная тема)"
if [ -n "${CI:-}" ]; then
    echo "  пропуск в CI: эталоны baselines/ отрендерены на macOS, шрифты и"
    echo "  антиалиасинг Ubuntu-раннера дали бы ложные пиксельные расхождения."
    echo "  Гейт обязателен локально (pre-push), в CI его закрывают шаги 1-7."
else
    (cd tools/visual-check && npm ci --no-audit --no-fund)
    $PY tools/visual-check/seed_db.py
    VISUAL_PORT=8799
    if curl -sf "http://127.0.0.1:$VISUAL_PORT/health" > /dev/null; then
        echo "  ОШИБКА: порт $VISUAL_PORT уже занят — погасите старый сервер" >&2
        exit 1
    fi
    # Окружение — то же, что пинит seed_db.py (_env): сервер обязан
    # смотреть в сид-базу, а не в боевую/корневую.
    CRM_DATA_DIR="$PWD/tmp/visual-seed" \
    CRM_SECRET_KEY="visual-regression-secret" \
    CRM_CRON_TOKEN="visual-cron-token" \
    TELEGRAM_BOT_TOKEN="visual-bot-token" \
    PORT=$VISUAL_PORT \
    "$PY" server.py --port $VISUAL_PORT > tmp/visual-server.log 2>&1 &
    VISUAL_PID=$!
    # Сервер обязан гаснуть при любом выходе из скрипта (красный гейт,
    # Ctrl-C): иначе зависший процесс держит порт 8799 до ребута.
    trap 'kill $VISUAL_PID 2>/dev/null || true; rm -rf "$VENV_DIR"' EXIT
    for _ in $(seq 1 30); do
        curl -sf "http://127.0.0.1:$VISUAL_PORT/health" > /dev/null && break
        sleep 1
    done
    curl -sf "http://127.0.0.1:$VISUAL_PORT/health" > /dev/null || {
        echo "  ОШИБКА: сервер не поднялся за 30 с, см. tmp/visual-server.log" >&2
        exit 1
    }
    (cd tools/visual-check && TZ=Europe/Minsk node regress.mjs)
    kill $VISUAL_PID 2>/dev/null || true
    wait $VISUAL_PID 2>/dev/null || true
fi

echo ""
echo "=== ВСЕ ГЕЙТЫ ЗЕЛЁНЫЕ ==="
