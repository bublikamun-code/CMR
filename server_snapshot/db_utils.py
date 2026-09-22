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
from typing import NamedTuple, Optional

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


# ---------------------------------------------------------------------------
# Пункт 16 плана V2-WORKPLAN-2026-09-22 (дефект B11): настоящие серверные
# страницы для тяжёлых списков. cap_list выше — предохранитель, он отдаёт ВСЁ
# и только обрезает аномальный рост; здесь — отдаём ровно запрошенный кусок.
#
# Контракт намеренно опциональный: без limit/offset роут ведёт себя как раньше
# (возвращает массив), поэтому клиент переводится на страницы отдельным шагом
# и ничего не ломается в день деплоя сервера.
#
# Пороги повторяют уже принятые в репо, а не изобретены заново:
#   PAGE_DEFAULT_SIZE = 50  — дефолт GET /activity (activity_router.Query(50, le=200));
#   PAGE_MAX_SIZE     = 200 — его же потолок le=200.
# Ограничение объявляется через Query(ge=/le=), поэтому невалидные значения
# (0, отрицательные, мусор) FastAPI сам отвергает кодом 422 — второй стиль
# обработки ошибок в репо не используется.
# ---------------------------------------------------------------------------
PAGE_DEFAULT_SIZE = 50
PAGE_MAX_SIZE = 200


class PageRequest(NamedTuple):
    """Разрешённые параметры страницы (после применения дефолтов)."""
    limit: int
    offset: int


def resolve_page(limit: Optional[int], offset: Optional[int]) -> Optional[PageRequest]:
    """Включён ли режим страницы, и если да — какими параметрами.

    None — пагинация ВЫКЛЮЧЕНА (ни один параметр не передан): вызывающий роут
    обязан вернуть прежнюю форму ответа. Переключатель — сам факт передачи
    limit ИЛИ offset: offset без limit не должен молча игнорироваться, иначе
    клиент получил бы весь список, полагая, что читает страницу.
    """
    if limit is None and offset is None:
        return None
    return PageRequest(
        limit=limit if limit is not None else PAGE_DEFAULT_SIZE,
        offset=offset if offset is not None else 0,
    )


def set_page_headers(response, total: int) -> None:
    """Заголовок общего числа в режиме страницы.

    X-Total-Count — уже существующий контракт реестра оплат и cap_list, так
    что клиент читает его тем же кодом. X-Truncated здесь не ставится намеренно:
    в режиме страницы усечения нет — есть ровно тот кусок, который запросили.
    """
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
