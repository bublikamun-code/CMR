import contextlib
import os
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

import models
import runtime_config
import schemas
from auth import get_current_user, require_role
from database import get_db
from db_utils import PAGE_MAX_SIZE, cap_list, resolve_page, set_page_headers
from services.card_money import compute_card_money
from tenant_policy import (
    assign_tenant,
    get_tenant_object,
    tenant_predicate,
    tenant_query,
    writable_tenant_id,
)
from versioning import save_version

router = APIRouter(
    prefix="/kanban",
    tags=["Канбан-доска"],
    dependencies=[Depends(get_current_user)]
)


class CardPage(BaseModel):
    """Страница сделок — пункт 16 плана (дефект B11: /kanban/cards отдавал
    474 КБ одним куском). Включается только явным limit/offset; без них роут
    по-прежнему возвращает плоский массив CardResponse, так что существующий
    клиент (v2-boot-template.js грузит всю доску разом) не ломается."""
    items: List[schemas.CardResponse]
    total: int
    limit: int
    offset: int


def _attach_money_fields(cards, db: Session, current_user):
    """A12: вычисляет remaining/writeoff_status/issued_total для списка карточек.

    Один batch-запрос вместо N+1: подтягиваем все записи реестра
    (is_document=False) для всех карточек за один SELECT, группируем
    по card_id, считаем через services.card_money.compute_card_money.
    """
    if not cards:
        return
    card_ids = [c.id for c in cards]
    tx_rows = (
        tenant_query(db, models.Transaction, current_user)
        .filter(
            models.Transaction.card_id.in_(card_ids),
            models.Transaction.is_document == False,  # noqa: E712
        )
        .all()
    )
    by_card: dict[int, list] = {}
    for tx in tx_rows:
        by_card.setdefault(tx.card_id, []).append(tx)
    for card in cards:
        money = compute_card_money(card, by_card.get(card.id, []))
        card.remaining = money["remaining"]
        card.remaining_kop = money["remaining_kop"]
        card.writeoff_status = money["writeoff_status"]
        card.issued_total = money["issued_total"]


@router.get("/cards", response_model=Union[CardPage, List[schemas.CardResponse]])
def get_cards(response: Response,
              limit: Optional[int] = Query(None, ge=1, le=PAGE_MAX_SIZE),
              offset: Optional[int] = Query(None, ge=0),
              db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    query = tenant_query(db, models.Card, current_user).filter(
        models.Card.is_deleted == False
    )
    page = resolve_page(limit, offset)
    total = None
    if page is not None:
        # Общее число — одним COUNT по ТОМУ ЖЕ фильтру, что и выборка: считается
        # до применения limit/offset, иначе total был бы размером страницы.
        total = query.with_entities(func.count()).scalar() or 0
    listing = query.options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).order_by(models.Card.position, models.Card.id.desc())
    if page is not None:
        # order_by обязан идти ДО limit/offset — SQLAlchemy иначе бросает
        # InvalidRequestError. Порядок (position, id DESC) — ровно прежний и
        # тотальный: id уникален, поэтому страницы не перекрываются и не
        # теряют сделки на стыках.
        listing = listing.limit(page.limit).offset(page.offset)
    cards = listing.all()
    # A12: серверные денежные поля одним batch-запросом (без N+1).
    # В режиме страницы считается только для строк страницы.
    _attach_money_fields(cards, db, current_user)
    if page is None:
        # Н11 (аудит 06.09): предохранитель от аномального роста таблицы —
        # канбану нужны все карточки сразу, так что это пробка, не пагинация.
        return cap_list(cards, response)
    set_page_headers(response, total)
    return {"items": cards, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/cards/{card_id}", response_model=schemas.CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = tenant_query(db, models.Card, current_user).filter(
        models.Card.id == card_id,
        models.Card.is_deleted == False,
    ).options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    # A12: одна карточка — тот же helper, batch из одного элемента.
    _attach_money_fields([card], db, current_user)
    return card


@router.get("/trash", response_model=list[schemas.CardResponse])
def get_trash(response: Response, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    cards = tenant_query(db, models.Card, current_user).filter(
        models.Card.is_deleted == True
    ).options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).order_by(models.Card.position, models.Card.id.desc()).all()
    # Н11: предохранитель, как в /cards
    return cap_list(cards, response)


