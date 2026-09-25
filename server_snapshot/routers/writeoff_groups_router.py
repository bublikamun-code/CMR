from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user, require_role
from constants import MONEY_EPSILON
from database import get_db
from services.payments import annul_group_writeoff
from services.sqlite_writer import reserve_model_rows, sqlite_write_transaction
from services.writeoffs import card_group_key, recompute_group_total
from tenant_policy import assign_tenant, get_tenant_object, tenant_query

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

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        return schemas.validate_money(
            v, field_name="Сумма групповой накладной", allow_zero=False
        )


ALLOWED_GROUP_STATUSES = {"Сборка", "На списание"}


def _reserve_group_write(
    db: Session,
    group_id: int,
    extra_card_ids=(),
    *,
    current_user,
    missing_card_detail="Карточка группы не найдена",
):
    """Захватывает writer slot и перечитывает группу после резервирования.

    UPDATE самой группы сериализует все операции с ней во всех процессах.
    Карточки группы резервируются после повторного чтения по возрастанию id;
    writer slot к этому моменту уже удерживается, поэтому состав не может
    измениться из-под операции.
    """
    reserve_model_rows(
        db,
        models.WriteoffGroup,
        [group_id],
        not_found_detail="Группа не найдена",
        busy_detail="Группа занята другой операцией. Повторите попытку.",
    )
    group = tenant_query(db, models.WriteoffGroup, current_user).options(
        selectinload(models.WriteoffGroup.cards)
    ).filter(models.WriteoffGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    reserve_model_rows(
        db,
        models.Card,
        [card.id for card in group.cards] + list(extra_card_ids),
        not_found_detail=missing_card_detail,
        busy_detail="Группа занята другой операцией. Повторите попытку.",
        reset_session=False,
    )
    return group


def _claim_group_issue(db: Session, group_id: int) -> bool:
    """Атомарно переводит открытую группу в состояние выписки.

    Условный UPDATE работает и для кода, который по какой-то причине начал
    операцию без предварительного no-op UPDATE. В пределах уже захваченного
    writer slot он также не даёт второй выписке пройти на устаревшем ORM-объекте.
    """
    claimed = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id,
        or_(
            models.WriteoffGroup.written_off == False,  # noqa: E712
            models.WriteoffGroup.written_off.is_(None),
        ),
    ).update(
        {models.WriteoffGroup.written_off: True},
        synchronize_session=False,
    )
    return claimed == 1


