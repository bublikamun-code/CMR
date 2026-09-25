"""Единая tenant-policy для объектных маршрутов CRM.

Legacy-конфигурация была однокомпаной, поэтому часть исторических записей
имела ``tenant_id = NULL``. Такие значения нельзя размазывать по нескольким
компаниям. Нормализация выполняется отдельной миграцией ``0018`` только после
проверки, что все уже заданные tenant_id и ссылки на родительские объекты
однозначно указывают на один tenant.

``CRM_LEGACY_TENANT_ID`` — явное переопределение для диагностической/тестовой
схемы. Постоянного production-id здесь нет. Если у аутентифицированного
пользователя tenant отсутствует, запись tenant-owned данных запрещается, пока
миграция не назначит однозначный tenant.

Политика чтения:

* ``superadmin`` сохраняет явную глобальную видимость (это существующий
  административный контракт);
* все остальные роли видят только строки с ``tenant_id == current_user.tenant_id``;
* объект чужого tenant намеренно не различается с отсутствующим и даёт 404,
  чтобы не подтверждать существование чужого ID;
* 403 остаётся для известного объекта, к которому у пользователя нет права
  внутри его tenant (например, чужой комментарий).
"""
import os

from fastapi import HTTPException, status
from sqlalchemy import true
from sqlalchemy.orm import Session

# Намеренно None: id текущей боевой базы не должен быть зашит в приложение.
DEFAULT_LEGACY_TENANT_ID = None
LEGACY_TENANT_ENV = "CRM_LEGACY_TENANT_ID"


class TenantPolicyError(RuntimeError):
    """Конфигурация tenant неоднозначна или неполна."""


def configured_legacy_tenant_id() -> int | None:
    raw = os.environ.get(LEGACY_TENANT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_LEGACY_TENANT_ID
    try:
        tenant_id = int(raw)
    except ValueError as exc:
        raise TenantPolicyError(
            f"{LEGACY_TENANT_ENV} должен быть положительным целым числом"
        ) from exc
    if tenant_id <= 0:
        raise TenantPolicyError(
            f"{LEGACY_TENANT_ENV} должен быть положительным целым числом"
        )
    return tenant_id


def writable_tenant_id(current_user) -> int:
    """Вернуть tenant для новой tenant-owned записи.

    Пустой tenant пользователя после миграции означает повреждённое/не
    подготовленное состояние. Молча подставлять configured id в новый API нельзя:
    это скрыло бы неполную миграцию и могло бы записать данные не в тот tenant.
    """
    tenant_id = current_user.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant пользователя не настроен",
        )
    return int(tenant_id)


def has_global_scope(current_user) -> bool:
    """Явная глобальная роль; tenant-scoped роли сюда не попадают."""
    return current_user.role == "superadmin"


def tenant_predicate(model, current_user):
    """SQL-предикат для любой ORM-модели с ``tenant_id``."""
    if has_global_scope(current_user):
        return true()
    if not hasattr(model, "tenant_id"):
        raise TenantPolicyError(f"У модели {model.__name__} нет tenant_id")
    if current_user.tenant_id is None:
        # Не смешиваем NULL-строки с NULL tenant пользователя.
        return false_predicate()
    return model.tenant_id == int(current_user.tenant_id)


def false_predicate():
    from sqlalchemy import false
    return false()


def tenant_query(db: Session, model, current_user):
    return db.query(model).filter(tenant_predicate(model, current_user))


def get_tenant_object(
    db: Session,
    model,
    object_id: int,
    current_user,
    *,
    detail: str = "Объект не найден",
    extra_filters=None,
):
    """Единый object-by-ID lookup с tenant-инвариантом.

    ``extra_filters`` добавляются в тот же SELECT и потому не могут обойти
    tenant-фильтр. Отсутствующий и чужой объект возвращают одинаковый 404.
    """
    query = tenant_query(db, model, current_user).filter(model.id == object_id)
    for condition in extra_filters or ():
        query = query.filter(condition)
    obj = query.first()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj


def get_tenant_child(
    db: Session,
    model,
    object_id: int,
    parent_model,
    parent_column: str,
    current_user,
    *,
    detail: str = "Объект не найден",
    parent_detail: str = "Родительский объект не найден",
):
    """Найти child по ID, сначала tenant-scoping его родителя.

    Используется для строк без собственного tenant_id (attachments,
    checklists, custom field values и т.п.). Чужой child никогда не
    загружается в ORM, даже если его родитель имеет глобальный ID.
    """
    parent_id = (
        db.query(getattr(model, parent_column))
        .filter(model.id == object_id)
        .scalar()
    )
    if parent_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    get_tenant_object(
        db, parent_model, parent_id, current_user, detail=parent_detail
    )
    child = db.query(model).filter(model.id == object_id).first()
    if child is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return child


def assign_tenant(instance, current_user) -> object:
    """Проставить tenant на tenant-owned ORM-объект перед insert."""
    if hasattr(instance, "tenant_id"):
        instance.tenant_id = writable_tenant_id(current_user)
    return instance


def require_tenant_reference(current_user, tenant_id: int) -> int:
    if int(tenant_id) != writable_tenant_id(current_user):
        raise HTTPException(status_code=404, detail="Объект не найден")
    return int(tenant_id)
