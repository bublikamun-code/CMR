"""
Утилита версионирования записей.
Сохраняет историю изменений для карточек, клиентов и других объектов.

ГРАНИЦА ТРАНЗАКЦИИ: save_version не решает за вызывающего, когда завершать его
транзакцию. Вставка версии идёт в SAVEPOINT, поэтому ошибка (гонка за
уникальным индексом ux_record_versions_t_r_v, битый JSON-снимок и т. п.) не
откатывает чужую работу и не переводит сессию вызывающего в pending-rollback.
Фиксацию выполняет владелец сессии: либо сам (commit=False), либо явным
аргументом commit=True (см. описание ниже).
"""
import json
import logging

from models import RecordVersion

logger = logging.getLogger(__name__)


def save_version(
    db,
    table_name,
    record_id,
    data,
    user_id=None,
    change_type="update",
    tenant_id=None,
    *,
    commit=True,
):
    """Сохранить версию записи.

    Параметры те же, что и раньше. ``commit`` — keyword-only; по умолчанию он
    равен True, и это осознанная совместимость с текущими вызовами, которые
    коммитят сразу после сохранения версии.

    Что изменилось (аудит 2026-09-26):

    * Коммит больше не спрятан в теле функции: вызов из середины незавершённой
      транзакции обязан передать ``commit=False`` и зафиксировать (или
      откатить) её сам — иначе версия уедет вместе с чужой транзакцией.
    * ``db.rollback()`` в ветке ошибки больше не откатывает транзакцию
      вызывающего. Вставка обёрнута в SAVEPOINT, поэтому откат затрагивает
      только нашу строку; незавершённые изменения вызывающей стороны остаются
      в сессии и доступны ей после возврата.

    Возвращает номер сохранённой версии либо None, если версия не записана
    (ошибка всегда попадает в лог — молчаливая потеря истории была дефектом
    аудита 2026-09-03).
    """
    try:
        # SAVEPOINT: откат ниже затрагивает только вставку версии. Порядок
        # событий в SQLAlchemy важен — перед SAVEPOINT выполняется autoflush,
        # то есть незавершённые изменения вызывающей стороны уходят в БД, но
        # ещё не закоммичены, поэтому ROLLBACK TO их не отменяет.
        with db.begin_nested():
            # Получаем текущую версию.
            # FIX 2026-09-03: фильтр по tenant_id убран — номер версии теперь
            # глобален на (table_name, record_id), иначе у одной записи появлялось
            # по «версии 1» на каждый тенант (дубли в истории карточек 277, 339...).
            # За уникальность отвечает индекс ux_record_versions_t_r_v; при гонке
            # двух писателей вставка второго упадёт в IntegrityError — поймаем ниже.
            last = db.query(RecordVersion).filter(
                RecordVersion.table_name == table_name,
                RecordVersion.record_id == record_id,
            ).order_by(RecordVersion.version.desc()).first()

            version = (last.version + 1) if last else 1

            new_version = RecordVersion(
                table_name=table_name,
                record_id=record_id,
                version=version,
                data_snapshot=json.dumps(data, default=str, ensure_ascii=False),
                changed_by=user_id,
                change_type=change_type,
                tenant_id=tenant_id
            )
            db.add(new_version)
            # flush, а не commit: номер версии и ошибки (уникальный индекс)
            # нужны сейчас, а завершение транзакции — дело вызывающего.
            db.flush()
    except Exception as e:
        # FIX 2026-09-03 (аудит): ошибка раньше глоталась наглухо —
        # история правок терялась без следа. Теперь причина в логе.
        logger.warning("save_version failed (%s/%s, tenant=%s): %s",
                       table_name, record_id, tenant_id, e)
        return None

    if not commit:
        return version
    try:
        db.commit()
    except Exception as e:
        logger.warning("save_version commit failed (%s/%s, tenant=%s): %s",
                       table_name, record_id, tenant_id, e)
        # Коммит — единственное место, где откат вызывающей стороны
        # обязателен: сессия после ошибки коммита непригодна.
        db.rollback()
        return None
    return version


def get_versions(db, table_name, record_id, tenant_id=None, limit=50):
    """Получить историю изменений записи."""
    query = db.query(RecordVersion).filter(
        RecordVersion.table_name == table_name,
        RecordVersion.record_id == record_id
    )
    if tenant_id:
        query = query.filter(RecordVersion.tenant_id == tenant_id)

    versions = query.order_by(RecordVersion.changed_at.desc()).limit(limit).all()

    result = []
    for v in versions:
        try:
            data = json.loads(v.data_snapshot)
        except Exception:
            data = {}
        result.append({
            "id": v.id,
            "version": v.version,
            "change_type": v.change_type,
            "data": data,
            "changed_by": v.changed_by,
            "changed_at": v.changed_at.isoformat() if v.changed_at else None
        })

    return result
