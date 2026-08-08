# Критичные уязвимости CRM — найдены 07.08.2026

## 🔴 КРИТИЧНО — требует немедленного патча

### 1. Открытый эндпоинт массовой синхронизации почты

**Файл:** `server_snapshot/routers/email_parser_router.py:377`  
**Проблема:** `POST /email-parser/sync-all` не требует авторизации

```python
@cron_router.post("/sync-all")
def sync_all_tenants(db: Session = Depends(get_db)):  # ← НЕТ get_current_user
    # Синхронизирует почту ВСЕХ арендаторов, создаёт карточки
```

**Последствия:**
- Любой может запустить массовую синхронизацию всех почтовых ящиков
- Создание карточек от имени системы без аутентификации
- DoS через многократный вызов (импорт почты — медленная операция)

**Исправление:**
```python
@cron_router.post("/sync-all")
def sync_all_tenants(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("superadmin"))
):
```

---

### 2. IDOR — доступ к объектам других арендаторов

**Файлы:** `clients_router.py:42`, `suppliers_router.py`, `kanban_router.py`, `tags_router.py`, `custom_objects_router.py`, `webhooks_router.py`  
**Проблема:** Эндпоинты проверяют только существование объекта, не проверяют `tenant_id`

```python
# clients_router.py:42-55
@router.get("/clients/{client_id}")
def get_client(client_id: int, db: Session = Depends(get_db), 
               current_user: models.User = Depends(get_current_user)):
    client = query.first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client  # ← НЕТ ПРОВЕРКИ tenant_id
```

**Последствия:**
- Пользователь арендатора A может прочитать/изменить данные арендатора B, зная ID
- Утечка коммерческих данных (клиенты, суммы заказов, поставщики)

**Количество уязвимых эндпоинтов:** ~40 (GET/PATCH/DELETE для clients, suppliers, cards, tags, webhooks, workflows, custom_objects)

**Исправление:** добавить фильтр `tenant_id` после проверки существования:
```python
if current_user.role != "superadmin":
    if client.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=404, detail="Клиент не найден")
```

---

### 3. Секретный ключ JWT в репозитории

**Файлы:** `server_snapshot/.secret_key`, `server_snapshot/routers/.secret_key`  
**Проблема:** `.secret_key` закоммичен в git, хотя должен быть в `.gitignore`

**Последствия:**
- Компрометация ключа → возможность подделать JWT-токен любого пользователя
- Утёкший ключ нельзя "разкоммитить" — он остаётся в истории навсегда
- Расшифровка паролей от почтовых ящиков (используется тот же ключ через Fernet)

**Исправление:**
1. Немедленно сгенерировать новый секретный ключ на сервере
2. Удалить `.secret_key` из git, добавить в `.gitignore` (уже сделано)
3. Инвалидировать все активные JWT-токены (перелогин всех пользователей)

---

## ⚠️ ВАЖНО — требует исправления в следующем спринте

### 4. Загрузка файлов — проверка только расширения

**Файл:** `card_details_router.py:120-123`  
**Проблема:** Проверяется только расширение файла, не MIME-type

```python
ext = os.path.splitext(file.filename or '')[1].lower()
if ext not in ALLOWED_EXTS:
    raise HTTPException(status_code=400, detail=f"Тип файла не разрешён: {ext}")
```

**Последствия:**
- Можно загрузить исполняемый файл `.exe`, переименованный в `.jpg`
- PHP-shell с именем `backdoor.php.jpg` пройдёт проверку (если веб-сервер неправильно настроен)

**Исправление:** проверять `file.content_type` и первые байты файла (magic bytes)

---

### 5. GET /files/{filename} — IDOR на файлах

**Файл:** `card_details_router.py:279-287`  
**Проблема:** Любой авторизованный пользователь может скачать файл другого тенанта, зная имя

```python
@router.get("/files/{filename}")
def download_file(filename: str, current_user: models.User = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    file_path = os.path.realpath(os.path.join(UPLOAD_DIR, safe_name))
    # ← НЕТ ПРОВЕРКИ, что файл принадлежит tenant_id пользователя
    return FileResponse(file_path)
```

**Исправление:** проверять `checklist.card.tenant_id` перед отдачей файла (требует запрос в БД по имени файла)

---

### 6. Отсутствие пагинации

**Файлы:** `kanban_router.py:23`, `clients_router.py:23`, `payments_router.py:77`  
**Проблема:** Списочные эндпоинты возвращают все записи без ограничения

**Последствия:**
- DoS при большом объёме данных (>10 000 карточек → OOM или таймаут)
- Медленные ответы на фронтенде

**Исправление:** добавить параметры `?limit=100&offset=0`

---

### 7. Rate limiting только на /login

**Файл:** `limiter_config.py:4`, `auth_router.py:17-19`  
**Проблема:** Остальные эндпоинты не защищены от DoS

**Исправление:** добавить глобальный лимит (например, 1000 req/min на IP) и строгий лимит на мутирующие операции

---

## 📋 Косметика — не критично, но желательно

- **JWT без blacklist:** при компрометации токен валиден 24 часа, отозвать нельзя
- **Email импорт:** `owner_id = tenant_id` вместо `user.id` — нарушение FK
- **Нет валидации длины строк:** можно отправить гигабайтную строку в поле `name`
- **Webhook SSRF:** не проверяются редиректы (можно обойти блокировку внутренних IP)
- **Дефолтный пароль `change_me_now!`** при отсутствии `CRM_DEFAULT_PASSWORD`

---

## 🎯 Приоритет исправлений

1. **Сегодня (07.08):** Закрыть sync-all аутентификацией, сгенерировать новый `.secret_key`
2. **Эта неделя:** Исправить IDOR во всех роутерах (добавить проверку `tenant_id`)
3. **Следующий спринт:** Пагинация, rate limiting, проверка MIME-type файлов

---

## Как проверили

- **Статический анализ:** чтение исходного кода FastAPI (14 роутеров, 1014 строк)
- **Подтверждено:** `grep -n "def sync_all" routers/email_parser_router.py` — L377 без `get_current_user`
- **Подтверждено:** клиенты/карточки/поставщики не фильтруют по `tenant_id` после `query.first()`
- **Подтверждено:** `find server_snapshot -name '.secret_key'` → 2 файла в снимке

---

## Окружение

- **Сервер:** FastAPI + SQLAlchemy (SQLite для dev, вероятно PostgreSQL на проде)
- **Multi-tenant:** `models_tenant.py`, поле `tenant_id` в большинстве таблиц
- **Авторизация:** JWT (HS256), bcrypt для паролей
- **Загрузка файлов:** `uploads/` (25 МБ лимит), имена вида `chk{id}_{uuid}_{original}`
