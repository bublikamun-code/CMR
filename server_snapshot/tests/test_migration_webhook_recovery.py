"""Миграция 0020: восстановление вебхуков после оборванной 0017.

Регрессия аудита 2026-09-30 (К-2). Сценарий: миграция 0017 пересобирает
таблицу webhooks (rename → CREATE → INSERT..SELECT → DROP). При падении между
шагами остаётся пустая webhooks рядом с webhooks_legacy_ba11 со всеми данными,
а повторный прогон 0017 их не подхватывает — потому что новая колонка secret
уже TEXT, и ветка, умеющая переносить данные, не выполняется.
"""

import importlib.util
import sqlite3

import pytest


def _load(path):
    spec = importlib.util.spec_from_file_location("m_" + path.split("/")[-1][:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Путь к миграциям — от этого файла, не абсолютный: захардкоженный путь
# чужой рабочей машины (/data/Project/CMR) ломал тест на любом другом месте.
import pathlib

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def broken_db(tmp_path, monkeypatch):
    """База ровно в том состоянии, в котором её оставляет убитый процесс."""
    monkeypatch.setenv("CRM_WEBHOOK_SECRET_KEY",
                       "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    db = tmp_path / "crm.db"
    conn = sqlite3.connect(db)
    # Старая таблица: секрет в BLOB (как до 0017), две живые интеграции.
    conn.execute("""
        CREATE TABLE webhooks_legacy_ba11 (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            secret BLOB,
            events TEXT,
            is_active INTEGER DEFAULT 1,
            tenant_id INTEGER NOT NULL,
            created_at TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO webhooks_legacy_ba11 "
        "(id, url, secret, events, is_active, tenant_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "https://first.example.com/hook", b"open-secret-1",
             '["card_created"]', 1, 101, "2026-01-01T00:00:00"),
            (2, "https://second.example.com/hook", None,
             '["card_created"]', 1, 101, "2026-01-02T00:00:00"),
        ],
    )
    # Новая пустая таблица: ровно то, что создаёт WEBHOOKS_DDL в 0017.
    conn.execute("""
        CREATE TABLE webhooks (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            secret TEXT,
            events TEXT,
            is_active INTEGER DEFAULT 1,
            tenant_id INTEGER NOT NULL,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


def _run0020(conn):
    mod = _load(str(MIGRATIONS_DIR / "0020_recover_webhook_legacy.py"))
    mod.up(conn.cursor())
    conn.commit()


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_restores_rows_from_legacy_table(broken_db):
    """Данные возвращаются в webhooks, остаток legacy удаляется."""
    assert broken_db.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0] == 0, \
        "исходное состояние: webhooks пуста"

    _run0020(broken_db)

    rows = broken_db.execute(
        "SELECT id, url, tenant_id FROM webhooks ORDER BY id").fetchall()
    assert rows == [
        (1, "https://first.example.com/hook", 101),
        (2, "https://second.example.com/hook", 101),
    ], rows
    assert "webhooks_legacy_ba11" not in _tables(broken_db), \
        "legacy-остаток должен быть удалён после переноса"


def test_secret_is_encrypted_not_lost(broken_db):
    """Секрет переносится и шифруется, а не остаётся открытым текстом."""
    _run0020(broken_db)

    secret = broken_db.execute(
        "SELECT secret FROM webhooks WHERE id=1").fetchone()[0]
    assert secret is not None, "секрет не должен потеряться"
    assert secret != "open-secret-1", "открытый текст в БД больше не хранится"
    # Значение, которого не было, остаётся NULL — а не «пустой шифротекст».
    assert broken_db.execute(
        "SELECT secret FROM webhooks WHERE id=2").fetchone()[0] is None


def test_restored_secret_can_be_decrypted(broken_db):
    """Секрет после переноса реально расшифровывается, а не просто изменился."""
    from webhook_security import decrypt_secret, obtain_fernet

    _run0020(broken_db)
    secret = broken_db.execute(
        "SELECT secret FROM webhooks WHERE id=1").fetchone()[0]
    assert decrypt_secret(secret, obtain_fernet()) == "open-secret-1"


def test_is_idempotent(broken_db):
    """Повторный прогон ничего не ломает — в этом весь смысл восстановления."""
    _run0020(broken_db)
    _run0020(broken_db)

    assert broken_db.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0] == 2


def test_healthy_database_is_untouched(tmp_path, monkeypatch):
    """Когда legacy-таблицы нет — миграция не трогает ничего."""
    conn = sqlite3.connect(tmp_path / "ok.db")
    conn.execute("""
        CREATE TABLE webhooks (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, secret TEXT,
            events TEXT, is_active INTEGER DEFAULT 1,
            tenant_id INTEGER NOT NULL, created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO webhooks (id, url, tenant_id) VALUES (1, 'https://x.example/h', 101)")
    conn.commit()

    _run0020(conn)

    assert conn.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0] == 1
    assert conn.execute("SELECT url FROM webhooks WHERE id=1").fetchone()[0] == \
        "https://x.example/h"


def test_partial_transfer_does_not_duplicate(tmp_path, monkeypatch):
    """Частичный перенос не плодит дубли: строки с тем же id пропускаются."""
    monkeypatch.setenv("CRM_WEBHOOK_SECRET_KEY",
                       "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    conn = sqlite3.connect(tmp_path / "partial.db")
    conn.execute("""
        CREATE TABLE webhooks_legacy_ba11 (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, secret BLOB, events TEXT,
            is_active INTEGER DEFAULT 1, tenant_id INTEGER NOT NULL, created_at TEXT)
    """)
    conn.executemany(
        "INSERT INTO webhooks_legacy_ba11 "
        "(id, url, secret, events, is_active, tenant_id, created_at) "
        "VALUES (?, ?, NULL, ?, 1, 101, '2026-01-01')",
        [(1, "https://a.example/h", '["x"]'), (2, "https://b.example/h", '["x"]')],
    )
    conn.execute("""
        CREATE TABLE webhooks (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, secret TEXT, events TEXT,
            is_active INTEGER DEFAULT 1, tenant_id INTEGER NOT NULL, created_at TEXT)
    """)
    # 0017 успела перенести первую строку и упала.
    conn.execute("INSERT INTO webhooks (id, url, tenant_id) VALUES (1, 'https://a.example/h', 101)")
    conn.commit()

    _run0020(conn)

    rows = conn.execute("SELECT id FROM webhooks ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [1, 2], rows
    assert "webhooks_legacy_ba11" not in _tables(conn)


def test_rerun_of_0017_does_not_restore_why_0020_is_needed(broken_db):
    """Зафиксировать саму причину: 0017 на этом состоянии данные не вернёт.

    Тест не про 0020, а про обоснование. Если бы повторный прогон 0017 чинил
    ситуацию, новая миграция была бы лишней.
    """
    assert broken_db.execute(
        "SELECT type FROM pragma_table_info('webhooks') WHERE name='secret'"
    ).fetchone()[0].upper() == "TEXT", \
        "новая таблица создана с secret TEXT — именно поэтому 0017 пропустит перенос"

    mod = _load(str(MIGRATIONS_DIR / "0017_secure_webhook_secrets.py"))
    mod.up(broken_db.cursor())
    broken_db.commit()

    assert broken_db.execute("SELECT COUNT(*) FROM webhooks").fetchone()[0] == 0, \
        "подтверждено: 0017 на оборванном состоянии данные не возвращает"