"""Миграция 0014 — users.is_active (пункт 15 плана v2).

Гоняется на синтетической sqlite-базе (DDL — как в tests/fixtures/prod_schema.sql),
боевые данные и таблицы тестовой сессии не затрагиваются.
"""
import sqlite3

from migrations import import_migration

MIGRATION = "0014_add_user_is_active.py"

# Точная форма таблицы с прода-дампа: колонки is_active там нет.
USERS_DDL = """
CREATE TABLE users (
      id INTEGER NOT NULL,
      username VARCHAR,
      hashed_password VARCHAR,
      role VARCHAR, updated_at DATETIME, created_at DATETIME, tenant_id INTEGER,
      PRIMARY KEY (id)
);
"""

# Вариант «колонку уже добавили вручную без NOT NULL DEFAULT»: на проде такое
# возможно после ручного ALTER, и миграция обязана выдержать его молча.
USERS_DDL_NULLABLE_COLUMN = """
CREATE TABLE users (
      id INTEGER NOT NULL,
      username VARCHAR,
      hashed_password VARCHAR,
      role VARCHAR, updated_at DATETIME, created_at DATETIME, tenant_id INTEGER,
      is_active INTEGER,
      PRIMARY KEY (id)
);
"""


def _db(with_column=False):
    conn = sqlite3.connect(":memory:")
    conn.executescript(USERS_DDL_NULLABLE_COLUMN if with_column else USERS_DDL)
    conn.executemany(
        "INSERT INTO users (id, username, hashed_password, role) VALUES (?,?,?,?)",
        [(1, "boss", "h1", "superadmin"), (2, "masha", "h2", "manager"),
         (3, "sklad", "h3", "warehouse")],
    )
    conn.commit()
    return conn


def _columns(conn):
    return {row[1]: row for row in conn.execute("PRAGMA table_info(users)")}


def test_0014_adds_column_and_keeps_everyone_active():
    conn = _db()
    assert "is_active" not in _columns(conn)

    import_migration(MIGRATION).up(conn.cursor())
    conn.commit()

    cols = _columns(conn)
    assert "is_active" in cols
    # NOT NULL + DEFAULT 1: «отключён» — только явное действие администратора,
    # существующие пользователи после миграции активны. PRAGMA отдаёт
    # dflt_value текстом, поэтому сравнение через str().
    assert cols["is_active"][3] == 1, f"колонка не NOT NULL: {cols['is_active']}"
    assert str(cols["is_active"][4]) == "1", f"нет DEFAULT 1: {cols['is_active']}"
    assert conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0] == 3


def test_0014_is_idempotent_and_does_not_reenable_disabled_users():
    conn = _db()
    mod = import_migration(MIGRATION)
    mod.up(conn.cursor())
    conn.commit()
    # Админ отключил кладовщика — повторный прогон не вправе возвращать его.
    conn.execute("UPDATE users SET is_active = 0 WHERE username = 'sklad'")
    conn.commit()

    mod.up(conn.cursor())
    mod.up(conn.cursor())
    conn.commit()

    rows = dict(conn.execute("SELECT username, is_active FROM users").fetchall())
    assert rows == {"boss": 1, "masha": 1, "sklad": 0}


def test_0014_normalises_nulls_when_column_already_exists():
    conn = _db(with_column=True)
    mod = import_migration(MIGRATION)

    mod.up(conn.cursor())
    conn.commit()

    # Колонка была NULL-able и пустой по дефолту: NULL трактуется как
    # «выключен», поэтому миграция обязана выставить 1, а не уронить команду.
    assert conn.execute("SELECT COUNT(*) FROM users WHERE is_active IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0] == 3
