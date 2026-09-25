import contextlib
import email
import hashlib
import imaplib
import json
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from limiter_config import limiter

from auth import get_current_user, require_cron_token, require_role
from database import get_db
from email_cleaner import clean_email_body, html_to_text
from email_credentials import (
    EmailEncryptionConfigError,
    InvalidCredentialCiphertextError,
    LegacyPlaintextError,
    decrypt_credential,
    encrypt_credential,
    obtain_fernet,
)
import models
import models_tenant
from secure_files import atomic_write_private
from tenant_policy import configured_legacy_tenant_id

logger = logging.getLogger(__name__)

# Абсолютный путь к папке загрузок. Раньше использовался относительный "uploads",
# и при смене CWD (cron, тесты, разные способы запуска) файлы сохранялись не туда,
# а в БД оставался путь относительно корня приложения. Теперь всегда привязываемся
# к директории, где лежит main.py (server_snapshot / app root).
# FIX 2026-08-29: как в card_details_router — CRM_UPLOADS_DIR или CRM_DATA_DIR/uploads
# позволяют хранить загрузки вне веб-корна.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.abspath(
    os.environ.get("CRM_UPLOADS_DIR")
    or os.path.join(os.environ.get("CRM_DATA_DIR") or _APP_DIR, "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _ensure_inside_data_dir(path: str) -> str:
    """Защита от выхода за каталог данных: файлы ключей и настроек пишутся
    только внутрь CRM_DATA_DIR (или каталога приложения)."""
    root = os.path.realpath(os.environ.get("CRM_DATA_DIR", _APP_DIR))
    resolved = os.path.realpath(path)
    if not (resolved == root or resolved.startswith(root + os.sep)):
        raise RuntimeError(f"Путь {path!r} вне каталога данных")
    return resolved

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

def _get_settings_path(tenant_id: int | None = None) -> str:
    """Locate the mailbox settings file.

    Resolution order, same rule as the secret keys in auth.py:
      1. when CRM_DATA_DIR is explicitly configured — the data dir always wins
         (FIX 18.09: a legacy file next to the code used to shadow it for
         reads while save_settings was guard-blocked from writing there,
         so every sync crashed AFTER importing mail and last_sync froze);
      2. otherwise (bare-metal without data dir) an existing file next to the
         code keeps a running server on its current settings;
      3. otherwise CRM_DATA_DIR / default dir.

    This file holds the mailbox password and is excluded from the image, so under
    Docker it can only live in the data volume. Resolving it relative to the code
    made /email-parser/sync fail with 400 "Настройки почты не заполнены" even
    though the settings existed.
    """
    from database import DATA_DIR

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if tenant_id:
        filename = f"email_settings_{tenant_id}.json"
        tenants_dir = os.path.join(DATA_DIR, "tenants")
        if not os.environ.get("CRM_DATA_DIR"):
            legacy = os.path.join(app_dir, "tenants", filename)
            if os.path.exists(legacy):
                return legacy
        os.makedirs(tenants_dir, exist_ok=True)
        return os.path.join(tenants_dir, filename)

    if not os.environ.get("CRM_DATA_DIR"):
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

def load_settings(tenant_id: int | None = None):
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
    """Получить пароль синхронизации с точным приоритетом источников.

    ``CRM_SMTP_PASSWORD`` — явный override оператора и используется только
    когда в settings нет сохранённого пароля. Если пароль уже настроен,
    override игнорируется: битый ключ, legacy plaintext или неверный токен
    обязаны закрыть синк. Fernet проверяется до выбора override, поэтому
    переменная окружения не маскирует неверную криптографическую настройку.
    """
    fernet = obtain_fernet()
    stored = settings.get("password", "")
    override = os.environ.get("CRM_SMTP_PASSWORD")
    if not stored:
        return override or ""
    return decrypt_credential(str(stored), fernet)


def _decode_part_text(part) -> str:
    """Декодирует текстовую часть письма с её собственной кодировкой.

    Раньше байты декодировались как UTF-8 с errors='ignore': письма в
    windows-1251 / koi8-r (типично для госпочты) теряли все не-UTF8 байты,
    и текст приходил «замыленным» — с пропусками букв.
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = (part.get_content_charset() or "utf-8").strip().lower()
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        # неизвестная/битая кодировка — пробуем популярные для СНГ, затем utf-8
        for fallback in ("windows-1251", "koi8-r", "utf-8"):
            try:
                return payload.decode(fallback, errors="replace")
            except Exception as e:
                logger.debug(f"Не удалось декодировать письмо как {fallback}: {e}")
        return payload.decode("utf-8", errors="replace")


def _decode_attachment_filename(part) -> Optional[str]:
    """Декодирует имя файла вложения из RFC 2047 / RFC 2231.

    get_filename() раскрывает RFC 2231 (filename*=utf-8''...), но НЕ трогает
    encoded-words RFC 2047 (=?UTF-8?B?...?=), которыми Outlook кодирует имена
    вложений в нарушение стандарта. Без ручного декодирования такие имена
    попадают в БД сырыми: с переносами строк и base64-мусором, ломают
    заголовок Content-Disposition при скачивании и нечитаемы в интерфейсе.
    """
    filename = part.get_filename()
    if not filename:
        return None
    if "=?" in filename:
        try:
            filename = str(make_header(decode_header(filename)))
        except Exception:
            logger.warning("Failed to decode attachment filename %r", filename)
    # в encoded-word могут остаться переносы строк между частями
    filename = re.sub(r"[\r\n\t]+", " ", filename).strip()
    return filename or None

def save_settings(
    settings,
    tenant_id: int | None = None,
    *,
    preserve_encrypted_password: bool = False,
):
    """Атомарно сохранить настройки и всегда хранить пароль в Fernet-форме.

    Обычный внутренний выход encrypt уже принятый plaintext. Путь
    ``preserve_encrypted_password`` есть только для неизменённого секрета при
    сохранении маски или служебного ``last_sync``; значение сначала строго
    проверяется расшифровкой, префикс не используется как доказательство.
    """
    settings_file = _ensure_inside_data_dir(_get_settings_path(tenant_id))
    stored = dict(settings)
    if stored.get("password"):
        fernet = obtain_fernet()
        if preserve_encrypted_password:
            decrypt_credential(str(stored["password"]), fernet)
        else:
            stored["password"] = encrypt_credential(str(stored["password"]), fernet)
    atomic_write_private(
        settings_file,
        json.dumps(stored, ensure_ascii=False, indent=4),
    )

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

def _credential_http_error(exc: Exception) -> HTTPException:
    """Единая контролируемая ошибка без key/token/пароля и raw exception."""
    if isinstance(exc, EmailEncryptionConfigError):
        return HTTPException(
            status_code=503,
            detail="Шифрование пароля почты не настроено корректно",
        )
    return HTTPException(status_code=409, detail=str(exc))


# Настройки ящика — это адрес, IMAP-сервер и пароль разбора почты CRM:
# менеджер или склад, перезаписав их, могут увести разбор писем на чужой
# сервер. Запись — только админ (находка воркера пункта 17, 23.09).
@router.post("/settings",
             dependencies=[Depends(require_role("admin", "superadmin"))])
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
    # "********" — не секрет, а маска: UI присылает её вместо пароля, который
    # не меняли, чтобы не передавать настоящий пароль туда-обратно.
    new_password_provided = data.password and data.password != "********"  # noqa: S105
    stored_password = settings.get("password")
    if not new_password_provided and not stored_password:
        raise HTTPException(status_code=400, detail="Пароль не указан: введите пароль почтового ящика")

    # Новый пароль всегда считается plaintext, даже если текст случайно
    # начинается с gAAAA. Существующий секрет при маске не перешифровывается,
    # но сначала обязан пройти реальную расшифровку.
    try:
        fernet = obtain_fernet()
        if new_password_provided:
            settings["password"] = encrypt_credential(data.password, fernet)
        else:
            decrypt_credential(str(stored_password), fernet)
    except (
        EmailEncryptionConfigError,
        InvalidCredentialCiphertextError,
        LegacyPlaintextError,
    ) as exc:
        raise _credential_http_error(exc) from None
    settings["email"] = data.email
    settings["imap_server"] = data.imap_server.strip()
    settings["target_status"] = data.target_status
    save_settings(
        settings,
        tenant_id,
        preserve_encrypted_password=True,
    )
    return {"detail": "Настройки успешно сохранены"}

def _search_unseen(mail, today: str) -> list:
    """Ищет непрочитанные письма за сегодня (UTC) и возвращает их id.

    Raise вынесен из try-блока синка в helper: статус != OK поднимает 500
    здесь (TRY301), снаружи он по-прежнему ловится общим except Exception.
    """
    status, messages = mail.search(None, f'(UNSEEN SINCE "{today}")')
    if status != "OK":
        raise HTTPException(status_code=500, detail="Не удалось получить список писем с почтового сервера")
    return messages[0].split()


def _resolve_sync_tenant_id(
    db: Session,
    settings: dict,
    tenant_id: int | None,
) -> int | None:
    """Определить tenant для импорта почты.

    Сначала используется tenant вызывающего tenant-синка, затем явный
    ``tenant_id`` из настроек главного ящика, legacy-конфигурация и только
    после этого — единственный tenant из БД. При нескольких tenant нельзя
    молча выбирать первую строку: это может импортировать письмо в чужую
    компанию, поэтому вызывающий код должен показать владельцу ошибку.
    """
    if tenant_id is not None:
        return tenant_id

    raw_tenant_id = settings.get("tenant_id")
    if raw_tenant_id is not None and str(raw_tenant_id).strip():
        try:
            if isinstance(raw_tenant_id, bool):
                raise ValueError("tenant_id должен быть положительным целым числом")
            if isinstance(raw_tenant_id, str):
                settings_tenant_id = int(raw_tenant_id.strip())
            elif isinstance(raw_tenant_id, int):
                settings_tenant_id = raw_tenant_id
            else:
                raise ValueError("tenant_id должен быть положительным целым числом")
            if settings_tenant_id <= 0:
                raise ValueError("tenant_id должен быть положительным целым числом")
        except (TypeError, ValueError):
            logger.warning(
                "Некорректный tenant_id в настройках почты: %r; "
                "применяется следующий источник",
                raw_tenant_id,
            )
        else:
            return settings_tenant_id

    legacy_tenant_id = configured_legacy_tenant_id()
    if legacy_tenant_id is not None:
        return legacy_tenant_id

    tenant_ids = db.query(models_tenant.Tenant.id).order_by(
        models_tenant.Tenant.id
    ).all()
    if len(tenant_ids) == 1:
        return tenant_ids[0][0]
    return None


def _sync_tenant_emails(tenant_id: int | None, settings: dict, db: Session):
    resolved_tenant_id = _resolve_sync_tenant_id(db, settings, tenant_id)
    if resolved_tenant_id is None:
        logger.warning(
            "Не удалось определить tenant для импорта писем: "
            "укажите tenant в настройках почты"
        )
        return {
            "tenant_id": None,
            "success": False,
            "error": "Не удалось определить tenant для импорта писем: укажите tenant в настройках почты",
            "count": 0,
        }

    email_addr = settings.get("email")
    imap_server = settings.get("imap_server")
    target_status = settings.get("target_status", "Новый запрос")

    imported_cards = []
    mail = None

    try:
        password = _get_smtp_password(settings)
        if not email_addr or not password:
            return {"tenant_id": resolved_tenant_id, "success": False, "error": "Настройки почты не заполнены", "count": 0}
        # FIX 2026-09-06 (аудит): таймаут на сам TCP-connect. Раньше settimeout
        # ставился уже после конструктора — «зависший» IMAP-хост подвешивал
        # запрос/cron на системный таймаут (минуты).
        try:
            mail = imaplib.IMAP4_SSL(imap_server, timeout=15)
        except TypeError:  # Python < 3.9: параметра timeout ещё нет
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.socket().settimeout(15)
        mail.login(email_addr, password)
        mail.select("inbox")

        # Дата для IMAP SINCE — в UTC, как и всё остальное в проекте (миграция
        # 0003). date.today() давал локальную дату сервера: ночью фильтр
        # уходил на сутки вперёд относительно UTC и отрезал письма последних
        # часов, то есть часть входящих не импортировалась.
        today = datetime.now(timezone.utc).strftime("%d-%b-%Y")
        email_ids = _search_unseen(mail, today)

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
                        sender = display_name or sender_email_addr
                        if not sender_email_addr:
                            sender_email_addr = sender

                        body = ""
                        html_body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                ctype = part.get_content_type()
                                if ctype == "text/plain" and not body:
                                    body = _decode_part_text(part)
                                elif ctype == "text/html" and not html_body:
                                    html_body = _decode_part_text(part)
                        else:
                            raw = _decode_part_text(msg)
                            if msg.get_content_type() == "text/html":
                                html_body = raw
                            else:
                                body = raw

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
                            client = db.query(models.Client).filter(
                                models.Client.email == sender_email_addr
                            ).first()
                        if not client:
                            client = db.query(models.Client).filter(models.Client.email == sender).first()
                        client_id = client.id if client else None

                        # Прошлые сделки того же отправителя здесь НЕ выбираются:
                        # их отдаёт GET /email-parser/related/{card_id}, а карточка
                        # рисует блоком renderRelatedCards. Прежний запрос
                        # выполнялся на каждое импортированное письмо, а результат
                        # выбрасывался — переменная related не читалась.

                        # Description (поле заметки) не заполняем: отправитель
                        # хранится в sender_email и в записи ленты, текст письма —
                        # в ленте «Импорт почты». Прошлые сделки отправителя
                        # показываются отдельным блоком в карточке
                        # (renderRelatedCards) — дублировать их в заметке не нужно.
                        desc = ""

                        new_card = models.Card(
                            title=title_str,
                            description=desc,
                            status=target_status,
                            total_amount=0.0,
                            # FIX 2026-08-29: было `tenant_id or 0` — в БД
                            # появлялась висячая ссылка на несуществующего
                            # пользователя id=0. NULL = «без ответственного».
                            owner_id=None,
                            client_id=client_id,
                            sender_email=sender_email_addr,
                            tenant_id=resolved_tenant_id,
                        )
                        db.add(new_card)
                        db.commit()
                        db.refresh(new_card)

                        for att_name, att_data in attachments:
                            if att_data:
                                # md5 не для защиты, а чтобы коротко и стабильно
                                # различать одноимённые вложения разных карточек.
                                digest = hashlib.md5(
                                    f"{new_card.id}_{att_name}".encode(),
                                    usedforsecurity=False,
                                ).hexdigest()[:8]
                                safe_name = digest + "_" + re.sub(r'[^a-zA-Z0-9._-]', '_', att_name)
                                att_path = os.path.join(UPLOAD_DIR, safe_name)
                                # Имя вложения приходит из внешнего письма —
                                # сохраняем строго внутри UPLOAD_DIR.
                                if not os.path.realpath(att_path).startswith(os.path.realpath(UPLOAD_DIR) + os.sep):
                                    logger.warning("Rejected unsafe attachment name %r", att_name)
                                    continue
                                try:
                                    Path(att_path).write_bytes(att_data)
                                except Exception:
                                    logger.exception(f"Failed to save attachment {att_name!r} for card {new_card.id}")
                                    raise
                                attachment = models.CardAttachment(
                                    file_name=att_name,
                                    file_path=os.path.join("uploads", safe_name),
                                    card_id=new_card.id
                                )
                                db.add(attachment)
                        if attachments:
                            db.commit()

                        sender_line = sender
                        if sender_email_addr and sender_email_addr != sender:
                            sender_line += f" <{sender_email_addr}>"
                        log_entry = models.ActivityLog(
                            user_id=None,
                            card_id=new_card.id,
                            action="Импорт почты",
                            details=f"От: {sender_line}\nТема: {subject or '—'}\n\n{body}"[:4000],
                            tenant_id=resolved_tenant_id,
                        )
                        db.add(log_entry)
                        db.commit()

                        imported_cards.append({
                            "id": new_card.id,
                            "title": new_card.title,
                            "sender": sender,
                            "subject": subject
                        })

                        mail.store(e_id, "+FLAGS", "\\Seen")
            except Exception as e:
                logger.warning(f"Failed to process email {e_id} for tenant {resolved_tenant_id}: {e}")
                continue

        settings["last_sync"] = datetime.now(timezone.utc).isoformat()
        try:
            save_settings(
                settings,
                tenant_id,
                preserve_encrypted_password=True,
            )
        except Exception:
            # FIX 18.09: сбой записи настроек (last_sync) — бухгалтерия, а не
            # импорт. Раньше он перечёркивал успешный синк: письма уже
            # обработаны, а ответ уходил «Ошибка подключения к почтовому
            # серверу» каждую итерацию watchdog.
            logger.exception(f"Не удалось сохранить last_sync (tenant {tenant_id}) — импорт выполнен")

        return {
            "tenant_id": resolved_tenant_id,
            "success": True,
            "count": len(imported_cards),
            "cards": imported_cards
        }

    except (
        EmailEncryptionConfigError,
        InvalidCredentialCiphertextError,
        LegacyPlaintextError,
    ):
        return {
            "tenant_id": resolved_tenant_id,
            "success": False,
            "error": "Не удалось безопасно использовать сохранённый пароль почты",
            "count": 0,
        }
    except imaplib.IMAP4.error:
        # FIX 2026-09-06 (аудит С7): текст исключения почтового сервера раньше
        # уходил клиенту в detail (там бывают имя хоста и данные сессии) —
        # наружу только обобщённая формулировка, детали в логе выше.
        logger.exception(f"IMAP error for tenant {resolved_tenant_id}")
        return {"tenant_id": resolved_tenant_id, "success": False, "error": "Ошибка авторизации на почтовом сервере: проверьте логин и пароль ящика", "count": 0}
    except TimeoutError:
        return {"tenant_id": resolved_tenant_id, "success": False, "error": "Превышено время ожидания при подключении к почте", "count": 0}
    except Exception:
        logger.exception(f"Email sync error for tenant {resolved_tenant_id}")
        return {"tenant_id": resolved_tenant_id, "success": False, "error": "Ошибка подключения к почтовому серверу", "count": 0}
    finally:
        # FIX 2026-09-06 (аудит): logout был только на успехе — при ошибке
        # посреди выборки соединение с IMAP оставалось висеть до таймаута.
        if mail is not None:
            with contextlib.suppress(Exception):
                mail.logout()


@router.post("/sync", dependencies=[Depends(require_role("admin", "superadmin"))])
# V12 (пункт 9 плана v2): запуск синхронизации — вместе с настройками ящика
# (POST /settings, V11) под админом. Синк забирает ВСЮ переписку ящика и сам
# создаёт карточки в реестре; кнопка «Синхронизировать» на «Управлении» от
# manager скрыта, так что сервер должен отвечать так же, а не только прятать
# кнопку. По почтам по-прежнему проходят GET (вкладка «Письма отправителя» в
# карточке сделки, пункт 8) и POST /link (связка дубля со своей сделкой).
# FIX 2026-09-06 (аудит С3): синк ходит на внешний IMAP и может зваться
# многократно подряд из UI — ограничиваем. nginx должен входить в
# CRM_TRUSTED_PROXY_NETWORKS, иначе лимит общий на всех.
@limiter.limit("5/minute")
def sync_emails(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
    settings = load_settings(tenant_id)
    email_addr = settings.get("email")
    try:
        password = _get_smtp_password(settings)
    except (
        EmailEncryptionConfigError,
        InvalidCredentialCiphertextError,
        LegacyPlaintextError,
    ) as exc:
        raise _credential_http_error(exc) from None
    # imap_server и target_status читает сам _sync_tenant_emails; здесь они
    # оставались со времён до выделения этой функции и не использовались.

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
    except Exception:
        logger.exception("main account sync failed")
        results.append({
            "tenant_id": None,
            "success": False,
            "error": "Ошибка синхронизации почты",
            "count": 0,
        })

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
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if not card.sender_email:
        return {"card_id": card_id, "sender_email": None, "related": []}
    rows = db.query(models.Card).filter(
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


@router.post("/link/{card_id}")
def link_card_to_existing(card_id: int, payload: LinkCardRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Переносит текст письма комментарием в целевую сделку и удаляет письмо-дубль."""
    src = db.query(models.Card).filter(models.Card.id == card_id).first()
    dst = db.query(models.Card).filter(models.Card.id == payload.target_card_id).first()
    if not src or not dst:
        raise HTTPException(status_code=404, detail="Карточка не найдена")

    stamp = src.created_at.strftime("%d.%m.%Y %H:%M") if src.created_at else ""
    addition = f"\n\n--- Письмо от {stamp} ---\n{src.title}\n{src.description or ''}"
    dst.description = (dst.description or "") + addition
    dst.updated_at = datetime.now(timezone.utc)

    db.add(models.ActivityLog(
        user_id=current_user.id,
        card_id=dst.id,
        action="Связано письмо",
        details=f"Письмо из карточки #{src.id} перенесено в сделку #{dst.id}",
        tenant_id=current_user.tenant_id,
    ))
    src.is_deleted = True
    db.commit()
    return {"success": True, "target_card_id": dst.id, "message": f"Письмо добавлено в сделку #{dst.id}"}
