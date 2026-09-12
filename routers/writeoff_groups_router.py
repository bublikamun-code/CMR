from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user
from database import get_db
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    prefix="/writeoffs/groups",
    tags=["Групповое списание"],
    dependencies=[Depends(get_current_user)]
)

class GroupCreateRequest(BaseModel):
    card_ids: List[int]
    name: Optional[str] = None

class AddCardRequest(BaseModel):
    card_id: int

class IssueGroupInvoiceRequest(BaseModel):
    invoice_number: str
    invoice_date: Optional[str] = None
    amount: Optional[float] = None


ALLOWED_GROUP_STATUSES = {"Сборка", "На списание"}


def _card_group_key(card: models.Card):
    """Ключ группировки: клиент + магазин."""
    return (card.client_id, card.store_location)


def _recompute_group_total(group: models.WriteoffGroup):
    group.total_amount = round(
        sum(float(c.total_amount or 0) for c in group.cards), 2
    )


@router.post("/", response_model=schemas.WriteoffGroupResponse)
def create_group(payload: GroupCreateRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if len(payload.card_ids) < 2:
            raise HTTPException(status_code=400, detail="Группа объединяет минимум две карточки")

        cards = session.query(models.Card).filter(models.Card.id.in_(payload.card_ids)).all()
        if len(cards) != len(payload.card_ids):
            raise HTTPException(status_code=404, detail="Одна или несколько карточек не найдены")

        key = _card_group_key(cards[0])
        for c in cards:
            if _card_group_key(c) != key:
                raise HTTPException(
                    status_code=400,
                    detail="Все карточки группы должны принадлежать одному клиенту и одному магазину"
                )
            if c.status not in ALLOWED_GROUP_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Карточка #{c.id} в статусе «{c.status}». Группировать можно только «Сборку» и «На списание»"
                )
            if c.writeoff_group_id is not None:
                raise HTTPException(status_code=400, detail=f"Карточка #{c.id} уже в другой группе")

        client = cards[0].client
        name = (payload.name or "").strip()
        if not name:
            name = f"{client.name} (группа)" if client else f"Группа сделок ({len(cards)})"

        group = models.WriteoffGroup(
            name=name,
            client_id=cards[0].client_id,
            store_location=cards[0].store_location,
            tenant_id=current_user.tenant_id,
        )
        session.add(group)
        session.flush()

        for c in cards:
            c.writeoff_group_id = group.id

        group.total_amount = round(sum(float(c.total_amount or 0) for c in cards), 2)
        session.commit()
        session.refresh(group)
        return group
    finally:
        if tdb is not db:
            tdb.close()


@router.get("/", response_model=List[schemas.WriteoffGroupResponse])
def list_groups(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        groups = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).order_by(models.WriteoffGroup.id.desc()).all()
        # Группа без участников не отдаётся: участники могли быть выведены
        # в обход API, и пустая плитка с устаревшей total_amount рисовалась
        # на доске списания, раздувая итог «к списанию» склада.
        return [g for g in groups if g.cards]
    finally:
        if tdb is not db:
            tdb.close()


