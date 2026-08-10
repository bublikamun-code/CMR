from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List
import models, schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    prefix="/activity",
    tags=["Лог действий"],
    dependencies=[Depends(get_current_user)]
)

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
