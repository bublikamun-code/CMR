# Сверка V2-FIX-PLAN-2026-09-19 с кодом и git — 22.09.2026

Документ сверки. План-источник: `docs/audits/V2-FIX-PLAN-2026-09-19.md`.
Точка сверки: `main @ 8e3fd1f` (локально; `origin/main` = `5293043`, ahead 4).
**Прод не изменялся**: только чтение (`curl`, `ssh`, `sqlite3 .backup`, `pm2 jlist`).
Пуш и деплой не выполнялись.

## 0. Как проверяли

Пять параллельных read-only разведок (реестр A; реестр B; паритет C; D + V7/V8/V11 + P0;
Пакет G + чек-лист) и точечная перепроверка несущих утверждений главным агентом.
Пометка **✓** ниже — факт перепроверен главным агентом командой 22.09, остальное —
данные разведок (пути и коммиты указаны, при сомнении перепроверяются одной командой).

Инструментальные проверки, выполненные сегодня:

| Проверка | Команда | Результат |
| --- | --- | --- |
| Тесты бэка, профиль prod | `cd server_snapshot && ./.venv/bin/python -m pytest -q` | **409 passed**, 75 с ✓ |
| Тесты бэка, профиль models | `CRM_TEST_SCHEMA=models ./.venv/bin/python -m pytest -q` | **405 passed, 4 skipped**, 74 с ✓ |
| Штампы v2 | `cd server_snapshot && python3 tools/stamp_assets.py --check` | 35 ассетов / 37 ссылок, «все хэши совпадают», exit 0 ✓ |
| Штампы legacy | `python3 tools/stamp_assets.py --check` | 37 ссылок, **0 расхождений**, exit 0 ✓ |
| Свежесть сборки | `diff tools/mockups/shell-v2-*.js site-v2/js/*` + шаблоны | 7/7 js и `boot.js`/`api.js` **идентичны** ✓ |
| Синтаксис JS | `node --check` | 9/9 исходников, 10/10 `site-v2/js/**` — OK |
| Линтеры Python/JS | поиск конфигов | **отсутствуют** (нет `pyproject.toml`/`ruff`/`.flake8`/`package.json`/`.eslintrc`); гейт — `scripts/check.sh` |

Линтеров в репо нет, поэтому «проверка линтером» сведена к `node --check`,
`scripts/check.sh` и pytest.

## 1. Итог одной таблицей

| Реестр | Всего | ЗАКРЫТО | ЧАСТИЧНО | НЕ НАЧАТО | НЕ ПОДТВЕРЖДАЕТСЯ |
| --- | --- | --- | --- | --- | --- |
| A — контракты клиент↔сервер | 15 | 12 | 2 (A7, A15) | 0 | 1 (A13) |
| B — надёжность клиента | 14 | 10 | 3 (B7, B8, B10) | 1 (B11) | 0 |
| C — паритет | 13 позиций | 4 | 3 | 6 | 6 утверждений плана ошибочны |
| D — данные и процесс | 4 | 3 | 0 | 0 | 1 за владельцем (D3) |
| V7/V8/V11 + P0 (1–6) + «Уже закрыто» | 15 | 13 | 1 (V7) | 1 (V11) | 0 |
| Чек-лист этапа | 19 | 14 | 3 | 1 (Пакет F) | 6 пунктов недоказуемы из репо |

План в целом выполнен: из 33 дефектов A+B закрыты 22, частично 5, не начат 1,
один (A13) оказался не дефектом. Единственный незакрытый пункт чек-листа —
**Пакет F (паритет)**, и именно он даёт оба дефекта, о которых владелец сообщил
22.09 (нет создания карточки; разделы настроек не перенесены).

## 2. Реестр A — контракты клиент ↔ сервер

Ключевые коммиты: `7fe6bfa` (пакет A), `412a8fb` (пакет B), `3e38767` (пакет C),
`61e0af6` (пакет D), `6243a62`/`1865aef` (пересборки). Пути от корня репо.

