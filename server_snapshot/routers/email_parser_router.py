import os
import json
import imaplib
import email
import re
import hashlib
import logging
import base64
from email.header import decode_header
from email.utils import collapse_rfc2231_value
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel
try:
    from cryptography.fernet import Fernet, InvalidToken
    _fernet_available = True
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _fernet_available = False


from auth import get_current_user, require_role, require_cron_token
from email_cleaner import clean_email_body, html_to_text, normalize_subject

logger = logging.getLogger(__name__)

# Абсолютный путь к папке загрузок. Раньше использовался относительный "uploads",
# и при смене CWD (cron, тесты, разные способы запуска) файлы сохранялись не туда,
# а в БД оставался путь относительно корня приложения. Теперь всегда привязываемся
# к директории, где лежит main.py (server_snapshot / app root).
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(_APP_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# This key encrypts stored mailbox passwords and is DIFFERENT from the JWT key in
# auth.py. It must survive image rebuilds, otherwise saved email credentials can no
# longer be decrypted, so it resolves to CRM_DATA_DIR when no legacy file is present.
def _email_key_path() -> str:
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    if os.path.exists(legacy):
        return legacy
    data_dir = os.environ.get("CRM_DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, ".email_secret_key")
    return legacy


_SECRET_KEY_FILE = _email_key_path()

def _load_secret_key() -> bytes:
    key = os.environ.get("CRM_SECRET_KEY")
    if key:
        return base64.urlsafe_b64encode(bytes.fromhex(key))
    if os.path.exists(_SECRET_KEY_FILE):
        with open(_SECRET_KEY_FILE, "r") as f:
            return base64.urlsafe_b64encode(f.read().strip().encode())
    key = os.urandom(32)
    b64_key = base64.urlsafe_b64encode(key)
    with open(_SECRET_KEY_FILE, "w") as f:
        f.write(key.hex())
    os.chmod(_SECRET_KEY_FILE, 0o600)
    return b64_key

try:
    _fernet = Fernet(_load_secret_key())
except Exception:
    _fernet = None
from database import get_db, get_tenant_db
from db_utils import resolve_tenant_db as _db
import models
import models_tenant
import schemas

router = APIRouter(
    prefix="/email-parser",
    tags=["Email Интеграция"],
    dependencies=[Depends(require_role("manager", "warehouse", "superadmin", "admin"))]
)

# Endpoints for scheduled jobs. They run without a logged-in user, so they are
# gated by a shared cron token rather than a JWT. Previously this router had no
# dependencies at all, leaving /email-parser/sync-all fully unauthenticated.
cron_router = APIRouter(
    prefix="/email-parser",
    tags=["Email Интеграция"],
    dependencies=[Depends(require_cron_token)]
)

def _get_settings_path(tenant_id: int = None) -> str:
    """Locate the mailbox settings file.

    Resolution order, same rule as the secret keys in auth.py:
      1. an existing file next to the code (legacy bare-metal layout) wins, so a
         running server keeps its current settings;
      2. otherwise CRM_DATA_DIR, which in Docker is a persistent volume.

    This file holds the mailbox password and is excluded from the image, so under
    Docker it can only live in the data volume. Resolving it relative to the code
    made /email-parser/sync fail with 400 "Настройки почты не заполнены" even
    though the settings existed.
    """
    from database import DATA_DIR

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if tenant_id:
        filename = f"email_settings_{tenant_id}.json"
        legacy = os.path.join(app_dir, "tenants", filename)
        if os.path.exists(legacy):
            return legacy
        tenants_dir = os.path.join(DATA_DIR, "tenants")
        os.makedirs(tenants_dir, exist_ok=True)
        return os.path.join(tenants_dir, filename)

    legacy = os.path.join(app_dir, "email_settings.json")
    if os.path.exists(legacy):
        return legacy
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "email_settings.json")

class EmailSettingsSchema(BaseModel):
    email: str
    password: str
    imap_server: str = ""
    target_status: str = "Новый запрос"

def load_settings(tenant_id: int = None):
    settings_file = _get_settings_path(tenant_id)
    if not os.path.exists(settings_file):
        return {
            "email": "",
            "password": "",
            "imap_server": "",
            "target_status": "Новый запрос",
            "last_sync": None
        }
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "email": "",
            "password": "",
            "imap_server": "",
            "target_status": "Новый запрос",
            "last_sync": None
        }

