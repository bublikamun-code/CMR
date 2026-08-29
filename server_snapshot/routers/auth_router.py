from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import List
import logging
import schemas
import models
import auth
from database import get_db, get_tenant_db
from limiter_config import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Авторизация"])


@router.post("/login")
@limiter.limit("30/minute")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()

    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        logger.warning(f"Failed login: {form_data.username}")
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")

    logger.info(f"Login: {user.username} (role={user.role})")
    # pv (password version) — первые 8 символов хэша: смена пароля
    # инвалидирует все ранее выданные токены (проверка в get_current_user).
    access_token = auth.create_access_token(data={"sub": user.username, "tenant_id": user.tenant_id, "pv": user.hashed_password[:8]})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@router.get("/users", response_model=List[schemas.UserResponse])
def list_users(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    # UI FIX 2026-08-26: компания одна — админ видит всех пользователей,
    # как и суперадмин (раньше фильтр по tenant_id прятал менеджеров,
    # созданных суперадмином с tenant_id = NULL).
    if current_user.role in ("superadmin", "admin"):
        return db.query(models.User).order_by(models.User.username).all()
    return [current_user]


@router.put("/me/password")
def change_own_password(data: schemas.PasswordChange, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """Самостоятельная смена пароля: старый проверяется, после смены
    все прежние токены пользователя умирают (claim pv)."""
    if not auth.verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Старый пароль указан неверно")
    if data.old_password == data.new_password:
        raise HTTPException(status_code=400, detail="Новый пароль совпадает со старым")
    current_user.hashed_password = auth.get_password_hash(data.new_password)
    db.commit()
    logger.info(f"Password changed by user: {current_user.username}")
    return {"detail": "Пароль изменён. Войдите заново."}


@router.get("/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@router.post("/users", response_model=schemas.UserResponse)
def create_user(data: schemas.UserCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    if current_user.role != "superadmin" and data.role == "admin":
        raise HTTPException(status_code=403, detail="Только суперадмин может назначать роль администратора")
    existing = db.query(models.User).filter(models.User.username == data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

    tenant_id = current_user.tenant_id
    new_user = models.User(
        username=data.username,
        hashed_password=auth.get_password_hash(data.password),
        role=data.role,
        tenant_id=tenant_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.role == "superadmin":
        raise HTTPException(status_code=400, detail="Нельзя удалить суперадмина")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    # UI FIX 2026-08-26: компания одна — проверка «из другого тенанта» убрана:
    # из-за неё админ не мог удалять пользователей с tenant_id = NULL.
    # FIX 2026-08-29 (FK ON): отвязываем артефакты пользователя — в DDL нет
    # ON DELETE, удаление юзера с карточками/лентой/задачами падало бы.
    db.query(models.Card).filter(models.Card.owner_id == user_id).update({"owner_id": None}, synchronize_session=False)
    db.query(models.ActivityLog).filter(models.ActivityLog.user_id == user_id).update({"user_id": None}, synchronize_session=False)
    db.query(models.RecordVersion).filter(models.RecordVersion.changed_by == user_id).update({"changed_by": None}, synchronize_session=False)
    db.query(models.Workflow).filter(models.Workflow.created_by == user_id).update({"created_by": None}, synchronize_session=False)
    db.query(models.SavedView).filter(models.SavedView.created_by == user_id).update({"created_by": None}, synchronize_session=False)
    db.query(models.CustomRecord).filter(models.CustomRecord.created_by == user_id).update({"created_by": None}, synchronize_session=False)
    db.query(models.Task).filter(models.Task.assignee_id == user_id).update({"assignee_id": None}, synchronize_session=False)
    db.query(models.Task).filter(models.Task.creator_id == user_id).update({"creator_id": None}, synchronize_session=False)
    db.query(models.Notification).filter(models.Notification.user_id == user_id).delete(synchronize_session=False)
    db.delete(target)
    db.commit()
    return {"detail": "Пользователь удалён"}


@router.patch("/users/{user_id}")
def update_user(user_id: int, data: schemas.UserUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Нельзя изменить суперадмина")
    # UI FIX 2026-08-26: тенант-проверка убрана (компания одна)
    if data.role and current_user.role != "superadmin" and data.role in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Только суперадмин может назначать роли admin/superadmin")
    if data.role:
        target.role = data.role
    if data.password:
        target.hashed_password = auth.get_password_hash(data.password)
    db.commit()
    return {"detail": "Пользователь обновлён"}


@router.post("/create-tenant", response_model=schemas.UserResponse)
def create_tenant_admin(data: schemas.UserCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_superadmin())):
    existing = db.query(models.User).filter(models.User.username == data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

    from models_tenant import Tenant
    import os, re
    slug = re.sub(r'[^a-zA-Z0-9_]', '', data.username.lower().replace(" ", "_"))
    if not slug or len(slug) < 3:
        raise HTTPException(status_code=400, detail="Некорректное имя пользователя")
    db_path = f"tenants/crm_{slug}.db"
    os.makedirs("tenants", exist_ok=True)

    tenant = Tenant(name=data.username, db_path=db_path)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    new_user = models.User(
        username=data.username,
        hashed_password=auth.get_password_hash(data.password),
        role="admin",
        tenant_id=tenant.id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    from database import Base, get_tenant_db
    tdb = get_tenant_db(tenant.id)
    tdb.close()

    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}
