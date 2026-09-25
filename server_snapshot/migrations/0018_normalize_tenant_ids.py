"""0018: явная нормализация legacy tenant_id и запрет новых NULL.

Миграция безопасна только для однозначного legacy-состояния. Перед UPDATE
она проверяет, что все непустые tenant_id принадлежат одному tenant и что все
ссылки tenant-owned child-строк ведут к родителю того же tenant. При двух
и более tenant, неизвестном tenant, несоответствии child/parent или отсутствии
источника однозначности операция прерывается ДО записи.

Постоянного production-id в коде нет. Внешний runner может явно передать
``CRM_LEGACY_TENANT_ID``; значение обязано совпасть с однозначно найденным
tenant. На пустой чистой схеме, где NULL-строк нет, миграция только создаёт
индексы и триггеры и не выбирает tenant.

SQLite не умеет ALTER TABLE ... SET NOT NULL без пересборки таблицы. Чтобы не
переписывать боевой DDL и не потерять его точные affinity/FK/index-контракты,
используются эквивалентные BEFORE INSERT/UPDATE-триггеры. Новые чистые базы,
созданные из models.py, получают настоящий NOT NULL.
"""
import os

# Таблицы, которым tenant принадлежит напрямую. Список явный: автоматическое
# добавление новой tenant-owned таблицы должно требовать review, а не незаметно
# менять уже опубликованную миграцию.
TENANT_TABLES = (
    "users",
    "clients",
    "client_payments",
    "suppliers",
    "tags",
    "writeoff_groups",
    "cards",
    "transactions",
    "activity_log",
    "record_versions",
    "custom_object_types",
    "custom_field_defs",
    "custom_records",
    "store_locations",
    "deal_statuses",
    "workflows",
    "workflow_triggers",
    "workflow_steps",
    "workflow_runs",
    "webhooks",
    "webhook_deliveries",
    "saved_views",
    "tasks",
    "task_checklist_items",
    "nakladnye",
    "notifications",
)

# Child-таблицы без собственного tenant_id. Каждая проверяется через parent.
PARENT_CHECKS = (
    ("card_attachments", "card_id", "cards"),
    ("card_checklists", "card_id", "cards"),
    ("task_checklist_items", "task_id", "tasks"),
    ("custom_field_defs", "object_type_id", "custom_object_types"),
    ("custom_records", "object_type_id", "custom_object_types"),
    ("workflow_triggers", "workflow_id", "workflows"),
    ("workflow_steps", "workflow_id", "workflows"),
    ("workflow_runs", "workflow_id", "workflows"),
    ("webhook_deliveries", "webhook_id", "webhooks"),
)

DEFAULT_LEGACY_TENANT_ID = None
LEGACY_TENANT_ENV = "CRM_LEGACY_TENANT_ID"


class AmbiguousLegacyTenant(RuntimeError):
    pass


def _table_exists(cur, table):
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _column_exists(cur, table, column):
    cur.execute(f'PRAGMA table_info("{table}")')
    return any(row[1] == column for row in cur.fetchall())


