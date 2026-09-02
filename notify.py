"""
Единая точка записи уведомлений для всех роутеров.

Пользователи глобальные (таблица users в основной БД), а данные разложены по
tenant-базам, поэтому уведомление пишется в БД ТЕНАТА ПОЛУЧАТЕЛЯ: каждый видит
ровно свои строки таблицы notifications своего приложения.

Первый аргумент — всегда основная сессия (Depends(get_db)): из неё берётся
маппинг user → tenant. Хелпер коммитит сам и вызывается ПОСЛЕ основного
commit'а вызывающего кода, чтобы не вмешиваться в чужую транзакцию.

Самоуведомления не создаются: recipient == actor_id пропускается.
"""
import models
from database import get_tenant_db


def notify(db, recipients, *, actor_id=None, type, title, details=None,
           entity_type=None, entity_id=None, dedupe=False):
    """Записать уведомления списку пользователей (по одному на получателя).

    recipients — Iterable[int] user_id. Возвращает число записанных строк.
    dedupe=True — не писать второе НЕПРОЧИТАННОЕ уведомление того же типа
    по той же сущности тому же получателю (используется cron-просрочками).
    """
    unique_ids = []
    seen = set()
    for uid in recipients or []:
        if uid is None or uid == actor_id or uid in seen:
            continue
        seen.add(uid)
        unique_ids.append(uid)
    if not unique_ids:
        return 0

    users = {u.id: u for u in db.query(models.User)
             .filter(models.User.id.in_(unique_ids)).all()}

    # Группировка получателей по их тенантной базе
    groups = {}
    for uid in unique_ids:
        u = users.get(uid)
        if not u:
            continue
        groups.setdefault(u.tenant_id, []).append(uid)

    created = 0
    for tid, uids in groups.items():
        session = db if tid is None else get_tenant_db(tid)
        try:
            for uid in uids:
                if dedupe:
                    exists = session.query(models.Notification.id).filter(
                        models.Notification.type == type,
                        models.Notification.user_id == uid,
                        models.Notification.entity_type != "client",
                        models.Notification.entity_id == entity_id if entity_id is not None else True,
                        models.Notification.is_read == False,
                    ).first()
                    if exists:
                        continue
                session.add(models.Notification(
                    user_id=uid,
                    type=type,
                    title=(title or "")[:255],
                    details=details,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    tenant_id=tid,
                ))
                created += 1
            session.commit()
        except Exception:
            # Уведомление не должно ломать основной сценарий запроса
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            if session is not db:
                session.close()
    return created


def admin_ids(db):
    """id пользователей с ролью admin/superadmin из ОСНОВНОЙ БД."""
    return [r[0] for r in db.query(models.User.id).filter(
        models.User.role.in_(("admin", "superadmin"))).all()]
