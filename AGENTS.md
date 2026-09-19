# AGENTS — конвенции проекта CRM «Свет в доме»

## Перед любой правкой фронтенда

Основной фронт — **v2**. Правки делаются ТОЛЬКО в источниках
`server_snapshot/tools/mockups/` (+ шаблоны `server_snapshot/tools/v2-{api,boot}-template.js`),
затем пересборка одним человеком: `cd server_snapshot && python3 tools/build_site_v2.py`
и `python3 tools/stamp_assets.py --check`. `server_snapshot/site-v2/` руками
не править — он генерируется. Конвенции визуала v2 (токены, движение) — в
`server_snapshot/tools/mockups/README.md`; текущий план работ —
`docs/audits/V2-FIX-PLAN-2026-09-19.md`. Проверка изменений — скриншотами в
обеих темах на стенде с копией прод-БД + `tools/v2_error_census.py`.

Legacy-фронт `site/` заморожен (маркер `site/ARCHIVED.md`, живёт на `/legacy`).
Править его можно только по отдельной договорённости; его дизайн-система —
`docs/design-system/SKILL.md` (роутер: tokens/themes/components/principles —
описывают ТОЛЬКО legacy, на v2 не распространяются).

## Стек и устройство

- Фронт v2 (основной): vanilla HTML/CSS/JS, генерируется сборщиком
  `server_snapshot/tools/build_site_v2.py` из мокапов в
  `server_snapshot/tools/mockups/` (прототип + модульные css/js); API-слой —
  встроенные в сборщик `js/v2/{api,boot}.js`. Раздаётся с `/v2`, `/` ведёт в v2
  (`CRM_FRONTEND` в коде, откат — `CRM_FRONTEND=legacy` в `.pm2.env` + рестарт).
- Фронт legacy (заморожен): `site/` — SPA `index.html`, `admin.html`, монолит
  `site/css/style.css` + слой `site/css/liquid-glass.css`.
- Бэк: FastAPI + SQLite — `server_snapshot/` (миграции `python3 migrate.py`,
  тесты `server_snapshot/tests/`).
- Раздача: nginx отдаёт статику; API — PM2 (прод).
- CSP (прод): `script-src 'self' 'unsafe-inline'` — onclick и eval запрещены,
  только `data-handler` / addEventListener. Инлайн-скрипты в v2 ровно два
  (тема + `V2_ASSET_VER` в собранном index.html) — при ужесточении CSP
  переносить в nonce одним списком.

## Верификация

v2 (основной фронт):
- Сборка и штампы: `cd server_snapshot && python3 tools/build_site_v2.py`,
  затем `python3 tools/stamp_assets.py --check` (0 расхождений).
- Синтаксис: `node --check` на правленых файлах mockups.
- Стенд на копии прод-БД (порт 8125, см. V2-FIX-PLAN): перепись
  `python3 tools/v2_error_census.py --base http://127.0.0.1:8125/v2/` —
  0 pageerror / 0 аномалий; функциональный аудит `tools/v2_functional_audit.py`
  при правках контрактов.
- Тесты бэка: `cd server_snapshot && ./.venv/bin/python -m pytest -q`.

Legacy (по договорённости):
- JS: `bash tools/check_js.sh` (после правок site/js).
- CSS: избыточные `!important` оценивает `tools/css_cascade.py report` —
  самостоятельное снятие `!important` вне отдельной кампании не делать.
- Кэш: после правки site/css|js — `python3 tools/stamp_assets.py`.

## Деплой (по договорённости с владельцем, не автоматом)

- `tools/deploy.sh v2` — пересборка site-v2 + rsync с --delete (основной путь).
- `tools/deploy.sh back <file>...` — файл(ы) бэка + рестарт PM2.
- `tools/deploy.sh front` — legacy `site/` (только для точечных правок замороженного).
- `tools/deploy.sh status | logs` — диагностика.

## Оркестрация субагентов

Главный агент (GLM-5.3) думает: декомпозирует задачу, выбирает подход,
ревьюит результат и делает коммиты. Исполнение делегируй субагентам на
GLM-5.3-Flash из `.zcode/agents/`:

- `scout` — read-only поиск и разведка (факты, `path:line`, без правок);
- `builder` — самодостаточные правки кода по готовой спецификации +
  прогоны проверок (`tools/check_js.sh`, `stamp_assets.py`, тесты бэка);
- `verifier` — гоняет гейты репозитория, честные PASS/FAIL с реальным
  выводом, ничего не чинит.

Правила делегирования:

- Промпт воркеру самодостаточен: воркер не видит историю сессии — давай
  файлы, контекст и критерии готовности целиком.
- Независимые задачи запускай параллельно (несколько Agent-вызовов в одном
  сообщении); правки одного файла — строго один воркер.
- Воркеры не коммитят и не деплоят — это остаётся за главным агентом и
  владельцем.
- Новых воркеров добавляй в `.zcode/agents/<имя>.md` с `model:
  GLM-5.3-Flash`; AgentsMd-контекст подключай через `injectAgentsMd:
  true` только тем, кто правит код. Подхватываются при старте сессии.

## Стиль

- Комментарии и тексты UI — по-русски; идентификаторы — по-английски.
- Коммиты — по-русски, развёрнуто: что и зачем, что проверено (см. `git log`).
- Каждый логичный шаг — отдельный коммит; деплой — отдельное решение владельца.
