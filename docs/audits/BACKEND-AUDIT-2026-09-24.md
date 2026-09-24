# Backend-аудит CRM «Свет в доме» — 24.09.2026

## Итог

Исходный проход был read-only: код, миграции, конфигурация, production и рабочая
production-БД не изменялись. В ходе него подтверждены три дефекта с
непосредственным влиянием на безопасность или целостность финансовых данных:
bearer оставался действительным после logout, параллельная выдача накладной по
одной сделке создавала перевыпуск, а soft-deleted card оставалась доступна через
direct GET.

В отдельной утверждённой волне BA-01, BA-02 и BA-03 исправлены локально и
закрыты регрессиями. Production, production-БД и v2/legacy-фронт не затрагивались;
миграция 0016 на production не применялась, commit, push и deploy не выполнялись.
BA-04 и остальные находки остаются открытыми.


## Ограничения и метод

Разрешено:

- читать исходники, тесты, миграции и локальные метаданные файлов;
- запускать тесты и одноразовые harness-скрипты на disposable SQLite/TestClient;
- читать публичные production GET/HEAD-заголовки, PM2 status и ограниченный
  log tail без секретов и без authenticated write-операций.

Запрещено и не выполнялось:

- login/logout, смена пароля, delete, restore, save, payment, write-off, sync,
  миграции и любые production write-операции;
- чтение/вывод содержимого `.pm2.env`, `.secret_key`, `.cron_token`,
  `email_settings.json`, JWT, паролей, bot/cron/webhook secrets;
- изменение `server_snapshot/`, legacy `site/`, конфигурации и production.

Ключевые команды:

```text
cd server_snapshot && ./.venv/bin/python -m pytest -q
CRM_TEST_SCHEMA=models ./.venv/bin/python -m pytest -q
```

Фактический результат исходного read-only baseline:

- профиль production DDL: `897 passed in 529.62s`, exit 0;
- профиль ORM `CRM_TEST_SCHEMA=models`: `893 passed, 4 skipped in 529.58s`, exit 0;
- disposable schema после fixture + migrations: `PRAGMA integrity_check = ok`,
  `PRAGMA foreign_key_check = 0`, 31 таблица, 76 индексов;
- `git diff --check`: exit 0; рабочее дерево не получило изменений от аудита;
- untracked `docs/SESSION-BRIEF-2026-09-22.md` не включён и не изменён.

Фактический результат после локальной волны исправлений:

- целевые auth/migration/payment/kanban-регрессии: production DDL — `88 passed`;
  ORM — `87 passed, 1 skipped` (ожидаемый profile-specific skip);
- полный suite, production DDL: `910 passed in 669.87s`, exit 0;
- полный suite, ORM: `906 passed, 4 skipped in 675.12s`, exit 0;
- `git diff --check`: exit 0; production, legacy `site/` и generated `site-v2/`
  в изменениях волны отсутствуют.

Четыре skip второго профиля ожидаемы: индекс `uq_remainder_per_card` есть только
в production DDL, а три schema-parity/smoke-проверки намеренно сравнивают только
production-схему. Это не доказательство отсутствия других дефектов.

## Реестр находок

### BA-01 — Bearer-токен не аннулируется logout

- **Severity:** high
- **Статус:** исправлено локально 24.09; production без деплоя
- **Область:** authentication/session
- **Доказательство:** `server_snapshot/routers/auth_router.py:64-69` —
  `/auth/logout` удаляет только cookie; `server_snapshot/auth.py:69-136` —
  bearer JWT проверяется только по сроку, пользователю и claim `pv`; серверного
  blacklist/revocation или проверки `jti` нет.
- **Динамика:** disposable `TestClient`:
  `protected_before_logout=200`, `logout=200`, затем тот же
  `Authorization: Bearer <token>` получил `bearer_reuse_after_logout=200`.
- **Ожидалось:** после logout ранее выданный bearer-токен не должен продолжать
  авторизовать запросы.
