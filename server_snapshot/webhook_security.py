"""Отдельный ключ и шифрование секретов webhook.

Ключ webhook никогда не берётся из ключа JWT или ключа почты. Основной
источник — ``CRM_WEBHOOK_ENCRYPTION_KEY``; канонический файл —
``$CRM_DATA_DIR/.webhook_encryption_key``. В production отсутствие источника
ошибка конфигурации; генерация файла допустима только вне production.
"""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
import tempfile
from typing import Mapping

from runtime_config import deployment_mode

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - отдельные тесты имитируют отсутствие пакета
    Fernet = None  # type: ignore[assignment]

    class InvalidToken(Exception):
        """Заглушка для тестового сценария без cryptography."""


_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FERNET_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}=$")
_FERNET_TOKEN_PREFIX = "gAAAA"
_FERNET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{80,8192}=*$")
_RAW_KEY_LENGTH = 32
_FERNET_KEY_LENGTH = 44
MAX_SECRET_LENGTH = 4096
MAX_CIPHERTEXT_LENGTH = 8192


class WebhookEncryptionConfigError(RuntimeError):
    """Ключ или библиотека шифрования webhook настроены неверно."""


class WebhookSecretState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    VALID_CIPHERTEXT = "valid_ciphertext"
    LEGACY_PLAINTEXT = "legacy_plaintext"
    INVALID_CIPHERTEXT = "invalid_ciphertext"


@dataclass(frozen=True)
class StoredWebhookSecret:
    state: WebhookSecretState
    plaintext: str | None = field(default=None, repr=False)


def normalize_fernet_key(value: str | bytes) -> bytes:
    """Строго принять 64 hex-символа либо 44-символьный URL-safe base64."""
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise WebhookEncryptionConfigError(
                "Ключ шифрования webhook имеет неверный формат"
            ) from exc
    elif isinstance(value, str):
        text = value
    else:
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        )

    stripped = text.strip()
    if _HEX_KEY_RE.fullmatch(stripped):
        return bytes.fromhex(stripped)
    if len(stripped) != _FERNET_KEY_LENGTH or not _FERNET_KEY_RE.fullmatch(stripped):
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        )
    try:
        raw = base64.b64decode(stripped, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        ) from exc
    if len(raw) != _RAW_KEY_LENGTH:
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        )
    return raw


