"""Fail-closed и идемпотентность tenant-нормализации 0018."""
import sqlite3

import pytest

from migrations import import_migration


def _legacy_db(*, references=(), nulls=True):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tenants (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id)
        );
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id)
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY, name TEXT,
            tenant_id INTEGER REFERENCES tenants(id)
        );
        INSERT INTO tenants(id, name) VALUES (1, 'one'), (2, 'two');
        """
    )
    for tenant_id in references:
        conn.execute(
            "INSERT INTO users(id, tenant_id) VALUES (?, ?)",
            (100 + tenant_id, tenant_id),
        )
    if nulls:
        conn.execute("INSERT INTO clients(id, tenant_id) VALUES (10, NULL)")
    conn.commit()
    return conn


def test_normalizes_unambiguous_legacy_nulls_and_is_idempotent(monkeypatch):
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    conn = _legacy_db(references=(1,))
    migration = import_migration("0018_normalize_tenant_ids.py")

    migration.up(conn.cursor())
    migration.up(conn.cursor())
    conn.commit()

    assert conn.execute(
        "SELECT tenant_id FROM clients WHERE id = 10"
    ).fetchone() == (1,)
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_clients_tenant_required_insert'"
    ).fetchall()
    assert triggers == [("trg_clients_tenant_required_insert",)]
    with pytest.raises(sqlite3.IntegrityError, match="tenant_id is required"):
        conn.execute("INSERT INTO clients(id, tenant_id) VALUES (11, NULL)")
    conn.close()


def test_explicit_config_can_resolve_null_without_existing_reference(monkeypatch):
    monkeypatch.setenv("CRM_LEGACY_TENANT_ID", "2")
    conn = _legacy_db(references=())
    import_migration("0018_normalize_tenant_ids.py").up(conn.cursor())
    conn.commit()
    assert conn.execute(
        "SELECT tenant_id FROM clients WHERE id = 10"
    ).fetchone() == (2,)
    conn.close()


def test_multi_tenant_null_normalization_fails_before_write(monkeypatch):
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    conn = _legacy_db(references=(1, 2))
    with pytest.raises(
        RuntimeError, match="данные относятся к нескольким tenant"
    ):
        import_migration("0018_normalize_tenant_ids.py").up(conn.cursor())
    assert conn.execute(
        "SELECT tenant_id FROM clients WHERE id = 10"
    ).fetchone() == (None,)
    conn.close()


def test_clean_schema_without_null_needs_no_config(monkeypatch):
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tenants (id INTEGER PRIMARY KEY)")
    migration = import_migration("0018_normalize_tenant_ids.py")
    migration.up(conn.cursor())
    migration.up(conn.cursor())
    conn.close()
