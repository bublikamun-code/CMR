import contextlib
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user, require_role
from database import get_db
from db_utils import cap_list
from versioning import save_version

router = APIRouter(
    prefix="/clients",
    tags=["Клиенты"],
    dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[schemas.ClientResponse])
def list_clients(q: str = Query(None), response: Response = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Client)
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            models.Client.name.ilike(pattern),
            models.Client.unp.ilike(pattern),
            models.Client.phone.ilike(pattern),
        ))
    # Н11 (аудит 06.09): предохранитель от неограниченного списка
    return cap_list(query.order_by(models.Client.name).all(), response)


@router.get("/{client_id}", response_model=schemas.ClientResponse)
def get_client(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Client).filter(models.Client.id == client_id)
    client = query.first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client


@router.get("/{client_id}/cards", response_model=List[schemas.CardResponse])
def get_client_cards(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Card).filter(models.Card.client_id == client_id, models.Card.is_deleted == False)
    # Н12 (аудит 06.09): selectinload вместо ленивых SELECT на каждую
    # карточку ( CardResponse тянет вложенные отношения при сериализации).
    return query.options(
        selectinload(models.Card.attachments),
        selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
        selectinload(models.Card.owner),
        selectinload(models.Card.client),
        selectinload(models.Card.tags),
    ).all()


@router.post("", response_model=schemas.ClientResponse)
def create_client(client: schemas.ClientCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    new_client = models.Client(**client.model_dump())
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    # Версионирование
    save_version(db, "clients", new_client.id,
        {"name": new_client.name, "phone": new_client.phone, "email": new_client.email, "unp": new_client.unp},
        user_id=current_user.id, change_type="create", tenant_id=current_user.tenant_id)
    # save_version делает второй commit — объект становится expired,
    # а finally: db.close() срабатывает ДО сериализации ответа.
    # Без этого refresh — DetachedInstanceError: запись в базе есть,
    # но клиент получает 500 и видит ложную ошибку.
    db.refresh(new_client)
    # Webhook уведомление
    with contextlib.suppress(Exception):
        from routers.webhooks_router import notify_webhooks_async
        notify_webhooks_async(current_user.tenant_id, "client.created",
            {"id": new_client.id, "name": new_client.name})
    # Уведомление администраторам этого тенанта о новом клиенте
    with contextlib.suppress(Exception):
        from notify import admin_ids, notify
        admin_recipients = []
        for aid in admin_ids(db):
            u = db.query(models.User).get(aid)
            if u and (u.tenant_id == current_user.tenant_id or u.role == "superadmin"):
                admin_recipients.append(aid)
        if admin_recipients:
            notify(db, admin_recipients, actor_id=current_user.id,
                   type="client_created", title=f"Новый клиент: {new_client.name}",
                   details=f"Добавил: {current_user.username}",
                   entity_type="client", entity_id=new_client.id)
    return new_client


@router.patch("/{client_id}", response_model=schemas.ClientResponse)
def update_client(client_id: int, update: schemas.ClientUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Client).filter(models.Client.id == client_id)
    client = query.first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(client, key, value)
    db.commit()
    db.refresh(client)
    save_version(db, "clients", client.id,
        {"name": client.name, "phone": client.phone, "email": client.email, "unp": client.unp},
        user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
    # см. комментарий в create_client: refresh обязателен после save_version
    db.refresh(client)
    return client


@router.delete("/{client_id}", dependencies=[Depends(require_role("admin", "superadmin"))])
def delete_client(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Client).filter(models.Client.id == client_id)
    client = query.first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    # FIX 2026-08-29 (FK ON): в фактическом DDL нет ON DELETE — отвязываем
    # детей вручную, иначе удаление клиента с карточками падает.
    db.query(models.Card).filter(models.Card.client_id == client_id).update({"client_id": None}, synchronize_session=False)
    db.query(models.WriteoffGroup).filter(models.WriteoffGroup.client_id == client_id).update({"client_id": None}, synchronize_session=False)
    db.query(models.Task).filter(models.Task.client_id == client_id).update({"client_id": None}, synchronize_session=False)
    db.delete(client)
    db.commit()
    return {"detail": "Клиент удалён"}


@router.get("/{client_id}/versions")
def get_client_versions(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from versioning import get_versions
    versions = get_versions(db, "clients", client_id, tenant_id=current_user.tenant_id)
    return versions


# ============================================================
# БАЛАНС КЛИЕНТА (фидбек 18.09)
# Приход денег от клиента — client_payments; баланс = Σ приходов −
# Σ total_amount его неудалённых карточек. Плюс — аванс (клиент может
# «добрать»), минус — долг. Закрытие счёта из баланса не создаёт приход:
# уменьшение баланса происходит само через рост paid_amount карточки.
# ============================================================

def _client_balance_payload(db: Session, client_id: int) -> dict:
    payments = db.query(models.ClientPayment).filter(
        models.ClientPayment.client_id == client_id
    ).order_by(models.ClientPayment.created_at.desc(), models.ClientPayment.id.desc()).all()
    payments_total = round(sum(float(p.amount or 0) for p in payments), 2)
    cards = db.query(models.Card).filter(
        models.Card.client_id == client_id,
        models.Card.is_deleted == False,  # noqa: E712
    ).all()
    deals_total = round(sum(float(c.total_amount or 0) for c in cards), 2)
    paid_on_cards = round(sum(float(c.paid_amount or 0) for c in cards), 2)
    return {
        "client_id": client_id,
        "balance": round(payments_total - deals_total, 2),
        "payments_total": payments_total,
        "deals_total": deals_total,
        "paid_on_cards": paid_on_cards,
        "payments": payments[:50],
    }


@router.get("/{client_id}/balance", response_model=schemas.ClientBalanceResponse)
def client_balance(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return _client_balance_payload(db, client_id)


@router.post("/{client_id}/payments", response_model=schemas.ClientPaymentResponse)
def add_client_payment(client_id: int, payload: schemas.ClientPaymentCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    if payload.card_id is not None:
        card = db.query(models.Card).filter(
            models.Card.id == payload.card_id, models.Card.client_id == client_id
        ).first()
        if not card:
            raise HTTPException(status_code=400, detail="Сделка не принадлежит этому клиенту")
    payment = models.ClientPayment(
        client_id=client_id,
        card_id=payload.card_id,
        amount=round(payload.amount, 2),
        note=(payload.note or "").strip() or None,
        created_by=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.delete("/payments/{payment_id}")
def delete_client_payment(payment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(require_role("admin", "superadmin"))):
    payment = db.query(models.ClientPayment).filter(models.ClientPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Приход не найден")
    db.delete(payment)
    db.commit()
    return {"detail": "Приход удалён", "id": payment_id}
