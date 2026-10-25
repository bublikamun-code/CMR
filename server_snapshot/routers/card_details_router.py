import contextlib
import logging
import os
import re
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from constants import MONEY_EPSILON
from services.writeoffs import money_is_present, money_is_zero
from services.card_money import derive_payment_status
from database import get_db
from limiter_config import limiter
from services.sqlite_writer import reserve_model_rows, sqlite_write_transaction
from tenant_policy import (
    assign_tenant,
    get_tenant_child,
    get_tenant_object,
    tenant_predicate,
    tenant_query,
    writable_tenant_id,
)

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

logger = logging.getLogger(__name__)


def _uploads_root() -> str:
    """Реальный (symlink-развёрнутый) путь каталога загрузок."""
    return os.path.realpath(UPLOAD_DIR)


def _path_inside_uploads(file_path: str) -> bool:
    """Лежит ли путь ФИЗИЧЕСКИ внутри каталога загрузок.

    FIX 2026-09-26: os.sep на конце обязателен. Без него проверка
    ``real.startswith(realpath(UPLOAD_DIR))`` пропускала соседний каталог:
    ``/var/uploads_evil/файл`` начинается с ``/var/uploads``, поэтому файл
    из чужого каталога проходил как «свой» и отдавался на скачивание.
    """
    root = _uploads_root()
    real = os.path.realpath(file_path)
    return real.startswith(root + os.sep)


def _require_inside_uploads(file_path: str) -> None:
    """Проверка ДО записи: не создаём файл за пределами каталога загрузок."""
    if not _path_inside_uploads(file_path):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")


def _require_written_inside_uploads(file_path: str) -> None:
    """Проверка ПОСЛЕ записи: файл обязан лежать в каталоге и быть обычным.

    Симлинк внутри uploads (или подмена каталога) мог бы вывести запись за
    его пределы уже после открытия файла на запись — тогда остаётся только
    удалить то, что не должно было появиться.
    """
    if _path_inside_uploads(file_path) and os.path.isfile(file_path):
        return
    with contextlib.suppress(OSError):
        os.remove(file_path)
    raise HTTPException(status_code=400, detail="Недопустимое имя файла")


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


def _card_field_changes(session, card, old: dict, current_user) -> list:
    """Сравнивает текущие значения карточки со снимком old, возвращает тексты изменений."""
    changes = []
    if (card.title or "") != (old.get("title") or ""):
        changes.append(f'Название: «{_short_log(old.get("title"))}» → «{_short_log(card.title)}»')
    if float(card.total_amount or 0) != float(old.get("total_amount") or 0):
        changes.append(f'Сумма: {_fmt_money_log(old.get("total_amount"))} → {_fmt_money_log(card.total_amount)} BYN')
    if (card.payment_status or "") != (old.get("payment_status") or ""):
        changes.append(f'Оплата: {old.get("payment_status") or "—"} → {card.payment_status}')
    if (card.payment_due_date or None) != (old.get("payment_due_date") or None):
        changes.append(f'Отсрочка до: {_fmt_date_log(old.get("payment_due_date"))} → {_fmt_date_log(card.payment_due_date)}')
    if (card.due_date or None) != (old.get("due_date") or None):
        changes.append(f'Дата окончания: {_fmt_date_log(old.get("due_date"))} → {_fmt_date_log(card.due_date)}')
    if (card.store_location or "") != (old.get("store_location") or ""):
        changes.append(f'Магазин: {old.get("store_location") or "—"} → {card.store_location or "—"}')
    if (card.client_id or None) != (old.get("client_id") or None):

        def _client_name(cid):
            if cid is None:
                return "—"
            rec = tenant_query(session, models.Client, current_user).filter(
                models.Client.id == cid
            ).first()
            return _short_log(rec.name, 30) if rec else "—"

        changes.append(f'Клиент: {_client_name(old.get("client_id"))} → {_client_name(card.client_id)}')
    if (card.owner_id or None) != (old.get("owner_id") or None):
        # дефект 13: переназначение владельца — событие, которое должно быть
        # видно в ленте так же, как смена клиента или магазина.
        def _user_name(uid):
            if uid is None:
                return "—"
            rec = tenant_query(session, models.User, current_user).filter(
                models.User.id == uid
            ).first()
            return _short_log(rec.username, 30) if rec else "—"

        changes.append(f'Ответственный: {_user_name(old.get("owner_id"))} → {_user_name(card.owner_id)}')
    if (card.sender_email or None) != (old.get("sender_email") or None):
        # Адрес — ключ, по которому «История» подбирает письма сделки
        # (email_parser_router.related_emails): менеджер обязан видеть, что
        # ключ переставили, иначе пустой список писем выглядит как их отсутствие.
        changes.append(f'Почта отправителя: {old.get("sender_email") or "—"} → {card.sender_email or "—"}')
    return changes


