"""Readiness-проверки основной БД, схемы, миграций и каталога загрузок."""
import os
from pathlib import Path
import re
import tempfile

from sqlalchemy import text

from runtime_config import uploads_dir


APP_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = APP_DIR / "migrations"
MIGRATION_NAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")
REQUIRED_SCHEMA_OBJECTS = frozenset({
    "table:tenants",
    "table:users",
    "table:cards",
    "table:transactions",
    "table:nakladnye",
    "table:schema_migrations",
})
REQUIRED_INDEXES = frozenset({
    "index:uq_remainder_per_card",
    "index:uq_nakladnye_doc_key",
    "index:ux_record_versions_t_r_v",
    "index:ux_revoked_auth_tokens_token_key",
})


def _ok():
    return {"ok": True}


def _failed(detail: str):
    return {"ok": False, "detail": detail}


def discover_expected_migrations(migrations_dir=None, environ=None) -> list[str]:
    """Получить ожидаемый baseline, не импортируя migration-файлы."""
    directory = Path(migrations_dir or MIGRATIONS_DIR)
    env = os.environ if environ is None else environ
    configured = env.get("CRM_REQUIRED_MIGRATIONS")
    if configured is not None:
        names = [item.strip() for item in configured.split(",") if item.strip()]
    elif directory.is_dir():
        names = sorted(
            path.name for path in directory.iterdir()
            if path.is_file() and MIGRATION_NAME_RE.match(path.name)
        )
    else:
        names = []

    if any(not MIGRATION_NAME_RE.match(name) for name in names):
        raise ValueError("invalid migration name")
    return names


def _check_database(engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:
        return False


def _check_schema(connection) -> bool:
    rows = connection.execute(text(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'index')"
    )).all()
    present = {f"{row[0]}:{row[1]}" for row in rows}
    return REQUIRED_SCHEMA_OBJECTS.issubset(present) and REQUIRED_INDEXES.issubset(present)


def _check_migrations(connection, expected: list[str]) -> bool:
    if not expected:
        return False
    try:
        rows = connection.execute(text("SELECT name FROM schema_migrations")).all()
    except Exception:
        return False
    applied = {row[0] for row in rows}
    return set(expected).issubset(applied)


def _check_uploads(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        if not path.is_dir():
            return False
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".crm-readiness-", dir=path
        )
        os.close(descriptor)
        os.unlink(temporary_name)
        return True
    except OSError:
        return False


def readiness_report(
    engine,
    *,
    upload_path=None,
    migrations_dir=None,
    environ=None,
    expected_migrations=None,
) -> dict:
    """Собрать безопасный readiness-отчёт без raw exception и путей.

    Tenant-БД намеренно не открываются: основной migration runner их не
    обновляет. Ответ явно сообщает, что их состояние не подтверждено.
    """
    configured_uploads = Path(upload_path) if upload_path is not None else uploads_dir(environ)
    migration_configuration_ok = True
    try:
        expected = (
            list(expected_migrations)
            if expected_migrations is not None
            else discover_expected_migrations(migrations_dir, environ)
        )
        catalog = discover_expected_migrations(migrations_dir, {"CRM_REQUIRED_MIGRATIONS": ",".join(expected)}) if expected else []
        if not expected or not set(expected).issubset(catalog):
            migration_configuration_ok = False
    except (OSError, ValueError):
        expected = []
        migration_configuration_ok = False

    database_ok = _check_database(engine)
    if database_ok:
        try:
            with engine.connect() as connection:
                schema_ok = _check_schema(connection)
                migrations_ok = (
                    migration_configuration_ok and _check_migrations(connection, expected)
                )
        except Exception:
            schema_ok = False
            migrations_ok = False
    else:
        schema_ok = False
        migrations_ok = False

    uploads_ok = _check_uploads(configured_uploads)
    checks = {
        "database": _ok() if database_ok else _failed("Основная база данных недоступна"),
        "schema": _ok() if schema_ok else _failed("Проверка обязательной схемы не пройдена"),
        "migrations": (
            _ok() if migrations_ok else _failed("Обязательные миграции основной базы не подтверждены")
        ),
        "uploads": (
            _ok() if uploads_ok else _failed("Каталог загрузок недоступен для записи")
        ),
    }
    ready = all(check["ok"] for check in checks.values())
    return {
        "status": "ok" if ready else "not_ready",
        "ready": ready,
        "detail": "Приложение готово" if ready else "Приложение не готово",
        "checks": checks,
        "tenant_databases": {
            "status": "not_checked",
            "detail": "Состояние тенантных баз не проверяется; миграции основной базы их не выполняют",
        },
    }