@router.get("/{group_id}", response_model=schemas.WriteoffGroupResponse)
def get_group(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        group = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        return group
    finally:
        if tdb is not db:
            tdb.close()


@router.post("/{group_id}/cards/{card_id}", response_model=schemas.WriteoffGroupResponse)
def add_card_to_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        group = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        if group.written_off:
            raise HTTPException(status_code=400, detail="Нельзя добавлять карточки в уже закрытую группу")

        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if card.writeoff_group_id is not None and card.writeoff_group_id != group_id:
            raise HTTPException(status_code=400, detail="Карточка уже в другой группе")
        if (card.client_id, card.store_location) != (group.client_id, group.store_location):
            raise HTTPException(status_code=400, detail="Клиент или магазин карточки не совпадает с группой")
        if card.status not in ALLOWED_GROUP_STATUSES:
            raise HTTPException(status_code=400, detail=f"Статус карточки «{card.status}» не позволяет добавить её в группу")

        card.writeoff_group_id = group_id
        _recompute_group_total(group)
        session.commit()
        session.refresh(group)
        return group
    finally:
        if tdb is not db:
            tdb.close()


@router.delete("/{group_id}/cards/{card_id}")
def remove_card_from_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        group = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        if group.written_off:
            raise HTTPException(status_code=400, detail="Нельзя менять состав закрытой группы")

        card = session.query(models.Card).filter(models.Card.id == card_id, models.Card.writeoff_group_id == group_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена в группе")

        # Фикс аудита 10.09: раньше ставили card.writeoff_group_id = NULL
        # напрямую, но загруженная коллекция group.cards об этом не узнавала —
        # _recompute_group_total считала сумму вместе с удалённой карточкой,
        # а «последняя карточка» не распускала группу. remove() обновляет
        # обе стороны back_populates (FK уйдёт в NULL сам).
        group.cards.remove(card)
        _recompute_group_total(group)

        if not group.cards:
            session.delete(group)
            session.commit()
            return {"detail": "Группа распущена"}

        session.commit()
        session.refresh(group)
        return group
    finally:
        if tdb is not db:
            tdb.close()


@router.post("/{group_id}/issue-invoice", response_model=schemas.WriteoffGroupResponse)
def issue_group_invoice(group_id: int, payload: IssueGroupInvoiceRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        group = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        if group.written_off:
            raise HTTPException(status_code=400, detail="По группе уже выписана накладная")
        if not group.cards:
            raise HTTPException(status_code=400, detail="В группе нет карточек")

        number = (payload.invoice_number or "").strip()
        if not number:
            raise HTTPException(status_code=400, detail="Укажите номер накладной")

        group_total = round(float(group.total_amount or 0), 2)
        amount = round(float(payload.amount or group_total), 2)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Сумма накладной должна быть больше нуля")
        if abs(amount - group_total) > 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"Групповая накладная пока выписывается только на полную сумму группы: {group_total:.2f} BYN"
            )

        # Закрываем все отдельные записи по карточкам группы.
        card_ids = [c.id for c in group.cards]
        session.query(models.Transaction).filter(
            models.Transaction.card_id.in_(card_ids),
            models.Transaction.is_document == False
        ).update({
            "is_written_off": True,
            "is_warehouse_writeoff": True,
            "is_invoice_issued": True,
        }, synchronize_session=False)

        # Документ групповой накладной.
        doc = models.Transaction(
            company_name=group.name,
            amount=amount,
            store_location=group.store_location,
            invoice_number=number,
            invoice_date=payload.invoice_date or None,
            is_document=True,
            is_invoice_issued=True,
            is_warehouse_writeoff=True,
            is_written_off=True,
            writeoff_group_id=group.id,
            tenant_id=current_user.tenant_id,
        )
        session.add(doc)

        group.invoice_number = number
        group.invoice_date = payload.invoice_date or None
        group.written_off = True

        for c in group.cards:
            c.status = "Закрыто"
            session.add(models.ActivityLog(
                user_id=current_user.id,
                card_id=c.id,
                action="Групповая накладная",
                details=f"{number} на {amount:.2f} BYN (группа #{group.id})",
                tenant_id=current_user.tenant_id,
            ))

        session.commit()
        session.refresh(group)
        return group
    finally:
        if tdb is not db:
            tdb.close()


@router.delete("/{group_id}")
def disband_group(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        group = session.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        if group.written_off:
            raise HTTPException(status_code=400, detail="Закрытую группу нельзя распустить")

        for c in group.cards:
            c.writeoff_group_id = None
        session.delete(group)
        session.commit()
        return {"detail": "Группа распущена"}
    finally:
        if tdb is not db:
            tdb.close()
