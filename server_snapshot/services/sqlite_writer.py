"""Резервирование единственного писательского слота SQLite.

SQLite сериализует писателей на уровне всей БД, а не отдельных строк.
Короткий no-op UPDATE существующей строки переводит сессию в write-транзакцию
и удерживает writer slot до commit/rollback. Поэтому после резервирования все
последующие SELECT видят уже устоявшееся состояние, а конкурентный писатель
либо ждёт освобождения слота, либо получает управляемый 503.
"""

from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError


_BUSY_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)


def _is_contention(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _BUSY_MESSAGES)


def _contention_response(exc: OperationalError, detail: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=detail,
        headers={"Retry-After": "1"},
    )


@contextmanager
def sqlite_write_transaction(session):
    """Откатывает незавершённую операцию при любой ошибке.

    Ожидаемая конкуренция SQLite превращается только в 503 с Retry-After;
    остальные исключения сохраняют прежнее поведение FastAPI.
    """
    try:
        yield
    except OperationalError as exc:
        session.rollback()
        if _is_contention(exc):
            raise _contention_response(
                exc, "База занята другой операцией. Повторите попытку."
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise


def reserve_model_rows(
    session,
    model,
    row_ids,
    *,
    not_found_detail,
    busy_detail="База занята другой операцией. Повторите попытку.",
    reset_session=True,
):
    """Резервирует writer slot и проверяет существование строк.

    Строки одного типа резервируются по возрастанию id. SQLite даёт глобальный
    writer slot уже на первом UPDATE, но единый порядок нужен и для явно
    читаемого протокола при расширении на несколько карточек. Второй вызов
    в рамках одной операции передаёт ``reset_session=False``, чтобы не отпустить
    уже захваченный writer slot.
    """
    if reset_session:
        session.rollback()
    ordered_ids = sorted(set(row_ids))
    for row_id in ordered_ids:
        try:
            updated = session.query(model).filter(model.id == row_id).update(
                {model.id: model.id},
                synchronize_session=False,
            )
        except OperationalError as exc:
            session.rollback()
            if _is_contention(exc):
                raise _contention_response(exc, busy_detail) from exc
            raise
        if updated != 1:
            session.rollback()
            raise HTTPException(status_code=404, detail=not_found_detail)
    return tuple(ordered_ids)
