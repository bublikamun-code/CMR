import os
import secrets
import logging
import jwt
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import models
from database import get_db

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _key_path(filename: str) -> str:
    """Resolve a secret file path.

    Priority: an existing file next to the code (legacy bare-metal layout) wins so
    running servers keep their current key. Otherwise the file is created in
    CRM_DATA_DIR, which in Docker is a persistent volume - without this the key
    would be regenerated on every image rebuild and log every user out.
    """
    legacy = os.path.join(_APP_DIR, filename)
    if os.path.exists(legacy):
        return legacy
    data_dir = os.environ.get("CRM_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, filename)
    return legacy


_KEY_FILE = _key_path(".secret_key")
_CRON_TOKEN_FILE = _key_path(".cron_token")

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
    with open(path, "w") as f:
        f.write(key)
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

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    import uuid
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить токен",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        tenant_id: int = payload.get("tenant_id")
        if username is None:
            raise credentials_exception
    except jwt.ExpiredSignatureError:
        raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
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
