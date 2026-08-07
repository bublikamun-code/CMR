from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
import models, schemas
from versioning import save_version
from database import get_db, get_tenant_db
from auth import get_current_user

router = APIRouter(
    prefix="/clients",
    tags=["Клиенты"],
    dependencies=[Depends(get_current_user)]
)

def _db(current_user, db):
    if current_user.role == "superadmin" and current_user.tenant_id is None:
        return db
    if current_user.tenant_id is None:
        return db
    return get_tenant_db(current_user.tenant_id)

@router.get("", response_model=List[schemas.ClientResponse])
def list_clients(q: str = Query(None), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Client)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Client)
        if q:
            pattern = f"%{q}%"
            query = query.filter(or_(
                models.Client.name.ilike(pattern),
                models.Client.unp.ilike(pattern),
                models.Client.phone.ilike(pattern),
            ))
        return query.order_by(models.Client.name).all()
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/{client_id}", response_model=schemas.ClientResponse)
def get_client(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Client).filter(models.Client.id == client_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Client).filter(models.Client.id == client_id)
        client = query.first()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return client
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/{client_id}/cards", response_model=List[schemas.CardResponse])
def get_client_cards(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Card).filter(models.Card.client_id == client_id, models.Card.is_deleted == False)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            query = db.query(models.Card).filter(models.Card.client_id == client_id, models.Card.is_deleted == False)
        return query.all()
    finally:
        if tdb is not db:
            tdb.close()

@router.post("", response_model=schemas.ClientResponse)
def create_client(client: schemas.ClientCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        new_client = models.Client(**client.model_dump())
        tdb.add(new_client)
        tdb.commit()
        tdb.refresh(new_client)
        # Версионирование
        save_version(tdb, "clients", new_client.id,
            {"name": new_client.name, "phone": new_client.phone, "email": new_client.email, "unp": new_client.unp},
            user_id=current_user.id, change_type="create", tenant_id=current_user.tenant_id)
        # save_version делает второй commit — объект становится expired,
        # а finally: tdb.close() срабатывает ДО сериализации ответа.
        # Без этого refresh — DetachedInstanceError: запись в базе есть,
        # но клиент получает 500 и видит ложную ошибку.
        tdb.refresh(new_client)
        # Webhook уведомление
        try:
            from routers.webhooks_router import notify_webhooks
            import asyncio
            asyncio.get_event_loop().create_task(notify_webhooks(
                current_user.tenant_id, "client.created",
                {"id": new_client.id, "name": new_client.name}
            ))
        except Exception: pass
        return new_client
    finally:
        if tdb is not db:
            tdb.close()

@router.patch("/{client_id}", response_model=schemas.ClientResponse)
def update_client(client_id: int, update: schemas.ClientUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Client).filter(models.Client.id == client_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Client).filter(models.Client.id == client_id)
        client = query.first()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        for key, value in update.model_dump(exclude_unset=True).items():
            setattr(client, key, value)
        session.commit()
        session.refresh(client)
        save_version(session, "clients", client.id,
            {"name": client.name, "phone": client.phone, "email": client.email, "unp": client.unp},
            user_id=current_user.id, change_type="update", tenant_id=current_user.tenant_id)
        # см. комментарий в create_client: refresh обязателен после save_version
        session.refresh(client)
        return client
    finally:
        if tdb is not db:
            tdb.close()

@router.delete("/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Client).filter(models.Client.id == client_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Client).filter(models.Client.id == client_id)
        client = query.first()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        session.delete(client)
        session.commit()
        return {"detail": "Клиент удалён"}
    finally:
        if tdb is not db:
            tdb.close()


@router.get("/{client_id}/versions")
def get_client_versions(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from versioning import get_versions
    tdb = _db(current_user, db)
    try:
        versions = get_versions(tdb, "clients", client_id, tenant_id=current_user.tenant_id)
        return versions
    finally:
        if tdb is not db:
            tdb.close()
