import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from database import get_db
from runtime_config import auth_cookie_secure
from secure_files import atomic_write_private

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
    atomic_write_private(path, key)
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


def set_auth_cookie(response: Response, value: str, max_age: int) -> None:
    """Установить auth-cookie по единой явной security policy.

    ``request.url.scheme`` и ``X-Forwarded-Proto`` намеренно не участвуют в
    выборе ``Secure``: значение ``CRM_COOKIE_SECURE`` валидируется при старте,
    а spoofed forwarded-заголовок не может изменить политику.
    """
    response.set_cookie(
        key="crm_token",
        value=value,
        httponly=True,
        samesite="lax",
        secure=auth_cookie_secure(),
        max_age=max_age,
        path="/",
    )


def password_version(hashed_password: str) -> str:
    """Получить безопасную версию пароля для claim ``pv``.

    В JWT попадает только SHA-256 от bcrypt-хэша, а не его часть. Короткий
    префикс bcrypt недостаточен: разные хэши могут закономерно иметь одинаковые
    первые символы, из-за чего ротация пароля оставляла бы старый токен валидным.
    """
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()


def create_access_token(data: dict, expires_minutes: int | None = None):
    import uuid
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token_metadata(token: str) -> dict:
    """Проверить подпись JWT и вернуть его метаданные.

    PyJWT также проверяет ``exp``/``nbf``, если соответствующие claims есть.
    Отсутствие ``exp`` допустимо: старые токены могут быть бессрочными, а в
    таблице отзыва срок хранения остаётся nullable.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# Имя оставлено явным для кода, которому нужно именно проверить токен перед
# использованием, а не только получить payload.
verify_token_metadata = decode_token_metadata


def derive_token_key(token: str, payload: dict) -> str:
    """Получить стабильный ключ JWT без сохранения самого токена."""
    jti = payload.get("jti")
    if jti:
        return f"jti:{jti}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{token_hash}"


def is_token_key_revoked(db: Session, token_key: str) -> bool:
    """Проверить наличие ключа в общем серверном списке отзыва."""
    return db.query(models.RevokedAuthToken).filter(
        models.RevokedAuthToken.token_key == token_key
    ).first() is not None


def _token_expiry(payload: dict) -> datetime | None:
    exp = payload.get("exp")
    if exp is None:
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def revoke_token(db: Session, token: str) -> bool:
    """Идемпотентно отозвать подписанный и не истёкший токен.

    Возвращает ``True``, если запись добавлена этим вызовом. Повторный вызов
    и гонка между двумя процессами завершаются безопасно: уникальный индекс
    не даёт создать вторую запись, а ``IntegrityError`` трактуется как уже
    выполненный отзыв. Ошибки декодирования здесь намеренно скрываются —
    logout идемпотентен и не должен превращать плохой токен в 500.
    """
    if not token:
        return False
    try:
        payload = decode_token_metadata(token)
    except (jwt.PyJWTError, TypeError, ValueError):
        return False

    token_key = derive_token_key(token, payload)
    if is_token_key_revoked(db, token_key):
        return False

    db.add(models.RevokedAuthToken(
        token_key=token_key,
        expires_at=_token_expiry(payload),
        revoked_at=datetime.now(timezone.utc),
    ))
    try:
        db.commit()
    except IntegrityError:
        # Другой worker мог успеть записать тот же клють между SELECT и INSERT.
        db.rollback()
        return False
    return True


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
        payload = decode_token_metadata(token)
        username: str = payload.get("sub")
        tenant_id = payload.get("tenant_id")
        # tenant_id — обязательный claim, а не подсказка. Принимаем только
        # настоящий int (bool в Python — subclass int), чтобы строковые и
        # неоднозначные значения не проходили как «tenant с ID 3».
        if (
            username is None
            or isinstance(tenant_id, bool)
            or not isinstance(tenant_id, int)
        ):
            raise credentials_exception
    except jwt.ExpiredSignatureError as e:
        raise credentials_exception from e
    except jwt.PyJWTError as e:
        raise credentials_exception from e

    # Проверка отзыва обязана идти до любого скользящего продления: новый
    # cookie не должен воскресить уже отозванный ключ.
    if is_token_key_revoked(db, derive_token_key(token, payload)):
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    # Claim фиксирует tenant на момент выпуска сессии. Любое расхождение с
    # текущей записью пользователя запрещено: это и смена tenant, и подмена
    # claim в старом токене. NULL тоже запрещён — 0018 обязан нормализовать
    # legacy-данные до выдачи tenant-bound сессий.
    if user.tenant_id is None or tenant_id != user.tenant_id:
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
    # UI FIX 2026-08-26: claim pv (password version) — SHA-256 от текущего
    # bcrypt-хэша. Токены, выпущенные ДО смены пароля, получают 401.
    # После миграционного окна pv обязателен: legacy-токен без claim не должен
    # переживать смену пароля.
    token_pv = payload.get("pv")
    if (not isinstance(token_pv, str) or not token_pv
            or not secrets.compare_digest(
                token_pv.encode("utf-8"),
                password_version(user.hashed_password).encode("ascii"),
            )):
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
            fresh_data = {
                "sub": username,
                "tenant_id": tenant_id,
                "pv": password_version(user.hashed_password),
            }
            if payload.get("rm"):
                fresh_data["rm"] = 1
            minutes = REMEMBER_MINUTES if payload.get("rm") else ACCESS_TOKEN_EXPIRE_MINUTES
            fresh = create_access_token(fresh_data, expires_minutes=minutes)
            set_auth_cookie(response, fresh, minutes * 60)
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
