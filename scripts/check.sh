#!/bin/bash
# Локальный gate без установки зависимостей и запуска служб.
# Запуск: bash scripts/check.sh
# Python можно переопределить: PYTHON=/absolute/path/to/venv/bin/python bash scripts/check.sh
# Обязательны bash, node и macOS JavaScriptCore (для tools/check_js.sh),
# зависимости requirements.txt, pytest и httpx в выбранном Python.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON-$ROOT/server_snapshot/.venv/bin/python}"
JSC=/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc

fail() {
    printf 'Предпосылка не выполнена: %s\n' "$*" >&2
    exit 2
}

# Проверяем всё до запуска проверок; системный Python автоматически не выбираем.
[[ $# -eq 0 ]] || fail 'аргументы не поддерживаются; для выбора Python используйте PYTHON'
for cmd in bash node; do
    command -v "$cmd" >/dev/null 2>&1 || fail "не найден $cmd в PATH"
done
command -v "$PYTHON" >/dev/null 2>&1 || fail "Python недоступен: $PYTHON"
# Относительный override разрешаем до смены рабочей папки.
PYTHON="$("$PYTHON" -c 'import sys; print(sys.executable)')" || fail 'Python не запускается'
[[ -x "$PYTHON" ]] || fail "Python не исполняемый: $PYTHON"
[[ -x "$JSC" ]] || fail "нужен macOS JavaScriptCore: $JSC; JS syntax не пропускается"
node --version >/dev/null || fail 'node не запускается'
for file in tools/check_js.sh tools/check_handlers.js tools/stamp_assets.py \
            server_snapshot/tests/conftest.py \
            server_snapshot/tools/build_site_v2.py \
            server_snapshot/tools/check_site_v2_fresh.py \
            server_snapshot/tools/check_secret_permissions.py; do
    [[ -f "$ROOT/$file" ]] || fail "нет $file"
done
"$PYTHON" - <<'PY' || fail 'в выбранном Python отсутствуют зависимости (автоустановка отключена)'
import importlib.util
modules = ('pytest', 'httpx', 'fastapi', 'uvicorn', 'multipart', 'sqlalchemy',
           'jwt', 'passlib', 'bcrypt', 'slowapi', 'cryptography', 'pydantic', 'alembic')
missing = [name for name in modules if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit('Не найдены модули: ' + ', '.join(missing))
PY

cd "$ROOT"
printf 'Python: %s\n' "$PYTHON"
status=0
# После preflight собираем результаты всех этапов, сохраняя первый код ошибки.
run() {
    local label="$1" code
    shift
    printf '\n== %s ==\n' "$label"
    if "$@"; then
        printf 'OK: %s\n' "$label"
    else
        code=$?
        printf 'FAIL: %s (exit %s)\n' "$label" "$code" >&2
        if [[ $status -eq 0 ]]; then status=$code; fi
    fi
}

# Только обслуживаемые каталоги; скрипты не исполняются, prototype не обходится.
for file in "$ROOT"/scripts/*.sh "$ROOT"/tools/*.sh "$ROOT"/server_snapshot/scripts/*.sh; do
    run "shell syntax: ${file#"$ROOT"/}" bash -n "$file"
done
run 'JS syntax' bash "$ROOT/tools/check_js.sh"

check_handlers() {
    local output code
    if output="$(node "$ROOT/tools/check_handlers.js" 2>&1)"; then
        printf '%s\n' "$output"
    else
        code=$?
        printf '%s\n' "$output" >&2
        return "$code"
    fi
    # Существующий анализатор печатает ошибки, но сам всегда выходит с 0.
    # Не называем его эвристические NOT RESOLVED доказанными ошибками UI.
    [[ "$output" != *'NOT RESOLVED:'* && "$output" =~ total\ handlers:\ [0-9]+\ unresolved:\ 0$ ]]
}
run 'data-handler (статическая эвристика)' check_handlers
# site-v2 — генерируемый артефакт (tools/mockups + шаблоны api/boot). В git он
# лежит обычными файлами, поэтому правка источника без пересборки расходится
# молча: deploy.sh v2 пересобирает на месте, а docker-образ и ручная выкладка
# берут устаревший каталог. Гейт собирает эталон во временный каталог и сверяет
# его с артефактом — рабочее дерево не меняется.
run 'Свежесть site-v2 (mockups ↔ артефакт)' \
    "$PYTHON" "$ROOT/server_snapshot/tools/check_site_v2_fresh.py"
run 'Права файлов с секретами (только метаданные)' \
    "$PYTHON" "$ROOT/server_snapshot/tools/check_secret_permissions.py"
run 'Версии статики (только проверка)' "$PYTHON" "$ROOT/tools/stamp_assets.py" --check

# conftest изолирует БД/uploads до импорта main. Дополнительно изолируем cwd,
# временные файлы и ключи: main создаёт относительные каталоги, auth иначе
# читает/создаёт ключи рядом с исходниками. Отдельного импорта приложения нет.
run 'Полный pytest server_snapshot/tests' "$PYTHON" - "$ROOT" <<'PY'
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix='.check-', dir=root) as work:
    # Приложение монтирует статику по относительным путям (main.py:
    # css, js, static, site-v2) — CWD тестов должен их видеть.
    # Ссылки ведут на реальные ассеты репозитория (site/, site-v2/).
    front = root / 'site'
    back = root / 'server_snapshot'
    for name, target in (('css', front / 'css'), ('js', front / 'js'),
                         ('static', front / 'static'),
                         ('site-v2', back / 'site-v2'),
                         ('index.html', front / 'index.html'),
                         ('admin.html', front / 'admin.html'),
                         ('manifest.json', front / 'manifest.json')):
        Path(work, name).symlink_to(target)
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(root / 'server_snapshot'),
        PYTHONDONTWRITEBYTECODE='1',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        PYTEST_ADDOPTS='',
        PYTEST_PLUGINS='',
        TMPDIR=work,
        CRM_SECRET_KEY=secrets.token_hex(32),
        CRM_CRON_TOKEN=secrets.token_hex(32),
    )
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
         str(root / 'server_snapshot' / 'tests')], cwd=work, env=env,
    )
    # Сигнал дочернего процесса тоже означает неуспех gate.
    code = result.returncode if result.returncode >= 0 else 128 - result.returncode
raise SystemExit(code)
PY

printf '\nЛокальный gate завершён: exit %s\n' "$status"
exit "$status"
