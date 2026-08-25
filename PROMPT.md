# CRM "Снабжение и Продажи" — Контекст проекта

## Архитектура
Backend: FastAPI + SQLAlchemy + SQLite (WAL-mode) + JWT auth (HS256 + bcrypt)
Frontend: Vanilla JS SPA (без сборщика, без фреймворков)
Мультитенантность: tenant_id в каждой таблице, раздельные БД для данных

## Стек
Python 3.12, FastAPI 0.115, SQLAlchemy 2.0, PyJWT, passlib[bcrypt], slowapi, pydantic 2.8

## Структура файлов
main.py — точка входа, подключение роутеров, static files
database.py — SQLAlchemy engine + session (sqlite:///./crm_app.db)
models.py — ORM модели: User, Client, Supplier, Card, Tag, Transaction, ActivityLog
schemas.py — Pydantic схемы
auth.py — JWT токены, require_role(), require_admin(), require_superadmin()
models_tenant.py — модель Tenant для мультитенантности
version.py — __version__ = "2.0.0"
routers/ — auth, kanban, card_details, payments, writeoffs, clients, suppliers, tags, activity, email_parser
js/ — api, auth, kanban, card_modal, payments, writeoffs, documents, clients, suppliers, dashboard, features, realtime, email_parser
css/style.css — стили с CSS-переменными

## Паттерны
- Все роутеры используют Depends(get_current_user) для авторизации
- Фильтрация по tenant_id для изоляции данных тенантов
- Frontend: отдельные .js файлы для каждой страницы, подключаются через <script defer>
- CRUD: GET/POST/PATCH/DELETE, response models через Pydantic
- SQLite WAL-mode + busy_timeout=5000 для concurrent access

## API эндпоинты
POST /auth/login — JWT токен
GET/POST/PATCH/DELETE /auth/users — управление пользователями
POST /auth/create-tenant — создание нового тенанта (superadmin only)
GET /kanban/cards, POST, PATCH /status, DELETE
GET /payments/transactions, /documents
GET /clients, POST, PATCH, DELETE
GET /tags, POST, DELETE
POST /email-parser/sync — импорт писем из почты

## Роли
superadmin — полный доступ, создаёт админов
admin — управляет своими пользователями, пустая CRM
manager — канбан, оплаты, карточки
warehouse — списание, склад
documents — только страница "Документы"

## Деплой
Сервер: 87.232.65.217, nginx:80 → uvicorn:8000
SSH: root@87.232.65.217
Scripts: scripts/deploy.sh, rollback.sh, backup.sh

## Как писать новый функциона
1. Backend: создать/изменить роутер в routers/, подключить в main.py
2. Frontend: создать .js файл в js/, подключить в index.html
3. Модели: добавить в models.py, создать миграцию через alembic
4. Деплой: scripts/deploy.sh

## Ограничения
- Не использовать React/Vue/Angular
- Не менять структуру БД без миграции
- Все БД тенантов в папке tenants/
- JWT содержит sub (username) + tenant_id