| # | Вердикт | Доказательство | Что осталось |
| --- | --- | --- | --- |
| A1 | ЗАКРЫТО | `server_snapshot/routers/dictionaries_router.py:55-57`; тесты `server_snapshot/tests/test_package_a_fixes.py:21-56`; `7fe6bfa` | Сервер отдаёт только строки БД, при пустом справочнике `[]`. Клиентский `STATUS_MAP` остался (`tools/v2-boot-template.js:30-40,94-101`) — см. находку N1 |
| A2 | ЗАКРЫТО | `server_snapshot/schemas.py:634,665-686,744-747,783-786`; `constants.py:17`; `7fe6bfa` | `constants.TTN_TYPES` никто не импортирует — канон дублируется в `schemas.NAKLADNYE_DOC_TYPES` |
| A3 | ЗАКРЫТО | `server_snapshot/routers/card_details_router.py:507-575`; тест `test_package_b_money.py:115-141`; `412a8fb` | Дефект (`total_amount := paid_amount`) не найден ни в HEAD, ни в истории |
| A4 | ЗАКРЫТО | `server_snapshot/services/nakladnye.py:64-103` (409 + `existing_id`); бот `telegram_nakladnye_bot.py:284-302`; клиент `tools/mockups/shell-v2-board.js:1622-1623`; `7fe6bfa` | — |
| A5 | ЗАКРЫТО (иначе, чем в плане) | `server_snapshot/services/payments.py:17-25,56`; `schemas.py:434-437`; `412a8fb` | `is_advance` клиентом не потребляется (0 вхождений); комментарий `schemas.py:437` недостоверен |
| A6 | ЗАКРЫТО | сервер `card_details_router.py:443-453` (`3071961`); клиент `tools/v2-boot-template.js:534-546` (`3e38767`) | — |
| A7 | **ЧАСТИЧНО** ✓ | сервер `card_details_router.py:458-471` + `schemas.py:480-481` (`412a8fb`); клиент `v2-boot-template.js:536-537` | **Снятие клиента не работает**: `shell-v2-board.js:1879` шлёт `client_id:null` → boot-шаблон вырезает null → PATCH с пустым телом; `clear_client:true` не шлёт никто. После `refresh()` старый клиент возвращается |
| A8 | ЗАКРЫТО | `schemas.py:277-283,493-498`; `412a8fb` | 422 вместо 400 (суть соблюдена: внятная ошибка вместо 500) |
| A9 | ЗАКРЫТО | `routers/tasks_router.py:58-61,79,117-127`; `schemas.py:606`; `9afbc7b`, `3b7040d` | `v2-boot-template.js:260-270` не мапит `checklist` → чек-лист задачи в UI v2 не показывается |
| A10 | ЗАКРЫТО | `card_details_router.py:298-334` (multipart `POST /files/attach/{card_id}`); клиент `shell-v2-board.js:964-1006`, `v2-api-template.js:66-75`; `412a8fb` | — |
| A11 | ЗАКРЫТО | `clients_router.py:24-72,201-227`; `schemas.py:226-227` (`412a8fb`); клиент `v2-boot-template.js:112-119`, `v2-api-template.js:141-143`, `shell-v2-board.js:1440` | — |
| A12 | ЗАКРЫТО | `services/card_money.py`; `kanban_router.py:23-49,63,81`; `schemas.py:349-352` (`412a8fb`); клиент `shell-v2-board.js:95-118` (`61e0af6`) | «/cards/{id}» из контракта фактически `/kanban/cards/{id}` |
| A13 | **НЕ ПОДТВЕРЖДАЕТСЯ** ✓ | `schemas.py:272,488,514` | Канона «Частично оплачен» в коде нет и не было; единственный источник — `docs/PLAN_CRM_SPRINTS.md:22`. Рассогласования нет — закрыть как не-дефект |
| A14 | ЗАКРЫТО | `clients_router.py:24-72` `_attach_client_aggregates`; `412a8fb` («2405 → 7 запросов на 400 клиентов») | — |
| A15 | **ЧАСТИЧНО** ✓ | `shell-v2-board.js:1949-1966`; `61e0af6` | `window.KBData.reload` **не определён нигде** → всегда ветка `refresh()`, которая перерисовывает старый кэш; загруженная `fresh`-карточка выбрасывается. Восстановленная карточка не появляется до F5 |

**Контракт пакета D/B — ВЫПОЛНЕН**: `remaining`, `remaining_kop`, `writeoff_status`,
`issued_total` считаются на сервере (`services/card_money.py`) и отдаются в
`/kanban/cards` и `/kanban/cards/{id}`; клиент берёт только их, при отсутствии — «—»
(`shell-v2-board.js:95-118`).

## 3. Реестр B — надёжность клиента

Пути от `server_snapshot/`. Ключевые коммиты: `3e38767`, `61e0af6`, `12876fa`, `2bdbf82`.