- **Фактически:** cookie удаляется, но bearer-токен остаётся валиден до истечения
  срока.
- **Влияние:** украденный/сохранённый в клиенте bearer-токен позволяет продолжать
  работу после явного выхода. httpOnly-cookie сама по себе уязвимость не устраняет,
  пока переходный bearer-путь остаётся рабочим.
- **Исходная рекомендация:** определить стратегию отзыва (server-side
  revocation/rotation либо короткоживущие access tokens с refresh revocation),
  покрыть bearer и cookie сценарии тестом.
- **As-built:** добавлена таблица `revoked_auth_tokens` и идемпотентная миграция
  0016. `logout` независимо отзывает bearer и cookie, а `get_current_user`
  проверяет ключ до скользящего продления cookie. В БД хранится только
  `jti:<jti>` либо SHA-256 ключ legacy-токена без `jti`; сырой JWT не
  сохраняется. Покрыты bearer-only, cookie-only, два разных токена, повторный
  logout, malformed/expired token, legacy token без `jti`, независимость второго
  токена пользователя и отсутствие resurrection через sliding renewal.
  Ошибка записи отзыва не маскируется как успешный logout.

### BA-02 — Race condition при параллельной выдаче накладной

- **Severity:** high
- **Статус:** исправлено локально 24.09; production без деплоя
- **Область:** payments/financial integrity
- **Доказательство:** `server_snapshot/services/payments.py:421-550` и
  `:475-489` делают чтение остатка, проверку duplicate, изменение остатка и
  commit без блокировки строки или условного атомарного UPDATE. Уникальный индекс
  защищает только remainder-запись, но не две invoice-записи с одинаковым
  нормализованным номером.
- **Динамика:** disposable SQLite, две отдельные SQLAlchemy-сессии синхронизированы
  barrier immediately after duplicate check:

  ```text
  concurrent_results= [('ok', 2), ('ok', 4)]
  invoice_rows= 2
  document_rows= 1
  ledger_amounts= [40.0, 60.0, 60.0]
  card_status= На списание
  ```

  На сделке с суммой 100 оба параллельных запроса успешно выписали по 60; два
  вызова завершились без 4xx/5xx, в ledger появились две выданные записи и итог
  160 вместо допустимых 100. Один документ не отменяет двойную финансовую запись.
- **Ожидалось:** максимум одна успешная выдача либо deterministic 409/400; после
  неё issued total не превышает остаток.
- **Влияние:** перевыпуск, неверные остаток/статус, расхождение реестра и
  финансовых документов. На SQLite проявление может дополнительно зависеть от
  locking/last-writer behaviour, но check-then-write race уже воспроизведён.
- **Исходная рекомендация:** перевести выдачу на транзакционную атомарную
  схему (блокировка/условный UPDATE или DB-level unique/serialized operation),
  добавить конкурентный regression-тест для `issue_invoice` и `add_invoice`.
- **As-built:** `add_invoice` и `issue_invoice` завершают read-транзакцию
  авторизации и до чтения остатка резервируют единственный SQLite writer-slot
  no-op `UPDATE cards SET id=id`; writer удерживается до существующего commit.
  Конкурентные HTTP-регрессии проверяют issue+issue, add+add, issue+add с одним
  нормализованным номером и разными номерами: ровно один успешный mutation,
  проигравший получает 400, ledger/document/remainder/status остаются
  согласованными.

### BA-03 — Soft-deleted card доступна прямым GET

- **Severity:** medium
- **Статус:** исправлено локально 24.09; production без деплоя
- **Область:** soft-delete/authorization
- **Доказательство:** `server_snapshot/routers/kanban_router.py:66-101` фильтрует
  `is_deleted == False` в списке, но `:104-117` direct GET ищет карточку только по
  `id` и не проверяет `is_deleted`.
