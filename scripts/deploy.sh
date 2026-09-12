#!/bin/bash
# scripts/deploy.sh — единственный путь деплоя: запускается ЛОКАЛЬНО из корня
# репозитория и доставляет код на прод через rsync+ssh.
#
# Составлен по фактам инспекции прода 2026-09-12 (см. PROGRESS.md, раздел
# «Проверенные факты о проде»):
#   - pm2 на сервере ЕСТЬ (пользовательский, через nvm), под ним два приложения:
#     crm (server.py) и nakladnye-bot;
#   - .git на проде нет, git-шаги не нужны;
#   - rsync --delete опасен: снесёт .pm2.env, tenants/*.db, .secret_key,
#     .cron_token, .venv/, logs/ — поэтому явные exclude и БЕЗ --delete;
#   - локальный rsync — openrsync (protocol 29): только базовые флаги;
#   - миграции идут СТРОГО ДО рестарта: новый код SELECT'ит колонки, которых
#     нет в боевом DDL, без миграции упадёт с «no such column»;
#   - на сервере нет ss/netstat/lsof, системный python 3.9 — всё Python-
#     действия только через .venv/bin/python;
#   - токены nakladnye-bot НЕ лежат в .pm2.env (только CRM_DATA_DIR,
#     CRM_SECRET_KEY, CRM_UPLOADS_DIR, PORT), поэтому бот рестартится БЕЗ
#     --update-env — иначе потеряет TELEGRAM_BOT_TOKEN/OPENAI_API_KEY из env,
#     которым его когда-то стартовали.
#
# Порядок: preflight → бэкап на сервере → штамповка ассетов (локально, с гейтом)
# → rsync → pip install (только если изменился requirements.txt) → миграции
# → рестарт обоих приложений + pm2 save → проверки здоровья.
#
# Запуск:  bash scripts/deploy.sh
set -euo pipefail

SSH_KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/crm_svetvdome_deploy}"
SSH_TARGET="${DEPLOY_SSH_TARGET:-h212005@87.232.64.12}"
REMOTE_APP="/var/www/h212005/data/www/cmr-svetvdome.online"
REMOTE_DATA="/var/www/h212005/data/crm_data"
PORT="20008"
BASE_URL="http://127.0.0.1:$PORT"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15)
ssh_run() { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }
# pm2 живёт в пользовательском nvm — подгружаем его в КАЖДОМ вызове
pm2_run() { ssh_run "export NVM_DIR=\"\$HOME/.nvm\"; . \"\$NVM_DIR/nvm.sh\" >/dev/null 2>&1; $*"; }
http_code() { ssh_run "curl -s -o /dev/null -m 15 -w '%{http_code}' '$BASE_URL$1'"; }

step() { echo ""; echo "=== $* ==="; }
die() { echo ""; echo "ДЕПЛОЙ ПРЕРВАН: $*" >&2; echo "Откат: ssh $SSH_TARGET 'bash $REMOTE_APP/scripts/rollback.sh latest'" >&2; exit 1; }

step "[0/7] Preflight"
ssh_run "true" || die "ssh до $SSH_TARGET не работает (ключ $SSH_KEY)"
ssh_run "test -s '$REMOTE_APP/.pm2.env'" || die "на сервере нет $REMOTE_APP/.pm2.env — без него воркеры откроют пустую БД"
ssh_run "test -x '$REMOTE_APP/.venv/bin/python'" || die "на сервере нет $REMOTE_APP/.venv/bin/python"
PY_VER=$(ssh_run "'$REMOTE_APP/.venv/bin/python' -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'") || die "не удалось запросить версию python на сервере"
echo "  серверный .venv: python $PY_VER"
pm2_run "pm2 ls >/dev/null" || die "pm2 недоступен на сервере"
[ "$(http_code /health)" = "200" ] || die "прод сейчас НЕ здоров (/health != 200) — сначала разберитесь с текущим состоянием"
echo "  прод здоров, деплоим поверх рабочего состояния"

step "[1/7] Бэкап на сервере"
ssh_run "bash '$REMOTE_APP/scripts/backup_crm.sh'" || die "бекап не создан — деплой отменён"
echo "  бэкап готов"

step "[2/7] Штамповка кэш-хэшей ассетов (локально)"
.venv/bin/python tools/stamp_assets.py || die "штамповка ассетов не удалась"
.venv/bin/python tools/stamp_assets.py --check >/dev/null || die "гейт stamp_assets --check не пройден"
echo "  хэши в HTML совпадают с содержимым файлов"

