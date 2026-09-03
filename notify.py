"""
Единая точка записи уведомлений для всех роутеров.

FIX 2026-09-03: уведомления пишутся в основную БД для всех получателей.
Раньше они раскладывались по tenant-БД получателей, но tenant-БД пустые
и выключены (см. db_utils.py) — уведомления уходили в никуда.

Первый аргумент — всегда основная сессия (Depends(get_db)). Хелпер коммитит
сам и вызывается ПОСЛЕ основного commit'а вызывающего кода, чтобы не
вмешиваться в чужую транзакцию.

Самоуведомления не создаются: recipient == actor_id пропускается.
"""
import models


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

    created = 0
    for uid in unique_ids:
        u = users.get(uid)
        if not u:
            continue
        if dedupe:
            exists = db.query(models.Notification.id).filter(
                models.Notification.type == type,
                models.Notification.user_id == uid,
                models.Notification.entity_type != "client",
                models.Notification.entity_id == entity_id if entity_id is not None else True,
                models.Notification.is_read == False,
            ).first()
            if exists:
                continue
        db.add(models.Notification(
            user_id=uid,
            type=type,
            title=(title or "")[:255],
            details=details,
            entity_type=entity_type,
            entity_id=entity_id,
            tenant_id=u.tenant_id,
        ))
        created += 1
    if created:
        try:
            db.commit()
        except Exception:
            # Уведомление не должно ломать основной сценарий запроса
            try:
                db.rollback()
            except Exception:
                pass
    return created


def admin_ids(db):
    """id пользователей с ролью admin/superadmin из ОСНОВНОЙ БД."""
    return [r[0] for r in db.query(models.User.id).filter(
        models.User.role.in_(("admin", "superadmin"))).all()]
