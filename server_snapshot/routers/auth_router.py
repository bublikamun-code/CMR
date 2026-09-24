import logging
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import auth
import models
import schemas
from database import get_db
from limiter_config import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Авторизация"])


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, response: Response, form_data: OAuth2PasswordRequestForm = Depends(), remember: str = Form(""), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()

    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        logger.warning(f"Failed login: {form_data.username}")
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")

    # Пункт 15 плана v2: отключённый пользователь не входит. Проверка стоит
    # ПОСЛЕ сверки пароля намеренно: неверный пароль и неизвестный логин
    # по-прежнему дают один и тот же 401, так что «пользователь отключён»
    # узнаёт только тот, кто уже знает правильный пароль, — канал
    # перечисления аккаунтов не появляется.
    if not user.is_active:
        logger.warning(f"Login rejected (user disabled): {user.username}")
        raise HTTPException(status_code=403, detail="Пользователь отключён. Обратитесь к администратору.")

    logger.info(f"Login: {user.username} (role={user.role})")
    # pv (password version) — первые 8 символов хэша: смена пароля
    # инвалидирует все ранее выданные токены (проверка в get_current_user).
    # «Запомнить меня» (фидбек 07.09): срок токена и куки — 30 дней вместо
    # 24 часов; claim rm включает скользящее продление в get_current_user.
    remember_on = remember.lower() in ("1", "true", "on", "yes")
    minutes = auth.REMEMBER_MINUTES if remember_on else auth.ACCESS_TOKEN_EXPIRE_MINUTES
    token_data = {"sub": user.username, "tenant_id": user.tenant_id, "pv": user.hashed_password[:8]}
    if remember_on:
        token_data["rm"] = 1
    access_token = auth.create_access_token(token_data, expires_minutes=minutes)
    # P2-1 (аудит 04.09): дублируем токен httpOnly-cookie — JS не может её
    # прочитать, поэтому кража токена через XSS невозможна. SameSite=Lax
    # закрывает CSRF для кросс-сайтовых POST. Secure — только по https,
    # пока прод живёт на HTTP (иначе кука не отправится вовсе).
    response.set_cookie(
        key="crm_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=minutes * 60,
        path="/",
    )
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    # Выход не требует действующей сессии: заголовок и cookie проверяются
    # независимо, чтобы отозвать bearer даже после удаления cookie клиентом.
    # Ошибки декодирования намеренно не попадают в ответ — logout всегда
    # идемпотентен и не должен раскрывать детали JWT.
    authorization = request.headers.get("Authorization", "")
    scheme, _, bearer = authorization.partition(" ")
    bearer_token = bearer.strip() if scheme.lower() == "bearer" else ""
    cookie_token = request.cookies.get("crm_token", "")
    try:
        for candidate in (bearer_token, cookie_token):
            if candidate:
                # revoke_token сам игнорирует malformed/expired/already revoked.
                # Ошибку записи в БД не маскируем: иначе logout ответил бы 200,
                # хотя bearer продолжал бы приниматься сервером.
                auth.revoke_token(db, candidate)
    finally:
        response.delete_cookie(key="crm_token", path="/")
    return {"detail": "Вы вышли из системы"}


@router.get("/users", response_model=List[schemas.UserResponse])
def list_users(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    # UI FIX 2026-08-26: компания одна — админ видит всех пользователей,
    # как и суперадмин (раньше фильтр по tenant_id прятал менеджеров,
    # созданных суперадмином с tenant_id = NULL).
    if current_user.role in ("superadmin", "admin"):
        return db.query(models.User).order_by(models.User.username).all()
    return [current_user]


@router.put("/me/password")
# FIX 2026-09-06 (аудит С3): здесь проверяется СТАРЫЙ пароль — без лимита
# эндпоинт годится для брутфорса чужого аккаунта при оставшейся открытой
# сессии/украденной куке. Ключ — IP; за nginx должен быть включён
# proxy-headers, иначе лимит станет общим на всех (см. P0-HTTPS).
@limiter.limit("5/minute")
def change_own_password(request: Request, data: schemas.PasswordChange, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
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
    # Фикс аудита 10.09: проверка ловила только role == "admin", и админ мог
    # создать себе superadmin (как в update_user ниже). Ловим обе роли.
    if current_user.role != "superadmin" and data.role in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Только суперадмин может назначать роли admin/superadmin")
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
    # Пункт 15 плана v2: отключение вместо удаления. Зеркало DELETE — себя
    # отключить нельзя, иначе админ лишает себя доступа одним неверным
    # переключателем, и в CRM некому будет включить его обратно.
    if data.is_active is not None:
        if not data.is_active and target.id == current_user.id:
            raise HTTPException(status_code=400, detail="Нельзя отключить самого себя")
        target.is_active = data.is_active
    if data.role:
        target.role = data.role
    if data.password:
        target.hashed_password = auth.get_password_hash(data.password)
    db.commit()
    return {"detail": "Пользователь обновлён", "is_active": bool(target.is_active)}