| # | Вердикт | Доказательство / остаток |
| --- | --- | --- |
| B1 | ЗАКРЫТО (почти) | Все 7 мест из плана исправлены: `tools/mockups/shell-v2-insights.js:326,814`, `shell-v2-management.js:50,188,261`, `site-v2/js/shell-v2-fin.js:106,145,175,268,289,373` (`61e0af6`, `3623fa5`). Остались два осознанных пустых catch: `shell-v2-board.js:915` (фолбэк разбора `detail`), `shell-v2-management.js:246` (logout) |
| B2 | ЗАКРЫТО | `shell-v2-insights.js:807` — `balanceDone` только в `.then`, при ошибке текст + повтор; boot не применяет данные частично (`v2-boot-template.js:368-405`) |
| B3 | ЗАКРЫТО ✓ | `todayUTC()` — `tools/v2-api-template.js:90`, экспорт `:139`; используется в insights и boot; в `shell-v2-board.js` локального «сегодня» нет вовсе. Остаток: локальные `new Date(cal.y, cal.m…)` в календаре `shell-v2-insights.js:544-560` — это рендер выбранного месяца, не «сегодня» |
| B4 | ЗАКРЫТО | `shell-v2-management.js:128-130` — `dialog.innerHTML=''` + возврат фокуса; видимая валидация `#mgmt-error` `role="alert"` (`:107-115`) |
| B5 | ЗАКРЫТО ✓ | `_guardCreate` — `tools/v2-api-template.js:100-105`, вызовы `v2-boot-template.js:550,557,562,566,586,593,598,611,623` |
| B6 | ЗАКРЫТО | `v2-boot-template.js:355-358` — `V2Api.me()` на каждый старт, роль перезаписывается подтверждённой |
| B7 | **ЧАСТИЧНО** | Drop: оптимистично + `refresh()` при отказе + тост (`shell-v2-board.js:334-336`, `2bdbf82`); «Оригинал ТН»: полный откат (`:1828-1841`). **Без отката**: чекбоксы закупки `data-check` (`:1924-1936`) и task-toggle (`shell-v2-insights.js:526-527`, fire-and-forget) |
| B8 | **ЧАСТИЧНО** ✓ | Единый `cents()` есть (`v2-api-template.js:79-86`, экспорт `:138`; boot использует `kopecks`). Остались 6 ручных `Math.round(x*100)`: `shell-v2-board.js:105,1441,1502,1613`, `shell-v2-insights.js:321,812` |
| B9 | ЗАКРЫТО ✓ | Коалесинг `all()` — `v2-api-template.js:147-166` (`_inFlight` + generation counter). Мелочь: ветка `if (self._gen !== gen) return data` возвращает данные вопреки комментарию |
| B10 | **ЧАСТИЧНО** | Точечно: drop (`updateColumnHead`), баланс в дровере (`shell-v2-insights.js:795-806`). Полным `innerHTML` остались: `renderBoard()` (`shell-v2-board.js:180-199`), 7 рендеров insights, 6 в fin |
| B11 | **НЕ НАЧАТО** ✓ | Пагинации нет: `v2-boot-template.js:375,380` грузят `/kanban/cards` и `/payments/transactions` целиком; 0 вхождений page/limit/offset. В плане помечен как отдельный этап |
| B12 | ЗАКРЫТО ✓ | `data-handler` в v2 не используется вовсе; restore — один источник: `shell-v2-board.js:491` + делегированный обработчик `:1949-1966` (`505d792`, `2122bcf`) |
| B13 | ЗАКРЫТО ✓ | `shell-v2-board.js:157-159` — `fullyPaid` из серверных чисел, `payment_status` только как текст; остаток/статус — из `remaining_kop`/`writeoff_status` |
| B14 | ЗАКРЫТО ✓ | `v2-boot-template.js:733` — `if (DEMO) { await bootDemo(); return; }`; демо грузит только `shell-v2-data.js`, ноль обращений к API; fallback «сбой → демо» удалён (`showFatal`, `:714-729`) |

## 4. Реестр C — паритет со старым фронтом

