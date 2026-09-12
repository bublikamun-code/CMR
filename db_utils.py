"""
Единая логика выбора сессии БД для всех роутеров.

Тенант-маршрутизация ОТКЛЮЧЕНА решением владельца 2026-09-03/2026-09-11
(баг 17 плана): таблица tenants пуста, tenant_id всех пользователей NULL,
/auth/create-tenant недоступен. Обе resolve-функции возвращают ОСНОВНУЮ
сессию всем пользователям; не-NULL tenant_id (нештатная ситуация) логируется
warning — это рабочий сигнал, что кто-то пролез мимо отключения.

resolve_tenant_db — точка для хендлеров с Depends(get_db);
resolve_tenant_db_standalone — для хендлеров без Depends(get_db)
(семейство D: webhooks/custom_objects/workflows), возвращает свежую
SessionLocal() с deferred-close обёрткой (см. database.get_tenant_db).
"""
import logging

from database import get_tenant_db

logger = logging.getLogger(__name__)


def resolve_tenant_db(current_user, db):
    """
    Возвращает инъектированную основную сессию `db` для всех пользователей.

    tenant-маршрутизация отключена — tenant_id, если он вдруг появится,
    только логируется (warning), сессия всё равно основная.
    """
    if getattr(current_user, "tenant_id", None) is not None:
        logger.warning(
            "resolve_tenant_db: пользователь %s имеет tenant_id=%s, "
            "но tenant-маршрутизация отключена — используется основная БД",
            getattr(current_user, "id", "?"), current_user.tenant_id,
        )
    return db


def resolve_tenant_db_standalone(current_user):
    """
    Для хендлеров БЕЗ Depends(get_db) (семейство D: webhooks/custom_objects/workflows).

    Возвращает свежую основную сессию с deferred-close обёрткой — если кто-то
    добавит Depends(get_db) в хендлер, deferred-close сработает автоматически.
    """
    tid = getattr(current_user, 'tenant_id', None)
    if tid is not None:
        logger.warning(
            "resolve_tenant_db_standalone: пользователь %s имеет tenant_id=%s, "
            "но tenant-маршрутизация отключена — используется основная БД",
            getattr(current_user, "id", "?"), tid,
        )
    return get_tenant_db(None)


# Н11 (аудит 06.09): предохранитель для списочных эндпоинтов без пагинации.
# Это не замена пагинации (канбану нужны все карточки сразу), а защита от
# аномального роста таблицы. При срабатывании ответ помечается заголовками
# X-Total-Count / X-Truncated — тот же контракт, что у реестра оплат
# (payments_router.REGISTRY_HARD_LIMIT).
LIST_HARD_LIMIT = 5000


def cap_list(items, response=None, limit: int = LIST_HARD_LIMIT):
    """Обрезать список до `limit`; при усечении пометить ответ заголовками."""
    total = len(items)
    if total <= limit:
        return items
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Truncated"] = "true"
    return items[:limit]