- **Динамика:** disposable card с `is_deleted=True`; запрос
  `GET /kanban/cards/{id}` с валидным manager JWT вернул:
  `deleted_card_direct_get=200`, `response_is_deleted=True`.
- **Ожидалось:** архивная карточка недоступна через обычный direct GET (либо
  требуется отдельная авторизованная archive-policy).
- **Влияние:** удалённые/архивные сделки и связанные данные можно получить в обход
  списка; это усложняет восстановление и расширяет поверхность IDOR-подобного
  доступа. Проверки финансовых/attachment-роутов на `is_deleted` отдельно не
  завершены.
- **Исходная рекомендация:** централизовать active-card lookup/guard и
  закрепить ожидаемую archive-policy тестами для GET/PATCH/financial/attachments.
- **As-built:** точечно добавлен `is_deleted == False` в
  `GET /kanban/cards/{card_id}`. `/kanban/trash`, restore, soft-delete и
  permanent-delete не менялись. Lifecycle-регрессия проверяет live detail,
  архивный detail 404, исключение из доски, включение в trash, restore и
  повторное исключение из trash. Broader guards для PATCH/payment/upload/
  checklist остаются отдельной волной.

### BA-04 — Legacy JWT без `pv` остаётся валидным после смены пароля

- **Severity:** medium
- **Статус:** подтверждено
- **Область:** authentication/password rotation
- **Доказательство:** `server_snapshot/auth.py:105-111` проверяет `pv` только
  если claim присутствует; legacy-токены без него намеренно принимаются.
- **Динамика:** после смены пароля disposable пользователя legacy bearer без `pv`
  получил `legacy_token_without_pv_after_password_change=200`.
- **Ожидалось:** смена пароля завершает все ранее выданные токены, включая старый
  формат.
- **Влияние:** старый токен, выпущенный до внедрения password-version claim,
  сохраняет доступ после ротации пароля. Риск ограничен сроком токена, но он
  реален для legacy-сессий.
- **Рекомендация без реализации:** после миграционного окна отклонять токены без
  `pv` или иметь явный безопасный план принудительного re-login; отдельно
  документировать обратную совместимость.

### BA-05 — Денежные Pydantic-схемы принимают NaN/Infinity и отрицательный PATCH

- **Severity:** medium
- **Статус:** подтверждено
- **Область:** validation/money
- **Доказательство:** `server_snapshot/schemas.py:247-289` — `total_amount: float`,
  проверяется только отрицательное значение; transaction/request schemas и
  `routers/payments_router.py:565-570` используют `float` без finite/range guard.
- **Динамика:** disposable schema harness:

  ```text
  card_nan ACCEPTED nan
  card_inf ACCEPTED inf
  tx_nan ACCEPTED nan
  tx_inf ACCEPTED inf
  tx_negative_update ACCEPTED -1.0
  invoice_nan ACCEPTED nan
  invoice_inf ACCEPTED inf
  ```

- **Ожидалось:** API отвергает нечисловые/нефинитные значения, отрицательные
  суммы там, где они недопустимы, и чрезмерную точность/диапазон.
- **Влияние:** `NaN`/`Infinity` могут проходить дальше в SQLite/вычисления и
  загрязнять финансовые агрегаты; отрицательный transaction update может нарушать
  инварианты. Полный набор затронутых downstream-операций требует отдельного
  regression-теста.
- **Рекомендация без реализации:** перейти на ограниченный decimal/validation
  policy, явно запретить non-finite, определить nonnegative/precision/range rules
  по каждому денежному полю.

### BA-06 — `Secure` cookie зависит от схемы request, а не от trusted proxy config

- **Severity:** medium
- **Статус:** подтверждённое поведение кода; фактический production Set-Cookie не проверялся
- **Область:** cookies/proxy
- **Доказательство:** `server_snapshot/routers/auth_router.py:52-59` и
  `server_snapshot/auth.py:127-135` используют
  `secure=(request.url.scheme == "https")`. Код не имеет отдельного
  `CRM_COOKIE_SECURE`/trusted-forwarded-protocol policy.