| Позиция | Вердикт | Факт |
| --- | --- | --- |
| Печать ТН/ТТН | **план ошибочен** ✓ | Роута `/payments/documents/{id}/print` **нет и никогда не было** (`grep '/print' routers/` пуст); в legacy печати тоже нет (`site/js/documents.js:140-190` — только бейдж `print_status`). В v2 `print_status` edit-ируется (`site-v2/js/shell-v2-fin.js:32,367-372`, `84150b4`) → фактический паритет ЗАКРЫТ |
| Экспорт реестра | ЧАСТИЧНО | Legacy отдаёт CSV, не Excel (`site/js/payments.js:73-84` → `features.js:614`); в v2 то же (`site-v2/js/shell-v2-fin.js:298-311`). Не перенесены: CSV «Документы» (`site/js/documents.js:19-28`) и CSV канбана (`site/js/kanban.js:904`). Серверный `/nakladnye/export/products-excel` не зовёт никто |
| Файл сделки (загрузка/скачивание) | ЗАКРЫТО | `shell-v2-board.js:892,912,952,978` + drag&drop `:760-800`; бэк `card_details_router.py:298-334`; коммиты `bf83a6c`, `3623fa5`, `8e3fd1f` |
| Workflows/автоматизации | НЕ НАЧАТО ✓ | В v2 0 упоминаний; legacy `site/js/settings.js:32,247-370`. Эндпоинтов **11** (`routers/workflows_router.py:47-188`), не 36 |
| Кастомные объекты | НЕ НАЧАТО ✓ | Legacy `site/js/settings.js` (`/custom/*`); бэк **9** роутов (`routers/custom_objects_router.py:42-233`), не 23 |
| Сохранённые виды | НЕ НАЧАТО ✓ | Роутера нет вообще (только таблица `models.py:379`); legacy хранит в localStorage (`site/js/saved_views.js:6,16,124`); v2 держит состояние в hash (`shell-v2-navigation.js:36-40`) |
| Админка: пользователи | ЧАСТИЧНО | Создание/правка есть (`shell-v2-management.js:20,86-135` → `v2-boot-template.js:566-589`, `f203e2e`, `3623fa5`). **Нет удаления/деактивации** (legacy `site/js/admin_page.js:265`) |
| Админка: справочники статусов/магазинов | ЗАКРЫТО | CRUD `v2-boot-template.js:554-563`, таблицы `shell-v2-prototype.html:1035,1046`; в legacy CRUD не было вовсе. НО на проде `deal_statuses` пуст — см. N1 |
| Админка: почта | ЧАСТИЧНО | Настройки + ручной синк (`shell-v2-management.js:177,201,221`), письма в карточке (`shell-v2-board.js:1045-1095`, `3623fa5`). Не перенесён авто-синк по таймеру (`site/js/email_parser.js:183`) |
| Админка: вебхуки | НЕ НАЧАТО | v2 — 0 упоминаний; legacy `site/js/settings.js:451-505`; бэк 6 роутов |
| Админка: журнал версий / диагностика тенанта | **план ошибочен** | В legacy UI их нет; роуты версий существуют (`kanban_router.py:270`, `clients_router.py:186`), но их не зовёт ни один фронт; `diagnos*` в бэке — 0 совпадений |
| Уведомления (badge, прочтение) | ЧАСТИЧНО | План неверен: v2 зовёт `GET /notifications` + `POST /notifications/read` (`shell-v2-management.js:250-295`), бейдж инжектится сборщиком (`build_site_v2.py:55`). Нет перехода к объекту по клику и удаления уведомления (legacy `site/js/notifications.js:84-104,145`) |
| Входящие накладные | ЗАКРЫТО (план ошибочен) ✓ | В v2 отдельная вкладка (`shell-v2-prototype.html:785,856,1124-1250`, живые галочки `d4f65cc`), фото и Excel (`3623fa5`). В legacy входящих **нет вовсе** (`site/js/nakladnye.bundle.js` не существует, `grep nakladnye site/` = 0) |
| `POST /notifications/sync-overdue` | не дефект | Роут закрыт токеном cron (`notifications_router.py:122`), клиенты звать его не должны. Вопрос — настроен ли cron на проде (в репо вызова нет) |
| `/email-parser/sync-status` | подтверждается ✓ | Роута нет; править надо документацию (HANDOFF), не код |

**Разделы legacy без аналога в v2:** кастомные объекты, воркфлоу, вебхуки,
переключатель Liquid Glass, вкладка «Списание» (отмена списания
`DELETE /payments/cards/{id}/writeoff`, `duplicate_as_document`,
`sync-writeoff-status`), вкладка «Контроль» (дебиторка), CSV-экспорт документов
и канбана, теги сделок (`routers/tags_router.py`), сохранённые виды, «Топ
менеджеров» / «По магазинам» / «Продажи по месяцам» из дашборда
(`site/js/dashboard.js:261-267`; в `shell-v2-insights.js` ни одного canvas),
переход к объекту из уведомления, удаление пользователя, авто-синк почты.

