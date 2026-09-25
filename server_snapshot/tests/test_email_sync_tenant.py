"""Регрессии определения tenant при импорте писем."""
import sys
from email.message import EmailMessage

import pytest
from sqlalchemy import event

import models
import models_tenant
import routers.email_parser_router as email_parser_router

# Pytest обычно загружает conftest как ``conftest``; при импорте как пакета
# он доступен как ``tests.conftest``. Берём уже загруженный модуль, иначе
# импорт второго экземпляра зарегистрирует ещё один маскирующий слушатель.
cft = sys.modules.get("conftest")
if cft is None:
    from tests import conftest as cft


def _without_test_tenant_listener():
    """Снять маскирующий слушатель conftest перед реальным запросом."""
    event.remove(models.Base, "before_insert", cft._set_test_tenant)


def _restore_test_tenant_listener():
    event.listen(models.Base, "before_insert", cft._set_test_tenant, propagate=True)


class _TestImapClient:
    """Одно простое письмо без вложений для прохождения реального пути импорта."""

    def __init__(self, host, timeout=15):
        self.host = host
        self.timeout = timeout

    def login(self, email_addr, password):
        return "OK", [b""]

    def select(self, mailbox):
        return "OK", [b"1"]

    def search(self, charset, criteria):
        return "OK", [b"1"]

    def fetch(self, message_id, query):
        message = EmailMessage()
        message["Subject"] = "Тестовое письмо"
        message["From"] = "sender@example.com"
        message["To"] = "sale@example.com"
        message.set_content("Нужна люстра в гостиную.")
        return "OK", [(b"1", message.as_bytes())]

    def store(self, message_id, command, flags):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


@pytest.fixture
def mailbox_settings(monkeypatch):
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "test-password")
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    monkeypatch.setattr(
        email_parser_router.imaplib,
        "IMAP4_SSL",
        _TestImapClient,
    )
    monkeypatch.setattr(
        email_parser_router,
        "save_settings",
        lambda settings, tenant_id, **kwargs: None,
    )

    def _factory(**overrides):
        settings = {
            "email": "sale@example.com",
            "password": "",
            "imap_server": "imap.example.com",
            "target_status": "Новый запрос",
        }
        settings.update(overrides)
        return settings

    return _factory


def _sync_without_test_listener(tenant_id, settings, db):
    _without_test_tenant_listener()
    try:
        return email_parser_router._sync_tenant_emails(tenant_id, settings, db)
    finally:
        _restore_test_tenant_listener()


def test_main_mailbox_uses_only_db_tenant_for_card_and_log(db, mailbox_settings):
    """Главный ящик при единственном tenant импортирует письмо без смешения данных."""
    tenant_id = db.query(models_tenant.Tenant.id).order_by(
        models_tenant.Tenant.id
    ).scalar()
    assert db.query(models_tenant.Tenant).count() == 1

    result = _sync_without_test_listener(
        None,
        mailbox_settings(),
        db,
    )

    assert result["success"] is True, result
    assert result["tenant_id"] == tenant_id
    assert result["count"] == 1

    db.expire_all()
    card = db.query(models.Card).filter(models.Card.sender_email == "sender@example.com").one()
    log_entry = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id,
        models.ActivityLog.action == "Импорт почты",
    ).one()
    assert card.tenant_id == tenant_id
    assert log_entry.tenant_id == tenant_id


def test_main_mailbox_stops_when_tenant_is_ambiguous(db, mailbox_settings):
    """При нескольких tenant синк сообщает об ошибке и ничего не импортирует."""
    second_tenant = models_tenant.Tenant(
        id=202,
        name="Второй тестовый tenant",
        db_path="tests/crm_202.db",
    )
    db.add(second_tenant)
    db.commit()
    assert db.query(models_tenant.Tenant).count() == 2

    result = _sync_without_test_listener(None, mailbox_settings(), db)

    assert result == {
        "tenant_id": None,
        "success": False,
        "error": "Не удалось определить tenant для импорта писем: укажите tenant в настройках почты",
        "count": 0,
    }
    assert db.query(models.Card).count() == 0
    assert db.query(models.ActivityLog).count() == 0


def test_mailbox_settings_tenant_wins_over_single_db_fallback(db, mailbox_settings):
    """Явный tenant в настройках главного ящика важнее неоднозначной БД."""
    settings_tenant = models_tenant.Tenant(
        id=303,
        name="Tenant почтовых настроек",
        db_path="tests/crm_303.db",
    )
    db.add(settings_tenant)
    db.commit()
    assert db.query(models_tenant.Tenant).count() == 2

    result = _sync_without_test_listener(
        None,
        mailbox_settings(tenant_id="303"),
        db,
    )

    assert result["success"] is True, result
    assert result["tenant_id"] == settings_tenant.id
    assert result["count"] == 1

    db.expire_all()
    card = db.query(models.Card).filter(models.Card.sender_email == "sender@example.com").one()
    log_entry = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id,
        models.ActivityLog.action == "Импорт почты",
    ).one()
    assert card.tenant_id == settings_tenant.id
    assert log_entry.tenant_id == settings_tenant.id


def test_invalid_mailbox_settings_tenant_falls_back_to_only_db_tenant(
    db, mailbox_settings, caplog
):
    """Некорректный tenant_id из JSON не ломает синк единственного tenant."""
    tenant_id = db.query(models_tenant.Tenant.id).order_by(
        models_tenant.Tenant.id
    ).scalar()

    result = _sync_without_test_listener(
        None,
        mailbox_settings(tenant_id="не tenant"),
        db,
    )

    assert result["success"] is True, result
    assert result["tenant_id"] == tenant_id
    assert "Некорректный tenant_id в настройках почты" in caplog.text
