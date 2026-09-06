from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_
from typing import List
import models, schemas
from versioning import save_version
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db, cap_list

router = APIRouter(
    prefix="/clients",
    tags=["Клиенты"],
    dependencies=[Depends(get_current_user)]
)

@router.get("", response_model=List[schemas.ClientResponse])
def list_clients(q: str = Query(None), response: Response = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Client)
        if q:
            pattern = f"%{q}%"
            query = query.filter(or_(
                models.Client.name.ilike(pattern),
                models.Client.unp.ilike(pattern),
                models.Client.phone.ilike(pattern),
            ))
        # Н11 (аудит 06.09): предохранитель от неограниченного списка
        return cap_list(query.order_by(models.Client.name).all(), response)
    finally:
        if tdb is not db:
            tdb.close()

@router.get("/{client_id}", response_model=schemas.ClientResponse)
def get_client(client_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        query = tdb.query(models.Client).filter(models.Client.id == client_id)
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
        # Н12 (аудит 06.09): selectinload вместо ленивых SELECT на каждую
        # карточку ( CardResponse тянет вложенные отношения при сериализации).
        return query.options(
            selectinload(models.Card.attachments),
            selectinload(models.Card.checklists).selectinload(models.CardChecklist.supplier),
            selectinload(models.Card.owner),
            selectinload(models.Card.client),
            selectinload(models.Card.tags),
        ).all()
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
            from routers.webhooks_router import notify_webhooks_async
            notify_webhooks_async(current_user.tenant_id, "client.created",
                {"id": new_client.id, "name": new_client.name})
        except Exception: pass
        # Уведомление администраторам этого тенанта о новом клиенте
        try:
            from notify import notify, admin_ids
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
        client = query.first()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        # FIX 2026-08-29 (FK ON): в фактическом DDL нет ON DELETE — отвязываем
        # детей вручную, иначе удаление клиента с карточками падает.
        session.query(models.Card).filter(models.Card.client_id == client_id).update({"client_id": None}, synchronize_session=False)
        session.query(models.WriteoffGroup).filter(models.WriteoffGroup.client_id == client_id).update({"client_id": None}, synchronize_session=False)
        session.query(models.Task).filter(models.Task.client_id == client_id).update({"client_id": None}, synchronize_session=False)
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
