#!/usr/bin/env python3
"""Проверить права файлов CRM с секретами и данными тенантов.

Скрипт читает только метаданные (путь, режим, UID и имя владельца).
По умолчанию проблемы — предупреждения для dev; с ``--production`` любая
неприватная существующая копия завершает проверку с кодом 1.
"""
import argparse
import os
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from secure_files import audit_private_files  # noqa: E402


def _tenant_private_files(tenants_dir: Path) -> list[Path]:
    if not tenants_dir.is_dir():
        return []
    paths = []
    for pattern in ("email_settings_*.json", "crm_*.db", "crm_*.db-wal", "crm_*.db-shm"):
        paths.extend(tenants_dir.glob(pattern))
    return sorted(set(paths))


def discover_paths() -> list[Path]:
    data_dir = Path(os.environ.get("CRM_DATA_DIR") or APP_DIR).resolve()
    legacy_settings = APP_DIR / "email_settings.json"
    candidates = [
        APP_DIR / ".secret_key",
        APP_DIR / ".cron_token",
        legacy_settings,
        data_dir / "email_settings.json",
        data_dir / ".email_secret_key",
    ]
    candidates.extend(_tenant_private_files(data_dir / "tenants"))
    return sorted(set(candidates), key=str)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="явные пути вместо автоматического обнаружения",
    )
    parser.add_argument(
        "--production", action="store_true",
        help="ненулевой код при неприватных правах",
    )
    args = parser.parse_args()

    findings = audit_private_files(
        args.paths or discover_paths(), production=args.production
    )
    for item in findings:
        if item["level"] == "OK":
            detail = "отсутствует" if not item["exists"] else f"{item['mode']} uid={item['uid']} owner={item['owner']}"
        else:
            detail = f"mode={item['mode']} uid={item['uid']} owner={item['owner']} type={item['type']}"
        print(f"{item['level']}: {item['path']} — {detail}")

    failed = any(item["level"] == "ERROR" for item in findings)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
