# CRM «Снабжение и Продажи» — Project Report

## 1. Общее описание
Full-stack веб-приложение для управления закупками, продажами, клиентами, поставщиками, документами и списаниями.  
Деплой на production через SSH + rsync. Бэкенд управляется PM2.

## 2. Технологический стек

### 2.1 Frontend
| Технология | Использование |
|------------|---------------|
| HTML5 | 5 страниц: `index.html`, `admin.html`, `settings.html`, `workflows.html`, `custom_objects.html` |
| CSS3 | Единый `style.css` (~7500 строк), CSS Variables, Flexbox, Grid, `@media` queries |
| Vanilla JS (ES6+) | 22 модуля в `site/js/` — нет React/Vue/Angular |

### 2.2 Backend
| Технология | Версия | Назначение |
|------------|--------|------------|
| Python | 3.x | Основной язык |
| FastAPI | 0.115.0 | Web-фреймворк, REST API |
| Uvicorn | 0.30.0 | ASGI-сервер |
| SQLAlchemy | 2.0.31 | ORM, работа с БД |
| PyJWT | 2.9.0 | JWT-аутентификация |
| Passlib + bcrypt | 1.7.4 | Хеширование паролей |
| SlowAPI | 0.1.9 | Rate limiting |
| Pydantic | 2.8.0 | Валидация данных |
| Alembic | 1.13.0 | Миграции базы данных |

### 2.3 Инфраструктура
| Компонент | Настройка |
|-----------|-----------|
| **PM2** | Process manager для Python-приложения (`ecosystem.config.js`) |
| **NVM** | Используется PM2 (интерпретатор через `.venv/bin/python`) |
| **rsync + SSH** | Деплой фронтенда и бэкенда |
| **SQLite** | База данных `crm_app.db` (~1.7 ГБ) |

## 3. Структура репозитория

```
crm-svetvdome/
├── site/                          # Фронтенд (статический)
│   ├── css/
│   │   └── style.css              # Единый stylesheet (~7500 строк)
│   ├── js/
│   │   ├── api.js                 # HTTP-клиент
│   │   ├── auth.js                # Аутентификация
│   │   ├── kanban.js              # Канбан-доска
│   │   ├── payments.js            # Реестр оплат
│   │   ├── writeoffs.js           # Списания
│   │   ├── ui-polish.js           # A11y, анимации, MutationObserver
│   │   └── ... (22 файла)
│   ├── index.html                 # Главная страница + inline critical CSS
│   ├── admin.html
│   ├── settings.html
│   ├── workflows.html
│   ├── custom_objects.html
│   └── manifest.json
├── server_snapshot/               # Бэкенд (Python/FastAPI)
│   ├── server.py                  # Точка входа
│   ├── database.py                # Подключение к БД
│   ├── models.py                  # SQLAlchemy модели
│   ├── schemas.py                 # Pydantic схемы
│   ├── auth.py                    # JWT-аутентификация
│   ├── requirements.txt           # Зависимости Python
│   ├── ecosystem.config.js        # PM2 конфиг
│   ├── routers/                   # 14 роутеров FastAPI
│   │   ├── kanban_router.py
│   │   ├── payments_router.py
│   │   ├── writeoffs_router.py
│   │   └── ...
│   └── scripts/
│       ├── deploy.sh              # Деплой скрипт
│       ├── watchdog_crm.sh        # Мониторинг
│       └── backup.sh              # Бэкапы
├── tools/
│   ├── deploy.sh                  # Основной деплой-скрипт (front/back/restart)
│   ├── stamp_assets.py            # Cache-busting версии для статики
│   ├── check_js.sh                # Проверка JS синтаксиса
│   └── css_cascade.py             # Анализ CSS каскада
└── README.md
```

## 4. Деплой-процесс

### 4.1 Frontend (статический)
```bash
tools/deploy.sh front
```
- Rsync `site/` → `/var/www/h212005/data/www/cmr-svetvdome.online/`
- Проверка JS синтаксиса и версий ассетов
- Перезапуск PM2 **не требуется** (только статика)

### 4.2 Backend (Python)
```bash
tools/deploy.sh back <файлы>
```
- Rsync указанных файлов из `server_snapshot/` → remote
- Автоматический `pm2 restart crm --update-env`

### 4.3 Управление
```bash
tools/deploy.sh status    # PM2 статус
tools/deploy.sh logs 40   # Логи ошибок
tools/deploy.sh restart   # Перезапуск приложения
```

## 5. Данные для SSH-подключения

| Параметр | Значение |
|----------|----------|
| **Host** | `87.232.64.12` (домен `87-232-64-12.nip.io`) |
| **User** | `h212005` |
| **SSH Key** | `~/.ssh/crm_svetvdome_deploy` |
| **Port** | `22` (стандартный) |
| **Remote Dir** | `/var/www/h212005/data/www/cmr-svetvdome.online` |
| **PM2 App** | `crm` |
| **App Port** | `20008` |

### Пример SSH-подключения
```bash
ssh -i ~/.ssh/crm_svetvdome_deploy h212005@87.232.64.12
```

### Пример rsync (фронтенд)
```bash
rsync -avz -e "ssh -i ~/.ssh/crm_svetvdome_deploy -o IdentitiesOnly=yes" \
  --exclude '*.db' --exclude 'uploads/' \
  ./site/ h212005@87.232.64.12:/var/www/h212005/data/www/cmr-svetvdome.online/
```

## 6. База данных
- **Тип:** SQLite
- **Файл:** `crm_app.db` (~1.7 ГБ)
- **ORM:** SQLAlchemy 2.0.31
- **Миграции:** Alembic 1.13.0
- **Аутентификация:** JWT (PyJWT 2.9.0)

## 7. Производительность и стабильность
- **PM2:** autorestart, max_memory_restart: 512MB, max_restarts: 10
- **Rate limiting:** SlowAPI 0.1.9
- **Статика:** cache-busting через `?v=` хеши (`stamp_assets.py`)
- **Валидация:** JS-синтакс-чек перед деплоем (`check_js.sh`)

## 8. Заметки для Docker-анализа
1. Приложение **не использует Docker** в текущем виде — деплой через rsync + PM2.
2. Для контейнеризации потребуется:
   - Multi-stage Dockerfile (Python backend + Nginx для статики)
   - Volume для SQLite БД (`crm_app.db`)
   - Expose порта `20008`
   - PM2 не нужен внутри контейнера (Uvicorn сам по себе)
3. Frontend можно собирать отдельно или просто копировать `site/` в образ.
4. Переменные окружения: `PORT`, `PYTHONUNBUFFERED`.

---
*Отчет сгенерирован: 2026-08-08*