@router.post("/", response_model=schemas.WriteoffGroupResponse)
def create_group(payload: GroupCreateRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if len(payload.card_ids) < 2:
        raise HTTPException(status_code=400, detail="Группа объединяет минимум две карточки")

    with sqlite_write_transaction(db):
        tenant_id = current_user.tenant_id
        reserve_model_rows(
            db,
            models.Card,
            payload.card_ids,
            not_found_detail="Одна или несколько карточек не найдены",
            busy_detail="Карточки заняты другой операцией. Повторите попытку.",
        )
        cards = tenant_query(db, models.Card, current_user).filter(models.Card.id.in_(payload.card_ids)).all()
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
            tenant_id=tenant_id,
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
    groups = tenant_query(db, models.WriteoffGroup, current_user).options(selectinload(models.WriteoffGroup.cards)).order_by(models.WriteoffGroup.id.desc()).all()
    # Группа без участников не отдаётся: участники могли быть выведены
    # в обход API, и пустая плитка с устаревшей total_amount рисовалась
    # на доске списания, раздувая итог «к списанию» склада.
    return [g for g in groups if g.cards]


@router.get("/{group_id}", response_model=schemas.WriteoffGroupResponse)
def get_group(group_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    group = get_tenant_object(db, models.WriteoffGroup, group_id, current_user, detail="Группа не найдена")
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return group


@router.post("/{group_id}/cards/{card_id}", response_model=schemas.WriteoffGroupResponse)
def add_card_to_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    with sqlite_write_transaction(db):
        group = _reserve_group_write(
            db,
            group_id,
            [card_id],
            current_user=current_user,
            missing_card_detail="Карточка не найдена",
        )
        if group.written_off:
            raise HTTPException(status_code=400, detail="Нельзя добавлять карточки в уже закрытую группу")

        card = get_tenant_object(db, models.Card, card_id, current_user, detail="Карточка не найдена")
        if card.writeoff_group_id is not None and card.writeoff_group_id != group_id:
            raise HTTPException(status_code=400, detail="Карточка уже в другой группе")
        if (card.client_id, card.store_location) != (group.client_id, group.store_location):
            raise HTTPException(status_code=400, detail="Клиент или магазин карточки не совпадает с группой")
        if card.status not in ALLOWED_GROUP_STATUSES:
            raise HTTPException(status_code=400, detail=f"Статус карточки «{card.status}» не позволяет добавить её в группу")

        # Загрузка relationship произошла до проверки карточки. Синхронизируем
        # обе стороны, иначе второй последовательный add посчитает только первую
        # добавленную карточку и закрепит устаревший total_amount.
        group.cards.append(card)
        recompute_group_total(group)
        db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}/cards/{card_id}")
def remove_card_from_group(group_id: int, card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    with sqlite_write_transaction(db):
        group = _reserve_group_write(
            db,
            group_id,
            [card_id],
            current_user=current_user,
            missing_card_detail="Карточка не найдена в группе",
        )
        if group.written_off:
            raise HTTPException(status_code=400, detail="Нельзя менять состав закрытой группы")

        card = get_tenant_object(
            db, models.Card, card_id, current_user,
            detail="Карточка не найдена в группе",
            extra_filters=[models.Card.writeoff_group_id == group_id],
        )

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
    with sqlite_write_transaction(db):
        actor_id = current_user.id
        actor_tenant_id = current_user.tenant_id
        group = _reserve_group_write(db, group_id, current_user=current_user)
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

        # Единственное условное изменение состояния: только один запрос может
        # перевести открытую группу в закрытое/идущее состояние. Всё остальное
        # внутри той же транзакции откатится вместе с проигравшим contender'ом.
        if not _claim_group_issue(db, group.id):
            raise HTTPException(status_code=400, detail="По группе уже выписана накладная")
        group.written_off = True

        # Закрываем все отдельные записи по карточкам группы.
        # Фидбек 18.09: is_written_off НЕ трогаем — галочка «Списание» в
        # реестре оплат ставится только вручную менеджером и не привязана
        # к выписке накладных (ни одиночной, ни групповой).
        card_ids = sorted(card.id for card in group.cards)
        db.query(models.Transaction).filter(
            models.Transaction.card_id.in_(card_ids),
            models.Transaction.is_document == False  # noqa: E712
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
            tenant_id=actor_tenant_id,
        )
        db.add(doc)

        group.invoice_number = number
        group.invoice_date = payload.invoice_date or None

        cards = sorted(group.cards, key=lambda card: card.id)
        for card in cards:
            card.status = "Закрыто"
            db.add(models.ActivityLog(
                user_id=actor_id,
                card_id=card.id,
                action="Групповая накладная",
                details=f"{number} на {amount:.2f} BYN (группа #{group.id})",
                tenant_id=actor_tenant_id,
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
    with sqlite_write_transaction(db):
        group = _reserve_group_write(db, group_id, current_user=current_user)
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
    with sqlite_write_transaction(db):
        group = _reserve_group_write(db, group_id, current_user=current_user)
        if group.written_off:
            raise HTTPException(status_code=400, detail="Закрытую группу нельзя распустить")

        for card in sorted(group.cards, key=lambda item: item.id):
            card.writeoff_group_id = None
        db.delete(group)
        db.commit()
    return {"detail": "Группа распущена"}
