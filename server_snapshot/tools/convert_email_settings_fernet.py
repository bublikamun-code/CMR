#!/usr/bin/env python3
"""Явная конвертация сохранённых паролей почты в Fernet-форму.

Обычное сохранение настроек никогда не делает неявную конвертацию. Утилита
сначала проверяет все указанные файлы, затем атомарно перезаписывает только
legacy plaintext. Корректный ciphertext не меняется.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from email_credentials import (  # noqa: E402
    EmailEncryptionConfigError,
    InvalidCredentialCiphertextError,
    StoredCredentialState,
    classify_stored_credential,
    encrypt_credential,
    normalize_fernet_key,
    obtain_fernet,
)
from secure_files import atomic_write_private  # noqa: E402


class ConversionError(RuntimeError):
    """Безопасная контролируемая ошибка миграции."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConversionError(f"Файл настроек не найден: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Не удалось прочитать настройки: {path}") from exc
    if not isinstance(payload, dict):
        raise ConversionError(f"Настройки должны быть JSON-объектом: {path}")
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise ConversionError(f"В настройках нет сохранённого пароля: {path}")
    return payload


def _legacy_fernet(path: Path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConversionError("Не удалось прочитать явный legacy-ключ") from exc
    raw_key = normalize_fernet_key(raw)
    # obtain_fernet с явным путём всё равно может прочитать основной env; это
    # нужно только для единой проверки формата. Здесь ключ уже нормализован.
    import base64

    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(raw_key))


def _plan_target(
    path: Path,
    payload: dict[str, Any],
    current_fernet,
    legacy_fernet,
) -> tuple[dict[str, Any], bool, str]:
    stored = payload["password"]
    current = classify_stored_credential(stored, current_fernet)
    if current.state is StoredCredentialState.VALID_CIPHERTEXT:
        return payload, False, "уже зашифрован"

    # Корректный токен старого ключа считается уже зашифрованным и не меняется.
    # Невалидный под явным legacy-ключом остаётся невалидным, а не plaintext.
    if current.state is StoredCredentialState.INVALID_CIPHERTEXT and legacy_fernet is not None:
        legacy = classify_stored_credential(stored, legacy_fernet)
        if legacy.state is StoredCredentialState.VALID_CIPHERTEXT:
            return payload, False, "уже зашифрован legacy-ключом"
        raise InvalidCredentialCiphertextError(
            "Сохранённый пароль почты повреждён или создан другим ключом"
        )
    if current.state is StoredCredentialState.INVALID_CIPHERTEXT:
        raise InvalidCredentialCiphertextError(
            "Сохранённый пароль почты повреждён или создан другим ключом"
        )

    converted = dict(payload)
    converted["password"] = encrypt_credential(stored, current_fernet)
    return converted, True, "конвертирован"


def convert_paths(
    paths: list[Path],
    *,
    environ: dict[str, str] | None = None,
    legacy_key_path: Path | None = None,
) -> list[tuple[Path, str]]:
    """Провалидировать все targets перед первой записью."""
    env = os.environ if environ is None else environ
    try:
        current_fernet = obtain_fernet(env)
    except EmailEncryptionConfigError as exc:
        raise ConversionError(
            "Ключ шифрования почты не настроен или имеет неверный формат"
        ) from exc
    legacy_fernet = _legacy_fernet(legacy_key_path) if legacy_key_path else None

    plans = []
    labels = []
    for path in paths:
        payload = _load_json(path)
        converted, changed, label = _plan_target(
            path, payload, current_fernet, legacy_fernet
        )
        plans.append((path, converted, changed))
        labels.append((path, label))

    for path, payload, changed in plans:
        if changed:
            atomic_write_private(
                path,
                json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
            )
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="явные settings JSON")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="подтвердить запись; без флага утилита ничего не делает",
    )
    parser.add_argument(
        "--legacy-key-path",
        type=Path,
        help="явный путь старого ключа для распознавания уже зашифрованных значений",
    )
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("--apply обязателен")

    try:
        results = convert_paths(
            args.paths,
            legacy_key_path=args.legacy_key_path,
        )
    except (
        ConversionError,
        EmailEncryptionConfigError,
        InvalidCredentialCiphertextError,
    ) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    for path, label in results:
        print(f"OK: {path} — {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
