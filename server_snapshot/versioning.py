"""
Утилита версионирования записей.
Сохраняет историю изменений для карточек, клиентов и других объектов.
"""
import json
from models import RecordVersion


def save_version(db, table_name, record_id, data, user_id=None, change_type="update", tenant_id=None):
    """Сохранить версию записи."""
    try:
        # Получаем текущую версию
        last = db.query(RecordVersion).filter(
            RecordVersion.table_name == table_name,
            RecordVersion.record_id == record_id,
            RecordVersion.tenant_id == tenant_id
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
        db.commit()
        return version
    except Exception:
        db.rollback()
        return None


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
