"""
Единая логика выбора сессии БД для всех роутеров.

До этого модуля каждый роутер определял собственный хелпер (_db, _get_db,
get_scoped_session), и копии расходились: одни проверяли `is None`, другие
`not tenant_id`, третьи вовсе возвращали свежую SessionLocal() для superadmin.

FIX 2026-09-03: tenant-маршрутизация отключена. Все данные (карточки, клиенты,
оплаты, история) живут в ОСНОВНОЙ БД; tenant-БД (tenants/crm_*.db) пустые.
Прежнее поведение отправляло пользователя с tenant_id в пустую tenant-БД:
правки карточек давали 404, история была пустой, уведомления и импорт почты
уходили в никуда. Теперь все работают на основной БД — ровно как работали
все пользователи до этого (ни у кого tenant_id не задан).

Когда потребуется настоящая мультитенантность — это отдельная миграция
данных в tenant-БД + изоляция на уровне tenant_id, а не возврат этой
маршрутизации. Появление пользователя с tenant_id логируется warning'ом,
чтобы назначение тенанта не было молчаливым.
"""
import logging

from database import get_tenant_db

logger = logging.getLogger(__name__)


def resolve_tenant_db(current_user, db):
    """
    Возвращает сессию основной БД для всех пользователей.

    tenant_id = 0 невозможен (FK → tenants.id, NOT NULL), поэтому `is None`
    и `not tenant_id` практически эквивалентны — используем `is None` как
    более точную проверку.
    """
    tid = current_user.tenant_id
    if tid is not None:
        logger.warning(
            "resolve_tenant_db: пользователь %s (id=%s) имеет tenant_id=%s, "
            "но tenant-БД отключены — работаем на основной БД",
            getattr(current_user, "username", "?"), current_user.id, tid)
    return db


def resolve_tenant_db_standalone(current_user):
    """
    Для хендлеров БЕЗ Depends(get_db) (семейство D: webhooks/custom_objects/workflows).

    get_tenant_db(None) возвращает SessionLocal() с deferred-close обёрткой,
    что безопаснее прямого SessionLocal() — если кто-то добавит Depends(get_db)
    в хендлер, deferred-close заработает автоматически.
    """
    tid = getattr(current_user, 'tenant_id', None)
    if tid is not None:
        logger.warning(
            "resolve_tenant_db_standalone: пользователь %s (id=%s) имеет "
            "tenant_id=%s, но tenant-БД отключены — работаем на основной БД",
            getattr(current_user, "username", "?"),
            getattr(current_user, "id", "?"), tid)
    return get_tenant_db(None)
