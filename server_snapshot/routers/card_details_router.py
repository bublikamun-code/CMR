import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import models
import schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

router = APIRouter(
    tags=["Детали карточки (Чек-листы и Файлы)"],
    dependencies=[Depends(get_current_user)]
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/cards/{card_id}/checklists", response_model=schemas.ChecklistResponse)
def add_checklist_item(card_id: int, item: schemas.ChecklistCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        data = item.model_dump()

        # Название берём из справочника поставщиков — оно ведущее.
        # Ручной ввод остаётся только для старых записей без supplier_id.
        if data.get("supplier_id"):
            sup = session.query(models.Supplier).filter(
                models.Supplier.id == data["supplier_id"]
            ).first()
            if not sup:
                raise HTTPException(status_code=404, detail="Поставщик не найден")
            data["company_name"] = sup.name
        if not (data.get("company_name") or "").strip():
            raise HTTPException(status_code=400, detail="Укажите поставщика")

        new_item = models.CardChecklist(**data, card_id=card_id)
        session.add(new_item)
        session.commit()
        session.refresh(new_item)
        return new_item
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/checklists/{checklist_id}", response_model=schemas.ChecklistResponse)
def update_checklist_item(checklist_id: int, item_update: schemas.ChecklistUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        update_data = item_update.model_dump(exclude_unset=True)

        # Сменили поставщика — подтягиваем актуальное название из справочника
        if "supplier_id" in update_data and update_data["supplier_id"]:
            sup = session.query(models.Supplier).filter(
                models.Supplier.id == update_data["supplier_id"]
            ).first()
            if not sup:
                raise HTTPException(status_code=404, detail="Поставщик не найден")
            update_data["company_name"] = sup.name

        for key, value in update_data.items():
            setattr(item, key, value)
        session.commit()
        session.refresh(item)
        return item
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/checklists/{checklist_id}")
def delete_checklist_item(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        if item.invoice_file_path and os.path.exists(item.invoice_file_path):
            os.remove(item.invoice_file_path)
        session.delete(item)
        session.commit()
        return {"detail": "Пункт успешно удален"}
    finally:
        if tdb is not db:
            tdb.close()

@router.post("/checklists/{checklist_id}/invoice", response_model=schemas.ChecklistResponse)
def upload_checklist_invoice(checklist_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        ALLOWED_EXTS = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt'}
        ext = os.path.splitext(file.filename or '')[1].lower()
        if ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Тип файла не разрешён: {ext}")
        if item.invoice_file_path and os.path.exists(item.invoice_file_path):
            os.remove(item.invoice_file_path)
        original_name = os.path.basename(file.filename or "schet")
        safe_filename = f"chk{checklist_id}_{uuid.uuid4().hex[:8]}_{original_name}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        size = 0
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    buffer.close()
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")
                buffer.write(chunk)
        item.invoice_file_name = original_name
        item.invoice_file_path = file_path
        session.commit()
        session.refresh(item)
        return item
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/checklists/{checklist_id}/invoice", response_model=schemas.ChecklistResponse)
def delete_checklist_invoice(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        if item.invoice_file_path and os.path.exists(item.invoice_file_path):
            os.remove(item.invoice_file_path)
        item.invoice_file_name = None
        item.invoice_file_path = None
        session.commit()
        session.refresh(item)
        return item
    finally:
        if tdb is not db:
            tdb.close()

ALLOWED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.odt', '.ods', '.zip', '.rar'}

@router.post("/cards/{card_id}/attachments", response_model=schemas.AttachmentResponse)
def upload_file(card_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        original_name = os.path.basename(file.filename or "file")
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Тип файла {ext} не разрешён")
        if not original_name:
            original_name = "file"
        safe_filename = f"{card_id}_{uuid.uuid4().hex[:8]}_{original_name}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        size = 0
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    buffer.close()
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")
                buffer.write(chunk)
        new_attachment = models.CardAttachment(file_name=original_name, file_path=file_path, card_id=card_id)
        session.add(new_attachment)
        session.commit()
        session.refresh(new_attachment)
        return new_attachment
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        attachment = session.query(models.CardAttachment).filter(models.CardAttachment.id == attachment_id).first()
        if not attachment:
            raise HTTPException(status_code=404, detail="Файл не найден")
        if os.path.exists(attachment.file_path):
            os.remove(attachment.file_path)
        session.delete(attachment)
        session.commit()
        return {"detail": "Файл успешно удален"}
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/cards/{card_id}", response_model=schemas.CardResponse)
def update_card(card_id: int, card_update: schemas.CardUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if card_update.title is not None:
            card.title = card_update.title
            for tx in card.transactions:
                tx.company_name = card_update.title
        if card_update.total_amount is not None:
            card.total_amount = card_update.total_amount
            for tx in card.transactions:
                tx.amount = card_update.total_amount
        if 'store_location' in card_update.model_fields_set:
            card.store_location = card_update.store_location
            for tx in card.transactions:
                tx.store_location = card_update.store_location
        if 'description' in card_update.model_fields_set:
            card.description = card_update.description
        if 'client_id' in card_update.model_fields_set:
            card.client_id = card_update.client_id
        if 'due_date' in card_update.model_fields_set:
            card.due_date = card_update.due_date
        if 'priority' in card_update.model_fields_set:
            card.priority = card_update.priority
        if 'paid_amount' in card_update.model_fields_set:
            card.paid_amount = card_update.paid_amount
        if 'payment_status' in card_update.model_fields_set:
            card.payment_status = card_update.payment_status
        if 'payment_due_date' in card_update.model_fields_set:
            card.payment_due_date = card_update.payment_due_date
        if 'tag_ids' in card_update.model_fields_set:
            tags = session.query(models.Tag).filter(models.Tag.id.in_(card_update.tag_ids)).all()
            card.tags = tags
        session.commit()
        session.refresh(card)
        # Версионирование
        from versioning import save_version as _save_version
        _save_version(session, "cards", card.id,
            {"title": card.title, "status": card.status, "total_amount": float(card.total_amount or 0), "description": card.description, "priority": card.priority},
            user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
        # Webhook уведомление
        try:
            from routers.webhooks_router import notify_webhooks
            import asyncio
            asyncio.get_event_loop().create_task(notify_webhooks(
                current_user.tenant_id, "card.updated",
                {"id": card.id, "title": card.title, "status": card.status}
            ))
        except Exception: pass
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/cards/{card_id}/payment", response_model=schemas.CardResponse)
def update_card_payment(card_id: int, payload: schemas.CardPaymentUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        if payload.paid_amount is not None:
            card.paid_amount = max(0, payload.paid_amount)
        if payload.payment_due_date is not None:
            card.payment_due_date = payload.payment_due_date
        if payload.payment_status is not None:
            card.payment_status = payload.payment_status
        else:
            # автоопределение статуса по сумме
            total = float(card.total_amount or 0)
            paid = float(card.paid_amount or 0)
            if total <= 0:
                card.payment_status = "Не оплачен"
            elif paid >= total - 0.01:
                card.payment_status = "Оплачен"
            elif paid > 0.01:
                card.payment_status = "Частично"
            else:
                card.payment_status = "Не оплачен"

        session.commit()
        session.refresh(card)
        return card
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/files/{filename}")
def download_file(filename: str, current_user: models.User = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    file_path = os.path.realpath(os.path.join(UPLOAD_DIR, safe_name))
    if not file_path.startswith(os.path.realpath(UPLOAD_DIR)):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(file_path, filename=safe_name)
