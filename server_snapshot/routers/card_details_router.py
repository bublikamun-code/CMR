import os
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import models
import schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db
from limiter_config import limiter

router = APIRouter(
    tags=["Детали карточки (Чек-листы и Файлы)"],
    dependencies=[Depends(get_current_user)]
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Абсолютный путь к uploads: не зависит от CWD процесса.
# FIX 2026-08-29: каталог загрузок можно вынести из веб-корна —
# CRM_UPLOADS_DIR, иначе CRM_DATA_DIR/uploads, иначе uploads рядом с кодом.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.abspath(
    os.environ.get("CRM_UPLOADS_DIR")
    or os.path.join(os.environ.get("CRM_DATA_DIR") or _APP_DIR, "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================
# История изменений полей карточки (план 1.2)
# Лента активности — единственное место, где менеджер видит,
# кто и когда поменял сумму/клиента/дату. Без этих записей
# остаётся только догадываться, откуда взялись данные в сделке.
# ============================================================

def _fmt_money_log(value) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _fmt_date_log(value) -> str:
    if not value:
        return "—"
    parts = str(value)[:10].split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else str(value)


def _short_log(text: str, limit: int = 40) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _log_card_changes(session, card, current_user, changes: list) -> None:
    """Одна запись «Изменение» на PATCH со всеми полями, поменявшими значение."""
    if not changes:
        return
    session.add(models.ActivityLog(
        user_id=current_user.id,
        card_id=card.id,
        action="Изменение",
        details="; ".join(changes)[:2000],
        tenant_id=current_user.tenant_id,
    ))


def _card_field_changes(session, card, old: dict) -> list:
    """Сравнивает текущие значения карточки со снимком old, возвращает тексты изменений."""
    changes = []
    if (card.title or "") != (old.get("title") or ""):
        changes.append(f'Название: «{_short_log(old.get("title"))}» → «{_short_log(card.title)}»')
    if float(card.total_amount or 0) != float(old.get("total_amount") or 0):
        changes.append(f'Сумма: {_fmt_money_log(old.get("total_amount"))} → {_fmt_money_log(card.total_amount)} BYN')
    if (card.due_date or None) != (old.get("due_date") or None):
        changes.append(f'Дата окончания: {_fmt_date_log(old.get("due_date"))} → {_fmt_date_log(card.due_date)}')
    if (card.store_location or "") != (old.get("store_location") or ""):
        changes.append(f'Магазин: {old.get("store_location") or "—"} → {card.store_location or "—"}')
    if (card.client_id or None) != (old.get("client_id") or None):

        def _client_name(cid):
            if cid is None:
                return "—"
            rec = session.query(models.Client).filter(models.Client.id == cid).first()
            return _short_log(rec.name, 30) if rec else "—"

        changes.append(f'Клиент: {_client_name(old.get("client_id"))} → {_client_name(card.client_id)}')
    return changes


@router.post("/cards/{card_id}/checklists", response_model=schemas.ChecklistResponse)
def add_checklist_item(card_id: int, item: schemas.ChecklistCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
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

# FIX 2026-09-06 (аудит С3): загрузки — тяжёлые операции (стриминг до 25 МБ)
# и вектор записи мусора в uploads; лимит на оба upload-эндпоинта.
# За nginx должен быть включён proxy-headers, иначе лимит общий на всех.
@router.post("/checklists/{checklist_id}/invoice", response_model=schemas.ChecklistResponse)
@limiter.limit("30/minute")
def upload_checklist_invoice(request: Request, checklist_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        ALLOWED_EXTS = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt'}
        ext = os.path.splitext(file.filename or '')[1].lower()
        if ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Тип файла не разрешён: {ext}")
        _validate_upload_content(file, ext)
        if item.invoice_file_path and os.path.exists(item.invoice_file_path):
            os.remove(item.invoice_file_path)
        original_name = os.path.basename(file.filename or "schet")
        safe_filename = f"chk{checklist_id}_{uuid.uuid4().hex[:8]}_{original_name}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        # Defense-in-depth: сохраняем строго внутри UPLOAD_DIR (в имя файла
        # попадает исходное имя вложения — проверяем, что оно не вынесло нас
        # за пределы каталога).
        if not os.path.realpath(file_path).startswith(os.path.realpath(UPLOAD_DIR) + os.sep):
            raise HTTPException(status_code=400, detail="Недопустимое имя файла")
        size = 0
        with Path(file_path).open("wb") as buffer:
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

# UI FIX 2026-08-31 (аудит): проверка содержимого по magic bytes, а не только
# по расширению — переименованный .exe под видом .jpg раньше проходил.
_MAGIC_SIGNATURES = [
    (b'%PDF', ('.pdf',)),
    (b'\xff\xd8\xff', ('.jpg', '.jpeg')),
    (b'\x89PNG', ('.png',)),
    (b'GIF8', ('.gif',)),
    (b'PK\x03\x04', ('.docx', '.xlsx', '.odt', '.ods', '.zip')),
    (b'\xd0\xcf\x11\xe0', ('.doc', '.xls')),
    (b'Rar!', ('.rar',)),
]
# Текстовые форматы не проверяем: валидный csv/txt может начинаться с чего угодно
# (BOM, цифры, кавычки), а исполняемый файл под видом .txt браузер исполнять не умеет.
_NO_CHECK_EXTS = {'.csv', '.txt'}


def _validate_upload_content(file, ext: str):
    """Первые байты файла должны соответствовать расширению, иначе 400."""
    if ext in _NO_CHECK_EXTS:
        return
    head = file.file.read(16)
    file.file.seek(0)
    if not head:
        raise HTTPException(status_code=400, detail="Файл пустой")
    for sig, exts in _MAGIC_SIGNATURES:
        if head.startswith(sig):
            if ext in exts:
                file.file.seek(0)
                return
            raise HTTPException(status_code=400,
                detail=f"Содержимое файла не соответствует типу {ext}")
    if ext == '.webp' and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        file.file.seek(0)
        return
    raise HTTPException(status_code=400,
        detail=f"Содержимое файла не соответствует типу {ext}")


@router.post("/cards/{card_id}/attachments", response_model=schemas.AttachmentResponse)
@limiter.limit("30/minute")
def upload_file(request: Request, card_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        original_name = os.path.basename(file.filename or "file")
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Тип файла {ext} не разрешён")
        _validate_upload_content(file, ext)
        if not original_name:
            original_name = "file"
        safe_filename = f"{card_id}_{uuid.uuid4().hex[:8]}_{original_name}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        size = 0
        with Path(file_path).open("wb") as buffer:
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
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        # Снимок значений до мутаций — по нему строим запись «Изменение» в ленту
        _old = {
            "title": card.title,
            "total_amount": card.total_amount,
            "due_date": card.due_date,
            "store_location": card.store_location,
            "client_id": card.client_id,
        }
        if card_update.title is not None:
            card.title = card_update.title
            for tx in card.transactions:
                tx.company_name = card_update.title
        if card_update.total_amount is not None:
            card.total_amount = card_update.total_amount
            # FIX 2026-09-06 (аудит): раньше новая сумма сделки копировалась в
            # ВСЕ транзакции — правка суммы после выписки накладных затирала и
            # их суммы (реестр оплат расходился с напечатанными ТН).
            # Пересчитываем только запись-остаток: сумма сделки минус уже
            # выписанное (накладные/списания). Сами накладные не трогаем.
            ledger = [t for t in card.transactions if not t.is_document]
            issued_total = round(sum(float(t.amount or 0) for t in ledger
                                     if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
            new_rest = round(float(card_update.total_amount) - issued_total, 2)
            remainder = next((t for t in ledger
                              if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()), None)
            if remainder is not None:
                if new_rest <= 0.01:
                    session.delete(remainder)
                else:
                    remainder.amount = new_rest
            elif new_rest > 0.01:
                session.add(models.Transaction(
                    company_name=card.title, amount=new_rest,
                    store_location=card.store_location, card_id=card.id,
                ))
        if 'store_location' in card_update.model_fields_set:
            card.store_location = card_update.store_location
            for tx in card.transactions:
                tx.store_location = card_update.store_location
        if 'description' in card_update.model_fields_set:
            old_note = (card.description or '').strip()
            new_note = (card_update.description or '').strip()
            # Заметка — единственное поле, которое пользователь редактирует
            # ради самого текста, поэтому её изменения попадают в ленту
            # активности отдельной записью «Комментарий».
            if new_note != old_note and new_note:
                session.add(models.ActivityLog(
                    user_id=current_user.id,
                    card_id=card_id,
                    action="Комментарий",
                    details=new_note[:4000],
                    tenant_id=current_user.tenant_id
                ))
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
        _log_card_changes(session, card, current_user, _card_field_changes(session, card, _old))
        session.commit()
        session.refresh(card)
        # Версионирование
        from versioning import save_version as _save_version
        _save_version(session, "cards", card.id,
            {"title": card.title, "status": card.status, "total_amount": float(card.total_amount or 0), "description": card.description, "priority": card.priority},
            user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
        # Webhook уведомление
        try:
            from routers.webhooks_router import notify_webhooks_async
            notify_webhooks_async(current_user.tenant_id, "card.updated",
                {"id": card.id, "title": card.title, "status": card.status})
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
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        _old_pay = {
            "paid_amount": card.paid_amount,
            "payment_status": card.payment_status,
            "payment_due_date": card.payment_due_date,
        }
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

        _pay_changes = []
        if (card.payment_status or "") != (_old_pay.get("payment_status") or ""):
            _pay_changes.append(f'Оплата: {_old_pay.get("payment_status") or "—"} → {card.payment_status}')
        if float(card.paid_amount or 0) != float(_old_pay.get("paid_amount") or 0):
            _pay_changes.append(f'Оплачено: {_fmt_money_log(_old_pay.get("paid_amount"))} → {_fmt_money_log(card.paid_amount)} BYN')
        if (card.payment_due_date or None) != (_old_pay.get("payment_due_date") or None):
            _pay_changes.append(f'Отсрочка до: {_fmt_date_log(_old_pay.get("payment_due_date"))} → {_fmt_date_log(card.payment_due_date)}')
        _log_card_changes(session, card, current_user, _pay_changes)

        session.commit()
        session.refresh(card)
        return card
    finally:
        if tdb is not db:
            tdb.close()

# FIX 2026-09-06 (аудит): эндпоинт GET /files/{filename} удалён.
# Он отдавал любой файл из uploads любому авторизованному пользователю без
# проверки владельца/тенанта (IDOR), а фронтенд им уже не пользуется —
# скачивание идёт по id: /attachments/{id}/download и
# /checklists/{id}/invoice/download.


def _sanitize_download_name(name: str) -> str:
    """Чистит имя файла для заголовка Content-Disposition.

    Старые вложения, импортированные из почты, могли сохранить имя сырой
    MIME-строкой (=?UTF-8?B?...?= с переносами строк). CRLF внутри имени
    ломает установку заголовка (header injection), и скачивание падает 500.
    """
    if not name:
        return "attachment"
    if "=?" in name:
        try:
            from email.header import decode_header, make_header
            name = str(make_header(decode_header(name)))
        except Exception:
            pass
    name = re.sub(r"[\r\n\t]+", " ", name).strip()
    return name or "attachment"


def _resolve_file_path(stored_path: str) -> str:
    """Превращает путь из БД (относительный или абсолютный) в абсолютный путь
    внутри UPLOAD_DIR. Если файл не найден по указанному пути, ищем по basename."""
    if not stored_path:
        return ''
    # Сначала пробуем как записано в БД
    candidates = []
    if os.path.isabs(stored_path):
        candidates.append(stored_path)
    else:
        candidates.append(os.path.join(UPLOAD_DIR, stored_path))
        candidates.append(os.path.join(UPLOAD_DIR, os.path.basename(stored_path)))
    # Fallback: поиск по basename среди файлов в UPLOAD_DIR
    base = os.path.basename(stored_path)
    for fname in os.listdir(UPLOAD_DIR):
        if fname == base:
            candidates.append(os.path.join(UPLOAD_DIR, fname))
            break
    seen = set()
    for p in candidates:
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        if real.startswith(os.path.realpath(UPLOAD_DIR)) and os.path.isfile(real):
            return real
    return ''


@router.get("/attachments/{attachment_id}/download")
def download_attachment_by_id(attachment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        attachment = session.query(models.CardAttachment).filter(models.CardAttachment.id == attachment_id).first()
        if not attachment:
            raise HTTPException(status_code=404, detail="Вложение не найдено")
        file_path = _resolve_file_path(attachment.file_path)
        if not file_path:
            # Диагностика: путь из БД и папка, где искали (как в /files/)
            import logging
            logging.getLogger(__name__).warning(
                "Attachment not found: id=%r stored=%r upload_dir=%r",
                attachment_id, attachment.file_path, os.path.realpath(UPLOAD_DIR)
            )
            raise HTTPException(status_code=404, detail="Файл не найден на сервере")
        return FileResponse(file_path, filename=_sanitize_download_name(attachment.file_name or os.path.basename(file_path)))
    finally:
        if tdb is not db:
            tdb.close()


@router.get("/checklists/{checklist_id}/invoice/download")
def download_checklist_invoice_by_id(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        item = session.query(models.CardChecklist).filter(models.CardChecklist.id == checklist_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
        if not item.invoice_file_path:
            raise HTTPException(status_code=404, detail="Счёт не прикреплён")
        file_path = _resolve_file_path(item.invoice_file_path)
        if not file_path:
            # Диагностика: путь из БД и папка, где искали (как в /files/)
            import logging
            logging.getLogger(__name__).warning(
                "Checklist invoice not found: checklist_id=%r stored=%r upload_dir=%r",
                checklist_id, item.invoice_file_path, os.path.realpath(UPLOAD_DIR)
            )
            raise HTTPException(status_code=404, detail="Файл счёта не найден на сервере")
        return FileResponse(file_path, filename=item.invoice_file_name or os.path.basename(file_path))
    finally:
        if tdb is not db:
            tdb.close()
