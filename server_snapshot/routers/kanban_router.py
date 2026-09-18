import contextlib
import os

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user, require_role
from database import get_db
from db_utils import cap_list
from versioning import save_version

router = APIRouter(
    prefix="/kanban",
    tags=["Канбан-доска"],
    dependencies=[Depends(get_current_user)]
)


@router.get("/cards", response_model=list[schemas.CardResponse])
def get_cards(response: Response, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Card).filter(models.Card.is_deleted == False)
    cards = query.options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).order_by(models.Card.position, models.Card.id.desc()).all()
    # Н11 (аудит 06.09): предохранитель от аномального роста таблицы —
    # канбану нужны все карточки сразу, так что это пробка, не пагинация.
    return cap_list(cards, response)


@router.get("/cards/{card_id}", response_model=schemas.CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    return card


@router.get("/trash", response_model=list[schemas.CardResponse])
def get_trash(response: Response, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    cards = db.query(models.Card).filter(models.Card.is_deleted == True).options(
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
    new_card = models.Card(**data, owner_id=owner_id)
    if card.tag_ids:
        tags = db.query(models.Tag).filter(models.Tag.id.in_(card.tag_ids)).all()
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
    cards = db.query(models.Card).filter(
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
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
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
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    card.is_deleted = False
    db.commit()
    db.refresh(card)
    return card


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    card.is_deleted = True
    db.commit()
    return {"detail": "Карточка перемещена в архив"}


@router.delete("/cards/{card_id}/permanent", dependencies=[Depends(require_role("admin", "superadmin"))])
def permanent_delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    for att in card.attachments:
        real_path = os.path.realpath(att.file_path)
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
        upload_dir = os.path.abspath(upload_dir)
        if real_path.startswith(upload_dir) and os.path.exists(real_path):
            os.remove(real_path)
    for cl in card.checklists:
        if cl.invoice_file_path:
            real_path = os.path.realpath(cl.invoice_file_path)
            upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
            upload_dir = os.path.abspath(upload_dir)
            if real_path.startswith(upload_dir) and os.path.exists(real_path):
                os.remove(real_path)
    # FIX 2026-08-29 (FK ON): лента и задачи не имеют каскада в DDL и
    # relationship на Card — отвязываем вручную (транзакции ORM занулит сам,
    # чек-листы и вложения удалятся каскадом relationship).
    db.query(models.ActivityLog).filter(models.ActivityLog.card_id == card_id).update({"card_id": None}, synchronize_session=False)
    db.query(models.Task).filter(models.Task.card_id == card_id).update({"card_id": None}, synchronize_session=False)
    db.delete(card)
    db.commit()
    return {"detail": "Карточка удалена навсегда"}


@router.get("/cards/{card_id}/versions")
def get_card_versions(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from versioning import get_versions
    versions = get_versions(db, "cards", card_id, tenant_id=current_user.tenant_id)
    return versions
import csv
import io


@router.get("/export/csv")
def export_cards_csv(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    cards = db.query(models.Card).filter(models.Card.is_deleted == False).options(selectinload(models.Card.client)).all()
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
