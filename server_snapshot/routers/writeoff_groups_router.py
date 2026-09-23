from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user, require_role
from constants import MONEY_EPSILON
from database import get_db
from services.payments import annul_group_writeoff
from services.writeoffs import card_group_key, recompute_group_total

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


@router.post("/", response_model=schemas.WriteoffGroupResponse)
def create_group(payload: GroupCreateRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if len(payload.card_ids) < 2:
        raise HTTPException(status_code=400, detail="Группа объединяет минимум две карточки")

    cards = db.query(models.Card).filter(models.Card.id.in_(payload.card_ids)).all()
    if len(cards) != len(payload.card_ids):
        raise HTTPException(status_code=404, detail="Одна или несколько карточек не найдены")

    key = card_group_key(cards[0])
    for c in cards:
        if card_group_key(c) != key:
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
    db.add(group)
    db.flush()

    for c in cards:
        c.writeoff_group_id = group.id

    group.total_amount = round(sum(float(c.total_amount or 0) for c in cards), 2)
    db.commit()
    db.refresh(group)
    return group


@router.get("/", response_model=List[schemas.WriteoffGroupResponse])
def list_groups(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    groups = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).order_by(models.WriteoffGroup.id.desc()).all()
    # Группа без участников не отдаётся: участники могли быть выведены
    # в обход API, и пустая плитка с устаревшей total_amount рисовалась
    # на доске списания, раздувая итог «к списанию» склада.
    return [g for g in groups if g.cards]


@router.get("/{group_id}", response_model=schemas.WriteoffGroupResponse)
def get_group(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return group


@router.post("/{group_id}/cards/{card_id}", response_model=schemas.WriteoffGroupResponse)
def add_card_to_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if group.written_off:
        raise HTTPException(status_code=400, detail="Нельзя добавлять карточки в уже закрытую группу")

    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.writeoff_group_id is not None and card.writeoff_group_id != group_id:
        raise HTTPException(status_code=400, detail="Карточка уже в другой группе")
    if (card.client_id, card.store_location) != (group.client_id, group.store_location):
        raise HTTPException(status_code=400, detail="Клиент или магазин карточки не совпадает с группой")
    if card.status not in ALLOWED_GROUP_STATUSES:
        raise HTTPException(status_code=400, detail=f"Статус карточки «{card.status}» не позволяет добавить её в группу")

    card.writeoff_group_id = group_id
    recompute_group_total(group)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}/cards/{card_id}")
def remove_card_from_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if group.written_off:
        raise HTTPException(status_code=400, detail="Нельзя менять состав закрытой группы")

    card = db.query(models.Card).filter(models.Card.id == card_id, models.Card.writeoff_group_id == group_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена в группе")

    # Фикс аудита 10.09: раньше ставили card.writeoff_group_id = NULL
    # напрямую, но загруженная коллекция group.cards об этом не узнавала —
    # recompute_group_total считала сумму вместе с удалённой карточкой,
    # а «последняя карточка» не распускала группу. remove() обновляет
    # обе стороны back_populates (FK уйдёт в NULL сам).
    group.cards.remove(card)
    recompute_group_total(group)

    # Ветки взаимоисключающие, но commit один на обеих (Фаза 4): удаление
    # пустой группы и обычный выход карточки уходят в БД одним commit'ом.
    dissolved = not group.cards
    if dissolved:
        db.delete(group)
    db.commit()
    if dissolved:
        return {"detail": "Группа распущена"}
    db.refresh(group)
    return group


@router.post("/{group_id}/issue-invoice", response_model=schemas.WriteoffGroupResponse)
def issue_group_invoice(group_id: int, payload: IssueGroupInvoiceRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
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
    if abs(amount - group_total) > MONEY_EPSILON:
        raise HTTPException(
            status_code=400,
            detail=f"Групповая накладная пока выписывается только на полную сумму группы: {group_total:.2f} BYN"
        )

    # Закрываем все отдельные записи по карточкам группы.
    # Фидбек 18.09: is_written_off НЕ трогаем — галочка «Списание» в
    # реестре оплат ставится только вручную менеджером и не привязана
    # к выписке накладных (ни одиночной, ни групповой).
    card_ids = [c.id for c in group.cards]
    db.query(models.Transaction).filter(
        models.Transaction.card_id.in_(card_ids),
        models.Transaction.is_document == False
    ).update({
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
    db.add(doc)

    group.invoice_number = number
    group.invoice_date = payload.invoice_date or None
    group.written_off = True

    for c in group.cards:
        c.status = "Закрыто"
        db.add(models.ActivityLog(
            user_id=current_user.id,
            card_id=c.id,
            action="Групповая накладная",
            details=f"{number} на {amount:.2f} BYN (группа #{group.id})",
            tenant_id=current_user.tenant_id,
        ))

    db.commit()
    db.refresh(group)
    # P0-хотфикс 23.09 (часть B): настоящий id документа группы в ответе.
    # Ad-hoc атрибут (колонки у writeoff_groups нет) — pydantic подхватывает
    # его через from_attributes в WriteoffGroupResponse.invoice_transaction_id.
    # Без него клиент знал только id группы и отменял групповую ТН через
    # DELETE /payments/transactions/{id группы}, снося постороннюю запись
    # реестра с тем же номером (дефект 1 реестра V2-WORKPLAN-2026-09-22).
    group.invoice_transaction_id = doc.id
    return group


@router.post("/{group_id}/annul", response_model=schemas.WriteoffGroupResponse,
             dependencies=[Depends(require_role("admin", "superadmin"))])
def annul_group_invoice(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Отмена групповой накладной: разматывает выписку группы целиком.

    P0-хотфикс 23.09 (часть B). До него отмена группы шла общим роутом
    DELETE /payments/transactions/{id}: документ группы — запись реестра с
    card_id=NULL, поэтому одиночная отмена не возвращала ни written_off группы,
    ни карточки из «Закрыто» (дефект 2 реестра), а клиент в сессии выписки
    вообще слал туда id группы и удалял постороннюю транзакцию (дефект 1).

    Гейт роли — как у одиночной отмены (DELETE /payments/transactions/{id},
    V11 коммита 5828f2d): групповая операция не может быть шире одиночной.
    Идемпотентность: повторный вызов для уже разматанной группы — 400 с
    причиной, а не 500 (см. services.payments.annul_group_writeoff).
    """
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if not group.written_off:
        raise HTTPException(
            status_code=400,
            detail=(f"Группа #{group.id} не закрыта накладной — отменять нечего. "
                    "Повторная отмена не требуется (или групповая ТН ещё не выписана).")
        )

    annul_group_writeoff(db, group, current_user)
    db.refresh(group)
    return group


# FIX 2026-09-23 (пункт 17, решение владельца): роспуск группы — админская
# ручка, тот же механизм и тот же уровень, что у /annul выше. Роспуск
# разрушителен и притом над чужой группой: он снимает writeoff_group_id у всех
# карточек сразу, и сборка «кто что складывал в одну накладную» теряется —
# восстанавливать её приходится переносом по одной. Прикрепление
# (POST /{group_id}/cards/{card_id}) и выход из группы
# (DELETE /{group_id}/cards/{card_id}) остаются открытыми всем ролям: это
# рабочая операция менеджера и склада над составом, она ничего не удаляет и
# отменяется обратным действием.
@router.delete("/{group_id}",
               dependencies=[Depends(require_role("admin", "superadmin"))])
def disband_group(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if group.written_off:
        raise HTTPException(status_code=400, detail="Закрытую группу нельзя распустить")

    for c in group.cards:
        c.writeoff_group_id = None
    db.delete(group)
    db.commit()
    return {"detail": "Группа распущена"}