- **Динамика:** HTTPS-like TestClient → `cookie_secure_https=True`; HTTP-like
  TestClient → `cookie_secure_http=False`. Cookie-only protected access в обоих
  harness-сценариях работал.
- **Ожидалось:** для HTTPS production cookie всегда получает `Secure`, независимо
  от того, как Uvicorn увидел forwarded protocol.
- **Фактически:** при ошибочном/неполном proxy protocol cookie может быть выпущена
  без `Secure`.
- **Ограничение:** production login не выполнялся; Set-Cookie на проде не снимался.
  Это подтверждённый кодовый риск, не утверждение о фактическом prod-инциденте.
- **Рекомендация без реализации:** закрыть proxy protocol/trust boundary и задать
  единую явную cookie-security policy; проверить фактический прод Set-Cookie
  только безопасным read-only тестом с временной учётной записью.

### BA-07 — CSP оставляет inline scripts/styles

- **Severity:** medium
- **Статус:** подтверждено, residual risk
- **Область:** browser security
- **Доказательство:** `server_snapshot/main.py:182-203`; production GET headers
  повторно показали:

  ```text
  content-security-policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' ...
  ```

- **Ожидалось:** минимизация `unsafe-inline` либо nonce/hash для оставшихся inline
  блоков.
- **Влияние:** CSP не блокирует успешно внедрённый inline script; текущая
  безопасность зависит от экранирования и отсутствия DOM-XSS. `object-src`,
  `frame-ancestors`, `base-uri` при этом заданы.
- **Рекомендация без реализации:** инвентаризировать inline-контексты v2, перейти
  на nonce/hash или external scripts, затем включить CSP в прикладном тесте.

### BA-08 — HSTS отсутствует; TLS на production самоподписанный

- **Severity:** medium
- **Статус:** подтверждено read-only на production
- **Область:** transport
- **Доказательство:** production `curl -k` GET `/health`, `/api/version`, `/v2/`
  вернул 200; в заголовках нет `Strict-Transport-Security`. Ранее зафиксирована
  ошибка обычного TLS-клиента `self signed certificate`.
- **Ожидалось:** доверенный сертификат и HSTS на HTTPS-домене.
- **Влияние:** браузер/клиент может получать предупреждение о сертификате, а
  downgrade/первый заход на HTTP не защищён политикой HSTS. Это транспортный и
  операционный долг, не доказанная компрометация.
- **Рекомендация без реализации:** боевой домен + доверенный сертификат,
  redirect HTTP→HTTPS и HSTS после успешного переключения; не менять сертификат
  в рамках аудита.

### BA-09 — `/health` проверяет только liveness

- **Severity:** medium
- **Статус:** подтверждённое поведение; readiness-дефект требует эксплуатационного решения
- **Область:** operations
- **Доказательство:** `server_snapshot/main.py:160-162` возвращает только
  `{"status":"ok","version":...}`; SQLite, migrations, uploads, disk, tenant DBs и
  внешние зависимости не опрашиваются.
- **Ожидалось:** отдельные liveness/readiness сигналы для оркестратора.
- **Влияние:** процесс может считаться готовым при недоступной/неисправной БД или
  сломанной миграционной схеме; PM2 `wait_ready=false`
  (`server_snapshot/ecosystem.config.js:14-17`) не добавляет application-level
  readiness gate.
- **Рекомендация без реализации:** разделить `/health/live` и `/health/ready`,
  проверять DB/schema/миграции и минимальные filesystem prerequisites.

### BA-10 — Проверки group issuance/membership остаются check-then-write

- **Severity:** high/medium — риск, не подтверждён отдельным воспроизведением
- **Статус:** риск/требует решения
- **Область:** group write-offs
- **Доказательство:** `server_snapshot/routers/writeoff_groups_router.py:104-126`,
  `:161-228` проверяют `written_off`, состав и сумму в Python, затем делают
  несколько updates/inserts и один commit без conditional transition/locking.
