"""Retention: постраничная чистка версий вместо загрузки всех в память (С-10).

Раньше sync_overdue тянул в Python ВСЕ записи старше отсечки одним .all() и
складывал их в список per_record. Объём памяти рос вместе с историей изменений,
а задание выполняется по расписанию на боевой базе — то есть чистка переставала
работать именно тогда, когда становилась нужна.

Теперь выборка идёт порциями по (table_name, record_id, version) и тяжёлые
поля записи не читаются вовсе: нужно лишь решить, какие id лишние.
"""

import datetime as dt

import models
from routers.notifications_router import sync_overdue


def _tenant_id(db):
    return db.query(models.User).filter(models.User.role == "admin").first().tenant_id


def _seed_versions(db, tenant_id, record_id, count, table="cards"):
    """Положить count версий одной записи с разным временем."""
    base = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(count):
        db.add(models.RecordVersion(
            table_name=table, record_id=record_id, version=i + 1,
            changed_at=base + dt.timedelta(minutes=i),
            tenant_id=tenant_id,
            # data_snapshot и changed_by в схеме NOT NULL. Без них запись
            # не создаётся, и падение IntegrityError маскировало бы смысл
            # теста, который вообще-то про чистку версий.
            data_snapshot='{"title": "тест"}', changed_by=None,
            # change_type тоже NOT NULL.
            change_type="update",
        ))
    db.commit()


def test_keeps_exactly_twenty_latest_per_record(db, admin):
    """После чистки у записи остаются ровно 20 самых свежих версий."""
    _headers = admin
    tid = _tenant_id(db)
    _seed_versions(db, tid, record_id=4242, count=35)

    sync_overdue(db=db)
    db.expire_all()

    left = (db.query(models.RecordVersion)
            .filter(models.RecordVersion.record_id == 4242)
            .order_by(models.RecordVersion.version.desc()).all())
    assert len(left) == 20, f"осталось {len(left)} версий вместо 20"
    # Правило «последние 20»: самые свежие выживают, самые старые удалены.
    assert {v.version for v in left} == set(range(16, 36)), \
        sorted(v.version for v in left)


def test_paging_covers_more_than_one_page(db, admin):
    """Записи больше одной порции обрабатываются целиком.

    Ключевой признак именно постраничной разборки: если бы цикл читал только
    первую страницу, старые версии вторых и последующих страниц остались бы
    жить, и счётчик purged показывал бы правдоподобное, но неполное число.
    """
    tid = _tenant_id(db)
    # 12 записей по 25 версий — заметно больше порции в 500 строк.
    for rec in range(100, 112):
        _seed_versions(db, tid, record_id=rec, count=25, table="paged")

    sync_overdue(db=db)
    db.expire_all()

    for rec in range(100, 112):
        left = (db.query(models.RecordVersion)
                .filter(models.RecordVersion.record_id == rec,
                        models.RecordVersion.table_name == "paged").count())
        assert left == 20, f"запись {rec}: осталось {left}, ожидалось 20"


def test_purged_counter_is_honest(db, admin):
    """Счётчик purged совпадает с тем, что реально удалено."""
    tid = _tenant_id(db)
    _seed_versions(db, tid, record_id=7777, count=30, table="counted")
    before = (db.query(models.RecordVersion)
              .filter(models.RecordVersion.record_id == 7777).count())

    result = sync_overdue(db=db)
    db.expire_all()

    after = (db.query(models.RecordVersion)
             .filter(models.RecordVersion.record_id == 7777).count())
    assert result["versions_purged"] == before - after, (
        f"отчитано {result['versions_purged']}, а удалено {before - after}")