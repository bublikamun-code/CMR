"""BA-11: расширить webhook secret и конвертировать legacy plaintext."""
from __future__ import annotations

import os
import sys

from webhook_security import (
    WebhookSecretState,
    classify_stored_secret,
    encrypt_secret,
    obtain_fernet,
)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

WEBHOOKS_DDL = """
CREATE TABLE webhooks (
    id INTEGER NOT NULL PRIMARY KEY,
    url VARCHAR(500) NOT NULL,
    secret TEXT,
    events TEXT NOT NULL,
    is_active BOOLEAN,
    tenant_id INTEGER,
    created_at DATETIME,
    FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)
"""

DELIVERIES_DDL = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER NOT NULL PRIMARY KEY,
    correlation_id VARCHAR(36) NOT NULL,
    webhook_id INTEGER,
    event VARCHAR(100) NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at DATETIME,
    last_error_code VARCHAR(64),
    response_status INTEGER,
    tenant_id INTEGER,
    created_at DATETIME,
    completed_at DATETIME,
    FOREIGN KEY(webhook_id) REFERENCES webhooks (id) ON DELETE SET NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)
"""

ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS webhook_attempts (
    id INTEGER NOT NULL PRIMARY KEY,
    delivery_id INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    error_code VARCHAR(64),
    response_status INTEGER,
    started_at DATETIME,
    completed_at DATETIME,
    FOREIGN KEY(delivery_id) REFERENCES webhook_deliveries (id) ON DELETE CASCADE
)
"""


def _table_exists(cur, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _secret_type(cur) -> str | None:
    row = cur.execute("SELECT type FROM pragma_table_info('webhooks') WHERE name='secret'").fetchone()
    return row[0].upper() if row else None


def _convert_rows(cur, fernet) -> list[tuple[int, str | None]]:
    converted = []
    for webhook_id, stored in cur.execute(
        "SELECT id, secret FROM webhooks ORDER BY id"
    ).fetchall():
        if stored is None:
            converted.append((webhook_id, None))
            continue
        if not isinstance(stored, str) or not stored:
            raise RuntimeError("Webhook secret migration failed code=secret_invalid")
        state = classify_stored_secret(stored, fernet)
        if state.state is WebhookSecretState.VALID_CIPHERTEXT:
            converted.append((webhook_id, stored))
        elif state.state is WebhookSecretState.LEGACY_PLAINTEXT:
            converted.append((webhook_id, encrypt_secret(stored, fernet)))
        else:
            raise RuntimeError("Webhook secret migration failed code=secret_invalid")
    return converted


def _rebuild_webhooks(cur, converted: list[tuple[int, str | None]]) -> None:
    connection = cur.connection
    foreign_keys = cur.execute("PRAGMA foreign_keys").fetchone()[0]
    legacy_alter = cur.execute("PRAGMA legacy_alter_table").fetchone()[0]
    if connection.in_transaction and foreign_keys:
        raise RuntimeError(
            "Webhook secret migration failed code=transaction_required"
        )
    try:
        # При rename SQLite иначе перепишет FK уже созданного audit-аудита на
        # временную таблицу. Старый режим rename сохраняет ссылку на webhooks.
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("PRAGMA legacy_alter_table=ON")
        cur.execute("DROP INDEX IF EXISTS ix_webhooks_id")
        cur.execute("ALTER TABLE webhooks RENAME TO webhooks_legacy_ba11")
        cur.execute(WEBHOOKS_DDL)
        for webhook_id, secret in converted:
            cur.execute(
                "INSERT INTO webhooks "
                "(id, url, secret, events, is_active, tenant_id, created_at) "
                "SELECT id, url, ?, events, is_active, tenant_id, created_at "
                "FROM webhooks_legacy_ba11 WHERE id=?",
                (secret, webhook_id),
            )
        cur.execute("DROP TABLE webhooks_legacy_ba11")
        cur.execute("CREATE INDEX ix_webhooks_id ON webhooks (id)")
    finally:
        cur.execute(f"PRAGMA legacy_alter_table={int(legacy_alter)}")
        cur.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")


def _ensure_audit_tables(cur) -> None:
    cur.execute(DELIVERIES_DDL)
    cur.execute(ATTEMPTS_DDL)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_id "
        "ON webhook_deliveries (id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_attempts_id "
        "ON webhook_attempts (id)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_webhook_deliveries_correlation_id "
        "ON webhook_deliveries (correlation_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook_id "
        "ON webhook_deliveries (webhook_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_event "
        "ON webhook_deliveries (event)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_status "
        "ON webhook_deliveries (status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_next_attempt_at "
        "ON webhook_deliveries (next_attempt_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_tenant_id "
        "ON webhook_deliveries (tenant_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_created_at "
        "ON webhook_deliveries (created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_attempts_delivery_id "
        "ON webhook_attempts (delivery_id)"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_webhook_attempts_delivery_number "
        "ON webhook_attempts (delivery_id, attempt_number)"
    )


def up(cur) -> None:
    """Идемпотентно расширить колонку, зашифровать plaintext и создать аудит."""
    fernet = obtain_fernet()
    if not _table_exists(cur, "webhooks"):
        cur.execute(WEBHOOKS_DDL)
        cur.execute("CREATE INDEX ix_webhooks_id ON webhooks (id)")
    else:
        converted = _convert_rows(cur, fernet)
        if _secret_type(cur) != "TEXT":
            _rebuild_webhooks(cur, converted)
        else:
            for webhook_id, secret in converted:
                if secret is not None:
                    cur.execute(
                        "UPDATE webhooks SET secret=? WHERE id=?", (secret, webhook_id)
                    )
    _ensure_audit_tables(cur)
