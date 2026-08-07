from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
import models, schemas
from database import get_db, get_tenant_db
from auth import get_current_user

router = APIRouter(
    prefix="/suppliers",
    tags=["Поставщики"],
    dependencies=[Depends(get_current_user)]
)

def _db(current_user, db):
    if current_user.role == "superadmin" and current_user.tenant_id is None:
        return db
    if current_user.tenant_id is None:
        return db
    return get_tenant_db(current_user.tenant_id)

@router.get("", response_model=List[schemas.SupplierResponse])
def list_suppliers(q: str = Query(None), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Supplier)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Supplier)
        if q:
            pattern = f"%{q}%"
            query = query.filter(or_(
                models.Supplier.name.ilike(pattern),
                models.Supplier.unp.ilike(pattern),
                models.Supplier.phone.ilike(pattern),
            ))
        return query.order_by(models.Supplier.name).all()
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/{supplier_id}", response_model=schemas.SupplierResponse)
def get_supplier(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        s = query.first()
        if not s:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        return s
    finally:
        if tdb is not db:
            tdb.close()

@router.post("", response_model=schemas.SupplierResponse)
def create_supplier(supplier: schemas.SupplierCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        new_s = models.Supplier(**supplier.model_dump())
        tdb.add(new_s)
        tdb.commit()
        tdb.refresh(new_s)
        return new_s
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/{supplier_id}", response_model=schemas.SupplierResponse)
def update_supplier(supplier_id: int, update: schemas.SupplierUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        s = query.first()
        if not s:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        for key, value in update.model_dump(exclude_unset=True).items():
            setattr(s, key, value)
        session.commit()
        session.refresh(s)
        return s
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/{supplier_id}")
def delete_supplier(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Supplier).filter(models.Supplier.id == supplier_id)
        s = query.first()
        if not s:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        session.delete(s)
        session.commit()
        return {"detail": "Поставщик удалён"}
    finally:
        if tdb is not db:
            tdb.close()


@router.get("/{supplier_id}/purchases")
def supplier_purchases(supplier_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Все закупки, закреплённые за поставщиком.

    Пункты чек-листов сделок, где выбран этот поставщик — так же,
    как у клиента видны его карточки. Показывает, сколько ему
    должны всего и сколько уже оплачено.
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

        sup = session.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
        if not sup:
            raise HTTPException(status_code=404, detail="Поставщик не найден")

        items = session.query(models.CardChecklist).filter(
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
    finally:
        if tdb is not db:
            tdb.close()

