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
        if "supplier_id" in update_data and update_data["supplier_id"]:
            sup = session.query(models.Supplier).filter(models.Supplier.id == update_data["supplier_id"]).first()
            if sup and "supplier_name" not in update_data:
                nak.supplier_name = sup.name

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

    nak = models.Nakladnaya(**data, created_by_bot=True)
    db.add(nak)
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
    doc_type: Optional[str] = None,
    doc_number: Optional[str] = None,
    doc_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not doc_number:
        return {"duplicate": False}
    norm = "".join(ch for ch in doc_number if ch.isdigit())
    if not norm:
        return {"duplicate": False}
    rows = db.query(models.Nakladnaya).all()
    for r in rows:
        r_norm = "".join(ch for ch in (r.doc_number or "") if ch.isdigit())
        if r_norm == norm and (not doc_type or r.doc_type == doc_type):
            return {"duplicate": True, "id": r.id}
    return {"duplicate": False}