def webhook_key_path(
    environ: Mapping[str, str] | None = None,
    *,
    app_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Вернуть канонический путь, не читая содержимое ключа."""
    env = os.environ if environ is None else environ
    default_dir = Path(__file__).resolve().parent if app_dir is None else Path(app_dir)
    data_dir = env.get("CRM_DATA_DIR")
    return (Path(data_dir) if data_dir else default_dir) / ".webhook_encryption_key"


def _resolved_key_path(
    environ: Mapping[str, str], key_path: str | os.PathLike[str] | None
) -> Path:
    if key_path is not None:
        return Path(key_path)
    explicit = environ.get("CRM_WEBHOOK_ENCRYPTION_KEY_FILE", "").strip()
    if explicit:
        return Path(explicit)
    return webhook_key_path(environ)


def _read_key_file(path: Path) -> bytes:
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise WebhookEncryptionConfigError(
                "Файл ключа шифрования webhook должен иметь режим 0600"
            )
        with path.open("rb") as stream:
            raw = stream.read(257)
    except WebhookEncryptionConfigError:
        raise
    except OSError as exc:
        raise WebhookEncryptionConfigError(
            "Не удалось прочитать ключ шифрования webhook"
        ) from exc
    if len(raw) > 256:
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        )
    return normalize_fernet_key(raw)


def _create_key_file_exclusive(path: Path) -> None:
    """Атомарно создать закрытый ключ, не перезаписывая параллельный."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(os.urandom(_RAW_KEY_LENGTH).hex())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return
        os.chmod(path, 0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def obtain_fernet(
    environ: Mapping[str, str] | None = None,
    *,
    key_path: str | os.PathLike[str] | None = None,
    app_dir: str | os.PathLike[str] | None = None,
):
    """Получить Fernet только из выделенного webhook-источника."""
    if Fernet is None:
        raise WebhookEncryptionConfigError("Шифрование секрета webhook недоступно")

    env = os.environ if environ is None else environ
    path = _resolved_key_path(env, key_path)
    configured = env.get("CRM_WEBHOOK_ENCRYPTION_KEY")
    if configured is not None and configured.strip():
        raw_key = normalize_fernet_key(configured)
    elif path.exists():
        raw_key = _read_key_file(path)
    else:
        if deployment_mode(env) == "production":
            raise WebhookEncryptionConfigError(
                "Шифрование секрета webhook не настроено"
            )
        _create_key_file_exclusive(path)
        raw_key = _read_key_file(path)

    try:
        return Fernet(base64.urlsafe_b64encode(raw_key))
    except (TypeError, ValueError) as exc:
        raise WebhookEncryptionConfigError(
            "Ключ шифрования webhook имеет неверный формат"
        ) from exc


def validate_encryption_config(environ: Mapping[str, str] | None = None) -> bool:
    """Проверить источник ключа; production закрывается при отсутствии."""
    obtain_fernet(environ)
    return True


def _decrypt(stored_value: str, fernet) -> str:
    if (
        not isinstance(stored_value, str)
        or not stored_value
        or len(stored_value) > MAX_CIPHERTEXT_LENGTH
        or stored_value != stored_value.strip()
    ):
        raise ValueError("invalid stored webhook secret")
    try:
        plaintext = fernet.decrypt(stored_value.encode("ascii"))
    except (InvalidToken, TypeError, ValueError, UnicodeError) as exc:
        if (
            stored_value.startswith(_FERNET_TOKEN_PREFIX)
            or _FERNET_TOKEN_RE.fullmatch(stored_value)
        ):
            raise ValueError("invalid webhook ciphertext") from exc
        raise LookupError("legacy webhook plaintext") from exc
    try:
        decoded = plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid webhook ciphertext") from exc
    if not decoded or len(decoded) > MAX_SECRET_LENGTH:
        raise ValueError("invalid webhook ciphertext")
    return decoded


def classify_stored_secret(stored_value: object, fernet) -> StoredWebhookSecret:
    """Классифицировать значение только после проверки успешной расшифровкой."""
    if stored_value is None or stored_value == "":
        return StoredWebhookSecret(WebhookSecretState.NOT_CONFIGURED)
    try:
        plaintext = _decrypt(stored_value, fernet)
    except LookupError:
        return StoredWebhookSecret(WebhookSecretState.LEGACY_PLAINTEXT)
    except (TypeError, ValueError):
        return StoredWebhookSecret(WebhookSecretState.INVALID_CIPHERTEXT)
    return StoredWebhookSecret(
        WebhookSecretState.VALID_CIPHERTEXT,
        plaintext=plaintext,
    )


def encrypt_secret(plaintext: str, fernet) -> str:
    if not isinstance(plaintext, str) or not plaintext or len(plaintext) > MAX_SECRET_LENGTH:
        raise ValueError("Секрет webhook должен быть непустой строкой")
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(stored_value: str | None, fernet) -> str | None:
    if stored_value is None or stored_value == "":
        return None
    try:
        return _decrypt(stored_value, fernet)
    except LookupError as exc:
        raise WebhookSecretDeliveryError("secret_legacy_plaintext") from exc
    except (TypeError, ValueError) as exc:
        raise WebhookSecretDeliveryError("secret_ciphertext_invalid") from exc


def convert_legacy_secret(stored_value: str, fernet) -> str:
    """Явная идемпотентная конвертация одного legacy-значения."""
    if not isinstance(stored_value, str) or not stored_value:
        raise ValueError("Секрет webhook имеет недопустимый формат")
    classification = classify_stored_secret(stored_value, fernet)
    if classification.state is WebhookSecretState.VALID_CIPHERTEXT:
        return stored_value
    if classification.state is not WebhookSecretState.LEGACY_PLAINTEXT:
        raise ValueError("Секрет webhook имеет недопустимый формат")
    if len(stored_value) > MAX_SECRET_LENGTH:
        raise ValueError("Секрет webhook имеет недопустимый формат")
    return encrypt_secret(stored_value, fernet)


class WebhookSecretDeliveryError(RuntimeError):
    """Контролируемая ошибка расшифровки без раскрытия значения."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


__all__ = [
    "MAX_CIPHERTEXT_LENGTH",
    "MAX_SECRET_LENGTH",
    "StoredWebhookSecret",
    "WebhookEncryptionConfigError",
    "WebhookSecretDeliveryError",
    "WebhookSecretState",
    "classify_stored_secret",
    "convert_legacy_secret",
    "decrypt_secret",
    "encrypt_secret",
    "normalize_fernet_key",
    "obtain_fernet",
    "validate_encryption_config",
    "webhook_key_path",
]
