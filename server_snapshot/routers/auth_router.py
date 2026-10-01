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
from tenant_policy import get_tenant_object, tenant_query, writable_tenant_id

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
    if user.tenant_id is None:
        # Не выдаём session с неоднозначным tenant. Миграция 0018 либо
        # назначает однозначный legacy tenant, либо останавливается с ошибкой.
        raise HTTPException(status_code=403, detail="Tenant пользователя не настроен")

    logger.info(f"Login: {user.username} (role={user.role})")
    # pv (password version) — SHA-256 от хэша: смена пароля инвалидирует все
    # ранее выданные токены (проверка в get_current_user).
    # «Запомнить меня» (фидбек 07.09): срок токена и куки — 30 дней вместо
    # 24 часов; claim rm включает скользящее продление в get_current_user.
    remember_on = remember.lower() in ("1", "true", "on", "yes")
    minutes = auth.REMEMBER_MINUTES if remember_on else auth.ACCESS_TOKEN_EXPIRE_MINUTES
    token_data = {
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "pv": auth.password_version(user.hashed_password),
    }
    if remember_on:
        token_data["rm"] = 1
    access_token = auth.create_access_token(token_data, expires_minutes=minutes)
    # P2-1 (аудит 04.09): дублируем токен httpOnly-cookie — JS не может её
    # прочитать, поэтому кража токена через XSS невозможна. SameSite=Lax
    # закрывает CSRF для кросс-сайтовых POST. Флаг Secure задаёт только
    # проверенная CRM_COOKIE_SECURE policy и не зависит от forwarded-заголовков.
    auth.set_auth_cookie(response, access_token, minutes * 60)
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
    # Администратор видит пользователей своего tenant; superadmin сохраняет
    # явную глобальную видимость. Менеджер видит только себя.
    if current_user.role in ("superadmin", "admin"):
        return tenant_query(db, models.User, current_user).order_by(
            models.User.username
        ).all()
    return [current_user]


@router.put("/me/password")
# FIX 2026-09-06 (аудит С3): здесь проверяется СТАРЫЙ пароль — без лимита
# эндпоинт годится для брутфорса чужого аккаунта при оставшейся открытой
# сессии/украденной куке. Ключ — IP; nginx должен быть в явном
# CRM_TRUSTED_PROXY_NETWORKS, иначе лимит станет общим на всех.
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


def _log_user_change(db, actor, target, data, old_role, old_active):
    """Записать мутацию учётной записи в ленту активности.

    FIX 2026-09-30 (аудит прав доступа). Мутации /auth/users шли в никуда:
    change_own_password писал только строку в лог процесса, create/update/
    delete — вообще ничего. По ленте активности нельзя было ответить на вопрос
    «кто и когда снял пароль с админа», хотя сам пароль там и не хранится —
    достаточно факта и автора.

    Прежнее состояние передаётся отдельными аргументами НАМЕРЕННО: к моменту
    вызова target.role и target.is_active уже новые, и «было/стало» иначе
    совпало бы.

    Сам пароль в ленту не пишется: там только видно, что менялось.
    """
    changed = []
    if data.is_active is not None and data.is_active != old_active:
        changed.append(f"включён {'да' if data.is_active else 'нет'} (было {'да' if old_active else 'нет'})")
    if data.role and data.role != old_role:
        changed.append(f"роль {old_role} → {data.role}")
    if data.password:
        changed.append("пароль сброшен")
    if not changed:
        return None
    entry = models.ActivityLog(
        user_id=actor.id,
        action="Изменение пользователя",
        details=(
            f"{'; '.join(changed)} — пользователь {target.username}, "
            f"выполнил {actor.username} ({actor.role})"
        ),
        tenant_id=actor.tenant_id,
    )
    db.add(entry)
    logger.info("User change by %s: %s on %s", actor.username, changed, target.username)
    return entry


@router.post("/users", response_model=schemas.UserResponse)
def create_user(data: schemas.UserCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    # Фикс аудита 10.09: проверка ловила только role == "admin", и админ мог
    # создать себе superadmin (как в update_user ниже). Ловим обе роли.
    if current_user.role != "superadmin" and data.role in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Только суперадмин может назначать роли admin/superadmin")
    existing = db.query(models.User).filter(models.User.username == data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

    # FIX 2026-09-30 (аудит прав доступа): было current_user.tenant_id. У
    # суперадмина это NULL, а users.tenant_id объявлен nullable=False и защищён
    # триггером миграции 0018 — суперадмин получал 500 вместо внятного отказа.
    # writable_tenant_id отдаёт 403 «Tenant пользователя не настроен».
    tenant_id = writable_tenant_id(current_user)
    new_user = models.User(
        username=data.username,
        hashed_password=auth.get_password_hash(data.password),
        role=data.role,
        tenant_id=tenant_id
    )
    db.add(new_user)
    db.flush()
    # FIX 2026-09-30: создание учётной записи тоже должно быть видно в ленте.
    db.add(models.ActivityLog(
        user_id=current_user.id,
        action="Создание пользователя",
        details=f"{data.username} с ролью {data.role}, создал {current_user.username}",
        tenant_id=tenant_id,
    ))
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    target = get_tenant_object(
        db, models.User, user_id, current_user, detail="Пользователь не найден"
    )
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
    # FIX 2026-09-30: удаление учётной записи — тоже след, который должен
    # остаться в ленте. Имя и роль снимаем ДО db.delete, иначе после удаления
    # объекта читать уже нечего.
    db.add(models.ActivityLog(
        user_id=current_user.id,
        action="Удаление пользователя",
        details=f"{target.username} (роль {target.role}), удалил {current_user.username}",
        tenant_id=writable_tenant_id(current_user),
    ))
    db.delete(target)
    db.commit()
    return {"detail": "Пользователь удалён"}


@router.patch("/users/{user_id}")
def update_user(user_id: int, data: schemas.UserUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_admin())):
    target = get_tenant_object(
        db, models.User, user_id, current_user, detail="Пользователь не найден"
    )
    if target.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Нельзя изменить суперадмина")
    # Прежнее состояние снимаем ДО правок: ниже роль и активность уже новые,
    # а лента должна показывать «было → стало».
    old_role, old_active = target.role, bool(target.is_active)
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
        # FIX 2026-09-30 (аудит прав доступа). Раньше пароль менялся кому угодно
        # без старого пароля цели и без единой записи в ленте: админ своего
        # tenant'а отправлял PATCH с {"password": ...} на ДРУГОГО админа и входил
        # под ним — эскалация admin→admin плюс полный захват аккаунта без следов.
        #
        # Теперь сменить пароль коллеге-админу может только владелец. Сброс
        # пароля менеджеру или сотруднику документов у админа остаётся — это
        # обычная поддержка, и ломать её было бы лишним.
        # Проверяем по ПРЕЖНЕЙ роли (old_role), а не по только что присвоенной:
        # иначе запрос вида {"role": "manager", "password": "..."} понизил бы
        # админа и тут же сбросил ему пароль, обойдя гейт одной правкой.
        if old_role in ("admin", "superadmin") and current_user.role != "superadmin":
            raise HTTPException(
                status_code=403,
                detail="Пароль администратора может сменить только владелец",
            )
        target.hashed_password = auth.get_password_hash(data.password)
    # FIX 2026-09-30: любая мутация учётной записи пишется в ленту. Раньше
    # change_own_password писал строку в лог, а update_user — ничего, поэтому по
    # ленте нельзя было понять, кто и что менял.
    _log_user_change(db, current_user, target, data, old_role, old_active)
    db.commit()
    return {"detail": "Пользователь обновлён", "is_active": bool(target.is_active)}
