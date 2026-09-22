# AGENTS — конвенции проекта CRM «Свет в доме»

## Перед любой правкой фронтенда

Основной фронт — **v2**. Правки делаются ТОЛЬКО в источниках
`server_snapshot/tools/mockups/` (+ шаблоны `server_snapshot/tools/v2-{api,boot}-template.js`),
затем пересборка одним человеком: `cd server_snapshot && python3 tools/build_site_v2.py`.
Она же пересчитывает кэш-штампы v2 — отдельной команды штамповки для site-v2 нет
(подробнее в «Верификации»). `server_snapshot/site-v2/` руками не править — он
генерируется, но git'ом отслеживается: результат сборки остаётся в дереве и
коммитится. Исключение — `shell-v2-fin.js`: у него нет файла-источника в
`mockups/`, сборщик вырезает его из inline-`<script>` внутри
`shell-v2-prototype.html` и падает `assert`, если тот пропал. Конвенции визуала
v2 (токены, движение) — в `server_snapshot/tools/mockups/README.md`; текущий план
работ — `docs/audits/V2-FIX-PLAN-2026-09-19.md`. Проверка изменений —
скриншотами в обеих темах на стенде с копией прод-БД + `tools/v2_error_census.py`.

Legacy-фронт `site/` заморожен (маркер `site/ARCHIVED.md`, живёт на `/legacy`).
Править его можно только по отдельной договорённости; его дизайн-система —
`docs/design-system/SKILL.md` (роутер: tokens/themes/components/principles —
описывают ТОЛЬКО legacy, на v2 не распространяются).

## Стек и устройство

- Фронт v2 (основной): vanilla HTML/CSS/JS, генерируется сборщиком
  `server_snapshot/tools/build_site_v2.py` из мокапов в
  `server_snapshot/tools/mockups/` (прототип + модульные css/js); API-слой и
  загрузчик — отдельные файлы-шаблоны `server_snapshot/tools/v2-{api,boot}-template.js`,
  сборщик кладёт их в `site-v2/js/v2/{api,boot}.js`. Раздаётся с `/v2`, `/` ведёт
  в v2 (`CRM_FRONTEND` в коде, откат — `CRM_FRONTEND=legacy` в `.pm2.env` + рестарт).
- Фронт legacy (заморожен): `site/` — SPA `index.html`, `admin.html`, монолит
  `site/css/style.css` + слой `site/css/liquid-glass.css`.
- Бэк: FastAPI + SQLite — `server_snapshot/` (миграции `python3 migrate.py`,
  тесты `server_snapshot/tests/`, окружение `server_snapshot/.venv`).
- Раздача: nginx отдаёт статику; API — PM2 (прод).
- CSP (прод): `script-src 'self' 'unsafe-inline'` (`server_snapshot/main.py:194-203`),
  `img-src` включает `blob:`. Инлайн-атрибуты событий (`onclick="..."`) и eval
  запрещены. `data-handler` — конвенция legacy `site/`; в v2 его нет, там
  `addEventListener` и присваивание `.onclick=` в JS-коде. Инлайн-скрипт в
  собранном `site-v2/index.html` один тег с двумя сниппетами (тема +
  `V2_ASSET_VER`, генерирует сборщик) — при ужесточении CSP переносить в nonce
  одним списком.

## Верификация

v2 (основной фронт):
- Сборка: `cd server_snapshot && python3 tools/build_site_v2.py`. Кэш-штампы v2
  (`window.V2_ASSET_VER` и `?v=` у api.js/boot.js) считает сама сборка — sha1 по
  всем `js/**/*.js` + `css/*.css` результата (`build_site_v2.py:124-141`); гейта
  «свежесть site-v2 относительно mockups» нет, только пересборка и сравнение дерева.
- Осторожно со штамповщиками: их два, и ни один не про site-v2.
  `server_snapshot/tools/stamp_assets.py` проверяет `server_snapshot/index.html` +
  `admin.html` против `server_snapshot/{css,js}` (`DEFAULT_ROOT = parent.parent`,
  строка 37) — это копия legacy, которую FastAPI отдаёт с корня. Корневой
  `tools/stamp_assets.py` — про `site/` (его вызывает `tools/deploy.sh:75`).
