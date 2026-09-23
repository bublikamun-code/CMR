import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

import models
from database import get_db

logger = logging.getLogger(__name__)

_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
_CRON_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cron_token")

def _load_or_create_key(path: str, env_var: str) -> str:
    key = os.environ.get(env_var)
    if key:
        return key
    if os.path.exists(path):
        with open(path, "r") as f:
            existing = f.read().strip()
        if existing:
            return existing
    key = secrets.token_hex(32)
    Path(path).write_text(key)
    os.chmod(path, 0o600)
    return key

def _load_or_create_secret() -> str:
    return _load_or_create_key(_KEY_FILE, "CRM_SECRET_KEY")

SECRET_KEY = _load_or_create_secret()

# Separate credential for unattended cron jobs. These call endpoints that have
# no user context, so they cannot present a JWT. Note this host is shared:
# other tenants can reach 127.0.0.1:<port>, so a loopback-only check would not
# actually restrict anything. The token is the control that matters.
CRON_TOKEN = _load_or_create_key(_CRON_TOKEN_FILE, "CRM_CRON_TOKEN")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
# «Запомнить меня»: срок токена/куки при включённой опции (фидбек 07.09 —
# пользователи вылетали каждые 24 часа).
REMEMBER_MINUTES = 30 * 24 * 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_minutes: int | None = None):
    import uuid
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

def get_current_user(request: Request, response: Response, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    # P2-1 (аудит 04.09): основной носитель токена — httpOnly-cookie
    # (нечитаема из JS), Authorization-заголовок оставлен как переходный
    # путь (admin.html, старые сессии). Приоритет: заголовок, затем cookie.
    if not token:
        token = request.cookies.get("crm_token")
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить токен",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        tenant_id: int = payload.get("tenant_id")
        if username is None:
            raise credentials_exception
    except jwt.ExpiredSignatureError as e:
        raise credentials_exception from e
    except jwt.PyJWTError as e:
        raise credentials_exception from e

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    # Пункт 15 плана v2: отключение действует сразу, без ожидания истечения
    # токена. 401 (а не 403) — чтобы фронт отработал существующим хуком
    # «сбросить токен → экран входа» и в v2, и в legacy.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь отключён. Обратитесь к администратору.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # UI FIX 2026-08-26: claim pv (password version) — первые 8 символов
    # текущего bcrypt-хэша. Токены, выпущенные ДО смены пароля, получают 401.
    # Токены без pv (старые, до внедрения) считаются валидными — иначе
    # деплой разлогинил бы всех пользователей разом.
    token_pv = payload.get("pv")
    if token_pv is not None and token_pv != user.hashed_password[:8]:
        raise credentials_exception

    # Скользящее продление (фидбек 07.09 «вылетает из аккаунта»): когда
    # токен прожил больше половины своего срока, активному пользователю
    # выпускается свежая кука с полным сроком (24ч, у «Запомнить меня» —
    # 30 дней). Смена пароля инвалидирует и новые токены (claim pv).
    exp = payload.get("exp")
    if exp and response is not None:
        lifetime = REMEMBER_MINUTES * 60 if payload.get("rm") else ACCESS_TOKEN_EXPIRE_MINUTES * 60
        remaining = exp - datetime.now(timezone.utc).timestamp()
        if remaining < lifetime / 2:
            fresh_data = {"sub": username, "tenant_id": tenant_id, "pv": user.hashed_password[:8]}
            if payload.get("rm"):
                fresh_data["rm"] = 1
            minutes = REMEMBER_MINUTES if payload.get("rm") else ACCESS_TOKEN_EXPIRE_MINUTES
            fresh = create_access_token(fresh_data, expires_minutes=minutes)
            response.set_cookie(
                key="crm_token",
                value=fresh,
                httponly=True,
                samesite="lax",
                secure=(request.url.scheme == "https"),
                max_age=minutes * 60,
                path="/",
            )
    return user

def require_role(*allowed_roles):
    def role_checker(current_user: models.User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return current_user
    return role_checker

def require_superadmin():
    def checker(current_user: models.User = Depends(get_current_user)):
        if current_user.role != "superadmin":
            raise HTTPException(status_code=403, detail="Только суперадмин")
        return current_user
    return checker

def require_admin():
    def checker(current_user: models.User = Depends(get_current_user)):
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Только администратор")
        return current_user
    return checker

def require_cron_token(x_cron_token: str = Header(default="", alias="X-Cron-Token")):
    """Guard for endpoints invoked by scheduled jobs instead of by a user.

    Compared with compare_digest so a wrong token cannot be recovered by
    timing the response.
    """
    if not x_cron_token or not secrets.compare_digest(x_cron_token, CRON_TOKEN):
        logger.warning("Rejected cron request with missing or invalid token")
        raise HTTPException(status_code=403, detail="Недействительный токен")
    return True