def _get_smtp_password(settings):
    """Read SMTP password from env first, then fall back to settings file."""
    env_pw = os.environ.get("CRM_SMTP_PASSWORD")
    if env_pw:
        return env_pw
    raw = settings.get("password", "")
    if not raw:
        return raw
    if _fernet is None:
        return raw
    try:
        return _fernet.decrypt(raw.encode()).decode()
    except InvalidToken:
        return raw

def _encrypt_password(password: str) -> str:
    if not password or _fernet is None:
        return password
    return _fernet.encrypt(password.encode()).decode()


def _decode_attachment_filename(part) -> Optional[str]:
    """Декодирует имя файла вложения из RFC 2047 / RFC 2231.

    Python 3 email.Message.get_filename() уже раскодирует оба стандарта.
    Ручной decode_header() на уже декодированной строке ломает кириллицу,
    поэтому используем значение от get_filename() как есть.
    """
    filename = part.get_filename()
    if not filename:
        return None
    return filename

def save_settings(settings, tenant_id: int = None):
    settings_file = _get_settings_path(tenant_id)
    if _fernet is not None and settings.get("password"):
        settings = dict(settings)
        settings["password"] = _encrypt_password(settings["password"])
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)

@router.get("/settings")
def get_settings(current_user: models.User = Depends(get_current_user)):
    tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
    settings = load_settings(tenant_id)
    masked_pw = ""
    if settings.get("password") or os.environ.get("CRM_SMTP_PASSWORD"):
        masked_pw = "********"
    return {
        "email": settings.get("email", ""),
        "password": masked_pw,
        "imap_server": settings.get("imap_server", ""),
        "target_status": settings.get("target_status", "Новый запрос"),
        "last_sync": settings.get("last_sync")
    }

@router.post("/settings")
def update_settings(data: EmailSettingsSchema, current_user: models.User = Depends(get_current_user)):
    tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
    settings = load_settings(tenant_id)
    # Раньше пустой imap_server молча сохранялся (или в форму подставлялся дефолт
    # imap.yandex.ru и уезжал в POST), после чего /sync ломался с AUTHENTICATIONFAILED
    # на чужом сервере. Отклоняем заведомо нерабочую конфигурацию сразу.
    if not data.imap_server.strip():
        raise HTTPException(status_code=400, detail="IMAP-сервер не указан")
    if not data.email.strip():
        raise HTTPException(status_code=400, detail="Электронная почта не указана")
    new_password_provided = data.password and data.password != "********"
    if not new_password_provided and not settings.get("password"):
        raise HTTPException(status_code=400, detail="Пароль не указан: введите пароль почтового ящика")
    settings["email"] = data.email
    if new_password_provided:
        settings["password"] = data.password
    settings["imap_server"] = data.imap_server.strip()
    settings["target_status"] = data.target_status
    save_settings(settings, tenant_id)
    return {"detail": "Настройки успешно сохранены"}