@router.post("/cards", response_model=schemas.CardResponse)
def create_card(card: schemas.CardCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    data = card.model_dump(exclude={"tag_ids", "owner_id"})
    owner_id = card.owner_id if card.owner_id else current_user.id
    if owner_id != current_user.id:
        owner = get_tenant_object(
            db, models.User, owner_id, current_user, detail="Владелец не найден"
        )
        if owner.tenant_id != writable_tenant_id(current_user):
            raise HTTPException(status_code=404, detail="Владелец не найден")
    if data.get("client_id") is not None:
        client = get_tenant_object(
            db,
            models.Client,
            data["client_id"],
            current_user,
            detail="Клиент не найден",
        )
        if client.tenant_id != writable_tenant_id(current_user):
            raise HTTPException(status_code=404, detail="Клиент не найден")
    tags = []
    if card.tag_ids:
        tags = tenant_query(db, models.Tag, current_user).filter(
            models.Tag.id.in_(card.tag_ids)
        ).all()
        if len(tags) != len(set(card.tag_ids)):
            raise HTTPException(status_code=404, detail="Тег не найден")
    new_card = assign_tenant(models.Card(**data, owner_id=owner_id), current_user)
    if tags:
        new_card.tags = tags
    db.add(new_card)
    # Атомарность (Фаза 4): карточка и ActivityLog уходят в БД одним
    # commit'ом. flush вместо промежуточного commit: id карточки нужен для
    # журнала, но внешняя видимость до конца запроса ни к чему.
    db.flush()
    db.refresh(new_card)
    log = models.ActivityLog(
        user_id=current_user.id,
        card_id=new_card.id,
        action="Создание карточки",
        details=f"Создана карточка: {new_card.title}",
        tenant_id=current_user.tenant_id,
    )
    db.add(log)
    db.commit()
    # Уведомление владельцу сделки, если её создал кто-то другой
    if owner_id and owner_id != current_user.id:
        from notify import notify
        notify(db, [owner_id], actor_id=current_user.id,
               type="card_created", title=f"Создана сделка: {new_card.title}",
               details=f"Автор: {current_user.username}",
               entity_type="card", entity_id=new_card.id)
    return new_card


@router.patch("/cards/reorder")
def reorder_cards(payload: schemas.CardReorder, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    cards = tenant_query(db, models.Card, current_user).filter(
        models.Card.id.in_(payload.card_ids),
        models.Card.status == payload.status,
        models.Card.is_deleted == False
    ).all()
    found = {c.id: c for c in cards}
    for index, card_id in enumerate(payload.card_ids):
        card = found.get(card_id)
        if card is not None:
            card.position = index
    db.commit()
    return {"updated": len(found)}


@router.patch("/cards/{card_id}/status", response_model=schemas.CardResponse)
def update_card_status(card_id: int, status_update: schemas.CardUpdateStatus, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    # Смена статуса — ключевое событие сделки, фиксируем в ленте (план 1.2):
    # раньше переходы «Новый запрос → В работе → …» нигде не сохранялись.
    if (card.status or "") != (status_update.status or ""):
        db.add(models.ActivityLog(
            user_id=current_user.id,
            card_id=card.id,
            action="Статус",
            details=f"{card.status or '—'} → {status_update.status}",
            tenant_id=current_user.tenant_id,
        ))
    card.status = status_update.status
    # Сделка в «Сборке» обязана быть в реестре оплат. Запись раньше
    # создавала только кнопка «В Сборку» в карточке; перетаскивание и
    # кнопки переноса оставляли сделку без записи — она пропадала из
    # «Реестра оплат» (кейс «ТрансЛИДИЯсервис», фидбек 09.09).
    if status_update.status == "Сборка":
        from routers.payments_router import ensure_registry_remainder
        ensure_registry_remainder(db, card)
    db.commit()
    db.refresh(card)
    save_version(db, "cards", card.id,
        {"title": card.title, "status": card.status, "total_amount": float(card.total_amount or 0)},
        user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
    from routers.webhooks_router import notify_webhooks_async
    # Фикс аудита 10.09: `or 0` подменял None (главная база) на 0,
    # под который вебхуки не создаются никогда, — «card.updated» с
    # доски не уходил ни одному подписчику. Остальные роутеры
    # передают tenant_id как есть.
    with contextlib.suppress(Exception):
        notify_webhooks_async(current_user.tenant_id, "card.updated",
            {"id": card.id, "title": card.title, "status": card.status})
    return card


@router.patch("/cards/{card_id}/restore", response_model=schemas.CardResponse)
def restore_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    card.is_deleted = False
    db.commit()
    db.refresh(card)
    return card


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    card.is_deleted = True
    db.commit()
    return {"detail": "Карточка перемещена в архив"}


@router.delete("/cards/{card_id}/permanent", dependencies=[Depends(require_role("admin", "superadmin"))])
def permanent_delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    upload_dir = os.path.realpath(os.fspath(runtime_config.uploads_dir()))
    for att in card.attachments:
        real_path = os.path.realpath(att.file_path)
        try:
            inside_uploads = os.path.commonpath((upload_dir, real_path)) == upload_dir
        except ValueError:
            inside_uploads = False
        if inside_uploads and os.path.exists(real_path):
            os.remove(real_path)
    for cl in card.checklists:
        if cl.invoice_file_path:
            real_path = os.path.realpath(cl.invoice_file_path)
            try:
                inside_uploads = os.path.commonpath((upload_dir, real_path)) == upload_dir
            except ValueError:
                inside_uploads = False
            if inside_uploads and os.path.exists(real_path):
                os.remove(real_path)
    # FIX 2026-08-29 (FK ON) + V3 (аудит прода 19.09): карточка удалена навсегда —
    # её собственность удаляется вместе с ней, а не остаётся сиротой. Ленту
    # (activity_log) раньше зануляли: сироты без card_id были источником редких
    # 500-х, чистка закреплена миграцией 0010. Задачи по-прежнему отвязываются,
    # а не удаляются — это чужие поручения, а не собственность сделки.
    # Группа запоминается до удаления: после db.delete(card) карточка недоступна.
    group_id = card.writeoff_group_id
    # Транзакции (остаток, накладные, копии в документах) удаляем физически:
    # FK transactions.card_id имеет ondelete="SET NULL", ORM занулил бы card_id,
    # и записи остались бы в «Реестре оплат» как «старые записи без привязки»
    # (V3, аудит прода 19.09). Кассу клиента (client_payments) не трогаем —
    # это реальные деньги, её card_id занулится сам по своему FK.
    rows = tenant_query(db, models.Transaction, current_user).filter(
        models.Transaction.card_id == card_id
    ).all()
    tx_ids = [t.id for t in rows]
    tenant_query(db, models.Transaction, current_user).filter(
        models.Transaction.card_id == card_id
    ).delete(synchronize_session=False)
    # История версий карточки и её транзакций без самой записи бессмысленна.
    tenant_query(db, models.RecordVersion, current_user).filter(
        models.RecordVersion.table_name == "cards",
        models.RecordVersion.record_id == card_id,
    ).delete(synchronize_session=False)
    if tx_ids:
        tenant_query(db, models.RecordVersion, current_user).filter(
            models.RecordVersion.table_name == "transactions",
            models.RecordVersion.record_id.in_(tx_ids),
        ).delete(synchronize_session=False)
    tenant_query(db, models.ActivityLog, current_user).filter(
        models.ActivityLog.card_id == card_id
    ).delete(synchronize_session=False)
    tenant_query(db, models.Task, current_user).filter(
        models.Task.card_id == card_id
    ).update({"card_id": None}, synchronize_session=False)
    db.delete(card)
    # flush, а не commit: карточка уходит из сессии в БД, чтобы group.cards
    # ниже отразил её отсутствие, но транзакция оставалась атомарной.
    db.flush()
    # Тотальная сумма группы — агрегат сумм карточек-участников; после удаления
    # последнего участника группа стала бы плиткой-призраком (такие уже чистит
    # POST /payments/repair-writeoffs — здесь то же правило на месте события).
    if group_id is not None:
        from services.writeoffs import recompute_group_total
        group = tenant_query(db, models.WriteoffGroup, current_user).filter(
            models.WriteoffGroup.id == group_id
        ).first()
        if group is not None:
            recompute_group_total(group)
            if not group.cards:
                db.delete(group)
    db.commit()
    return {"detail": "Карточка удалена навсегда"}


@router.get("/cards/{card_id}/versions")
def get_card_versions(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    from versioning import get_versions
    versions = get_versions(db, "cards", card_id, tenant_id=current_user.tenant_id)
    return versions
import csv
import io


@router.get("/export/csv")
def export_cards_csv(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    cards = tenant_query(db, models.Card, current_user).filter(
        models.Card.is_deleted == False
    ).options(selectinload(models.Card.client)).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL)
    writer.writerow(["ID", "Название", "Статус", "Сумма", "Клиент", "Приоритет", "Дата создания", "Описание"])
    for card in cards:
        writer.writerow([
            card.id,
            card.title,
            card.status,
            card.total_amount or 0,
            card.client.name if card.client else "",
            card.priority or "",
            card.created_at.isoformat() if card.created_at else "",
            (card.description or "").replace('\n', ' ').replace('\r', ' ')
        ])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=cards_export.csv"})