@router.post("/cards/{card_id}/checklists", response_model=schemas.ChecklistResponse)
def add_checklist_item(card_id: int, item: schemas.ChecklistCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")

    data = item.model_dump()

    # Название берём из справочника поставщиков — оно ведущее.
    # Ручной ввод остаётся только для старых записей без supplier_id.
    if data.get("supplier_id"):
        sup = get_tenant_object(
            db,
            models.Supplier,
            data["supplier_id"],
            current_user,
            detail="Поставщик не найден",
        )
        if not sup:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        data["company_name"] = sup.name
    if not (data.get("company_name") or "").strip():
        raise HTTPException(status_code=400, detail="Укажите поставщика")

    new_item = models.CardChecklist(**data, card_id=card_id)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return new_item


@router.patch("/checklists/{checklist_id}", response_model=schemas.ChecklistResponse)
def update_checklist_item(checklist_id: int, item_update: schemas.ChecklistUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = get_tenant_child(
        db,
        models.CardChecklist,
        checklist_id,
        models.Card,
        "card_id",
        current_user,
        detail="Пункт чек-листа не найден",
        parent_detail="Карточка не найдена",
    )
    if not item:
        raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
    update_data = item_update.model_dump(exclude_unset=True)

    # Сменили поставщика — подтягиваем актуальное название из справочника
    if update_data.get("supplier_id"):
        sup = get_tenant_object(
            db,
            models.Supplier,
            update_data["supplier_id"],
            current_user,
            detail="Поставщик не найден",
        )
        if not sup:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        update_data["company_name"] = sup.name

    for key, value in update_data.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/checklists/{checklist_id}")
def delete_checklist_item(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = get_tenant_child(
        db,
        models.CardChecklist,
        checklist_id,
        models.Card,
        "card_id",
        current_user,
        detail="Пункт чек-листа не найден",
        parent_detail="Карточка не найдена",
    )
    if not item:
        raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
    if item.invoice_file_path and os.path.exists(item.invoice_file_path):
        os.remove(item.invoice_file_path)
    db.delete(item)
    db.commit()
    return {"detail": "Пункт успешно удален"}
# FIX 2026-09-06 (аудит С3): загрузки — тяжёлые операции (стриминг до 25 МБ)
# и вектор записи мусора в uploads; лимит на оба upload-эндпоинта.
# nginx должен входить в CRM_TRUSTED_PROXY_NETWORKS, иначе лимит общий на всех.


@router.post("/checklists/{checklist_id}/invoice", response_model=schemas.ChecklistResponse)


@limiter.limit("30/minute")
def upload_checklist_invoice(request: Request, checklist_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = get_tenant_child(
        db,
        models.CardChecklist,
        checklist_id,
        models.Card,
        "card_id",
        current_user,
        detail="Пункт чек-листа не найден",
        parent_detail="Карточка не найдена",
    )
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
    _require_inside_uploads(file_path)
    size = 0
    with Path(file_path).open("wb") as buffer:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                buffer.close()
                os.remove(file_path)
                raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")
            buffer.write(chunk)
    _require_written_inside_uploads(file_path)
    item.invoice_file_name = original_name
    item.invoice_file_path = file_path
    db.commit()
    db.refresh(item)
    return item


@router.delete("/checklists/{checklist_id}/invoice", response_model=schemas.ChecklistResponse)
def delete_checklist_invoice(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = get_tenant_child(
        db,
        models.CardChecklist,
        checklist_id,
        models.Card,
        "card_id",
        current_user,
        detail="Пункт чек-листа не найден",
        parent_detail="Карточка не найдена",
    )
    if not item:
        raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
    if item.invoice_file_path and os.path.exists(item.invoice_file_path):
        os.remove(item.invoice_file_path)
    item.invoice_file_name = None
    item.invoice_file_path = None
    db.commit()
    db.refresh(item)
    return item
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
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
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
    # FIX 2026-09-26: проверки реального пути здесь не было вообще — ни до
    # записи, ни после. Оба соседних загрузчика её имели, этот — нет.
    _require_inside_uploads(file_path)
    size = 0
    with Path(file_path).open("wb") as buffer:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                buffer.close()
                os.remove(file_path)
                raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")
            buffer.write(chunk)
    _require_written_inside_uploads(file_path)
    new_attachment = models.CardAttachment(file_name=original_name, file_path=file_path, card_id=card_id)
    db.add(new_attachment)
    db.commit()
    db.refresh(new_attachment)
    return new_attachment


# A10: v2-клиент шлёт multipart POST на /files/attach/{card_id} с полем file.
# Дубликат существующего /cards/{card_id}/attachments под контракт v2.
# Старый JSON-эндпоинт не тронут — старый фронт работает как раньше.
@router.post("/files/attach/{card_id}", response_model=schemas.AttachmentResponse)
@limiter.limit("30/minute")
def upload_file_v2_compat(request: Request, card_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Multipart-загрузка вложения (контракт v2-клиента).

    Полностью повторяет POST /cards/{card_id}/attachments: те же лимиты,
    расширения, magic-bytes проверка и запись в card_attachments.
    """
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
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
    _require_inside_uploads(file_path)
    size = 0
    with Path(file_path).open("wb") as buffer:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                buffer.close()
                os.remove(file_path)
                raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")
            buffer.write(chunk)
    _require_written_inside_uploads(file_path)
    new_attachment = models.CardAttachment(file_name=original_name, file_path=file_path, card_id=card_id)
    db.add(new_attachment)
    db.commit()
    db.refresh(new_attachment)
    return new_attachment


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    attachment = get_tenant_child(
        db,
        models.CardAttachment,
        attachment_id,
        models.Card,
        "card_id",
        current_user,
        detail="Файл не найден",
        parent_detail="Карточка не найдена",
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if os.path.exists(attachment.file_path):
        os.remove(attachment.file_path)
    db.delete(attachment)
    db.commit()
    return {"detail": "Файл успешно удален"}


@router.patch("/cards/{card_id}", response_model=schemas.CardResponse)
@router.patch("/cards/{card_id}", response_model=schemas.CardResponse)
def update_card(card_id: int, card_update: schemas.CardUpdate,
               db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    # FIX 2026-09-30 (аудит, Д-3): writer slot берётся ДО первого чтения.
    #
    # Раньше слот резервировался только на пути /payment (Д-4), а общий
    # PATCH /cards/{id} писал total_amount без него. Это один и тот же класс
    # гонки: total_amount входит в Σ сделок клиента, от которого считается
    # доступный баланс, а списание с баланса пишет в ту же таблицу. Два
    # одновременных PATCH читали одно и то же значение и оба проходили
    # проверку «не превысит ли сумма сделок доступное» — после чего баланс
    # уезжал в минус, и «мне не хватало денег» становилось неправдой.
    #
    # reserve_model_rows делает session.rollback() и короткий no-op UPDATE,
    # который переводит сессию в write-транзакцию и держит writer slot до
    # commit. Второй конкурентный запрос либо дождётся освобождения и прочтёт
    # уже устоявшиеся числа, либо получит управляемый 503 с Retry-After.
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    with sqlite_write_transaction(db):
        reserve_model_rows(
            db, models.Card, [card_id],
            not_found_detail="Карточка не найдена",
            busy_detail="Сделка занята другой операцией. Повторите попытку.",
        )
        # Слот взят — сброшены все кэшированные объекты сессии, поэтому
        # карточку нужно перечитать: старая копия осталась бы из снимка
        # ДО резервирования и содержала бы устаревшую сумму.
        db.expire_all()
        card = get_tenant_object(
            db, models.Card, card_id, current_user,
            detail="Карточка не найдена",
        )
        return _update_card_locked(db, card, card_update, current_user)


def _update_card_locked(db, card, card_update, current_user):
    # Карточка уже выбрана и залочена вызывающим: writer slot взят до чтения,
    # а строка перечитана после резервирования. Повторный get_tenant_object
    # здесь был бы лишним обращением к базе и вернул бы ту же строку —
    # раньше он был нужен только потому, что сам был первой строкой хендлера.
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    # Аудит 2026-09-26: общий PATCH карточки писал paid_amount и payment_status
    # напрямую, минуя PATCH /cards/{id}/payment — то есть мимоя проверки баланса
    # клиента и без writer slot. Поскольку баланс клиента выводится из
    # Σ paid_amount его карточек, этим обходилось списание: кредит уходил в
    # минус. Оба фронта (v2 и legacy) шлют оплату только в /payment — проверил
    # по tools/mockups/shell-v2-board.js и site/js/card_modal.js, — поэтому
    # запрет здесь ничего не ломает, а обход закрывает.
    if 'paid_amount' in card_update.model_fields_set or \
            'payment_status' in card_update.model_fields_set:
        raise HTTPException(
            status_code=400,
            detail="Оплату меняйте через PATCH /cards/{id}/payment",
        )
    # FIX 2026-09-12 (Фаза 2, дефект 13): owner_id вернулся в CardUpdate —
    # переназначить владельца сделки было нельзя, PATCH отвечал 200 и ничего
    # не менял. Проверка существования вынесена ДО мутаций: FK enforcement
    # включён (PRAGMA foreign_keys=ON в database.py), поэтому чужой id дал
    # бы IntegrityError и 500 в середине применения остальных полей.
    # None разрешён: у FK ondelete="SET NULL", а auth_router.remove_user
    # уже обнуляет owner_id у карточек удаляемого пользователя.
    if 'owner_id' in card_update.model_fields_set and card_update.owner_id is not None:
        owner = get_tenant_object(
            db,
            models.User,
            card_update.owner_id,
            current_user,
            detail=f"Пользователь #{card_update.owner_id} не найден",
        )
        if owner.tenant_id != writable_tenant_id(current_user):
            raise HTTPException(
                status_code=404,
                detail=f"Пользователь #{card_update.owner_id} не найден",
            )
        if not owner:
            raise HTTPException(
                status_code=400,
                detail=f"Пользователь #{card_update.owner_id} не найден")
    if (
        'client_id' in card_update.model_fields_set
        and card_update.client_id is not None
    ):
        client = get_tenant_object(
            db,
            models.Client,
            card_update.client_id,
            current_user,
            detail="Клиент не найден",
        )
        if client.tenant_id != writable_tenant_id(current_user):
            raise HTTPException(status_code=404, detail="Клиент не найден")
    if (
        'tag_ids' in card_update.model_fields_set
    ):
        tags = tenant_query(db, models.Tag, current_user).filter(
            models.Tag.id.in_(card_update.tag_ids)
        ).all()
        if len(tags) != len(set(card_update.tag_ids)):
            raise HTTPException(status_code=404, detail="Тег не найден")
    else:
        tags = None
    # FIX 2026-09-23 (пункт 5 плана v2): sender_email был в CardUpdate, но
    # роутер его не применял — PATCH отвечал 200, а адрес не менялся.
    # Формат здесь НЕ проверяется: фронт уже валидирует input[type=email], а
    # жёсткая серверная проверка отвергла бы исторические значения, которые
    # parser писал как есть. Ограничена только длина колонки: SQLite не следит
    # за VARCHAR(255), поэтому длиннее — молча легло бы в БД и пропало бы при
    # переносе на движок, который следит. Обрезать адрес по 255 символам —
    # значит сохранить заведомо чужой адрес, поэтому 400 без мутаций.
    _sender_email_max = models.Card.__table__.columns['sender_email'].type.length
    if ('sender_email' in card_update.model_fields_set
            and len((card_update.sender_email or '').strip()) > _sender_email_max):
        raise HTTPException(
            status_code=400,
            detail=f"Адрес отправителя длиннее {_sender_email_max} символов")
    # Снимок значений до мутаций — по нему строим запись «Изменение» в ленту
    _old = {
        "title": card.title,
        "total_amount": card.total_amount,
        "due_date": card.due_date,
        "store_location": card.store_location,
        "client_id": card.client_id,
        "owner_id": card.owner_id,
        "sender_email": card.sender_email,
        # Статус оплаты вычисляемый (решение владельца 24.10): смена суммы
        # сделки или даты отсрочки может его перещёлкнуть — фиксируем в
        # снимке, чтобы лента показала «Оплата: Частично → Оплачен».
        "payment_status": card.payment_status,
        "payment_due_date": card.payment_due_date,
    }
    if card_update.title is not None:
        card.title = card_update.title
        for tx in tenant_query(db, models.Transaction, current_user).filter(
            models.Transaction.card_id == card.id
        ).all():
            tx.company_name = card_update.title
    if card_update.total_amount is not None:
        card.total_amount = card_update.total_amount
        # FIX 2026-09-06 (аудит): раньше новая сумма сделки копировалась в
        # ВСЕ транзакции — правка суммы после выписки накладных затирала и
        # их суммы (реестр оплат расходился с напечатанными ТН).
        # Пересчитываем только запись-остаток: сумма сделки минус уже
        # выписанное (накладные/списания). Сами накладные не трогаем.
        ledger = [
            t for t in tenant_query(db, models.Transaction, current_user).filter(
                models.Transaction.card_id == card.id
            ).all() if not t.is_document
        ]
        issued_total = round(sum(float(t.amount or 0) for t in ledger
                                 if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
        # Д-4 (аудит 2026-10-05): clamp обязательный. Уменьшение суммы сделки
        # ниже уже выписанного (накладные + складские списания) давало
        # отрицательный остаток: money_is_zero(-500) = False, минус попадал
        # в remainder.amount, сделка уезжала в «На списание» и не возвращалась.
        new_rest = max(0.0, round(float(card_update.total_amount) - issued_total, 2))
        remainder = next((t for t in ledger
                          if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()), None)
        if remainder is not None:
            if money_is_zero(new_rest):
                db.delete(remainder)
            else:
                remainder.amount = new_rest
        elif money_is_present(new_rest) and card.status in ("Сборка", "На списание", "Закрыто"):
            # Фикс аудита 10.09: остаток = «сделка в реестре оплат»
            # (инвариант: запись появляется при входе в «Сборку»).
            # Правка суммы в «Новом запросе»/«В работе» раньше сразу
            # протаскивала сделку в реестр. Пересчитать существующий
            # остаток можно в любом статусе, создавать — только с «Сборки».
            db.add(assign_tenant(models.Transaction(
                company_name=card.title, amount=new_rest,
                store_location=card.store_location, card_id=card.id,
            ), current_user))
        # Правка суммы создаёт/убирает остаток к выписке — статус
        # выравнивается по тому же правилу, что и везде (иначе сделка с
        # появившимся остатком застревала в «Закрыто» и пропадала с доски
        # списания). «Сборку» не трогаем: там сделка по решению менеджера.
        if card.status in ("На списание", "Закрыто"):
            from services.writeoffs import writeoff_status_for
            card.status = writeoff_status_for(card, ledger)
        # FIX 2026-09-12 (Фаза 2, дефект 9): writeoff_groups.total_amount —
        # агрегат сумм входящих в группу сделок, но пересчитывался он только
        # в эндпоинтах самой группы (создание, добавление/вывод карточки,
        # выписка накладной по группе). Правка суммы сделки через
        # PATCH /cards/{id} группу не трогала, и итог группы расходился
        # с суммой её карточек: на доске списания и в акте висело старое
        # число. Функция пересчёта переиспользуется из writeoff_groups_router,
        # второй копии формулы здесь появиться не должно.
        if card.writeoff_group_id is not None:
            from services.writeoffs import recompute_group_total
            group = tenant_query(db, models.WriteoffGroup, current_user).filter(
                models.WriteoffGroup.id == card.writeoff_group_id
            ).first()
            if group is not None:
                # autoflush=False: фиксируем новую сумму сделки, чтобы
                # group.cards считался от актуальных значений
                db.flush()
                recompute_group_total(group)
    if 'store_location' in card_update.model_fields_set:
        # Н4 (решение владельца, 06.09): правило «склад сделки единый» —
        # СОЗНАТЕЛЬНОЕ. Смена склада сделки синхронно обновляет склад у
        # всех её транзакций, включая выписанные накладные: сделка
        # ведётся с одного склада. Вопросы оплат от склада не зависят.
        card.store_location = card_update.store_location
        for tx in tenant_query(db, models.Transaction, current_user).filter(
            models.Transaction.card_id == card.id
        ).all():
            tx.store_location = card_update.store_location
    if 'description' in card_update.model_fields_set:
        old_note = (card.description or '').strip()
        new_note = (card_update.description or '').strip()
        # Заметка — единственное поле, которое пользователь редактирует
        # ради самого текста, поэтому её изменения попадают в ленту
        # активности отдельной записью «Комментарий».
        if new_note != old_note and new_note:
            db.add(models.ActivityLog(
                user_id=current_user.id,
                card_id=card.id,
                action="Комментарий",
                details=new_note[:4000],
                tenant_id=current_user.tenant_id
            ))
        card.description = card_update.description
    # A7: защита от случайного затирания клиента при полном сохранении v2.
    # client_id=null принимается только если:
    #   (а) старый фронт: поле единственное в запросе (model_fields_set == 1);
    #   (б) v2: явный флаг clear_client=true.
    # В остальных случаях null игнорируется — клиент не затирается.
    if 'client_id' in card_update.model_fields_set:
        new_cid = card_update.client_id
        if new_cid is None:
            is_only_field = len(card_update.model_fields_set) == 1
            explicit_clear = bool(getattr(card_update, 'clear_client', False))
            if is_only_field or explicit_clear:
                card.client_id = None
            # иначе — null из полного сохранения v2, игнорируем
        else:
            card.client_id = new_cid
    if 'owner_id' in card_update.model_fields_set:
        card.owner_id = card_update.owner_id   # проверен выше, до мутаций
    if 'due_date' in card_update.model_fields_set:
        card.due_date = card_update.due_date
    if 'priority' in card_update.model_fields_set:
        card.priority = card_update.priority
    if 'payment_due_date' in card_update.model_fields_set:
        card.payment_due_date = card_update.payment_due_date
    if 'payment_terms' in card_update.model_fields_set:
        # Условие оплаты (Этап 2.4 плана замены фронта) — не факт оплаты:
        # paid_amount/payment_status этим полем не меняются.
        card.payment_terms = card_update.payment_terms
    if 'sender_email' in card_update.model_fields_set:
        # Пустая строка и строка из пробелов = «адреса нет»: колонка nullable,
        # и NULL (адрес не указан) должен быть отличим от ''.
        card.sender_email = (card_update.sender_email or '').strip() or None
    if 'tag_ids' in card_update.model_fields_set:
        card.tags = tags
    # Статус оплаты вычисляемый (решение владельца 24.10): правка суммы
    # сделки или даты отсрочки перещёлкивает его вместе с деньгами.
    if 'total_amount' in card_update.model_fields_set or \
            'payment_due_date' in card_update.model_fields_set:
        card.payment_status = derive_payment_status(
            card.total_amount, card.paid_amount, card.payment_due_date
        )
    _log_card_changes(
        db, card, current_user, _card_field_changes(db, card, _old, current_user)
    )
    db.commit()
    db.refresh(card)
    # Версионирование
    from versioning import save_version as _save_version
    _save_version(db, "cards", card.id,
        {"title": card.title, "status": card.status, "total_amount": float(card.total_amount or 0), "description": card.description, "priority": card.priority},
        user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
    # Webhook уведомление
    with contextlib.suppress(Exception):
        from routers.webhooks_router import notify_webhooks_async
        notify_webhooks_async(current_user.tenant_id, "card.updated",
            {"id": card.id, "title": card.title, "status": card.status})
    return card


def _reserve_balance_write(session, card_id: int, client_id: int) -> None:
    """Захватить писательский слот SQLite до чтения баланса клиента.

    Тот же механизм, что у пути списания/выписки
    (``services.payments._reserve_card_write`` → ``reserve_model_rows``):
    короткий no-op UPDATE переводит сессию в write-транзакцию и удерживает
    writer slot до commit. Второй конкурентный запрос либо дожидается
    освобождения слота и читает уже устоявшийся баланс, либо получает
    управляемый 503 — но не «успевает» списать по устаревшему остатку.
    """
    reserve_model_rows(
        session,
        models.Client,
        [client_id],
        not_found_detail="Клиент не найден",
        busy_detail="Баланс клиента занят другой операцией. Повторите попытку.",
    )
    reserve_model_rows(
        session,
        models.Card,
        [card_id],
        not_found_detail="Карточка не найдена",
        busy_detail="Сделка занята другой операцией. Повторите попытку.",
        reset_session=False,
    )


def _sync_card_allocations(db, card, old_paid: float, new_paid: float, source: str,
                           current_user, payment_id=None) -> None:
    """Волна C (24.10): держит инвариант card.paid_amount == Σ активных привязок.

    Рост покрытия → новая привязка на разницу; снижение → аннулирование с
    конца (последние созданные первые, последняя — частично), кассу не
    двигает. Аннулированная часть сохраняется отдельной строкой со статусом
    annulled — история «откуда взялось и что отменили» не теряется.
    """
    diff = round(float(new_paid) - float(old_paid), 2)
    if abs(diff) <= 0.005:
        return
    if diff > 0:
        db.add(models.ClientPaymentAllocation(
            client_id=card.client_id,
            card_id=card.id,
            payment_id=payment_id,
            amount=diff,
            source=source,
            status="active",
            created_by=current_user.id,
            tenant_id=current_user.tenant_id,
        ))
        return
    remaining = -diff
    allocs = (
        db.query(models.ClientPaymentAllocation)
        .filter(
            models.ClientPaymentAllocation.card_id == card.id,
            models.ClientPaymentAllocation.status == "active",
            tenant_predicate(models.ClientPaymentAllocation, current_user),
        )
        .order_by(models.ClientPaymentAllocation.id.desc())
        .all()
    )
    for a in allocs:
        if remaining <= 0.005:
            break
        part = min(float(a.amount or 0), remaining)
        a.amount = round(float(a.amount or 0) - part, 2)
        db.add(models.ClientPaymentAllocation(
            client_id=a.client_id,
            card_id=card.id,
            payment_id=a.payment_id,
            amount=part,
            source=a.source,
            status="annulled",
            note="отмена покрытия счёта",
            created_by=current_user.id,
            tenant_id=current_user.tenant_id,
        ))
        remaining = round(remaining - part, 2)


@router.patch("/cards/{card_id}/payment", response_model=schemas.CardResponse)
def update_card_payment(card_id: int, payload: schemas.CardPaymentUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if payload.from_balance and card.client_id is None:
        raise HTTPException(status_code=400,
            detail="Закрытие из баланса доступно только для сделок с клиентом")

    # FIX 2026-09-26 (денежный дефект): writer slot берётся ДО чтения
    # баланса. Раньше проверка была обычным SELECT, поэтому два параллельных
    # PATCH с from_balance оба видели один и тот же остаток и оба
    # увеличивали Σ paid_amount — кредит клиента уходил в минус, и
    # _client_balance_payload потом показывал отрицательный остаток.
    # Резервируем те же строки, что и путь выписки накладной
    # (services.payments._reserve_card_write): клиента — потому что кредит
    # общий на все его сделки, карточку — потому что её мы и правим.
    # sqlite_write_transaction сверху превращает ожидаемую конкуренцию в
    # 503 с Retry-After, а любую другую ошибку — в откат без полузаписанной
    # карточки.
    with sqlite_write_transaction(db):
        if payload.from_balance:
            _reserve_balance_write(db, card_id, card.client_id)
            # reserve_model_rows завершил read-транзацию сессии откатом,
            # поэтому карточка expired — перечитываем её по свежим данным.
            db.expire_all()
            card = get_tenant_object(
                db, models.Card, card_id, current_user, detail="Карточка не найдена"
            )
            if card.client_id is None:
                raise HTTPException(status_code=400,
                    detail="Закрытие из баланса доступно только для сделок с клиентом")

        _old_pay = {
            "paid_amount": card.paid_amount,
            "payment_status": card.payment_status,
            "payment_due_date": card.payment_due_date,
        }
        # V1 (аудит прода 19.09): закрытие из баланса тратит кредит клиента —
        # деньги, реально внесённые в кассу (client_payments) и ещё не
        # потреблённые покрытием счетов (Σ paid_amount неудалённых карточек).
        # Без проверки касса-минус молча уходила в минус. Кассу закрытие из
        # баланса не двигает вовсе: paid_amount — покрытие счёта, не приход.
        # Проверка ДО мутаций: отказ не должен оставлять в сессии
        # полуизменённую карточку.
        draw = 0.0  # сколько списывается из баланса этой правкой (для журнала)
        if payload.from_balance:
            new_paid = float(card.paid_amount or 0) if payload.paid_amount is None else max(0.0, payload.paid_amount)
            draw = round(new_paid - float(card.paid_amount or 0), 2)
            if draw > MONEY_EPSILON:
                # Ленивый импорт: clients_router не зависит от card_details_router,
                # цикла нет; формула баланса одна — здесь её дублировать нельзя.
                from routers.clients_router import _client_balance_payload
                bal = _client_balance_payload(db, card.client_id, current_user)
                # FIX 2026-09-30 (аудит, С-4): берём готовое поле available, а
                # не складываем формулу заново. Раньше здесь стояло
                # payments_total - paid_on_cards — математически то же, но
                # вторая копия формулы и есть тот самый дефект: интерфейс в этот
                # момент считал по balance (payments_total - deals_total) и прятал
                # чекбокс «закрыть из баланса», то есть человек с реальным
                # авансом не мог закрыть сделку. Теперь обе стороны читают одно
                # и то же поле одного и того же расчёта.
                available = bal["available"]
                # FIX 2026-09-30 (аудит денег): обе величины округлены до копеек,
                # поэтому допуск на границе небаланса не нужен. На проверке выше
                # (draw > MONEY_EPSILON) он остаётся — там он отвечает на другой
                # вопрос: «изменение значимое или это хвост», а не «хватает ли денег».
                if draw > available:
                    raise HTTPException(status_code=400,
                        detail=f"Баланса клиента не хватает: доступно {available} BYN, требуется {draw} BYN")
        # Д-1 (аудит 2026-10-05, решение владельца 24.10): покрытие счёта не
        # может превышать сам счёт — переплата живёт свободным остатком
        # клиента, а не статусом «Оплачен» сверх суммы. Раньше paid_amount
        # писался как есть, и сделку можно было «оплатить» без копейки в кассе.
        effective_paid = float(card.paid_amount or 0) if payload.paid_amount is None \
            else max(0.0, payload.paid_amount)
        _total = float(card.total_amount or 0)
        if _total <= 0.005 and effective_paid > 0.005:
            raise HTTPException(status_code=400,
                detail="У сделки не задана сумма — укажите сумму сделки, прежде чем вносить оплату")
        if effective_paid > _total + 0.005:
            raise HTTPException(
                status_code=400,
                detail=(f"Оплата {_fmt_money_log(effective_paid)} BYN превышает счёт "
                        f"{_fmt_money_log(_total)} BYN. Лишнее внесите авансом на баланс "
                        f"клиента — оно зачтётся при доборе"))
        if payload.paid_amount is not None:
            card.paid_amount = max(0, payload.paid_amount)
        # V2 (аудит прода 19.09): явный null стирает дату отсрочки — оба фронта
        # шлют payment_due_date: null при статусе «Не оплачен». Поле не пришло
        # в запросе — дату не трогаем (та же логика model_fields_set, что
        # у client_id в PATCH /cards/{id}).
        if 'payment_due_date' in payload.model_fields_set:
            card.payment_due_date = payload.payment_due_date
        # Решение владельца 24.10: статус оплаты — ВЫЧИСЛЯЕМОЕ поле, ручной
        # выбор «Оплачен/Частично/Не оплачен» без денег — пустая операция и
        # убран из контракта (поле исчезло из CardPaymentUpdate; присланное
        # старым клиентом значение молча игнорируется). Формула одна —
        # services.card_money.derive_payment_status, включая «Отсрочку»:
        # счёт открыт и указана дата, до которой ждём остаток.
        card.payment_status = derive_payment_status(
            card.total_amount, card.paid_amount, card.payment_due_date
        )
        # Волна C (24.10): покрытие материализовано привязками — инвариант
        # card.paid_amount == Σ активных привязок карточки.
        _sync_card_allocations(
            db, card,
            old_paid=float(_old_pay.get("paid_amount") or 0),
            new_paid=float(card.paid_amount or 0),
            source="balance" if payload.from_balance else "payment",
            current_user=current_user,
        )

        _pay_changes = []
        if (card.payment_status or "") != (_old_pay.get("payment_status") or ""):
            _pay_changes.append(f'Оплата: {_old_pay.get("payment_status") or "—"} → {card.payment_status}')
        if float(card.paid_amount or 0) != float(_old_pay.get("paid_amount") or 0):
            _pay_changes.append(f'Оплачено: {_fmt_money_log(_old_pay.get("paid_amount"))} → {_fmt_money_log(card.paid_amount)} BYN')
        if (card.payment_due_date or None) != (_old_pay.get("payment_due_date") or None):
            _pay_changes.append(f'Отсрочка до: {_fmt_date_log(_old_pay.get("payment_due_date"))} → {_fmt_date_log(card.payment_due_date)}')
        # «Закрыто из баланса…» — часть той же записи «Изменение»: после
        # _log_card_changes дописывать бессмысленно, в ленту уже не попадёт.
        if draw > MONEY_EPSILON:
            _pay_changes.append(f'Закрыто из баланса клиента на {_fmt_money_log(draw)} BYN')
        _log_card_changes(db, card, current_user, _pay_changes)

        db.commit()
        db.refresh(card)
        return card


@router.post("/cards/{card_id}/pay", response_model=schemas.CardResponse)
def pay_card(card_id: int, payload: schemas.CardPayRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Волна C (24.10): атомарная оплата карточки — один запрос вместо двух.

    source='cash': приход в кассу клиента + привязка к этой карточке +
    рост покрытия — одной транзакцией (раньше фронт делал PATCH оплаты и
    POST кассы отдельно, второй мог потеряться, и деньги расходились с
    системой). Оплата из карточки без клиента невозможна: некому записать
    приход — привяжите клиента.

    source='balance': закрытие счёта свободным остатком клиента — кассу
    не двигает, создаёт привязку source='balance' (то, что раньше делал
    чекбокс from_balance в PATCH).

    Гейты: сумма ≤ остаток счёта (переплата на карточку запрещена — лишнее
    вносится авансом, решение владельца); balance-ветка — сумма ≤ available.
    """
    import datetime as _dt

    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.client_id is None:
        raise HTTPException(status_code=400,
            detail="Оплата доступна для сделок с клиентом: некому записать деньги")
    with sqlite_write_transaction(db):
        _reserve_balance_write(db, card_id, card.client_id)
        db.expire_all()
        card = get_tenant_object(
            db, models.Card, card_id, current_user, detail="Карточка не найдена"
        )
        if card.client_id is None:
            raise HTTPException(status_code=400,
                detail="Оплата доступна для сделок с клиентом")

        old_paid = float(card.paid_amount or 0)
        total = float(card.total_amount or 0)
        if total <= 0.005:
            raise HTTPException(status_code=400,
                detail="У сделки не задана сумма — укажите сумму сделки")
        amount = round(float(payload.amount), 2)
        rest = round(total - old_paid, 2)
        if amount > rest + 0.005:
            raise HTTPException(
                status_code=400,
                detail=(f"Оплата {_fmt_money_log(amount)} BYN превышает остаток счёта "
                        f"{_fmt_money_log(rest)} BYN. Лишнее внесите авансом на баланс "
                        f"клиента — оно зачтётся при доборе"))

        payment = None
        if payload.source == "cash":
            paid_at = (
                _dt.datetime.combine(payload.paid_at, _dt.time.min)
                if payload.paid_at is not None else None
            )
            payment = models.ClientPayment(
                client_id=card.client_id,
                card_id=card.id,
                amount=amount,
                kind="deposit",
                method=payload.method,
                paid_at=paid_at,
                note=(payload.note or "").strip() or None,
                created_by=current_user.id,
                tenant_id=current_user.tenant_id,
            )
            db.add(payment)
            db.flush()
        else:
            from routers.clients_router import _client_balance_payload
            bal = _client_balance_payload(db, card.client_id, current_user)
            if amount > bal["available"] + 0.005:
                raise HTTPException(
                    status_code=400,
                    detail=(f"Баланса клиента не хватает: доступно {bal['available']} BYN, "
                            f"требуется {_fmt_money_log(amount)} BYN"))

        db.add(models.ClientPaymentAllocation(
            client_id=card.client_id,
            card_id=card.id,
            payment_id=payment.id if payment is not None else None,
            amount=amount,
            source=payload.source,
            status="active",
            note=(payload.note or "").strip() or None,
            created_by=current_user.id,
            tenant_id=current_user.tenant_id,
        ))
        if payment is not None:
            db.add(models.ActivityLog(
                user_id=current_user.id,
                action="Приход денег",
                details=(f"Клиент #{card.client_id}: +{amount} BYN по сделке #{card.id}"
                         + (f" ({payload.method})" if payload.method else "")),
                tenant_id=current_user.tenant_id,
            ))

        old_status = card.payment_status
        card.paid_amount = round(old_paid + amount, 2)
        card.payment_status = derive_payment_status(
            card.total_amount, card.paid_amount, card.payment_due_date
        )
        _log_card_changes(db, card, current_user, [
            f'Оплачено: {_fmt_money_log(old_paid)} → {_fmt_money_log(card.paid_amount)} BYN',
            ('Закрыто из баланса клиента на ' if payload.source == "balance"
             else 'Оплата деньгами на ') + f'{_fmt_money_log(amount)} BYN',
        ] + ([f'Оплата: {old_status or "—"} → {card.payment_status}']
             if (old_status or "") != card.payment_status else []))

        db.commit()
        db.refresh(card)
        return card

# FIX 2026-09-06 (аудит): эндпоинт GET /files/{filename} удалён.
# Он отдавал любой файл из uploads любому авторизованному пользователю без
# проверки владельца/тенанта (IDOR), а фронтенд им уже не пользуется —
# скачивание идёт по id: /attachments/{id}/download и
# /checklists/{id}/invoice/download.


_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|]')
# Браузер и файловая система всё равно не примут длинное имя; обрезаем здесь,
# чтобы не упереться в лимит заголовка (RFC 6266) на не-ASCII именах.
MAX_DOWNLOAD_NAME_LEN = 120


def _sanitize_download_name(name: str) -> str:
    """Чистит имя файла для заголовка Content-Disposition.

    Старые вложения, импортированные из почты, могли сохранить имя сырой
    MIME-строкой (=?UTF-8?B?...?= с перевонами строк). CRLF внутри имени
    ломает установку заголовка (header injection), и скачивание падает 500.

    FIX 2026-09-26: кроме переводов строк убираем путь (имя могло прийти
    как ``../../etc/passwd`` или с чужой Windows-разделителем), кавычки и
    прочие управляющие/служебные символы — иначе они либо ломают заголовок,
    либо подставляют в него произвольное значение filename.
    """
    if not name:
        return "attachment"
    if "=?" in name:
        with contextlib.suppress(Exception):
            from email.header import decode_header, make_header
            name = str(make_header(decode_header(name)))
    # Сначала разделители пути (в т.ч. и обратный слэш), потом — управляющие
    # символы: после basename в имени может остаться хвост вида "a\rb".
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME_CHARS.sub("_", name).strip(" .")
    name = name[:MAX_DOWNLOAD_NAME_LEN].strip(" .")
    return name or "attachment"


def _content_disposition(filename: str) -> str:
    """Собрать Content-Disposition с ASCII-запасным именем и RFC 5987.

    Значения заголовков Starlette кодирует в latin-1, поэтому не-ASCII имя
    в filename= роняло ответ 500 ещё на этапе сборки заголовка. Имя
    уходит в ``filename*=UTF-8''…`` (RFC 5987), а в кавычках — безопасная
    ASCII-транслитерация, которую понимают старые клиенты.
    """
    ascii_fallback = unicodedata.normalize("NFKD", filename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_fallback = re.sub(r"\s+", "_", ascii_fallback).strip("._") or "attachment"
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _file_download_response(file_path: str, stored_name: str) -> FileResponse:
    """Ответ со скачиванием: безопасное имя + корректный Content-Disposition."""
    safe_name = _sanitize_download_name(stored_name or os.path.basename(file_path))
    return FileResponse(
        file_path,
        filename=safe_name,
        headers={"Content-Disposition": _content_disposition(safe_name)},
    )


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
        # FIX 2026-09-26: сравнение с os.sep на конце. Без него соседний
        # каталог с тем же префиксом (uploads_evil, uploads.bak) проходил
        # проверку как свой, и файл оттуда отдавался на скачивание.
        if _path_inside_uploads(real) and os.path.isfile(real):
            return real
    return ''


@router.get("/attachments/{attachment_id}/download")
def download_attachment_by_id(attachment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    attachment = get_tenant_child(
        db,
        models.CardAttachment,
        attachment_id,
        models.Card,
        "card_id",
        current_user,
        detail="Файл не найден",
        parent_detail="Карточка не найдена",
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    file_path = _resolve_file_path(attachment.file_path)
    if not file_path:
        # Диагностика: путь из БД и папка, где искали (как в /files/)
        logger.warning(
            "Attachment not found: id=%r stored=%r upload_dir=%r",
            attachment_id, attachment.file_path, _uploads_root()
        )
        raise HTTPException(status_code=404, detail="Файл не найден на сервере")
    return _file_download_response(
        file_path, attachment.file_name or os.path.basename(file_path)
    )


@router.get("/checklists/{checklist_id}/invoice/download")
def download_checklist_invoice_by_id(checklist_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = get_tenant_child(
        db,
        models.CardChecklist,
        checklist_id,
        models.Card,
        "card_id",
        current_user,
        detail="Пункт чек-листа не найден",
        parent_detail="Карточка не найдена",
    )
    if not item:
        raise HTTPException(status_code=404, detail="Пункт чек-листа не найден")
    if not item.invoice_file_path:
        raise HTTPException(status_code=404, detail="Счёт не прикреплён")
    file_path = _resolve_file_path(item.invoice_file_path)
    if not file_path:
        # Диагностика: путь из БД и папка, где искали (как в /files/)
        logger.warning(
            "Checklist invoice not found: checklist_id=%r stored=%r upload_dir=%r",
            checklist_id, item.invoice_file_path, _uploads_root()
        )
        raise HTTPException(status_code=404, detail="Файл счёта не найден на сервере")
    # FIX 2026-09-26: имя счёта уходило в ответ сырым — с не-ASCII символами
    # ответ падал 500 на кодировке заголовка, с кавычками и переводами
    # строк — позволял подменить filename в Content-Disposition.
    return _file_download_response(
        file_path, item.invoice_file_name or os.path.basename(file_path)
    )
