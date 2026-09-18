from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user
from database import get_db
from db_utils import cap_list

router = APIRouter(
    prefix="/suppliers",
    tags=["Поставщики"],
    dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[schemas.SupplierResponse])
def list_suppliers(q: str = Query(None), response: Response = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Supplier)
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            models.Supplier.name.ilike(pattern),
            models.Supplier.unp.ilike(pattern),
            models.Supplier.phone.ilike(pattern),
        ))
    # Н11 (аудит 06.09): предохранитель от неограниченного списка
    return cap_list(query.order_by(models.Supplier.name).all(), response)


@router.get("/{supplier_id}", response_model=schemas.SupplierResponse)
def get_supplier(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
    s = query.first()
    if not s:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    return s


@router.post("", response_model=schemas.SupplierResponse)
def create_supplier(supplier: schemas.SupplierCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    new_s = models.Supplier(**supplier.model_dump())
    db.add(new_s)
    db.commit()
    db.refresh(new_s)
    return new_s


@router.patch("/{supplier_id}", response_model=schemas.SupplierResponse)
def update_supplier(supplier_id: int, update: schemas.SupplierUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
    s = query.first()
    if not s:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    old_name = s.name
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(s, key, value)

    # FIX 2026-09-12 (Фаза 2, дефект 8): переименование поставщика
    # синхронизирует денормализованные снимки его имени.
    #
    # Оба поля — копии названия из справочника, и обе рассинхронизировались:
    # после переименования таблица накладных и чек-листы закупок продолжали
    # показывать старое имя. Это не только косметика: поиск накладных
    # (list_nakladnye, фильтр q) и группировка идут по supplier_name
    # строкой, поэтому один поставщик начинал искаться под двумя именами.
    #
    # Правила для двух таблиц РАЗНЫЕ, и это не случайность:
    new_name = s.name or ""
    if new_name and new_name != (old_name or ""):
        # nakladnye.supplier_name — снимок, который всегда берётся из
        # справочника: create_nakladnaya подставляет sup.name, а модалка CRM
        # шлёт supplier.name того поставщика, что выбран в селекте. Записи
        # из telegram-бота сюда не попадают вовсе — бот supplier_id не шлёт,
        # его supplier_name распознан из фото и со справочником не связан.
        # Поэтому обновляем все строки этого поставщика.
        db.query(models.Nakladnaya).filter(
            models.Nakladnaya.supplier_id == supplier_id,
        ).update({"supplier_name": new_name}, synchronize_session=False)

        # card_checklists.company_name — снимок названия КОМПАНИИ ЗАКУПКИ,
        # и он не обязан совпадать с именем поставщика:
        # update_checklist_item принимает company_name отдельным полем,
        # поэтому пользователь может вписать своё название, оставив
        # supplier_id. Поголовная перезапись уничтожила бы эти значения.
        # Обновляем только те строки, где снимок всё ещё равен СТАРОМУ имени
        # поставщика, — то есть ровно те, что устарели из-за переименования.
        if old_name:
            db.query(models.CardChecklist).filter(
                models.CardChecklist.supplier_id == supplier_id,
                models.CardChecklist.company_name == old_name,
            ).update({"company_name": new_name}, synchronize_session=False)

    db.commit()
    db.refresh(s)
    return s


@router.delete("/{supplier_id}")
def delete_supplier(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
    s = query.first()
    if not s:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    # FIX 2026-08-29 (FK ON): отвязываем пункты чек-листов этого поставщика
    db.query(models.CardChecklist).filter(models.CardChecklist.supplier_id == supplier_id).update({"supplier_id": None}, synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"detail": "Поставщик удалён"}


@router.get("/{supplier_id}/purchases")
def supplier_purchases(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Все закупки, закреплённые за поставщиком.

    Пункты чек-листов сделок, где выбран этот поставщик — так же,
    как у клиента видны его карточки. Показывает, сколько ему
    должны всего и сколько уже оплачено.
    """
    sup = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Поставщик не найден")

    items = db.query(models.CardChecklist).options(
        # FIX 2026-08-30 (N+1): сделка грузилась отдельным SELECT на каждый
        # пункт чек-листа (it.card в цикле ниже).
        selectinload(models.CardChecklist.card)
    ).filter(
        models.CardChecklist.supplier_id == supplier_id
    ).order_by(models.CardChecklist.id.desc()).all()

    result, total, paid = [], 0.0, 0.0
    for it in items:
        amount = float(it.amount or 0)
        total += amount
        if it.is_paid:
            paid += amount
        card = it.card
        result.append({
            "id": it.id,
            "amount": round(amount, 2),
            "is_paid": bool(it.is_paid),
            "is_secondary_check": bool(it.is_secondary_check),
            "note": it.note or "",
            "has_invoice": bool(it.invoice_file_path),
            "card_id": it.card_id,
            "card_title": card.title if card else "",
            "card_status": card.status if card else "",
            "created_at": it.created_at,
        })

    return {
        "supplier_id": supplier_id,
        "supplier_name": sup.name,
        "items": result,
        "count": len(result),
        "total_amount": round(total, 2),
        "paid_amount": round(paid, 2),
        "unpaid_amount": round(total - paid, 2),
    }
