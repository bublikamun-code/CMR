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

router = APIRouter(prefix="/dictionaries", tags=["Справочники"])


@router.get("/stores", response_model=list[schemas.StoreLocationResponse])
def list_stores(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return db.query(models.StoreLocation).order_by(models.StoreLocation.name).all()


@router.post("/stores", response_model=schemas.StoreLocationResponse)
def create_store(data: schemas.StoreLocationCreate, db: Session = Depends(get_db),
                 current_user: models.User = Depends(auth.require_admin())):
    store = models.StoreLocation(**data.model_dump())
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
    store = db.query(models.StoreLocation).filter(models.StoreLocation.id == store_id).first()
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
    return db.query(models.DealStatus).order_by(models.DealStatus.position, models.DealStatus.id).all()


@router.post("/statuses", response_model=schemas.DealStatusResponse)
def create_status(data: schemas.DealStatusCreate, db: Session = Depends(get_db),
                  current_user: models.User = Depends(auth.require_admin())):
    status = models.DealStatus(**data.model_dump())
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
    status = db.query(models.DealStatus).filter(models.DealStatus.id == status_id).first()
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
