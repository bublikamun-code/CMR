from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user
from database import get_db
from db_utils import cap_list
from tenant_policy import get_tenant_object, tenant_query

router = APIRouter(
    prefix="/writeoffs",
    tags=["Списание со склада (Страница 3)"],
    dependencies=[Depends(get_current_user)]
)


@router.post("/{card_id}/finish_assembly", response_model=schemas.CardResponse)
def finish_assembly(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
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
    # V5 (аудит прода 19.09): карточка в корзине не должна попадать в
    # очередь списания — она «удалена» для всех досок; при восстановлении
    # вернётся в очередь сама.
    query = tenant_query(db, models.Card, current_user).filter(
        models.Card.status == "На списание",
        models.Card.is_deleted == False,  # noqa: E712
    )
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
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.is_deleted:
        raise HTTPException(status_code=400, detail="Карточка в корзине — сначала восстановите её")
    if card.status != "На списание":
        raise HTTPException(status_code=400, detail="Списание проводится только из колонки «На списание»")
    ledger = tenant_query(db, models.Transaction, current_user).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    # Складские флаги — канон кнопки «Списать» с доски (PATCH
    # /payments/transactions/{id} {"is_warehouse_writeoff": true}):
    # каждая запись реестра сделки помечается is_warehouse_writeoff,
    # включая запись-остаток. Без этого sync-writeoff-status и
    # repair-writeoffs считали бы по остатку, что списывать нечего,
    # и вернули бы сделку в «На списание» поверх только что
    # проведённого закрытия.
    for t in ledger:
        t.is_warehouse_writeoff = True
    issued_sum = round(sum(float(t.amount or 0) for t in ledger), 2)
    db.add(models.ActivityLog(
        user_id=current_user.id, card_id=card_id,
        tenant_id=current_user.tenant_id,
        action="Списание",
        details=f"Списание со склада проведено: позиций {len(ledger)} на {issued_sum:.2f} BYN. Сделка закрыта.",
    ))
    card.status = "Закрыто"
    db.commit()
    return {"status": "success", "message": f"Списание по заказу {card.title} успешно проведено. Сделка закрыта."}
