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
#
# Запуск:  bash scripts/check.sh
# Выход 0 — все гейты зелёные; первый же красный гейт останавливает прогон.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=".venv/bin/python"

step() { echo ""; echo "=== [$1] $2 ==="; }

step 1/6 "import main"
$PY -c "import main; print('  роутов:', len(main.app.routes))"

step 2/6 "pytest — профиль боевого DDL (prod)"
$PY -m pytest -q

step 3/6 "pytest — профиль models.py"
CRM_TEST_SCHEMA=models $PY -m pytest -q

step 4/7 "кэш-бастеры ассетов"
$PY tools/stamp_assets.py --check

step 5/7 "frontend-бандл свеж"
npm ci --no-audit --no-fund
npm run build
git diff --exit-code -- js/nakladnye.bundle.js

step 6/7 "ruff"
.venv/bin/ruff check .

step 7/7 "чистый venv: requirements.txt + bcrypt"
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

echo ""
echo "=== ВСЕ ГЕЙТЫ ЗЕЛЁНЫЕ ==="