- Синтаксис: `node --check` на правленых файлах mockups (node в PATH, nvm v20).
- Стенд на копии прод-БД (порт 8125, см. V2-FIX-PLAN): перепись
  `python3 tools/v2_error_census.py --base http://127.0.0.1:8125/v2/` —
  0 pageerror / 0 аномалий; функциональный аудит `tools/v2_functional_audit.py`
  при правках контрактов.
- Тесты бэка: `cd server_snapshot && ./.venv/bin/python -m pytest -q`.

Legacy (по договорённости):
- JS: `bash tools/check_js.sh` (после правок site/js). Покрывает только
  `site/js/*.js` и использует JavaScriptCore из macOS — v2 им не проверяется.
- CSS: избыточные `!important` оценивает `tools/css_cascade.py report` —
  самостоятельное снятие `!important` вне отдельной кампании не делать.
- Кэш: после правки site/css|js — `python3 tools/stamp_assets.py` (корневой).

## Деплой (по договорённости с владельцем, не автоматом)

- `tools/deploy.sh v2` — пересборка site-v2 + rsync с --delete (основной путь).
- `tools/deploy.sh back <file>...` — файл(ы) бэка + рестарт PM2.
- `tools/deploy.sh front` — legacy `site/` (только для точечных правок замороженного).
- `tools/deploy.sh status | logs` — диагностика.

## Оркестрация субагентов

Главный агент думает: декомпозирует задачу, выбирает подход, ревьюит результат и
делает коммиты. Исполнение делегируй субагентам из `.qwen/agents/` (Qwen Code
читает `.qwen/agents/` в проекте и `~/.qwen/agents/` у пользователя):

- `scout` — read-only поиск и разведка (факты, `path:line`, без правок);
- `builder` — самодостаточные правки кода по готовой спецификации + прогоны
  проверок (`node --check`, сборка v2, тесты бэка);
- `verifier` — гоняет гейты репозитория, честные PASS/FAIL с реальным выводом,
  ничего не чинит.

Все три сидят на `model: qwen3.8-flash` (провайдер Token Plan). Каталог
`.zcode/agents/` — определения для другого CLI, Qwen Code их не видит; они
устарели (модель `GLM-5.3-Flash` здесь неразрешима, фронт описан как `site/`,
штампы и `data-handler` — по старой схеме). Не копируй из них факты.

Правила делегирования:

- Промпт воркеру самодостаточен: воркер не видит историю сессии — давай файлы,
  контекст и критерии готовности целиком.
- Независимые задачи запускай параллельно (несколько Agent-вызовов в одном
  сообщении); правки одного файла — строго один воркер.
- Воркеры не коммитят и не деплоят — это остаётся за главным агентом и
  владельцем.
- Новых воркеров добавляй в `.qwen/agents/<имя>.md` с `model: qwen3.8-flash`.
  Формат — Markdown с YAML-фронтматтером; поддерживаются `name`, `description`,
  `model`, `tools`, `disallowedTools`, `approvalMode`, `color`, `maxTurns`,
  `hooks`. Поля `thoughtLevel` и `injectAgentsMd` Qwen Code не поддерживает —
  они молча отбрасываются. Имена инструментов qwen-овские: `run_shell_command`,
  `read_file`, `write_file`, `edit`, `glob`, `grep_search`, `web_fetch`,
  `web_search`. Подхватываются сразу, без рестарта сессии.
- Read-only воркеру задавай границы списком `tools` (без `write_file`/`edit`) —
  это жёстче, чем запрет в промпте.

## Стиль

- Комментарии и тексты UI — по-русски; идентификаторы — по-английски.
- Коммиты — по-русски, развёрнуто: что и зачем, что проверено (см. `git log`).
- Каждый логичный шаг — отдельный коммит; деплой — отдельное решение владельца.