## 5. Реестр D — данные и процесс

| # | Вердикт | Факт |
| --- | --- | --- |
| D1 | ЗАКРЫТО ✓ | `server_snapshot/migrations/0010_fix_fk_orphans.py`, `0011_fill_store_locations.py` (`4825a0f`). На **сегодняшнем** снимке прод-БД: `PRAGMA integrity_check` = ok, `foreign_key_check` = **0 строк** |
| D2 | ЗАКРЫТО ✓ | Оба `stamp_assets.py --check` — 0 расхождений, exit 0 (22.09). Упомянутые в плане коммиты `site/` не трогали; legacy последний раз менялся в `9a63bc8` (18.09) и заморожен |
| D3 | ЗА ВЛАДЕЛЬЦЕМ ✓ | `origin` = `git@github.com:bublikamun-code/CMR.git` (SSH, PAT в URL нет). Отзыв токена в GitHub — только веб-интерфейс |
| D4 | ЗАКРЫТО | Решение принято: второй клон не трогать, источник правды — Documents |

## 6. V7 / V8 / V11, P0 и «Уже закрыто»

- **V7 — ЧАСТИЧНО** ✓. Выделенного поля нет (`grep originals_returned` = **0** по репо),
  миграции не было; сохранение достигнуто маппингом на `is_invoice_doc`
  (`shell-v2-board.js:1824-1838` → `v2-boot-template.js:654-660`, чтение `:219`) —
  флаг больше не теряется после F5. Остаётся семантический конфликт: тот же
  `is_invoice_doc` означает «ТН у нас» в `site-v2/js/shell-v2-fin.js:195,410`.
- **V8 — ЗАКРЫТО** (`8e3fd1f`): `shell-v2-board.js:1008-1026` — после загрузки
  `item.invFile = saved.invoice_file_name` + `openCard(activeCard)`, строка
  перерисовывается без F5; drag&drop `:782`, клик-пикер `:1349`.
- **V11 — НЕ НАЧАТО (осознанно)** ✓: `routers/payments_router.py:296-297` —
  `DELETE /transactions/{id}` только с `Depends(get_current_user)`, без `require_role`:
  любой аутентифицированный (manager/warehouse/documents) удаляет платежи.
  Частичные роли есть в `clients_router.py:170,256`, `tasks_router.py:85,105,268`.
- **P0 (пункты 1–6) — все живы**: `allSettled` + `showFatal` (`v2-boot-template.js:387,396-403,714-725`);
  `paintPayment`/`updatePayment` разделены (`shell-v2-board.js:626,648`, вызов из input/change `:1801-1820`);
  `buildFinSource(cards, transactions, documents, nakladnye)` — 4 параметра, вызов с 4 аргументами ✓
  (`v2-boot-template.js:450` и `:406`); `ordered/received/amount/originalsReturned` с сервера (`:209-219`);
  подстановки чужих значений убраны (`:169-177`); группы списания грузятся (`:385,405`). Коммиты `3e38767`, `61e0af6`.
- **«Уже закрыто» (6 пунктов аудита) — живо**: пути `/writeoffs/groups/…`; `invoice_id`
  (`services/payments.py:408` → `shell-v2-board.js:1609`); `issueGroup` правит кэш только в
  `.then` (`:2040-2054`, разблокировка `:2067-2068`); смена пароля = logout + reload
  (`shell-v2-management.js:245-248`); баланс в дровере после присвоения `client`
  (`shell-v2-insights.js:314-318`); миграция 0010.

## 7. Чек-лист этапа — по пунктам

Подтверждаются кодом/git: незакоммиченный бэкенд (0009), инструменты проверки
(`git ls-files tools/` = 9 файлов), пакеты A, B, C, D, E, пересборка + штампы
(`6243a62`), деплой-путь `tools/deploy.sh v2`, V1–V5 (`0e069e6`, `19984fa`,
`dfe3e11`, `df0cab4`), Пакет G (`c25d418`, `a01b5b4` + `tests/test_frontend_switch.py`),
плавность UI (все 7 коммитов: `00ec177`, `e58bc66`, `e7ec197`, `379be2c`, `12876fa`,
`2bdbf82`, `b996446`), D1, D3 (частично).

