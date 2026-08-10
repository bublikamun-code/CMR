from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

import models
import schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    prefix="/writeoffs",
    tags=["Списание со склада (Страница 3)"],
    dependencies=[Depends(get_current_user)]
)

@router.post("/{card_id}/finish_assembly", response_model=schemas.CardResponse)
def finish_assembly(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if card.status != "Сборка":
            raise HTTPException(status_code=400, detail="Заказ должен находиться в колонке 'Сборка'")
        card.status = "На списание"
        session.commit()
        session.refresh(card)
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/pending", response_model=List[schemas.CardResponse])
def get_pending_writeoffs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Card).filter(models.Card.status == "На списание")
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Card).filter(models.Card.status == "На списание")
        return query.all()
    finally:
        if tdb is not db:
            tdb.close()

@router.post("/{card_id}/execute")
def execute_writeoff(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        card.status = "Закрыто"
        session.commit()
        return {"status": "success", "message": f"Списание по заказу {card.title} успешно проведено. Сделка закрыта."}
    finally:
        if tdb is not db:
            tdb.close()