def _configured_tenant():
    raw = os.environ.get(LEGACY_TENANT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_LEGACY_TENANT_ID
    try:
        value = int(raw)
    except ValueError as exc:
        raise AmbiguousLegacyTenant(
            f"{LEGACY_TENANT_ENV} должен быть положительным целым числом"
        ) from exc
    if value <= 0:
        raise AmbiguousLegacyTenant(
            f"{LEGACY_TENANT_ENV} должен быть положительным целым числом"
        )
    return value


def _validate_parent_tenants(cur):
    for child, column, parent in PARENT_CHECKS:
        if not (_table_exists(cur, child) and _table_exists(cur, parent)):
            continue
        if not (_column_exists(cur, child, column) and _column_exists(cur, parent, "tenant_id")):
            continue
        # Внутренние NULL-FK допустимы, но непустой child обязан принадлежать
        # непустому parent того же tenant.
        cur.execute(
            f'SELECT COUNT(*) FROM "{child}" c '
            f'JOIN "{parent}" p ON p.id = c."{column}" '
            f'WHERE c."{column}" IS NOT NULL '
            f'AND (c.tenant_id IS NOT DISTINCT FROM p.tenant_id) = 0'
            if _column_exists(cur, child, "tenant_id")
            else f'SELECT COUNT(*) FROM "{child}" c '
                 f'JOIN "{parent}" p ON p.id = c."{column}" '
                 f'WHERE c."{column}" IS NOT NULL AND p.tenant_id IS NULL'
        )
        if cur.fetchone()[0]:
            raise AmbiguousLegacyTenant(
                f"{child}.{column} ссылается на parent с другим tenant_id"
            )


def _validate_bridge_tenants(cur):
    if not all(_table_exists(cur, name) for name in ("card_tags", "cards", "tags")):
        return
    cur.execute(
        "SELECT COUNT(*) FROM card_tags ct "
        "JOIN cards c ON c.id = ct.card_id "
        "JOIN tags t ON t.id = ct.tag_id "
        "WHERE c.tenant_id IS NOT NULL AND t.tenant_id IS NOT NULL "
        "AND c.tenant_id <> t.tenant_id"
    )
    if cur.fetchone()[0]:
        raise AmbiguousLegacyTenant("card_tags связывает card и tag разных tenant")


def resolve_tenant(cur):
    existing = [table for table in TENANT_TABLES if _table_exists(cur, table)]
    if not existing:
        return _configured_tenant()

    placeholders = ",".join("?" for _ in existing)
    cur.execute(
        f"SELECT DISTINCT tenant_id FROM ("
        + " UNION ALL ".join(
            f'SELECT tenant_id FROM "{table}" WHERE tenant_id IS NOT NULL'
            for table in existing
        )
        + f") ORDER BY tenant_id"
    )
    referenced = {int(row[0]) for row in cur.fetchall()}

    if _table_exists(cur, "tenants"):
        for tenant_id in referenced:
            cur.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,))
            if cur.fetchone() is None:
                raise AmbiguousLegacyTenant(
                    f"tenant_id={tenant_id} отсутствует в tenants"
                )

    null_count = 0
    for table in existing:
        if _column_exists(cur, table, "tenant_id"):
            cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id IS NULL')
            null_count += cur.fetchone()[0]

    configured = _configured_tenant()
    if null_count:
        if len(referenced) > 1:
            raise AmbiguousLegacyTenant(
                "NULL tenant_id нельзя нормализовать: данные относятся к нескольким tenant"
            )
        if len(referenced) == 1:
            inferred = next(iter(referenced))
        elif configured is None:
            raise AmbiguousLegacyTenant(
                "NULL tenant_id нельзя нормализовать однозначно; задайте CRM_LEGACY_TENANT_ID"
            )
        else:
            inferred = configured
        if configured is not None and configured != inferred:
            raise AmbiguousLegacyTenant(
                "CRM_LEGACY_TENANT_ID не совпадает с tenant существующих данных"
            )
        _validate_parent_tenants(cur)
        _validate_bridge_tenants(cur)
        for table in existing:
            if _column_exists(cur, table, "tenant_id"):
                cur.execute(
                    f'UPDATE "{table}" SET tenant_id=? WHERE tenant_id IS NULL',
                    (inferred,),
                )
        return inferred

    # Нормализация уже завершена (или чистая схема). Непрерывная multi-tenant
    # база допустима: запрет касается именно неоднозначного заполнения NULL.
    _validate_parent_tenants(cur)
    _validate_bridge_tenants(cur)
    return configured


def _add_index(cur, table):
    cur.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_{table}_tenant_id" '
        f'ON "{table}" ("tenant_id")'
    )


def _add_not_null_guard(cur, table):
    # Один триггер на операцию. IF NOT EXISTS у trigger не поддерживается,
    # поэтому наличие проверяем через sqlite_master.
    for suffix, event_name in (("insert", "INSERT"), ("update", "UPDATE OF tenant_id")):
        name = f"trg_{table}_tenant_required_{suffix}"
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
        )
        if cur.fetchone():
            continue
        cur.execute(
            f'CREATE TRIGGER "{name}" BEFORE {event_name} ON "{table}" '
            f'WHEN NEW.tenant_id IS NULL BEGIN '
            f'SELECT RAISE(ABORT, \'tenant_id is required\'); END'
        )


def _replace_tenant_scoped_unique_indexes(cur):
    """Убрать глобальные name/doc-key ограничения после tenant-нормализации."""
    replacements = {
        "ix_tags_name": ("tags", ("tenant_id", "name")),
        "ix_store_locations_name": ("store_locations", ("tenant_id", "name")),
        "ix_deal_statuses_name": ("deal_statuses", ("tenant_id", "name")),
        "ix_custom_object_types_name": ("custom_object_types", ("tenant_id", "name")),
    }
    for old_name, (table, columns) in replacements.items():
        if not _table_exists(cur, table):
            continue
        cur.execute(f'DROP INDEX IF EXISTS "{old_name}"')
        column_sql = ", ".join(f'"{column}"' for column in columns)
        cur.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "{old_name}" '
            f'ON "{table}" ({column_sql})'
        )

    if _table_exists(cur, "nakladnye"):
        cur.execute('DROP INDEX IF EXISTS "uq_nakladnye_doc_key"')
        cur.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_nakladnye_doc_key" '
            'ON "nakladnye" ("tenant_id", "doc_series_norm", "doc_number_norm") '
            'WHERE doc_number_norm IS NOT NULL AND doc_number_norm <> \'\''
        )


def up(cur):
    tenant_id = resolve_tenant(cur)
    for table in TENANT_TABLES:
        if not _table_exists(cur, table) or not _column_exists(cur, table, "tenant_id"):
            continue
        _add_index(cur, table)
        _add_not_null_guard(cur, table)
    _replace_tenant_scoped_unique_indexes(cur)
    if tenant_id is not None:
        print(f"  tenant_id: legacy NULL нормализованы для tenant {tenant_id}")
    else:
        print("  tenant_id: NULL не найден; tenant выбирать не требовалось")
