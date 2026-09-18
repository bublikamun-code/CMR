from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user
from database import get_db
from db_utils import cap_list

router = APIRouter(
    prefix="/writeoffs",
    tags=["Списание со склада (Страница 3)"],
    dependencies=[Depends(get_current_user)]
)


@router.post("/{card_id}/finish_assembly", response_model=schemas.CardResponse)
def finish_assembly(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.status != "Сборка":
        raise HTTPException(status_code=400, detail="Заказ должен находиться в колонке 'Сборка'")
    card.status = "На списание"
    db.commit()
    db.refresh(card)
    return card


@router.get("/pending", response_model=List[schemas.CardResponse])
def get_pending_writeoffs(response: Response, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Card).filter(models.Card.status == "На списание")
    cards = query.options(
        # Н12 (аудит 06.09): без selectinload сериализация CardResponse
        # давала ленивый SELECT на каждую карточку очереди списания.
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).all()
    # Н11: предохранитель, как в kanban /cards
    return cap_list(cards, response)


@router.post("/{card_id}/execute")
def execute_writeoff(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    card.status = "Закрыто"
    db.commit()
    return {"status": "success", "message": f"Списание по заказу {card.title} успешно проведено. Сделка закрыта."}
