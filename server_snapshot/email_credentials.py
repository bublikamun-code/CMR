"""Строгая шифрация учётных данных почтового ящика.

Ключ почты не связан с ключом JWT. Основной источник — ``CRM_EMAIL_SECRET_KEY``,
канонический файл — ``$CRM_DATA_DIR/.email_secret_key``. В production отсутствие
источника является ошибкой конфигурации; автоматическая генерация разрешена
только вне production.
"""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
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
TOKEN_SHAPE_PREFIX = "gAAAA"
_RAW_KEY_LENGTH = 32
_FERNET_KEY_LENGTH = 44


class EmailEncryptionConfigError(RuntimeError):
    """Ключ или библиотека шифрования почты настроены неверно."""


class LegacyPlaintextError(RuntimeError):
    """В настройках найден старый пароль, сохранённый открытым текстом."""


class InvalidCredentialCiphertextError(RuntimeError):
    """Сохранённый пароль не является корректным Fernet-токеном."""


class StoredCredentialState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    VALID_CIPHERTEXT = "valid_ciphertext"
    LEGACY_PLAINTEXT = "legacy_plaintext"
    INVALID_CIPHERTEXT = "invalid_ciphertext"


@dataclass(frozen=True)
class StoredCredentialClassification:
    state: StoredCredentialState
    plaintext: str | None = None


