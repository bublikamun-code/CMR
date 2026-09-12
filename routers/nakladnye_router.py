import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user
from database import get_db

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.abspath(
    os.environ.get("CRM_UPLOADS_DIR")
    or os.path.join(os.environ.get("CRM_DATA_DIR") or _APP_DIR, "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

# Токен бота: совпадает с TELEGRAM_BOT_TOKEN в окружении.
# Эндпоинты с bot_auth не требуют JWT — бот не пользователь CRM.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

router = APIRouter(
    prefix="/nakladnye",
    tags=["Накладные"],
)


def _bot_auth(request: Request) -> None:
    token = request.headers.get("X-Bot-Token") or request.query_params.get("bot_token")
    if not BOT_TOKEN or token != BOT_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bot token")
    return None


def _parse_products(n) -> list:
    if not n.products_json:
        return []
    try:
        return json.loads(n.products_json)
    except (json.JSONDecodeError, TypeError):
        return []


def _ensure_amounts_consistent(amount, vat_amount):
    """FIX 2026-09-12 (Фаза 2, дефект 11): НДС не может превышать сумму с НДС.

    Схема NakladnayaUpdate проверяет пару только когда оба значения присланы
    вместе. Частичная правка одного поля (vat_amount=500 при сохранённом
    amount=100) схеме неподвластна: второе значение лежит в базе. Поэтому
    здесь сравниваются ЭФФЕКТИВНЫЕ значения — присланное поверх сохранённого.

    Отказ 400, а не 422: это проверка бизнес-правила по данным из базы,
    а не ошибка формата запроса.
    """
    if amount is None or vat_amount is None:
        return
    if round(float(amount), 2) < round(float(vat_amount), 2):
        raise HTTPException(
            status_code=400,
            detail=(f"НДС ({round(float(vat_amount), 2):.2f}) не может превышать "
                    f"сумму документа с НДС ({round(float(amount), 2):.2f})"),
        )


def _effective_amounts(nak: models.Nakladnaya, update_data: dict):
    """Сумма и НДС, которые получатся после применения update_data к nak."""
    amount = update_data.get("amount", nak.amount)
    vat = update_data.get("vat_amount", nak.vat_amount)
    return amount, vat


def _find_nakladnaya_by_doc_key(session, doc_series, doc_number):
    """Существующая накладная с тем же нормализованным ключом документа."""
    series, number = models.nakladnaya_doc_key(doc_series, doc_number)
    if not number:
        return None
    return session.query(models.Nakladnaya).filter(
        models.Nakladnaya.doc_series_norm == series,
        models.Nakladnaya.doc_number_norm == number,
    ).first()


def _commit_with_doc_key_guard(session, doc_series, doc_number):
    """FIX 2026-09-12 (Фаза 2, дефект 7): commit с переводом дубля в 400.

    Уникальность обеспечивает частичный индекс uq_nakladnye_doc_key
    (миграция 0005), поэтому проверка АТОМАРНА: гонка check-then-insert,
    из-за которой параллельная отправка одного документа плодила две записи,
    закрыта на уровне базы, а не ещё одним SELECT перед INSERT.

    Здесь IntegrityError превращается в понятный ответ вместо 500. Значения
    ключа передаются аргументами, а не читаются с объекта: после rollback
    объект разобран, и обращение к его атрибутам подняло бы новую ошибку.

    Если нарушение НЕ про уникальный ключ документа (например, битый FK),
    ошибка пробрасывается дальше: объявить её дублем значило бы соврать
    пользователю и увести разбор в сторону.
    """
    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        existing = _find_nakladnaya_by_doc_key(session, doc_series, doc_number)
        if existing is None:
            raise
        series, number = models.nakladnaya_doc_key(doc_series, doc_number)
        raise HTTPException(
            status_code=400,
            detail=(f"Накладная с серией «{series}» и номером {number} уже принята "
                    f"(id={existing.id}). Повторно тот же документ не создаётся — "
                    f"если это повторная отгрузка, откройте существующую запись."),
        ) from e


def _nak_dict(n: models.Nakladnaya) -> dict:
    photos = []
    if n.photo_paths:
        try:
            photos = json.loads(n.photo_paths)
        except (json.JSONDecodeError, TypeError):
            photos = []
    return {
        "id": n.id,
        "supplier_id": n.supplier_id,
        "supplier_name": n.supplier_name or "",
        "doc_type": n.doc_type,
        "doc_series": n.doc_series,
        "doc_number": n.doc_number,
        "doc_date": n.doc_date,
        "amount": float(n.amount) if n.amount is not None else None,
        "vat_amount": float(n.vat_amount) if n.vat_amount is not None else None,
        # дефект 14: поле доступно на запись, поэтому обязано читаться обратно
        "amount_no_vat": float(n.amount_no_vat) if n.amount_no_vat is not None else None,
        "unload_address": n.unload_address,
        "store": n.store,
        "is_verified": bool(n.is_verified),
        "is_arrived": bool(n.is_arrived) if n.is_arrived is not None else False,
        "is_paid": bool(n.is_paid),
        "status": n.status or "new",
        "photo_paths": photos,
        "products": _parse_products(n),
        "excel_path": n.excel_path,
        "created_by_bot": bool(n.created_by_bot),
        "created_at": n.created_at,
        "supplier": {"id": n.supplier.id, "name": n.supplier.name} if n.supplier else None,
    }


# ============================================================
# CRM endpoints (JWT auth)
# ============================================================


@router.get("", dependencies=[Depends(get_current_user)])
def list_nakladnye(
    store: Optional[str] = None,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        query = db.query(models.Nakladnaya)

        if store:
            query = query.filter(models.Nakladnaya.store == store)
        if doc_type:
            query = query.filter(models.Nakladnaya.doc_type == doc_type)
        if status:
            query = query.filter(models.Nakladnaya.status == status)
        if supplier_id:
            query = query.filter(models.Nakladnaya.supplier_id == supplier_id)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (models.Nakladnaya.supplier_name.ilike(like))
                | (models.Nakladnaya.doc_number.ilike(like))
                | (models.Nakladnaya.doc_series.ilike(like))
            )

        rows = query.order_by(models.Nakladnaya.created_at.desc()).limit(5000).all()
        result = [_nak_dict(r) for r in rows]

        if date_from or date_to:
            filtered = []
            for r in result:
                d = r.get("doc_date") or ""
                if date_from and d < date_from:
                    continue
                if date_to and d > date_to:
                    continue
                filtered.append(r)
            result = filtered

        return result
    finally:
        db.close()


@router.post("", dependencies=[Depends(get_current_user)])
def create_nakladnaya(
    payload: schemas.NakladnayaCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:

        data = payload.model_dump(exclude_unset=True)
        if payload.supplier_id and not data.get("supplier_name"):
            sup = db.query(models.Supplier).filter(models.Supplier.id == payload.supplier_id).first()
            if sup:
                data["supplier_name"] = sup.name

        # Serialize products list to JSON text
        products = data.pop("products", None)
        if products:
            data["products_json"] = json.dumps(products, ensure_ascii=False)

        nak = models.Nakladnaya(**data, tenant_id=current_user.tenant_id)
        db.add(nak)
        # дефект 7: уникальность гарантирует индекс uq_nakladnye_doc_key, а не
        # проверка перед вставкой. Значения ключа читаются ДО commit — после
        # rollback объект разобран и его атрибуты недоступны.
        _commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number)
        db.refresh(nak)
        return _nak_dict(nak)
    finally:
        db.close()


@router.patch("/{nak_id}", dependencies=[Depends(get_current_user)])
def update_nakladnaya(
    nak_id: int,
    updates: schemas.NakladnayaUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        query = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)

        nak = query.first()
        if not nak:
            raise HTTPException(status_code=404, detail="Накладная не найдена")

        update_data = updates.model_dump(exclude_unset=True)
        # дефект 11: НДС не больше суммы документа — по эффективным значениям,
        # потому что схема видит только присланные поля (см. _ensure_amounts_consistent)
        _ensure_amounts_consistent(*_effective_amounts(nak, update_data))
        if update_data.get("supplier_id"):
            sup = db.query(models.Supplier).filter(models.Supplier.id == update_data["supplier_id"]).first()
            if sup and "supplier_name" not in update_data:
                nak.supplier_name = sup.name

        # Serialize products
        products = update_data.pop("products", None)
        if products is not None:
            nak.products_json = json.dumps(products, ensure_ascii=False) if products else None

        for key, value in update_data.items():
            setattr(nak, key, value)
        nak.updated_at = datetime.now(timezone.utc)

        # дефект 7: правка серии/номера может увести запись на занятый ключ —
        # индекс это поймает, а guard переведёт в 400 вместо 500
        _commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number)
        db.refresh(nak)
        return _nak_dict(nak)
    finally:
        db.close()


@router.delete("/{nak_id}", dependencies=[Depends(get_current_user)])
def delete_nakladnaya(
    nak_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        query = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)

        nak = query.first()
        if not nak:
            raise HTTPException(status_code=404, detail="Накладная не найдена")

        if nak.photo_paths:
            try:
                paths = json.loads(nak.photo_paths)
                for p in paths:
                    full = os.path.join(UPLOAD_DIR, os.path.basename(p))
                    if os.path.isfile(full):
                        os.remove(full)
            except (json.JSONDecodeError, TypeError):
                pass

        db.delete(nak)
        db.commit()
        return {"detail": "Накладная удалена"}
    finally:
        db.close()


@router.post("/{nak_id}/photos", dependencies=[Depends(get_current_user)])
async def upload_nakladnaya_photo(
    nak_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        query = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)

        nak = query.first()
        if not nak:
            raise HTTPException(status_code=404, detail="Накладная не найдена")

        fname = (file.filename or "photo.jpg").strip()
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ALLOWED_IMAGE_EXT:
            raise HTTPException(status_code=400, detail=f"Допустимые форматы: {', '.join(ALLOWED_IMAGE_EXT)}")

        safe_name = f"nak{nak.id}_{uuid.uuid4().hex[:8]}{ext}"
        dest = os.path.join(UPLOAD_DIR, safe_name)

        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")

        # Запись уводим в поток: блокирующий open() внутри async-хендлера держал
        # весь event loop на время записи до 25 МБ, а server.py поднимает два
        # воркера — на это время вставали бы все остальные запросы.
        await run_in_threadpool(Path(dest).write_bytes, content)

        photos = []
        if nak.photo_paths:
            try:
                photos = json.loads(nak.photo_paths)
            except (json.JSONDecodeError, TypeError):
                photos = []
        photos.append(safe_name)
        nak.photo_paths = json.dumps(photos)
        nak.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(nak)
        return _nak_dict(nak)
    finally:
        db.close()


@router.get("/photos/{filename}", dependencies=[Depends(get_current_user)])
def serve_nakladnaya_photo(filename: str):
    safe = os.path.basename(filename)
    full = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(full)


@router.get("/{nak_id}/excel", dependencies=[Depends(get_current_user)])
def get_nakladnaya_excel(
    nak_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Генерирует и скачивает Excel для одной накладной."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as e:
        raise HTTPException(status_code=500, detail="openpyxl не установлен") from e

    try:
        nak = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first()
        if not nak:
            raise HTTPException(status_code=404, detail="Накладная не найдена")

        products = _parse_products(nak)

        wb = Workbook()
        ws = wb.active
        ws.title = "Товары"

        # Шапка накладной
        info = [
            ("Поставщик", nak.supplier_name or ""),
            ("Тип", nak.doc_type or ""),
            ("Серия", nak.doc_series or ""),
            ("Номер", nak.doc_number or ""),
            ("Дата", nak.doc_date or ""),
            ("Сумма с НДС", float(nak.amount) if nak.amount else ""),
            ("НДС", float(nak.vat_amount) if nak.vat_amount else ""),
            ("Магазин", nak.store or ""),
        ]
        bold = Font(bold=True)
        for i, (label, val) in enumerate(info, 1):
            ws.cell(row=i, column=1, value=label).font = bold
            ws.cell(row=i, column=2, value=val)

        # Таблица товаров
        row_start = len(info) + 2
        headers = ["Товар", "Кол-во", "Ед.", "Цена без НДС", "Сумма без НДС"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=row_start, column=col, value=h)
            cell.font = bold
            cell.alignment = Alignment(horizontal="center")

        for i, p in enumerate(products, row_start + 1):
            qty = p.get("qty") or 0
            price = p.get("price_no_vat") or 0
            total = round(qty * price, 2)
            ws.cell(row=i, column=1, value=p.get("name", ""))
            ws.cell(row=i, column=2, value=qty)
            ws.cell(row=i, column=3, value=p.get("unit", ""))
            ws.cell(row=i, column=4, value=price)
            ws.cell(row=i, column=5, value=total)

        for col in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        fname = f"nak_{nak.doc_type or 'doc'}_{nak.doc_series or ''}{nak.doc_number or nak.id}.xlsx"
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        db.close()


@router.get("/export/products-excel", dependencies=[Depends(get_current_user)])
def export_products_excel(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    store: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Экспорт товаров из накладных в Excel."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as e:
        raise HTTPException(status_code=500, detail="openpyxl не установлен") from e

    try:
        query = db.query(models.Nakladnaya).filter(models.Nakladnaya.products_json.isnot(None))
        if store:
            query = query.filter(models.Nakladnaya.store == store)
        rows = query.order_by(models.Nakladnaya.doc_date.desc()).all()

        wb = Workbook()
        ws = wb.active
        ws.title = "Товары"
        headers = ["Дата", "Поставщик", "Тип", "Серия", "№ накладной", "Магазин",
                    "Товар", "Кол-во", "Ед.", "Цена без НДС", "Сумма без НДС"]
        header_font = Font(bold=True)
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        row_num = 2
        for nak in rows:
            d = nak.doc_date or ""
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            products = _parse_products(nak)
            for p in products:
                qty = p.get("qty") or 0
                price = p.get("price_no_vat") or 0
                total = round(qty * price, 2)
                ws.cell(row=row_num, column=1, value=nak.doc_date or "")
                ws.cell(row=row_num, column=2, value=nak.supplier_name or "")
                ws.cell(row=row_num, column=3, value=nak.doc_type or "")
                ws.cell(row=row_num, column=4, value=nak.doc_series or "")
                ws.cell(row=row_num, column=5, value=nak.doc_number or "")
                ws.cell(row=row_num, column=6, value=nak.store or "")
                ws.cell(row=row_num, column=7, value=p.get("name", ""))
                ws.cell(row=row_num, column=8, value=qty)
                ws.cell(row=row_num, column=9, value=p.get("unit", ""))
                ws.cell(row=row_num, column=10, value=price)
                ws.cell(row=row_num, column=11, value=total)
                row_num += 1

        for col in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=nakladnye_products.xlsx"},
        )
    finally:
        db.close()


# ============================================================
# Bot endpoints (bot token auth, no JWT)
# ============================================================


@router.post("/bot/create", dependencies=[Depends(_bot_auth)])
def bot_create_nakladnaya(payload: schemas.NakladnayaCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    if payload.supplier_id:
        sup = db.query(models.Supplier).filter(models.Supplier.id == payload.supplier_id).first()
        if sup and not data.get("supplier_name"):
            data["supplier_name"] = sup.name

    products = data.pop("products", None)
    if products:
        data["products_json"] = json.dumps(products, ensure_ascii=False)

    nak = models.Nakladnaya(**data, created_by_bot=True)
    db.add(nak)
    # FIX 2026-09-12 (Фаза 2, дефект 7): приём стал атомарным.
    #
    # Раньше бот делал два отдельных запроса — GET /bot/check-duplicate и
    # POST /bot/create, — а этот эндпоинт не проверял дубли вовсе. Между
    # проверкой и вставкой повторная отправка того же документа (сеть
    # не ответила, оператор нажал ещё раз, магазин прислал фото дважды)
    # плодила вторую запись: обе потом висели в сверке с поставщиком.
    # Проверкой перед INSERT гонка не закрывается в принципе, поэтому
    # уникальность обеспечивает индекс uq_nakladnye_doc_key (миграция 0005),
    # а guard переводит нарушение в 400 с id существующей записи.
    #
    # GET /bot/check-duplicate оставлен как есть: бот по-прежнему спрашивает
    # его, чтобы дописать фото и товары к существующей накладной вместо
    # создания новой. Он намеренно сравнивает сырые значения в Python, а не
    # читает *_norm — тогда его ответ не зависит от того, заполнены ли
    # производные колонки у старых строк.
    _commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number)
    db.refresh(nak)
    return _nak_dict(nak)


@router.patch("/bot/{nak_id}", dependencies=[Depends(_bot_auth)])
def bot_update_nakladnaya(nak_id: int, updates: schemas.NakladnayaUpdate, db: Session = Depends(get_db)):
    nak = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first()
    if not nak:
        raise HTTPException(status_code=404, detail="Накладная не найдена")

    update_data = updates.model_dump(exclude_unset=True)
    # дефект 11: то же правило, что и в CRM PATCH /nakladnye/{id}
    _ensure_amounts_consistent(*_effective_amounts(nak, update_data))
    products = update_data.pop("products", None)
    if products is not None:
        nak.products_json = json.dumps(products, ensure_ascii=False) if products else None

    for key, value in update_data.items():
        setattr(nak, key, value)
    nak.updated_at = datetime.now(timezone.utc)

    # дефект 7: то же, что в CRM PATCH — правка серии/номера не должна
    # уводить запись на уже занятый ключ
    _commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number)
    db.refresh(nak)
    return _nak_dict(nak)


@router.post("/bot/{nak_id}/photos", dependencies=[Depends(_bot_auth)])
async def bot_upload_photo(
    nak_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    nak = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first()
    if not nak:
        raise HTTPException(status_code=404, detail="Накладная не найдена")

    fname = (file.filename or "photo.jpg").strip()
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        ext = ".jpg"

    safe_name = f"nak{nak.id}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(UPLOAD_DIR, safe_name)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")

    # См. комментарий в upload_nakladnaya_photo: не блокируем event loop.
    await run_in_threadpool(Path(dest).write_bytes, content)

    photos = []
    if nak.photo_paths:
        try:
            photos = json.loads(nak.photo_paths)
        except (json.JSONDecodeError, TypeError):
            photos = []
    photos.append(safe_name)
    nak.photo_paths = json.dumps(photos)
    nak.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(nak)
    return _nak_dict(nak)


@router.get("/bot/check-duplicate", dependencies=[Depends(_bot_auth)])
def bot_check_duplicate(
    doc_series: Optional[str] = None,
    doc_number: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not doc_number:
        return {"duplicate": False}
    norm_num = "".join(ch for ch in doc_number if ch.isdigit())
    norm_ser = (doc_series or "").strip().upper()
    if not norm_num:
        return {"duplicate": False}
    rows = db.query(models.Nakladnaya).all()
    for r in rows:
        r_num = "".join(ch for ch in (r.doc_number or "") if ch.isdigit())
        r_ser = (r.doc_series or "").strip().upper()
        if r_num == norm_num and r_ser == norm_ser:
            return {"duplicate": True, "id": r.id}
    return {"duplicate": False}
