from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
import models, schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    prefix="/activity",
    tags=["Лог действий"],
    dependencies=[Depends(get_current_user)]
)


class ActivityDetailsUpdate(BaseModel):
    details: str = Field(min_length=1, max_length=4000)


def _get_own_entry(entry_id: int, tdb: Session, current_user) -> models.ActivityLog:
    """Находит запись ленты и проверяет право на изменение.

    Редактировать/удалять комментарий может его автор; admin/superadmin —
    любой комментарий в своём тенанте. Служебные записи (импорт почты,
    накладные) неизменяемы: правится только action='Комментарий'.
    """
    entry = tdb.query(models.ActivityLog).filter(models.ActivityLog.id == entry_id).first()
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
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.ActivityLog)
        # superadmin без привязки к тенанту видит главную базу целиком
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.ActivityLog)
        else:
            # изоляция тенантов: не отдаём чужие записи
            query = query.filter(models.ActivityLog.tenant_id == current_user.tenant_id)
        if card_id:
            query = query.filter(models.ActivityLog.card_id == card_id)
        return query.order_by(models.ActivityLog.created_at.desc()).limit(limit).all()
    finally:
        if tdb is not db:
            tdb.close()


@router.patch("/{entry_id}", response_model=schemas.ActivityLogResponse)
def update_activity_entry(
    entry_id: int,
    payload: ActivityDetailsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        entry = _get_own_entry(entry_id, tdb, current_user)
        entry.details = payload.details.strip()
        tdb.commit()
        tdb.refresh(entry)
        return entry
    finally:
        if tdb is not db:
            tdb.close()


@router.delete("/{entry_id}")
def delete_activity_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        entry = _get_own_entry(entry_id, tdb, current_user)
        tdb.delete(entry)
        tdb.commit()
        return {"detail": "Комментарий удалён"}
    finally:
        if tdb is not db:
            tdb.close()