step "[3/7] rsync на сервер"
REQ_MD5_BEFORE=$(ssh_run "md5sum '$REMOTE_APP/requirements.txt' 2>/dev/null | cut -d' ' -f1 || echo none")
rsync -avz \
  -e "ssh -i $SSH_KEY -o IdentitiesOnly=yes" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude '.env' --exclude '.pm2.env' \
  --exclude '.secret_key' --exclude 'routers/.secret_key' --exclude '.cron_token' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude 'tenants/' --exclude 'uploads/' --exclude 'logs/' \
  --exclude 'tmp/' --exclude '.qwen/' --exclude 'node_modules' \
  "$ROOT/" "$SSH_TARGET:$REMOTE_APP/" || die "rsync не удался"
echo "  код доставлен (без --delete: три осиротевших файла в корне прода не тронуты)"

step "[4/7] Зависимости"
REQ_MD5_AFTER=$(ssh_run "md5sum '$REMOTE_APP/requirements.txt' 2>/dev/null | cut -d' ' -f1 || echo none")
if [ "$REQ_MD5_BEFORE" != "$REQ_MD5_AFTER" ]; then
  echo "  requirements.txt изменился ($REQ_MD5_BEFORE -> $REQ_MD5_AFTER), ставлю зависимости"
  ssh_run "'$REMOTE_APP/.venv/bin/python' -m pip install -q -r '$REMOTE_APP/requirements.txt'" || die "pip install на сервере не удался"
else
  echo "  requirements.txt не менялся, pip install пропущен"
fi

step "[5/7] Миграции БД (строго ДО рестарта)"
ssh_run "cd '$REMOTE_APP' && CRM_DATA_DIR='$REMOTE_DATA' .venv/bin/python migrate.py" || die "миграции не применились — рестарт отменён, прод НЕ тронут"
FK_VIOLATIONS=$(ssh_run "sqlite3 '$REMOTE_DATA/crm_app.db' 'PRAGMA foreign_key_check;'")
[ -z "$FK_VIOLATIONS" ] || die "foreign_key_check нашёл нарушения: $FK_VIOLATIONS"
echo "  миграции применены, FK-нарушений нет"

step "[6/7] Рестарт приложений"
pm2_run "pm2 restart crm --update-env" || die "pm2 restart crm не удался"
# БЕЗ --update-env: токены бота отсутствуют в .pm2.env и в shell деплоя —
# --update-env затёр бы их пустым окружением.
pm2_run "pm2 restart nakladnye-bot" || die "pm2 restart nakladnye-bot не удался"
pm2_run "pm2 save" || die "pm2 save не удался — после ребута вернётся старый дамп"
echo "  оба приложения перезапущены, дамп обновлён"

step "[7/7] Проверки здоровья"
sleep 5
[ "$(http_code /health)" = "200" ] || die "/health не отвечает 200 после рестарта"

LOCAL_VERSION=$(.venv/bin/python -c "from version import __version__; print(__version__)")
REMOTE_VERSION=$(ssh_run "curl -s -m 15 '$BASE_URL/api/version' | '$REMOTE_APP/.venv/bin/python' -c 'import sys,json; print(json.load(sys.stdin)[\"version\"])'")
[ "$REMOTE_VERSION" = "$LOCAL_VERSION" ] || die "на проде версия $REMOTE_VERSION, ожидалась $LOCAL_VERSION — рестартовался старый код?"
echo "  /api/version = $REMOTE_VERSION"

for page in /settings.html /workflows.html /custom_objects.html; do
  CODE=$(http_code "$page")
  [ "$CODE" = "404" ] || die "$page отдаёт $CODE, ожидался 404 (раньше был 500)"
done
echo "  страницы-сироты отдают 404 (маркер нового main.py)"

NAK_CODE=$(http_code /nakladnye)
case "$NAK_CODE" in
  200|401|403) echo "  /nakladnye отвечает $NAK_CODE (приложение маршрутизирует)" ;;
  *) die "/nakladnye отвечает $NAK_CODE" ;;
esac

ssh_run "cd '$REMOTE_APP' && .venv/bin/python tools/stamp_assets.py --check" >/dev/null || die "на сервере хэши ассетов не совпадают с HTML"
echo "  серверные хэши ассетов совпадают"

echo "  --- последние строки crm-error.log (для сверки глазами) ---"
ssh_run "tail -5 '$REMOTE_APP/logs/crm-error.log' 2>/dev/null || echo '(crm-error.log пуст или отсутствует — это хорошо)'"

step "ДЕПЛОЙ ЗАВЕРШЁН"
echo "Версия $LOCAL_VERSION на проде, бэкап перед деплоем в ~/backups на сервере."
