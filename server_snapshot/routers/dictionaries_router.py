"""Справочники магазинов и статусов сделок (Этап 2.3 плана замены фронта).

Чтение — любому авторизованному пользователю; создание и правка — только
админам (require_admin). Дубликаты имён запрещены unique-индексом модели:
IntegrityError переводится в честный 400, а не 500.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
import models
import schemas
from database import get_db
from tenant_policy import assign_tenant, get_tenant_object, tenant_query

router = APIRouter(prefix="/dictionaries", tags=["Справочники"])


@router.get("/stores", response_model=list[schemas.StoreLocationResponse])
def list_stores(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return tenant_query(db, models.StoreLocation, current_user).order_by(
        models.StoreLocation.name
    ).all()


@router.post("/stores", response_model=schemas.StoreLocationResponse)
def create_store(data: schemas.StoreLocationCreate, db: Session = Depends(get_db),
                 current_user: models.User = Depends(auth.require_admin())):
    store = assign_tenant(models.StoreLocation(**data.model_dump()), current_user)
    db.add(store)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Магазин с таким названием уже есть") from exc
    db.refresh(store)
    return store


@router.patch("/stores/{store_id}", response_model=schemas.StoreLocationResponse)
def update_store(store_id: int, data: schemas.StoreLocationUpdate, db: Session = Depends(get_db),
                 current_user: models.User = Depends(auth.require_admin())):
    store = get_tenant_object(
        db, models.StoreLocation, store_id, current_user, detail="Магазин не найден"
    )
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(store, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Магазин с таким названием уже есть") from exc
    db.refresh(store)
    return store


@router.get("/statuses", response_model=list[schemas.DealStatusResponse])
def list_statuses(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return tenant_query(db, models.DealStatus, current_user).order_by(
        models.DealStatus.position, models.DealStatus.id
    ).all()


@router.post("/statuses", response_model=schemas.DealStatusResponse)
def create_status(data: schemas.DealStatusCreate, db: Session = Depends(get_db),
                  current_user: models.User = Depends(auth.require_admin())):
    status = assign_tenant(models.DealStatus(**data.model_dump()), current_user)
    db.add(status)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Статус с таким названием уже есть") from exc
    db.refresh(status)
    return status


@router.patch("/statuses/{status_id}", response_model=schemas.DealStatusResponse)
def update_status(status_id: int, data: schemas.DealStatusUpdate, db: Session = Depends(get_db),
                  current_user: models.User = Depends(auth.require_admin())):
    status = get_tenant_object(
        db, models.DealStatus, status_id, current_user, detail="Статус не найден"
    )
    if not status:
        raise HTTPException(status_code=404, detail="Статус не найден")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(status, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Статус с таким названием уже есть") from exc
    db.refresh(status)
    return status
