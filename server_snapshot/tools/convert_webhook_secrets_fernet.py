#!/usr/bin/env python3
"""Явно и идемпотентно зашифровать legacy-секреты webhook."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from webhook_security import (  # noqa: E402
    WebhookSecretState,
    classify_stored_secret,
    encrypt_secret,
    obtain_fernet,
)


def convert_database(db_path: Path, *, key_path: str | None = None) -> tuple[int, int]:
    """Конвертировать plaintext; malformed/ambiguous останавливают транзакцию."""
    fernet = obtain_fernet(key_path=key_path)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA busy_timeout=10000")
    converted = 0
    already_encrypted = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT id, secret FROM webhooks WHERE secret IS NOT NULL ORDER BY id"
        ).fetchall()
        for webhook_id, stored in rows:
            if not isinstance(stored, str) or not stored:
                raise RuntimeError("Webhook conversion failed code=secret_invalid")
            state = classify_stored_secret(stored, fernet)
            if state.state is WebhookSecretState.VALID_CIPHERTEXT:
                already_encrypted += 1
                continue
            if state.state is not WebhookSecretState.LEGACY_PLAINTEXT:
                raise RuntimeError("Webhook conversion failed code=secret_invalid")
            ciphertext = encrypt_secret(stored, fernet)
            connection.execute(
                "UPDATE webhooks SET secret=? WHERE id=?",
                (ciphertext, webhook_id),
            )
            converted += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return converted, already_encrypted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Конвертировать plaintext-секреты webhook в Fernet ciphertext"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("CRM_DATA_DIR", APP_DIR)) / "crm_app.db",
    )
    parser.add_argument("--key-file", default=None)
    args = parser.parse_args()
    try:
        converted, already_encrypted = convert_database(
            args.db, key_path=args.key_file
        )
    except Exception:
        # Ни значение, ни URL, ни текст стороннего исключения не выводятся.
        print("Ошибка: конвертация webhook не выполнена code=conversion_failed")
        return 1
    print(
        f"Готово: конвертировано {converted}, уже зашифровано {already_encrypted}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