Частично: дизайн-фиксы (`prefers-reduced-motion` и `pointer:coarse` есть, но
**`@media` под 1024 не найдено** — брейкпоинты только `max-width:540/600px`,
`max-height:650px`); верификация после деплоя (стендовая часть недоказуема из репо,
прод-перепись ждёт кредов); Пакет F — `[ ]`, подтверждается кодом.

**Недоказуемы из репо** (процессные артефакты, в git не осели): снятие копии прод-БД
и md5-сверка, «аудит 24/25», «перепись на проде — чисто», деплой 19.09 с побайтовой
сверкой, факт применения 0010/0011 на проде, исторические числа pytest.
Сегодня перепроверено заново: pytest 409/405+4, штампы 0/0, FK 0, прод отдаёт v2.

## 8. Пакет G и этап переключения — факты за 22.09 ✓

- `server_snapshot/main.py:113` — `FRONTEND_MODE = os.environ.get("CRM_FRONTEND", "v2")`;
  `/` → 302 `/v2/` (`:121`), `/legacy` (`:125`), `/legacy/admin` (`:132`), `/admin` → 302 `/v2/` (`:142`).
- Прод это подтверждает: `curl -I https://87-232-64-12.nip.io/` → **302 `/v2/`**;
  `/v2/` → 200 (36 325 Б); `/legacy` → 200 (73 362 Б); `/v2/js/v2/boot.js` → 200,
  49 848 Б, `cache-control: public, max-age=31536000, immutable`.
- Кэш-политика `/v2`: `main.py:214-236` (`V2_IMMUTABLE_PREFIXES` + middleware), `f76e02b`.
  **Теста нет**: в `tests/` `immutable` проверяется только для legacy (`test_smoke.py:49-56`).
- CSP: `main.py:191-193` всё ещё разрешает `https://fonts.googleapis.com` и
  `https://fonts.gstatic.com` — после само-хостинга Manrope (`f54f81b`) устарело.
  Внешних ссылок в `site-v2/index.html` — 0; инлайн-скрипт **один тег** (`site-v2/index.html:16`)
  с двумя сниппетами (тема + `V2_ASSET_VER`), а не «ровно два», как в AGENTS.md.
- pm2 на проде: `crm` — online, `restarts=170`, **`unstable_restarts=0`**, аптайм 31 ч;
  `nakladnye-bot` — online, `restarts=28`. `CRM_DATA_DIR=/var/www/h212005/data/crm_data`,
  `CRM_UPLOADS_DIR=/var/www/h212005/data/crm_data/uploads`, **`CRM_FRONTEND` не задан**
  (работает код-дефолт `v2`). 170 рестартов при 0 нестабильных = деплои, не падения.
- `tools/deploy.sh`: цель `v2` (`:99-136`) — jsc-синтаксис → `build_site_v2.py` →
  `rsync -avz --delete` в `site-v2/`, **без рестарта и без `stamp_assets`**;
  `front` (`:76-98`) — `check_js.sh` + `stamp_assets.py --check` + rsync `site/` без `--delete`;
  `back` (`:138-186`) — `py_compile` → rsync → рестарт pm2 (бот без `--update-env`).
  Usage-строка `:203` цель `v2` не упоминает.
- **nginx в репо не знает про v2**: `grep -rn "v2\|legacy" nginx/` — 0 совпадений;
  `nginx/snippets/app-locations.conf:18-36` — `root` + `try_files … /index.html`.
  Docker-путь на `/v2/` отдаст legacy. Боевой nginx лежит только на сервере.
- **CI деплоит legacy**: `.github/workflows/build-deploy.yml:187` — `bash tools/deploy.sh front`.
  v2 из CI не пересобирается и не выкатывается; свежесть `site-v2` не проверяет ни один
  гейт (`server_snapshot/tools/stamp_assets.py` сканирует только `index.html`/`admin.html`/`css`/`js`,
  то есть legacy-симлинки, но не `site-v2`).
- `tools/check_js.sh` проверяет только `site/js/*.js` (legacy); mockups и site-v2 — не входят.

## 9. Новые находки 22.09 (свежий снимок прод-БД)

Снимок снят сегодня: `sqlite3 .backup` на проде → `/tmp/v2-audit-prod/crm_app.db`
(3 334 144 Б, `integrity_check: ok`); временный файл на сервере удалён.
Стенд: `http://127.0.0.1:8126/` (порт 8125 занят стендом прошлой сессии на
устаревших данных — PID 89005, не использовался).

