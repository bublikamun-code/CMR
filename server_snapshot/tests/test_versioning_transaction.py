"""Граница транзакции save_version (аудит 2026-09-26).

Раньше save_version сам делал db.commit() и db.rollback(), то есть фиксировал
или откатывал транзакцию ВЫЗЫВАЮЩЕЙ стороны: соседняя операция получала
pending-rollback или её изменения уезжали в базу вместе с версией. Теперь
вставка версии идёт в SAVEPOINT, а commit стал явным аргументом.

Проверяем здесь именно границу:
  * save_version(commit=False) не фиксирует незавершённые изменения вызывающего;
  * ошибка внутри save_version (гонка за ux_record_versions_t_r_v) не откатывает
    изменения вызывающей стороны и не оставляет его сессию непригодной;
  * в обоих режимах версия не теряется: она уезжает с коммитом вызывающего либо
    фиксируется сразу (режим по умолчанию, на который пока опираются роутеры).

Проверка «не закоммичено» идёт через ОТДЕЛЬНОЕ соединение с БД: та же сессия
в своей транзакции видит и незакоммиченные строки, поэтому такой запрос ничего
не доказывал бы.
"""
import pytest
from sqlalchemy import event, text

import database
import models
from conftest import DEFAULT_TEST_TENANT_ID
from versioning import get_versions, save_version

pytestmark = pytest.mark.integrity

CLIENT_NAME = "Клиент незавершённой транзакции"


def _committed(sql, **params):
    """Строки, видимые другому соединению: только закоммиченные."""
    with database.engine.connect() as conn:
        return conn.execute(text(sql), params).fetchall()


def _count_committed(table, column, value):
    return len(_committed(
        f"SELECT 1 FROM {table} WHERE {column} = :v", v=value  # noqa: S608 — таблица/колонка заданы константами теста
    ))


def test_save_version_without_commit_leaves_caller_changes_uncommitted(db):
    """Режим без коммита: версия и правка вызывающего ждут его решения."""
    db.add(models.Client(name=CLIENT_NAME))
    # Изменение вызывающей стороны в БД уже есть (autoflush), но не закоммичено.
    db.flush()

    assert save_version(db, "clients", 4242, {"name": CLIENT_NAME},
                        commit=False) == 1

    # Ни правка вызывающего, ни версия из другого соединения не видны.
    assert _count_committed("clients", "name", CLIENT_NAME) == 0
    assert _count_committed("record_versions", "record_id", 4242) == 0

    db.commit()

    assert _count_committed("clients", "name", CLIENT_NAME) == 1
    assert _count_committed("record_versions", "record_id", 4242) == 1
    history = get_versions(db, "clients", 4242)
    assert [entry["version"] for entry in history] == [1]


def test_save_version_failure_keeps_caller_changes_and_session_usable(db):
    """Ошибка вставки не откатывает чужую транзакцию (регресс на rollback)."""
    db.add(models.Client(name=CLIENT_NAME))
    db.flush()

    def _inject_conflict(mapper, connection, target):
        # Имитация гонки двух писателей: конкурент занимает тот же
        # (table_name, record_id, version) прямо в момент нашей вставки, и
        # уникальный индекс ux_record_versions_t_r_v роняет наш flush.
        connection.execute(models.RecordVersion.__table__.insert().values(
            table_name=target.table_name,
            record_id=target.record_id,
            version=target.version,
            data_snapshot="{}",
            change_type="create",
            tenant_id=DEFAULT_TEST_TENANT_ID,
        ))

    event.listen(models.RecordVersion, "before_insert", _inject_conflict)
    try:
        assert save_version(db, "clients", 777, {"name": CLIENT_NAME},
                            commit=False) is None
    finally:
        event.remove(models.RecordVersion, "before_insert", _inject_conflict)

    # Правка вызывающего уцелела и всё ещё не закоммичена...
    assert _count_committed("clients", "name", CLIENT_NAME) == 0
    # ...а его сессия пригодна для дальнейшей работы (не pending-rollback).
    assert db.query(models.Client).filter(
        models.Client.name == CLIENT_NAME).count() == 1
    db.commit()
    assert _count_committed("clients", "name", CLIENT_NAME) == 1
    # Подсунутая конкурентом версия откатилась вместе с нашей вставкой.
    assert _count_committed("record_versions", "record_id", 777) == 0


def test_default_mode_commits_and_keeps_working_for_current_callers(db):
    """Совместимость: дефолт commit=True фиксирует версию сразу.

    На этом поведении пока держатся пять вызовов в роутерах — перевод их на
    commit=False с собственным db.commit() вынесен отдельным пакетом.
    """
    db.add(models.Client(name=CLIENT_NAME))
    db.commit()

    assert save_version(db, "clients", 900, {"name": CLIENT_NAME}) == 1

    assert _count_committed("record_versions", "record_id", 900) == 1
    assert get_versions(db, "clients", 900)[0]["data"] == {"name": CLIENT_NAME}