- **Ожидалось:** две одновременные выдачи группы не могут обе пройти проверку и
  создать две группы/документы.
- **Фактически:** статический код показывает тот же класс race, что уже
  воспроизведён в BA-02, но отдельный group-race harness в этом цикле не довёл
  результат до подтверждения.
- **Рекомендация:** добавить такой же disposable concurrency regression и
  перевести операцию на атомарный state transition.

### BA-11 — Webhook secret хранится plaintext; test endpoint возвращает raw exception

- **Severity:** medium
- **Статус:** риск/требует решения
- **Область:** integrations/secrets/error disclosure
- **Доказательство:** `server_snapshot/routers/webhooks_router.py:91-100,
  127-132` принимает и сохраняет `secret` открытым текстом; `:228-243` возвращает
  `str(e)` клиенту; `:281-289` нет retry/dead-letter queue.
- **Ожидалось:** секрет интеграции защищён at-rest, ошибки доставки не раскрывают
  внутренние детали, доставка имеет наблюдаемую политику повторов.
- **Фактически:** plaintext storage и raw error подтверждены кодом; внешняя
  доставка и эксплуатационная политика повторов не проверялись.
- **Рекомендация:** решить, нужен ли reversible encryption, ограничить ошибку
  generic-кодом с correlation id, определить retry/dead-letter и мониторинг.

### BA-12 — Email Fernet fallback fail-open

- **Severity:** medium
- **Статус:** риск/требует решения
- **Область:** email credentials
- **Доказательство:** `server_snapshot/routers/email_parser_router.py:180-198`:
  при `_fernet is None` или `InvalidToken` возвращается исходная строка; при
  неправильном ключе приложение продолжает импорт с warning/fallback.
- **Динамика:** harness с намеренно invalid Fernet key подтвердил warning на
  импорте и продолжение работы приложения.
- **Ожидалось:** невозможность расшифровать credentials должна быть явной
  safe-fail/rejected-config ошибкой, а не молчаливой обработкой plaintext.
- **Влияние:** зависит от фактического состояния settings; при неверном ключе
  система может пытаться использовать нерасшифрованное значение или работать с
  ошибочной конфигурацией. Фактический production email-sync не запускался.
- **Рекомендация:** валидировать encryption configuration при старте/настройке,
  различать legacy plaintext и decrypt failure, не логировать секретные детали.

### BA-13 — Tenant-изоляция не является доказанным инвариантом

- **Severity:** medium/high — риск
- **Статус:** не проверено полностью
- **Область:** multi-tenant/authorization
- **Доказательства:** в `writeoff_groups_router.py:40-82, 96-126` чтение группы и
  карточки идёт по `id` без tenant-фильтра; `main.py:96-101` включает cron
  routers; в коде есть `tenant_id`, но для части объектов фактическая политика
  смешана с single-company режимом. В `webhooks_router.py:117-121` фильтр tenant
  есть, а `kanban` list и many card routes используют company-wide semantics.
- **Почему не подтверждено как дефект:** текущая бизнес-конфигурация допускает одну
  компанию и `tenant_id=NULL`; cross-tenant dynamic matrix не завершена.
- **Рекомендация:** зафиксировать модель «одна компания» или сделать tenant guard
  обязательным на каждом object route; не считать UI-скрытие заменой API guard.

### BA-14 — Bot token принимается в query string

- **Severity:** medium
- **Статус:** подтверждённый риск
- **Область:** bot authentication
- **Доказательство:** `server_snapshot/routers/nakladnye_router.py:35-48` —
  `X-Bot-Token` или `request.query_params.get("bot_token")`.
- **Влияние:** query credentials могут попасть в access logs, proxy logs,
  browser history, referrers и диагностические URL. Фактического утекающего
  production-лога в этом аудите не установлено.
