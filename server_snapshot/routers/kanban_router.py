from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload
from auth import get_current_user, require_role
import models
import schemas
from database import get_db, get_tenant_db
from versioning import save_version
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    prefix="/kanban",
    tags=["Канбан-доска"],
    dependencies=[Depends(get_current_user)]
)

@router.get("/cards", response_model=list[schemas.CardResponse])
def get_cards(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            query = db.query(models.Card).filter(models.Card.is_deleted == False)
        else:
            query = tdb.query(models.Card).filter(models.Card.is_deleted == False)
        return query.options(
            selectinload(models.Card.attachments),
            selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
            selectinload(models.Card.owner),
            selectinload(models.Card.client),
            selectinload(models.Card.tags),
        ).order_by(models.Card.position, models.Card.id.desc()).all()
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/cards/{card_id}", response_model=schemas.CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            card = db.query(models.Card).filter(models.Card.id == card_id).options(
                selectinload(models.Card.attachments),
                selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
                selectinload(models.Card.owner),
                selectinload(models.Card.client),
                selectinload(models.Card.tags),
            ).first()
        else:
            card = tdb.query(models.Card).filter(models.Card.id == card_id).options(
                selectinload(models.Card.attachments),
                selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
                selectinload(models.Card.owner),
                selectinload(models.Card.client),
                selectinload(models.Card.tags),
            ).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/trash", response_model=list[schemas.CardResponse])
def get_trash(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            cards = db.query(models.Card).filter(models.Card.is_deleted == True).options(
                selectinload(models.Card.attachments),
                selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
                selectinload(models.Card.owner),
                selectinload(models.Card.client),
                selectinload(models.Card.tags),
            ).order_by(models.Card.position, models.Card.id.desc()).all()
        else:
            cards = tdb.query(models.Card).filter(models.Card.is_deleted == True).options(
                selectinload(models.Card.attachments),
                selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
                selectinload(models.Card.owner),
                selectinload(models.Card.client),
                selectinload(models.Card.tags),
            ).order_by(models.Card.position, models.Card.id.desc()).all()
        return cards
    finally:
        if tdb is not db:
            tdb.close()

@router.post("/cards", response_model=schemas.CardResponse)
def create_card(card: schemas.CardCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        data = card.model_dump(exclude={"tag_ids"})
        new_card = models.Card(**data, owner_id=current_user.id)
        if card.tag_ids:
            tags = tdb.query(models.Tag).filter(models.Tag.id.in_(card.tag_ids)).all()
            new_card.tags = tags
        tdb.add(new_card)
        tdb.commit()
        tdb.refresh(new_card)
        log = models.ActivityLog(
            user_id=current_user.id,
            card_id=new_card.id,
            action="Создание карточки",
            details=f"Создана карточка: {new_card.title}"
        )
        tdb.add(log)
        tdb.commit()
        return new_card
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/cards/reorder")
def reorder_cards(payload: schemas.CardReorder, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        cards = tdb.query(models.Card).filter(
            models.Card.id.in_(payload.card_ids),
            models.Card.status == payload.status,
            models.Card.is_deleted == False
        ).all()
        found = {c.id: c for c in cards}
        for index, card_id in enumerate(payload.card_ids):
            card = found.get(card_id)
            if card is not None:
                card.position = index
        tdb.commit()
        return {"updated": len(found)}
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/cards/{card_id}/status", response_model=schemas.CardResponse)
def update_card_status(card_id: int, status_update: schemas.CardUpdateStatus, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            card = db.query(models.Card).filter(models.Card.id == card_id).first()
        else:
            card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        card.status = status_update.status
        tdb.commit()
        tdb.refresh(card)
        save_version(tdb, "cards", card.id,
            {"title": card.title, "status": card.status, "total_amount": float(card.total_amount or 0)},
            user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
        try:
            from routers.webhooks_router import notify_webhooks
            import asyncio
            asyncio.get_event_loop().create_task(notify_webhooks(
                current_user.tenant_id or 0, "card.updated",
                {"id": card.id, "title": card.title, "status": card.status}
            ))
        except Exception: pass
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/cards/{card_id}/restore", response_model=schemas.CardResponse)
def restore_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            card = db.query(models.Card).filter(models.Card.id == card_id).first()
        else:
            card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        card.is_deleted = False
        tdb.commit()
        tdb.refresh(card)
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            card = db.query(models.Card).filter(models.Card.id == card_id).first()
        else:
            card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        card.is_deleted = True
        tdb.commit()
        return {"detail": "Карточка перемещена в архив"}
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/cards/{card_id}/permanent")
def permanent_delete_card(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            card = db.query(models.Card).filter(models.Card.id == card_id).first()
        else:
            card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
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
        tdb.delete(card)
        tdb.commit()
        return {"detail": "Карточка удалена навсегда"}
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/cards/{card_id}/versions")
def get_card_versions(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from versioning import get_versions
    tdb = _db(current_user, db)
    try:
        versions = get_versions(tdb, "cards", card_id, tenant_id=current_user.tenant_id)
        return versions
    finally:
        if tdb is not db:
            tdb.close()

import io
import csv

@router.get("/export/csv")
def export_cards_csv(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        if tdb is db:
            cards = db.query(models.Card).filter(models.Card.is_deleted == False).options(selectinload(models.Card.client)).all()
        else:
            cards = tdb.query(models.Card).filter(models.Card.is_deleted == False).options(selectinload(models.Card.client)).all()
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
    finally:
        if tdb is not db:
            tdb.close()