def normalize_fernet_key(value: str | bytes) -> bytes:
    """Нормализовать поддерживаемые форматы ключа в 32 сырых байта.

    Поддерживаются ровно 64 hex-символа и 44-символьный URL-safe base64.
    Ошибки не включают входное значение.
    """
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise EmailEncryptionConfigError(
                "Ключ шифрования почты имеет неверный формат"
            ) from exc
    elif isinstance(value, str):
        text = value
    else:
        raise EmailEncryptionConfigError("Ключ шифрования почты имеет неверный формат")

    stripped = text.strip()
    if _HEX_KEY_RE.fullmatch(stripped):
        return bytes.fromhex(stripped)

    if len(stripped) != _FERNET_KEY_LENGTH or not _FERNET_KEY_RE.fullmatch(stripped):
        raise EmailEncryptionConfigError("Ключ шифрования почты имеет неверный формат")
    try:
        raw = base64.b64decode(stripped, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EmailEncryptionConfigError(
            "Ключ шифрования почты имеет неверный формат"
        ) from exc
    if len(raw) != _RAW_KEY_LENGTH:
        raise EmailEncryptionConfigError("Ключ шифрования почты имеет неверный формат")
    if base64.urlsafe_b64encode(raw).decode("ascii") != stripped:
        raise EmailEncryptionConfigError("Ключ шифрования почты имеет неверный формат")
    return raw


def email_key_path(
    environ: Mapping[str, str] | None = None,
    *,
    app_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Вернуть канонический путь ключа почты без чтения содержимого."""
    env = os.environ if environ is None else environ
    default_dir = Path(__file__).resolve().parent if app_dir is None else Path(app_dir)
    data_dir = env.get("CRM_DATA_DIR")
    return (Path(data_dir) if data_dir else default_dir) / ".email_secret_key"


def _read_key_file(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EmailEncryptionConfigError(
            "Не удалось прочитать ключ шифрования почты"
        ) from exc
    return normalize_fernet_key(raw)


def _create_key_file_exclusive(path: Path) -> None:
    """Опубликовать полностью записанный 64-hex ключ без перезаписи."""
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
            # link публикует готовый inode атомарно и, в отличие от replace,
            # никогда не перезаписывает ключ, созданный параллельным процессом.
            os.link(temporary_path, path)
        except FileExistsError:
            return
        os.chmod(path, 0o600)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
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
    """Получить Fernet из env или канонического файла.

    ``key_path`` используется только явными утилитами миграции. Обычный
    приложение никогда не передаёт сюда legacy-путь.
    """
    if Fernet is None:
        raise EmailEncryptionConfigError("Шифрование пароля почты недоступно")

    env = os.environ if environ is None else environ
    path = Path(key_path) if key_path is not None else email_key_path(env, app_dir=app_dir)
    configured = env.get("CRM_EMAIL_SECRET_KEY")
    if configured is not None and configured.strip():
        raw_key = normalize_fernet_key(configured)
    elif path.exists():
        raw_key = _read_key_file(path)
    else:
        if deployment_mode(env) == "production":
            raise EmailEncryptionConfigError("Шифрование пароля почты не настроено")
        path.parent.mkdir(parents=True, exist_ok=True)
        _create_key_file_exclusive(path)
        raw_key = _read_key_file(path)

    try:
        return Fernet(base64.urlsafe_b64encode(raw_key))
    except (TypeError, ValueError) as exc:
        raise EmailEncryptionConfigError(
            "Ключ шифрования почты имеет неверный формат"
        ) from exc


def looks_like_fernet_token(value: object) -> bool:
    """Отличить явно испорченный токен для безопасной конвертации.

    Этот признак используется только после неудачной расшифровки и никогда
    не считается доказательством корректности токена.
    """
    return isinstance(value, str) and value.strip().startswith(TOKEN_SHAPE_PREFIX)


def _decrypt_bytes(stored_value: str, fernet) -> str:
    try:
        plaintext = fernet.decrypt(stored_value.encode("utf-8"))
    except (InvalidToken, TypeError, ValueError, UnicodeError) as exc:
        if looks_like_fernet_token(stored_value):
            raise InvalidCredentialCiphertextError(
                "Сохранённый пароль почты повреждён или создан другим ключом"
            ) from exc
        raise LegacyPlaintextError(
            "Сохранённый пароль почты находится в legacy-формате; "
            "запустите явную конвертацию"
        ) from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidCredentialCiphertextError(
            "Сохранённый пароль почты повреждён или создан другим ключом"
        ) from exc


def classify_stored_credential(
    stored_value: object,
    fernet,
) -> StoredCredentialClassification:
    """Проверить значение только успешной расшифровкой."""
    if not isinstance(stored_value, str) or not stored_value:
        return StoredCredentialClassification(StoredCredentialState.NOT_CONFIGURED)
    try:
        plaintext = _decrypt_bytes(stored_value, fernet)
    except LegacyPlaintextError:
        return StoredCredentialClassification(StoredCredentialState.LEGACY_PLAINTEXT)
    except InvalidCredentialCiphertextError:
        return StoredCredentialClassification(StoredCredentialState.INVALID_CIPHERTEXT)
    return StoredCredentialClassification(
        StoredCredentialState.VALID_CIPHERTEXT,
        plaintext=plaintext,
    )


def decrypt_credential(stored_value: str, fernet) -> str:
    """Расшифровать значение или выдать контролируемую ошибку."""
    if not isinstance(stored_value, str) or not stored_value:
        return ""
    return _decrypt_bytes(stored_value, fernet)


def encrypt_credential(plaintext: str, fernet) -> str:
    """Зашифровать новый пароль обычным Fernet-токеном."""
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("Пароль почты должен быть непустой строкой")
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def convert_plaintext_to_ciphertext(plaintext: str, fernet) -> str:
    """Явная операция миграции; непустое plaintext-значение не принимает."""
    classification = classify_stored_credential(plaintext, fernet)
    if classification.state is StoredCredentialState.VALID_CIPHERTEXT:
        raise ValueError("Значение уже является корректным Fernet-токеном")
    if classification.state is StoredCredentialState.INVALID_CIPHERTEXT:
        raise InvalidCredentialCiphertextError(
            "Сохранённый пароль почты повреждён или создан другим ключом"
        )
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("Legacy-пароль почты должен быть непустой строкой")
    return encrypt_credential(plaintext, fernet)


__all__ = [
    "EmailEncryptionConfigError",
    "InvalidCredentialCiphertextError",
    "LegacyPlaintextError",
    "StoredCredentialState",
    "classify_stored_credential",
    "convert_plaintext_to_ciphertext",
    "decrypt_credential",
    "email_key_path",
    "encrypt_credential",
    "looks_like_fernet_token",
    "normalize_fernet_key",
    "obtain_fernet",
]