Данные: 1060 карточек / **383 активных**, 25 клиентов, 14 входящих накладных,
23 `client_payments`, 1456 `record_versions`, 1638 `activity_log`,
6 боевых пользователей (ManagerY superadmin, ManagerA manager, ManagerN/V warehouse,
Dokumenty documents, ManagerD admin) + 2 локальные аудит-учётки.

- **N1. Справочник статусов сделок на проде пуст.** `/dictionaries/statuses` → `[]`
  (2 байта); `deal_statuses` = **0 строк**, ни одна миграция его не заполняла
  (0011 заполнила только `store_locations`). Доска работает на клиентском хардкоде
  `STATUS_MAP` (`v2-boot-template.js:30-40`) + добор неизвестных статусов из карточек
  (`:147-153`). Реальные статусы в данных: Закрыто 177, В работе 168, На списание 17,
  Сборка 11, Новый запрос 6, Ждет оплаты 4. Следствие: раздел «Статусы» в админке v2
  пуст — статусы нельзя переименовать, перекрасить или добавить, канон живёт в JS.
- **N2. В v2 нельзя создать карточку.** `POST /kanban/cards` из v2 не зовётся ни разу
  (grep по mockups, шаблонам и сборке); единственная «Новая сделка» — в «Клиент 360»
  (`shell-v2-insights.js:409`). В legacy создание есть: `site/js/kanban.js:840`.
- **N3. Сервер не является причиной тормозов.** Замеры на стенде при прод-объёме:
  `/kanban/cards` — **484 867 Б, 383 объекта, 30–57 мс** (3 прогона стабильно);
  `/payments/transactions` — 135 544 Б за 14 мс; `/clients` — 8 012 Б; `/nakladnye` —
  7 328 Б; `/notifications` — 29 Б; `/tasks` — 447 Б. В сумме клиент на каждый заход
  разбирает ~640 КБ JSON и рисует сотни плиток полным `innerHTML` (B10/B11).
- **N4. Данные, требующие решения владельца:** 180 активных карточек с
  `paid_amount > 0` и **без единой строки** в `client_payments` (исторические суммы
  без реестра); при этом среди карточек, у которых платежи есть, расхождений
  `paid_amount` ↔ сумма платежей — **0** (план 19.09 фиксировал 6). Также 333 активные
  карточки без клиента и 41 без магазина. Данные не правились.
- **N5. Локальная dev-БД `server_snapshot/crm_app.db` отстала** (миграции до 0007).
- **N6. `uploads` на проде — 384 МБ / 705 файлов** (367 pdf, 83 docx, 79 xlsx, 27 jpg);
  в локальный стенд взяты только изображения (15 МБ), поэтому 404 на PDF — артефакт стенда.
- **N7. Прод-`email_settings.json` существует и менялся сегодня (20:40)** — на стенд не
  копировался намеренно (содержит креды). Пустые настройки почты на стенде — артефакт.

## 10. Ошибки в самом плане (переписать, чтобы не чинить дважды)

1. A13 — дефекта нет: канона «Частично оплачен» в коде никогда не было.
2. Печать ТН/ТТН — роута `/payments/documents/{id}/print` никогда не существовало;
   реальный паритет по `print_status` закрыт.
3. Числа эндпоинтов завышены: workflows — 11 (не 36), custom objects — 9 (не 23),
   saved views — роутера нет (не 6).
4. `site/js/nakladnye.bundle.js` не существует; legacy входящих накладных не имеет —
   это не паритет-разрыв, а новая функция v2.
5. Уведомления в v2 — не `list_unread` (такого роута нет), а `GET /notifications` +
   `POST /notifications/read`.
6. «Журнал версий» и «диагностика тенанта» в legacy UI отсутствуют.
7. «Карточки: 1360 всего» (19.09) против 1060 на снимке 22.09 — цифру перепроверить.
8. Числа pytest в плане (382/404) устарели: сегодня 409 (prod) и 405 + 4 skipped (models).
9. AGENTS.md: «инлайн-скриптов в v2 ровно два» — фактически один тег с двумя сниппетами.

## 11. Остаток — что чинить, по приоритету

**P0 — блокирует ежедневную работу (жалобы владельца 22.09):**

1. Создание карточки на доске (N2) — единственный незакрытый пункт чек-листа (Пакет F).
2. Отзывчивость при прод-объёме (N3 + B11 + B10): пагинация/ленивая отрисовка доски,
   точечные обновления вместо полного `innerHTML`.