- **Рекомендация:** оставить только header, удалить query-совместимость после
  проверки клиентов и добавить отрицательный тест.

### BA-15 — Локальные secret/settings-файлы имеют широкие права

- **Severity:** medium для локальной машины, low для production
- **Статус:** подтверждено локально; production проверено отдельно без чтения содержимого
- **Доказательство:** `stat` в `server_snapshot/`: `.secret_key` `0644`,
  `email_settings.json` `0644`, `.cron_token` `0600`, `.pm2.env` отсутствует.
  Production `.pm2.env`, `.secret_key`, `.cron_token` ранее проверены как `0600`;
  production `email_settings.json` отсутствует.
- **Ожидалось:** secret и mailbox-password files — `0600` на всех хостах.
- **Риск:** локальный пользователь/процесс с доступом к рабочей копии может
  прочитать эти файлы. Содержимое не раскрывалось.
- **Рекомендация:** закрыть права локальных файлов и добавить preflight проверку
  режимов без вывода содержимого.

## Что подтверждено как работает

- production `/health`, `/api/version`, `/v2/`: HTTP 200; security headers
  присутствуют;
- CORS на изолированном TestClient: доверенный origin разрешён, недоверенный
  preflight отклонён (400), `allow_credentials=True` присутствует;
- cookie `HttpOnly` и `SameSite=Lax` подтверждены на login; HTTPS-like login
  получает `Secure`;
- отключение пользователя блокирует новый login и текущую проверку пользователя;
- SQLAlchemy-пути в проверенных зонах параметризованы; disposable
  `foreign_key_check` чист;
- загрузки имеют отдельные auth/extension/magic-byte ограничения в проверенных
  card-контурах; полный traversal/symlink/IDOR matrix ещё не завершён;
- webhook URL получает scheme/hostname/DNS/IP validation и запрет redirect;
  полный DNS-rebinding/IPv6/alternate-form harness не завершён.

## Не проверено или требует отдельного решения

1. полная authorization matrix для manager/warehouse/documents/admin/superadmin
   и cross-owner/cross-tenant object IDs;
2. все PATCH-пути транзакций и recomputation остатка/статуса после частичных
   обновлений;
3. group invoice/membership concurrency;
4. полный upload/download adversarial matrix: traversal, Unicode/CRLF,
   extension-magic mismatch, symlink, oversized/empty, IDOR;
5. SSRF edge cases: IPv6, alternate IP representations, DNS rebinding-like
   повторная проверка, error disclosure;
6. controlled IMAP/cron/bot runtime с реальными протоколами и attachments;
7. readiness/disk/migration/tenant-DB behavior в реальном deployment;
8. эффективная tenant-модель и необходимость полноценной изоляции;
9. drift между `requirements.txt` и optional bot imports/локальным deployment;
10. actual production Set-Cookie через HTTPS (без production login в этом цикле).

## Порядок исправлений после первой волны

1. **Закрыто локально:** BA-01 — server-side logout/revocation;
2. **Закрыто локально:** BA-02 — сериализация invoice mutation и конкурентные
   регрессии;
3. **Закрыто локально:** BA-03 — direct GET скрывает soft-deleted card;
4. **P1:** BA-04/BA-05 — завершить password-version migration и money validation;
5. **P1:** BA-10 — atomically закрыть group operations;
6. **P1:** BA-06/BA-08 — proxy-aware Secure cookie, trusted TLS/HSTS;
7. **P1:** BA-09 — readiness вместо liveness-only health;
8. **P2:** BA-07/BA-11/BA-12/BA-13/BA-14/BA-15 — CSP, integrations/secrets,
   email fail-closed, tenant policy, bot transport и filesystem permissions.

Пункты 1–3 не применены к production в этой волне; перед их релизом требуется
отдельное решение владельца на backup, migration 0016, backend deploy и post-deploy
проверки. Остальные пункты требуют отдельных решений и новых проверок.