def _sync_tenant_emails(tenant_id: int, settings: dict, db: Session):
    email_addr = settings.get("email")
    password = _get_smtp_password(settings)
    imap_server = settings.get("imap_server")
    target_status = settings.get("target_status", "Новый запрос")

    if not email_addr or not password:
        return {"tenant_id": tenant_id, "success": False, "error": "Настройки почты не заполнены", "count": 0}

    imported_cards = []
    tdb = get_tenant_db(tenant_id) if tenant_id else db

    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.socket().settimeout(15)
        mail.login(email_addr, password)
        mail.select("inbox")

        from datetime import date as _date
        today = _date.today().strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(UNSEEN SINCE "{today}")')
        if status != "OK":
            raise HTTPException(status_code=500, detail="Не удалось получить список писем с почтового сервера")

        email_ids = messages[0].split()

        for e_id in email_ids:
            try:
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                if res != "OK":
                    continue

                for response in msg_data:
                    if isinstance(response, tuple):
                        msg = email.message_from_bytes(response[1])

                        subject, encoding = decode_header(msg["Subject"])[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")

                        from email.utils import parseaddr
                        raw_from = msg.get("From", "")
                        display_name, sender_email_addr = parseaddr(raw_from)
                        if display_name and "=?" in display_name:
                            decoded_parts = decode_header(display_name)
                            if decoded_parts:
                                dn_bytes, dn_enc = decoded_parts[0]
                                if isinstance(dn_bytes, bytes):
                                    display_name = dn_bytes.decode(dn_enc if dn_enc else "utf-8", errors="ignore")
                        if display_name:
                            sender = display_name
                        else:
                            sender = sender_email_addr
                        if not sender_email_addr:
                            sender_email_addr = sender

                        body = ""
                        html_body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                ctype = part.get_content_type()
                                if ctype == "text/plain" and not body:
                                    try:
                                        body = part.get_payload(decode=True).decode(errors="ignore")
                                    except Exception:
                                        pass
                                elif ctype == "text/html" and not html_body:
                                    try:
                                        html_body = part.get_payload(decode=True).decode(errors="ignore")
                                    except Exception:
                                        pass
                        else:
                            try:
                                raw = msg.get_payload(decode=True).decode(errors="ignore")
                                if msg.get_content_type() == "text/html":
                                    html_body = raw
                                else:
                                    body = raw
                            except Exception:
                                pass

                        # письма из форм на сайте часто приходят ТОЛЬКО в html
                        if not body.strip() and html_body:
                            body = html_to_text(html_body)

                        # чистим цитаты, подписи, трекинговые ссылки, режем длину
                        body = clean_email_body(body)

                        if not body:
                            body = "Текст письма пуст или недоступен"

                        attachments = []
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_disposition = str(part.get("Content-Disposition", ""))
                                if "attachment" in content_disposition:
                                    filename = _decode_attachment_filename(part)
                                    if filename:
                                        attachments.append((filename, part.get_payload(decode=True)))

                        title_str = f"Письмо: {subject}" if subject else f"Письмо без темы от {sender}"
                        if len(title_str) > 190:
                            title_str = title_str[:190] + "..."

                        # Раньше сравнивалось с display-name, поэтому клиент почти не находился
                        client = None
                        if sender_email_addr:
                            client = tdb.query(models.Client).filter(
                                models.Client.email == sender_email_addr
                            ).first()
                        if not client:
                            client = tdb.query(models.Client).filter(models.Client.email == sender).first()
                        client_id = client.id if client else None

                        # Прошлые сделки того же отправителя — для предложения связать
                        related = []
                        if sender_email_addr:
                            related = tdb.query(models.Card).filter(
                                models.Card.sender_email == sender_email_addr,
                                models.Card.is_deleted == False
                            ).order_by(models.Card.id.desc()).limit(5).all()

                        desc = f"Отправитель: {sender}"
                        if sender_email_addr and sender_email_addr != sender:
                            desc += f" <{sender_email_addr}>"
                        desc += f"\n\n{body}"
                        if related:
                            refs = ", ".join(f"#{c.id}" for c in related)
                            desc += f"\n\n— Ранее от этого отправителя: {refs}"

                        new_card = models.Card(
                            title=title_str,
                            description=desc,
                            status=target_status,
                            total_amount=0.0,
                            owner_id=tenant_id or 0,
                            client_id=client_id,
                            sender_email=sender_email_addr
                        )
                        tdb.add(new_card)
                        tdb.commit()
                        tdb.refresh(new_card)

                        for att_name, att_data in attachments:
                            if att_data:
                                safe_name = hashlib.md5(f"{new_card.id}_{att_name}".encode()).hexdigest()[:8] + "_" + re.sub(r'[^a-zA-Z0-9._-]', '_', att_name)
                                att_path = os.path.join(UPLOAD_DIR, safe_name)
                                try:
                                    with open(att_path, "wb") as f:
                                        f.write(att_data)
                                except Exception as e:
                                    logger.error(f"Failed to save attachment {att_name!r} for card {new_card.id}: {e}")
                                    raise
                                attachment = models.CardAttachment(
                                    file_name=att_name,
                                    file_path=os.path.join("uploads", safe_name),
                                    card_id=new_card.id
                                )
                                tdb.add(attachment)
                        if attachments:
                            tdb.commit()

                        log_entry = models.ActivityLog(
                            user_id=tenant_id or 0,
                            card_id=new_card.id,
                            action="Импорт из почты (cron)",
                            details=f"Импортировано письмо от {sender}: {subject}"
                        )
                        tdb.add(log_entry)
                        tdb.commit()

                        imported_cards.append({
                            "id": new_card.id,
                            "title": new_card.title,
                            "sender": sender,
                            "subject": subject
                        })

                        mail.store(e_id, "+FLAGS", "\\Seen")
            except Exception as e:
                logger.warning(f"Failed to process email {e_id} for tenant {tenant_id}: {e}")
                continue

        mail.logout()

        settings["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_settings(settings, tenant_id)

        return {
            "tenant_id": tenant_id,
            "success": True,
            "count": len(imported_cards),
            "cards": imported_cards
        }

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error for tenant {tenant_id}: {e}")
        return {"tenant_id": tenant_id, "success": False, "error": f"Ошибка авторизации на почтовом сервере: {e}", "count": 0}
    except TimeoutError:
        return {"tenant_id": tenant_id, "success": False, "error": "Превышено время ожидания при подключении к почте", "count": 0}
    except Exception as e:
        logger.error(f"Email sync error for tenant {tenant_id}: {type(e).__name__}: {e}")
        return {"tenant_id": tenant_id, "success": False, "error": f"Ошибка подключения к почте: {e}", "count": 0}


@router.post("/sync")
def sync_emails(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
    settings = load_settings(tenant_id)
    email_addr = settings.get("email")
    password = _get_smtp_password(settings)
    imap_server = settings.get("imap_server")
    target_status = settings.get("target_status", "Новый запрос")

    if not email_addr or not password:
        raise HTTPException(status_code=400, detail="Настройки почты не заполнены")

    result = _sync_tenant_emails(tenant_id, settings, db)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@cron_router.post("/sync-all")
def sync_all_tenants(db: Session = Depends(get_db)):
    results = []
    # Главный аккаунт (без тенанта) — именно в нём лежат все рабочие карточки.
    # Раньше он не синхронизировался вообще: цикл шёл только по таблице tenants.
    try:
        results.append(_sync_tenant_emails(None, load_settings(None), db))
    except Exception as e:
        logger.error(f"main account sync failed: {e}")
        results.append({"tenant_id": None, "success": False, "error": str(e), "count": 0})

    tenants = db.query(models_tenant.Tenant).all()
    for tenant in tenants:
        settings = load_settings(tenant.id)
        result = _sync_tenant_emails(tenant.id, settings, db)
        results.append(result)

    total_count = sum(r.get("count", 0) for r in results if r.get("success"))
    errors = [r for r in results if not r.get("success")]

    return {
        "success": True,
        "total_count": total_count,
        "tenants_processed": len(results),
        "tenants_failed": len(errors),
        "results": results
    }


class LinkCardRequest(BaseModel):
    target_card_id: int


@router.get("/related/{card_id}")
def get_related_cards(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Прошлые сделки того же отправителя — чтобы предложить связать, а не склеивать вслепую.

    Тему письма намеренно НЕ используем: заявки с сайта всегда приходят
    с одинаковым заголовком, но это разные клиенты и разные сделки.
    """
    tdb = _db(current_user, db)
    try:
        card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if not card.sender_email:
            return {"card_id": card_id, "sender_email": None, "related": []}
        rows = tdb.query(models.Card).filter(
            models.Card.sender_email == card.sender_email,
            models.Card.id != card_id,
            models.Card.is_deleted == False
        ).order_by(models.Card.id.desc()).limit(10).all()
        return {
            "card_id": card_id,
            "sender_email": card.sender_email,
            "related": [
                {"id": c.id, "title": c.title, "status": c.status,
                 "created_at": c.created_at.isoformat() if c.created_at else None,
                 "total_amount": float(c.total_amount or 0)}
                for c in rows
            ],
        }
    finally:
        if tdb is not db:
            tdb.close()


@router.post("/link/{card_id}")
def link_card_to_existing(card_id: int, payload: LinkCardRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Переносит текст письма комментарием в целевую сделку и удаляет письмо-дубль."""
    tdb = _db(current_user, db)
    try:
        src = tdb.query(models.Card).filter(models.Card.id == card_id).first()
        dst = tdb.query(models.Card).filter(models.Card.id == payload.target_card_id).first()
        if not src or not dst:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        stamp = src.created_at.strftime("%d.%m.%Y %H:%M") if src.created_at else ""
        addition = f"\n\n--- Письмо от {stamp} ---\n{src.title}\n{src.description or ''}"
        dst.description = (dst.description or "") + addition
        dst.updated_at = datetime.now(timezone.utc)

        tdb.add(models.ActivityLog(
            user_id=current_user.id,
            card_id=dst.id,
            action="Связано письмо",
            details=f"Письмо из карточки #{src.id} перенесено в сделку #{dst.id}",
        ))
        src.is_deleted = True
        tdb.commit()
        return {"success": True, "target_card_id": dst.id, "message": f"Письмо добавлено в сделку #{dst.id}"}
    finally:
        if tdb is not db:
            tdb.close()