3. A7 — снятие клиента: слать `clear_client:true` (сервер и тест уже готовы).
4. A15 — определить `KBData.reload` (обвязка над `KBApi.all()` уже есть,
   `v2-api-template.js:147-166`) либо вставлять `fresh` в кэш: сейчас restore не обновляет доску.
5. N1 — заполнить `deal_statuses` аддитивной миграцией из фактических статусов
   (по образцу 0011 для магазинов) и убрать зависимость от клиентского хардкода.

**P1 — надёжность и эксплуатация:**

6. B7 — откат для чекбоксов закупки и task-toggle; B8 — 6 ручных округлений на `cents()`.
7. Тест на кэш-политику `/v2` (`main.py:214-236`) и чистка CSP от `fonts.googleapis/gstatic`.
8. nginx: внести боевой конфиг в репо, добавить `/v2` и `/legacy`; в CI — сборка и
   выкатка v2 (сейчас `deploy.sh front`, то есть legacy); гейт на свежесть `site-v2`.
9. V7 — решить: отдельная колонка `originals_returned` или легализовать маппинг на
   `is_invoice_doc`, устранив конфликт с «ТН у нас» в fin.

**P2 — паритет и порядок:**

10. Разделы настроек: workflows (11 роутов), custom objects (9), webhooks (6),
    сохранённые виды; теги сделок; CSV-экспорт документов и канбана; удаление
    пользователя; переход к объекту из уведомления; авто-синк почты; «Контроль» и
    отмена списания; «Топ менеджеров»/графики.
11. V11 — ролевая модель для `DELETE /payments/transactions/{id}` и аналогичных
    операций (решение владельца).
12. Мелочи: A2 (свести `TTN_TYPES` и `NAKLADNYE_DOC_TYPES` к одному источнику),
    A5 (`is_advance` потребить или убрать комментарий), A9 (показать чек-лист задачи),
    usage-строка `deploy.sh:203`, мусор `server_snapshot/js/.mimosa/`,
    `migrate.py` для локальной dev-БД.

**За владельцем:** отзыв GitHub PAT (D3), регистрация домена и HTTPS, офсайт-копия
бэкапов, решение по V11 и N1/N4, пуш 4 локальных коммитов и деплой.

## 12. Как поднять стенд заново (для следующих сессий)

```bash
# 1. Свежий снимок прод-БД (read-only к проду, временный файл потом удалить)
ssh -i ~/.ssh/crm_svetvdome_deploy h212005@87.232.64.12 \
  'sqlite3 /var/www/h212005/data/crm_data/crm_app.db ".backup /tmp/crm_snap.db"'
mkdir -p /tmp/v2-audit-prod/uploads
scp -i ~/.ssh/crm_svetvdome_deploy h212005@87.232.64.12:/tmp/crm_snap.db /tmp/v2-audit-prod/crm_app.db
ssh -i ~/.ssh/crm_svetvdome_deploy h212005@87.232.64.12 'rm -f /tmp/crm_snap.db'
# картинки uploads (PDF — 367 шт., 384 МБ — по желанию)
rsync -az -e "ssh -i ~/.ssh/crm_svetvdome_deploy" --include='*.jpg' --include='*.png' \
  --exclude='*' h212005@87.232.64.12:/var/www/h212005/data/crm_data/uploads/ /tmp/v2-audit-prod/uploads/

# 2. Учётка для входа (в КОПИИ, models_tenant обязателен — иначе FK-ошибка маппера)
cd server_snapshot && CRM_DATA_DIR=/tmp/v2-audit-prod ./.venv/bin/python - <<'PY'
import auth, models, models_tenant  # noqa: F401
from database import SessionLocal
db = SessionLocal()
u = db.query(models.User).filter_by(username="audit_admin").first() or models.User(username="audit_admin")
u.role = "admin"; u.hashed_password = auth.get_password_hash("Audit-2026!")
db.add(u); db.commit(); db.close()
PY

# 3. Стенд (8125 бывает занят старым процессом — брать 8126)
cd server_snapshot && CRM_DATA_DIR=/tmp/v2-audit-prod \
  CRM_UPLOADS_DIR=/tmp/v2-audit-prod/uploads \
  ./.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8126

# 4. Проверки (логин form-encoded, лимит 10/мин)
curl -s -X POST http://127.0.0.1:8126/auth/login -d 'username=audit_admin&password=Audit-2026!'
python3 tools/v2_error_census.py --base http://127.0.0.1:8126/v2/
```
