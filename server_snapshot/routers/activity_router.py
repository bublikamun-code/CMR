from typing import List

import contextlib

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db
from tenant_policy import get_tenant_object, tenant_query

router = APIRouter(
    prefix="/activity",
    tags=["Лог действий"],
    dependencies=[Depends(get_current_user)]
)


class ActivityDetailsUpdate(BaseModel):
    details: str = Field(min_length=1, max_length=4000)


class ActivityCreate(BaseModel):
    card_id: int
    details: str = Field(min_length=1, max_length=4000)


def _get_own_entry(entry_id: int, db: Session, current_user) -> models.ActivityLog:
    """Находит запись ленты и проверяет право на изменение.

    Редактировать/удалять комментарий может его автор; admin/superadmin —
    любой комментарий. Служебные записи (импорт почты,
    накладные) неизменяемы: правится только action='Комментарий'.
    """
    entry = get_tenant_object(
        db, models.ActivityLog, entry_id, current_user, detail="Запись не найден"
    )
    if entry.action != "Комментарий":
        raise HTTPException(status_code=400, detail="Эту запись нельзя изменять")
    is_admin = current_user.role in ("admin", "superadmin")
    if not is_admin and entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Можно изменять только свои комментарии")
    return entry


@router.get("", response_model=List[schemas.ActivityLogResponse])
def list_activity(
    card_id: int = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = tenant_query(db, models.ActivityLog, current_user)
    if card_id:
        get_tenant_object(
            db, models.Card, card_id, current_user, detail="Карточка не найдена"
        )
        query = query.filter(models.ActivityLog.card_id == card_id)
    entries = query.order_by(models.ActivityLog.created_at.desc()).limit(limit).all()
    # Имя автора — лента показывает «кто изменил»: раньше фронт собирал
    # инициалы из текста действия, реального имени в ответе не было.
    names = {}
    user_ids = {e.user_id for e in entries if e.user_id}
    if user_ids:
        names = dict(db.query(models.User.id, models.User.username).filter(models.User.id.in_(user_ids)).all())
    return [
        {
            "id": e.id,
            "user_id": e.user_id,
            "card_id": e.card_id,
            "action": e.action,
            "details": e.details,
            "created_at": e.created_at,
            "user_name": names.get(e.user_id),
        }
        for e in entries
    ]


@router.post("", response_model=schemas.ActivityLogResponse)
def create_comment(
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Комментарий по сделке — та же лента ActivityLog, action='Комментарий'.

    Писать может любой авторизованный пользователь; править/удалять —
    только автор (админ — любой), это делает PATCH/DELETE ниже.
    """
    card = get_tenant_object(
        db, models.Card, payload.card_id, current_user, detail="Карточка не найдена"
    )
    entry = models.ActivityLog(
        user_id=current_user.id,
        card_id=card.id,
        action="Комментарий",
        details=payload.details.strip(),
        tenant_id=current_user.tenant_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    # Подписчику важна карточка, а не текст: он может сам перечитать ленту.
    with contextlib.suppress(Exception):
        from routers.webhooks_router import notify_webhooks_async

        notify_webhooks_async(current_user.tenant_id, "card.commented",
            {"id": card.id, "title": card.title, "comment_id": entry.id})
    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "card_id": entry.card_id,
        "action": entry.action,
        "details": entry.details,
        "created_at": entry.created_at,
        "user_name": current_user.username,
    }


@router.patch("/{entry_id}", response_model=schemas.ActivityLogResponse)
def update_activity_entry(
    entry_id: int,
    payload: ActivityDetailsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    entry = _get_own_entry(entry_id, db, current_user)
    entry.details = payload.details.strip()
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}")
def delete_activity_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    entry = _get_own_entry(entry_id, db, current_user)
    db.delete(entry)
    db.commit()
    return {"detail": "Комментарий удалён"}
