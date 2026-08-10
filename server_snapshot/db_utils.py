"""
Единая логика выбора сессии БД для всех роутеров.

До этого модуля каждый роутер определял собственный хелпер (_db, _get_db,
get_scoped_session), и копии расходились: одни проверяли `is None`, другие
`not tenant_id`, третьи вовсе возвращали свежую SessionLocal() для superadmin.
Это приводило к хрупкой изоляции тенантов — ошибка в одной из 6 копий
нарушала бы изоляцию, а изменение контракта требовало правок везде.

resolve_tenant_db — единая точка: superadmin или пользователь без tenant_id
работают на инъектированной FastAPI-сессии (db), остальные — на tenant-сессии.

Семейство D (_get_db с прямым SessionLocal()) теперь также использует
get_tenant_db, что подключает deferred-close обёртку — это устраняет
потенциальный DetachedInstanceError, если хендлер когда-нибудь вернёт
ORM-объект с relationship вместо dict.
"""
from database import get_tenant_db


def resolve_tenant_db(current_user, db):
    """
    Выбирает сессию БД в зависимости от tenant_id пользователя.

    Возвращает инъектированную `db` для superadmin или пользователя без
    tenant_id (основная БД), либо tenant-сессию для остальных.

    tenant_id = 0 невозможен (FK → tenants.id, NOT NULL), поэтому `is None`
    и `not tenant_id` практически эквивалентны — используем `is None` как
    более точную проверку.
    """
    if current_user.tenant_id is None:
        return db
    return get_tenant_db(current_user.tenant_id)


def resolve_tenant_db_standalone(current_user):
    """
    Для хендлеров БЕЗ Depends(get_db) (семейство D: webhooks/custom_objects/workflows).

    get_tenant_db(None) возвращает SessionLocal() с deferred-close обёрткой,
    что безопаснее прямого SessionLocal() — если кто-то добавит Depends(get_db)
    в хендлер, deferred-close заработает автоматически.
    """
    tid = getattr(current_user, 'tenant_id', None)
    return get_tenant_db(tid)
