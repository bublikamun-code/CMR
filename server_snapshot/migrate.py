#!/usr/bin/env python3
"""Мини-раннер миграций схемы (Н5, аудит 06.09; план P2-3 из аудита 04.09).

Зачем: alembic в requirements, но без каталога миграций — эволюция схемы шла
через create_all + ручные правки (migrate_add_*.py запускались руками на
сервере). Риск рассинхрона: следующее изменение моделей легко забыть
прокатить на проде.

Как работает:
  - миграции — файлы migrations/NNNN_имя.py с функцией `up(cur: sqlite3.Cursor)`;
    каждая обязана быть идемпотентной (повторный вызов не ломает базу);
  - применённые отмечаются в таблице schema_migrations (имя + дата);
  - НЕ применяется ничего к тенант-базам (tenants/crm_N.db) — это отдельные
    схемы; create_all() в main.py по-прежнему создаёт недостающие таблицы.

Запуск на сервере (из каталога приложения):
    CRM_DATA_DIR=/var/www/.../crm_data python3 migrate.py          # применить
    CRM_DATA_DIR=... python3 migrate.py status                     # что применено
"""
import importlib.util
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("CRM_DATA_DIR", APP_DIR)
DB_PATH = os.path.join(DATA_DIR, "crm_app.db")
MIGRATIONS_DIR = os.path.join(APP_DIR, "migrations")

# Файлы миграций: NNNN_имя.py — применяются строго по возрастанию номера.
_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.py$")


def _connect():
    if not os.path.exists(DB_PATH):
        print(f"База не найдена: {DB_PATH} (создаётся при первом старте приложения)")
        sys.exit(2)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return conn


def _applied(conn):
    return {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}


def _discover():
    files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if _NAME_RE.match(f))
    return files


def _load(path):
    spec = importlib.util.spec_from_file_location("crm_migration_" + Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "up", None)):
        raise RuntimeError(f"{path}: нет функции up(cur)")
    return mod


def cmd_status():
    conn = _connect()
    done = _applied(conn)
    print(f"База: {DB_PATH}")
    for fname in _discover():
        mark = "применена " if fname in done else "ОЖИДАЕТ   "
        print(f"  [{mark}] {fname}")
    conn.close()


def cmd_apply():
    conn = _connect()
    done = _applied(conn)
    cur = conn.cursor()
    ran = 0
    for fname in _discover():
        if fname in done:
            continue
        mod = _load(os.path.join(MIGRATIONS_DIR, fname))
        print(f"Применяю {fname} ...", flush=True)
        mod.up(cur)
        cur.execute(
            "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
            (fname, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        ran += 1
    print(f"Готово: применено {ran}, всего миграций {len(_discover())}.")
    conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "status":
        cmd_status()
    elif cmd == "apply":
        cmd_apply()
    else:
        print(__doc__)
        sys.exit(1)
