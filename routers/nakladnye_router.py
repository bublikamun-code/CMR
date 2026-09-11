import os
import re
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import models
import schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

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
    amount = update_data["amount"] if "amount" in update_data else nak.amount
    vat = update_data["vat_amount"] if "vat_amount" in update_data else nak.vat_amount
    return amount, vat


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
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
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
        tdb.close()


@router.post("", dependencies=[Depends(get_current_user)])
def create_nakladnaya(
    payload: schemas.NakladnayaCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

        data = payload.model_dump(exclude_unset=True)
        if payload.supplier_id and not data.get("supplier_name"):
            sup = session.query(models.Supplier).filter(models.Supplier.id == payload.supplier_id).first()
            if sup:
                data["supplier_name"] = sup.name

        # Serialize products list to JSON text
        products = data.pop("products", None)
        if products:
            data["products_json"] = json.dumps(products, ensure_ascii=False)

        nak = models.Nakladnaya(**data, tenant_id=current_user.tenant_id)
        session.add(nak)
        session.commit()
        session.refresh(nak)
        return _nak_dict(nak)
    finally:
        tdb.close()


@router.patch("/{nak_id}", dependencies=[Depends(get_current_user)])
def update_nakladnaya(
    nak_id: int,
    updates: schemas.NakladnayaUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)

        nak = query.first()
        if not nak:
            raise HTTPException(status_code=404, detail="Накладная не найдена")

        update_data = updates.model_dump(exclude_unset=True)
        # дефект 11: НДС не больше суммы документа — по эффективным значениям,
        # потому что схема видит только присланные поля (см. _ensure_amounts_consistent)
        _ensure_amounts_consistent(*_effective_amounts(nak, update_data))
        if "supplier_id" in update_data and update_data["supplier_id"]:
            sup = session.query(models.Supplier).filter(models.Supplier.id == update_data["supplier_id"]).first()
            if sup and "supplier_name" not in update_data:
                nak.supplier_name = sup.name

        # Serialize products
        products = update_data.pop("products", None)
        if products is not None:
            nak.products_json = json.dumps(products, ensure_ascii=False) if products else None

        for key, value in update_data.items():
            setattr(nak, key, value)
        nak.updated_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(nak)
        return _nak_dict(nak)
    finally:
        tdb.close()


@router.delete("/{nak_id}", dependencies=[Depends(get_current_user)])
def delete_nakladnaya(
    nak_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
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

        session.delete(nak)
        session.commit()
        return {"detail": "Накладная удалена"}
    finally:
        tdb.close()


@router.post("/{nak_id}/photos", dependencies=[Depends(get_current_user)])
async def upload_nakladnaya_photo(
    nak_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
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

        with open(dest, "wb") as f:
            f.write(content)

        photos = []
        if nak.photo_paths:
            try:
                photos = json.loads(nak.photo_paths)
            except (json.JSONDecodeError, TypeError):
                photos = []
        photos.append(safe_name)
        nak.photo_paths = json.dumps(photos)
        nak.updated_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(nak)
        return _nak_dict(nak)
    finally:
        tdb.close()


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
        from openpyxl.styles import Font, Alignment
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl не установлен")

    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id)
        nak = query.first()
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
        tdb.close()


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
        from openpyxl.styles import Font, Alignment
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl не установлен")

    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Nakladnaya).filter(models.Nakladnaya.products_json.isnot(None))
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
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
        tdb.close()


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
    db.commit()
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

    db.commit()
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

    with open(dest, "wb") as f:
        f.write(content)

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
