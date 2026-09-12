from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

router = APIRouter(
    prefix="/activity",
    tags=["Лог действий"],
    dependencies=[Depends(get_current_user)]
)


class ActivityDetailsUpdate(BaseModel):
    details: str = Field(min_length=1, max_length=4000)


def _get_own_entry(entry_id: int, db: Session, current_user) -> models.ActivityLog:
    """Находит запись ленты и проверяет право на изменение.

    Редактировать/удалять комментарий может его автор; admin/superadmin —
    любой комментарий. Служебные записи (импорт почты,
    накладные) неизменяемы: правится только action='Комментарий'.
    """
    entry = db.query(models.ActivityLog).filter(models.ActivityLog.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if entry.action != "Комментарий":
        raise HTTPException(status_code=400, detail="Эту запись нельзя изменять")
    is_admin = current_user.role in ("admin", "superadmin")
    if not is_admin and entry.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Можно изменять только свои комментарии")
    # изоляция тенантов
    if current_user.role != "superadmin" and entry.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return entry


@router.get("", response_model=List[schemas.ActivityLogResponse])
def list_activity(
    card_id: int = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.ActivityLog)
    if card_id:
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
