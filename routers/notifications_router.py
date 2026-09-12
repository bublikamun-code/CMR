"""
Лента уведомлений: чтение, отметка прочитанным, синхронизация просрочек.

sync-overdue вызывается внешним cron'ом (заголовок X-Cron-Token, как
/email-parser/sync-all) и создаёт уведомления о просрочках:
- task_overdue  — задачи с прошедшим сроком, не выполненные;
- card_overdue  — сделки с истёкшим due_date, не закрытые;
- card_payment  — сделки с истёкшим payment_due_date и не оплаченные.

Защита от дублей: для пары (получатель, entity) уже есть НЕПРОЧИТАННОЕ
уведомление того же типа — новое не создаётся. Повторная просрочка после
прочтения старого снова уведомит.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user, require_cron_token
from database import get_db, get_tenant_db
from db_utils import resolve_tenant_db as _db
from notify import notify

router = APIRouter(
    prefix="/notifications",
    tags=["Уведомления"],
    dependencies=[Depends(get_current_user)],
)

LIST_LIMIT = 30
# FIX 2026-08-29: фактический статус закрытия сделки — «Закрыто»
# (см. payments_router / writeoffs_router). Прежний дефолт («Выполнено»,
# «Закрыта») не совпадал ни с одним реальным статусом, и закрытые сделки
# продолжали получать уведомления о просрочке.
# FIX 2026-09-06 (фидбек): «На списание» — сделка фактически выписана,
# её дата окончания больше не генерирует «Просрочена сделка». Вопросы
# оплат отслеживаются отдельно по payment_due_date (блок 3 в sync_overdue).
DEFAULT_OPEN_STATUSES = ("На списание", "Закрыто")


@router.get("", response_model=schemas.NotificationsListResponse)
def list_notifications(unread_only: bool = False, limit: int = LIST_LIMIT,
                       db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        base = tdb.query(models.Notification).filter(models.Notification.user_id == current_user.id)
        unread_count = base.filter(models.Notification.is_read == False).count()
        query = base
        if unread_only:
            query = query.filter(models.Notification.is_read == False)
        items = (query.order_by(models.Notification.created_at.desc(), models.Notification.id.desc())
                 .limit(max(1, min(limit or LIST_LIMIT, 100))).all())
        return {
            "unread_count": unread_count,
            "items": [
                {"id": n.id, "type": n.type, "title": n.title, "details": n.details,
                 "entity_type": n.entity_type, "entity_id": n.entity_id,
                 "is_read": bool(n.is_read), "created_at": n.created_at}
                for n in items
            ],
        }
    finally:
        tdb.close()


@router.post("/read")
def mark_read(data: schemas.NotificationReadRequest, db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    """Отметить прочитанными конкретные ids или всё (если ids пуст)."""
    tdb = _db(current_user, db)
    try:
        query = (tdb.query(models.Notification)
                 .filter(models.Notification.user_id == current_user.id,
                         models.Notification.is_read == False))
        if data.ids:
            query = query.filter(models.Notification.id.in_(data.ids))
        rows = query.all()
        now = datetime.now(timezone.utc)
        for n in rows:
            n.is_read = True
            n.read_at = now
        tdb.commit()
        return {"marked": len(rows)}
    finally:
        tdb.close()


@router.delete("/{notification_id}")
def delete_notification(notification_id: int, db: Session = Depends(get_db),
                        current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        n = (tdb.query(models.Notification)
             .filter(models.Notification.id == notification_id,
                     models.Notification.user_id == current_user.id).first())
        if not n:
            raise HTTPException(status_code=404, detail="Уведомление не найдено")
        tdb.delete(n)
        tdb.commit()
        return {"ok": True}
    finally:
        tdb.close()


# --- Синхронизация просрочек (внешний cron, без пользовательской авторизации) ---

cron_router = APIRouter(prefix="/notifications", tags=["Уведомления"])


def _done_status_names(session):
    """Статусы сделок, считающиеся закрытыми (без «Просрочена сделка»).

    FIX 2026-08-29: поле DealStatus.is_done не существует в модели — прежняя
    проверка getattr() всегда возвращала пустое множество и подставляла
    неверный дефолт. Справочник статусов не хранит признак «закрыта», поэтому
    закрытым считается только явный статус «Закрыто» (DEFAULT_OPEN_STATUSES).
    FIX 2026-09-06: добавлен «На списание» — сделка фактически выписана;
    по ней могут оставаться только вопросы оплат (отдельный блок ниже).
    """
    return set(DEFAULT_OPEN_STATUSES)


@cron_router.post("/sync-overdue", dependencies=[Depends(require_cron_token)])
def sync_overdue(db: Session = Depends(get_db)):
    """Пробегает по основной и всем tenant-базам, создаёт уведомления о просрочках.

    Уведомления адресуются конкретным пользователям внутри соответствующей базы,
    поэтому каждый scope обрабатывается своей сессией.
    """
    created_total = 0
    notif_purged = 0
    versions_purged = 0
    # Ключ дня берём из UTC-времени, а не из date.today(). Локальная дата
    # сервера (Europe/Minsk, UTC+3) расходится с UTC, и это ломало две вещи:
    # 1) day_key уходит в детали уведомления как ключ дедупликации — сутки
    #    переключались бы в 21:00 UTC и просрочка получила бы повторное
    #    уведомление на три часа раньше;
    # 2) day_key сравнивается строкой с Card.due_date/payment_due_date в
    #    фильтрах ниже — сделки, ещё не просроченные по UTC, попадали бы
    #    в выборку вечером.
    now = datetime.now(timezone.utc)
    day_key = now.date().isoformat()

    # 0. Просроченные задачи — задачи живут в ОСНОВНОЙ базе, один проход.
    overdue_tasks = (db.query(models.Task)
                     .filter(models.Task.status != "done",
                             models.Task.due_date != None,
                             models.Task.due_date < now).all())
    for t in overdue_tasks:
        recipients = [r for r in {t.assignee_id, t.creator_id} if r]
        created_total += notify(
            db, recipients, type="task_overdue",
            title=f"Просрочена задача: {t.title}",
            details=day_key,
            entity_type="task", entity_id=t.id, dedupe=True)

    tenant_ids = sorted({int(r[0]) for r in db.query(models.User.tenant_id)
                         .filter(models.User.tenant_id != None).distinct().all()})

    scopes = [("main", db)] + [(tid, get_tenant_db(tid)) for tid in tenant_ids]

    for scope, session in scopes:
        # Пользователи этой базы — только им пишем в этом scope.
        base_users = {u.id for u in session.query(models.User).all()}
        if scope != "main":
            base_users &= {u.id for u in db.query(models.User.id)
                           .filter(models.User.tenant_id == scope).all()}
        try:
            done_statuses = _done_status_names(session)

            # 2. Просроченные сделки (истёк due_date)
            cards_overdue = (session.query(models.Card)
                             .filter(models.Card.is_deleted == False,
                                     models.Card.due_date != None,
                                     models.Card.due_date < day_key)
                             .all())
            cards_overdue = [c for c in cards_overdue if c.status not in done_statuses]
            for c in cards_overdue:
                if c.owner_id not in base_users:
                    continue
                created_total += notify(
                    db, [c.owner_id], type="card_overdue",
                    title=f"Просрочена сделка: {c.title}",
                    details=day_key,
                    entity_type="card", entity_id=c.id, dedupe=True)

            # 3. Просроченные оплаты (истёк payment_due_date)
            pay_overdue = (session.query(models.Card)
                           .filter(models.Card.is_deleted == False,
                                   models.Card.payment_due_date != None,
                                   models.Card.payment_due_date < day_key,
                                   models.Card.payment_status.notin_(["Оплачен", "Оплачено"]))
                           .all())
            for c in pay_overdue:
                if c.owner_id not in base_users:
                    continue
                created_total += notify(
                    db, [c.owner_id], type="card_payment",
                    title=f"Истёк срок оплаты: {c.title}",
                    details=day_key,
                    entity_type="card", entity_id=c.id, dedupe=True)
        except Exception:
            # Ошибка на одной базе не должна останавливать остальные
            try:
                session.rollback()
            except Exception:
                pass
            continue
        finally:
            # FIX аудита 10.09: ретенция раньше шла только по главной базе,
            # tenant-базы росли безлимитно. Чистим в каждом scope, пока
            # сессия ещё открыта (закрытие — в finally ниже).
            cutoff_read = datetime.now(timezone.utc) - timedelta(days=30)
            notif_purged += (session.query(models.Notification)
                             .filter(models.Notification.is_read == True,
                                     models.Notification.read_at < cutoff_read)
                             .delete(synchronize_session=False))
            cutoff_versions = datetime.now(timezone.utc) - timedelta(days=180)
            old_versions = (session.query(models.RecordVersion)
                            .filter(models.RecordVersion.changed_at < cutoff_versions)
                            .order_by(models.RecordVersion.table_name, models.RecordVersion.record_id,
                                      models.RecordVersion.version.desc())
                            .all())
            per_record = {}
            ids_to_delete = []
            for v in old_versions:
                key = (v.table_name, v.record_id)
                per_record[key] = per_record.get(key, 0) + 1
                if per_record[key] > 20:
                    ids_to_delete.append(v.id)
            if ids_to_delete:
                session.query(models.RecordVersion).filter(models.RecordVersion.id.in_(ids_to_delete)).delete(synchronize_session=False)
            versions_purged += len(ids_to_delete)
            session.commit()
            if scope != "main":
                session.close()

    return {"created": created_total, "notifications_purged": notif_purged, "versions_purged": versions_purged}
