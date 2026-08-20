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
    access_token = auth.create_access_token(data={"sub": user.username, "tenant_id": user.tenant_id})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@router.get("/users", response_model=List[schemas.UserResponse])
def list_users(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    if current_user.role == "superadmin":
        return db.query(models.User).order_by(models.User.username).all()
    elif current_user.role == "admin":
        return db.query(models.User).filter(models.User.tenant_id == current_user.tenant_id).order_by(models.User.username).all()
    else:
        return [current_user]


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
    if current_user.role == "admin" and target.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Нельзя удалить пользователя из другого тенанта")
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
    if current_user.role == "admin" and target.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Нельзя изменить пользователя из другого тенанта")
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
