"""Liveness/readiness: стабильные ответы и проверки основной БД."""
import json

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import database
import health_checks


def test_health_is_backward_compatible_liveness_alias(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_liveness_does_not_query_database(client, monkeypatch):
    def fail_if_called(_engine):
        raise AssertionError("liveness не должен обращаться к БД")

    monkeypatch.setattr(health_checks, "_check_database", fail_if_called)

    for path in ("/health/live", "/health"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_healthy_readiness_checks_main_database_schema_journal_and_uploads(client):
    response = client.get("/health/ready")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["status"] == "ok"
    assert all(check["ok"] for check in body["checks"].values())
    assert body["tenant_databases"]["status"] == "not_checked"
    assert "не проверяется" in body["tenant_databases"]["detail"]


def test_readiness_returns_503_for_closed_database_without_raw_exception(
        client, monkeypatch):
    import main

    class ClosedEngine:
        def connect(self):
            raise OperationalError(
                "SELECT 1",
                {},
                RuntimeError("raw database secret must not escape"),
            )

    monkeypatch.setattr(main, "engine", ClosedEngine())

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["detail"] == "Приложение не готово"
    assert body["checks"]["database"]["detail"] == "Основная база данных недоступна"
    assert "traceback" not in response.text.lower()
    assert "exception" not in response.text.lower()
    assert "raw database secret" not in response.text


def test_readiness_reports_missing_core_table_with_stable_detail(tmp_path):
    isolated = create_engine("sqlite:///:memory:")
    with isolated.begin() as connection:
        connection.execute(text(
            "CREATE TABLE schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        ))

    report = health_checks.readiness_report(
        isolated,
        upload_path=tmp_path,
        expected_migrations=["0001_add_card_position.py"],
    )
    isolated.dispose()

    assert report["ready"] is False
    assert report["checks"]["database"] == {"ok": True}
    assert report["checks"]["schema"] == {
        "ok": False,
        "detail": "Проверка обязательной схемы не пройдена",
    }
    assert "users" not in json.dumps(report, ensure_ascii=False)


def test_readiness_reports_missing_required_migration_journal_entry(tmp_path):
    migration = "0016_revoked_auth_tokens.py"
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migrations WHERE name = :name"),
            {"name": migration},
        )

    try:
        report = health_checks.readiness_report(
            database.engine,
            upload_path=tmp_path,
        )
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO schema_migrations(name, applied_at) "
                    "VALUES (:name, '2026-01-01T00:00:00+00:00')"
                ),
                {"name": migration},
            )

    assert report["ready"] is False
    assert report["checks"]["migrations"] == {
        "ok": False,
        "detail": "Обязательные миграции основной базы не подтверждены",
    }
    assert migration not in json.dumps(report, ensure_ascii=False)


def test_readiness_reports_upload_path_not_writable_as_directory(tmp_path):
    upload_file = tmp_path / "uploads"
    upload_file.write_text("not a directory", encoding="utf-8")

    report = health_checks.readiness_report(
        database.engine,
        upload_path=upload_file,
    )

    assert report["ready"] is False
    assert report["checks"]["uploads"] == {
        "ok": False,
        "detail": "Каталог загрузок недоступен для записи",
    }
    assert str(upload_file) not in json.dumps(report, ensure_ascii=False)
