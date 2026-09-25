"""Сфокусированные security-тесты BA-11 для webhook."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import socket
import stat
import threading

import pytest

import models
from migrations import import_migration
from tools.convert_webhook_secrets_fernet import convert_database
from webhook_delivery import (
    BoundedDeliveryExecutor,
    DeliveryOutcome,
    WebhookTransportError,
    build_signed_body,
    create_delivery,
    deliver_delivery,
    resolve_public_target,
)
from webhook_security import (
    WebhookEncryptionConfigError,
    WebhookSecretState,
    classify_stored_secret,
    decrypt_secret,
    encrypt_secret,
    obtain_fernet,
    webhook_key_path,
)

PUBLIC_IP = "93.184.216.34"


def _public_resolver(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]


def _webhook(db, **overrides):
    values = {
        "url": "https://example.com/hook",
        "events": '["card.created"]',
        "is_active": True,
        "tenant_id": None,
    }
    values.update(overrides)
    webhook = models.Webhook(**values)
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


# --- ключ и шифрование ------------------------------------------------------


def test_production_webhook_key_never_falls_backs_to_jwt_or_email(tmp_path):
    environ = {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "production",
        "CRM_SECRET_KEY": "00" * 31 + "03",
        "CRM_EMAIL_SECRET_KEY": "00" * 31 + "04",
    }
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet(environ)
    assert "00" not in str(caught.value)


def test_development_creates_private_webhook_key_file(tmp_path):
    environ = {"CRM_DATA_DIR": str(tmp_path), "CRM_DEPLOYMENT": "development"}
    fernet = obtain_fernet(environ)
    path = webhook_key_path(environ)
    assert fernet.decrypt(fernet.encrypt(b"ok")) == b"ok"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("bad_key", ["short", "00" * 31, "!" * 43 + "="])
def test_invalid_key_error_does_not_echo_key(bad_key):
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet({"CRM_WEBHOOK_ENCRYPTION_KEY": bad_key})
    assert bad_key not in str(caught.value)


def test_ciphertext_is_stored_and_api_masks_secret(client, admin, db, monkeypatch):
    import routers.webhooks_router as webhook_router

    monkeypatch.setattr(webhook_router.socket, "getaddrinfo", _public_resolver)
    _, headers = admin
    response = client.post(
        "/webhooks/",
        headers=headers,
        json={
            "url": "https://example.com/hook",
            "secret": "signature-secret",
            "events": ["card.created"],
        },
    )
    assert response.status_code == 200
    webhook_id = response.json()["id"]

    db.expire_all()
    stored = db.get(models.Webhook, webhook_id).secret
    assert stored != "signature-secret"
    assert decrypt_secret(stored, obtain_fernet()) == "signature-secret"

    listed = client.get("/webhooks/", headers=headers)
    assert listed.status_code == 200
    assert "secret" not in listed.json()[0]
    assert listed.json()[0]["has_secret"] is True
    assert stored not in listed.text

    updated = client.patch(
        f"/webhooks/{webhook_id}", headers=headers, json={"events": []}
    )
    assert updated.status_code == 200
    assert "secret" not in updated.text
    assert "signature-secret" not in listed.text + updated.text


# --- миграция и явная конвертация -----------------------------------------


def _old_webhooks_db():
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE webhooks (
            id INTEGER PRIMARY KEY,
            url VARCHAR(500) NOT NULL,
            secret VARCHAR(200),
            events TEXT NOT NULL,
            is_active BOOLEAN,
            tenant_id INTEGER,
            created_at DATETIME
        );
        INSERT INTO webhooks
            (id, url, secret, events, is_active, created_at)
        VALUES
            (1, 'https://example.com/hook', 'legacy-secret', '["card.created"]', 1, '2026-01-01');
        """
    )
    return connection


def test_0017_converts_plaintext_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("CRM_WEBHOOK_ENCRYPTION_KEY", "00" * 31 + "05")
    connection = _old_webhooks_db()
    migration = import_migration("0017_secure_webhook_secrets.py")
    # create_all на новой модели мог уже создать audit-таблицы до запуска
    # миграции; rebuild обязан сохранить их FK-ссылку на таблицу webhooks.
    connection.execute(migration.DELIVERIES_DDL)
    connection.execute(migration.ATTEMPTS_DDL)
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")

    migration.up(connection.cursor())
    first = connection.execute("SELECT secret FROM webhooks WHERE id=1").fetchone()[0]
    migration.up(connection.cursor())
    second = connection.execute("SELECT secret FROM webhooks WHERE id=1").fetchone()[0]

    assert first != "legacy-secret"
    assert second == first
    assert decrypt_secret(second, obtain_fernet()) == "legacy-secret"
    secret_type = connection.execute(
        "SELECT type FROM pragma_table_info('webhooks') WHERE name='secret'"
    ).fetchone()[0]
    assert secret_type == "TEXT"
    delivery_fk = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='webhook_deliveries'"
    ).fetchone()[0]
    assert "REFERENCES webhooks" in delivery_fk
    assert "webhooks_legacy_ba11" not in delivery_fk
    assert connection.execute(
        "SELECT COUNT(*) FROM webhook_deliveries"
    ).fetchone()[0] == 0
    connection.close()


def test_0017_rejects_malformed_ciphertext_without_leaking(monkeypatch):
    monkeypatch.setenv("CRM_WEBHOOK_ENCRYPTION_KEY", "00" * 31 + "06")
    connection = _old_webhooks_db()
    malformed = "gAAAAA" + "A" * 120
    connection.execute("UPDATE webhooks SET secret=?", (malformed,))
    migration = import_migration("0017_secure_webhook_secrets.py")

    with pytest.raises(RuntimeError) as caught:
        migration.up(connection.cursor())
    assert malformed not in str(caught.value)
    assert "secret_invalid" in str(caught.value)
    assert connection.execute("SELECT secret FROM webhooks").fetchone()[0] == malformed
    connection.close()


def test_explicit_conversion_tool_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_WEBHOOK_ENCRYPTION_KEY", "00" * 31 + "07")
    db_path = tmp_path / "webhooks.db"
    connection = _old_webhooks_db()
    disk_connection = __import__("sqlite3").connect(db_path)
    connection.backup(disk_connection)
    disk_connection.close()
    connection.close()

    assert convert_database(db_path) == (1, 0)
    assert convert_database(db_path) == (0, 1)


# --- SSRF и transport ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "https://user:password@example.com/hook",
        "https://example.com/hook?token=secret",
        "https://example.com/hook#fragment",
        "https://example.com:8443/hook",
        "http://example.com:443/hook",
    ],
)
def test_url_shape_rejects_unsafe_components(url):
    with pytest.raises(Exception) as caught:
        resolve_public_target(url, resolver=_public_resolver)
    assert "password" not in str(caught.value)
    assert "token=secret" not in str(caught.value)


@pytest.mark.parametrize(
    "numeric_ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_resolver_rejects_non_public_ipv4_and_ipv6(numeric_ip):
    family = socket.AF_INET6 if ":" in numeric_ip else socket.AF_INET
    resolver = lambda *_args, **_kwargs: [
        (family, socket.SOCK_STREAM, 6, "", (numeric_ip, 443))
    ]
    with pytest.raises(Exception) as caught:
        resolve_public_target("https://example.com/hook", resolver=resolver)
    assert caught.value.code == "address_not_public"


@pytest.mark.parametrize("alternate", ["2130706433", "0x7f000001", "127.1"])
def test_numeric_alternate_forms_are_rejected(alternate):
    resolver = lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
    ]
    with pytest.raises(Exception) as caught:
        resolve_public_target(f"http://{alternate}/hook", resolver=resolver)
    assert caught.value.code == "address_not_public"


def test_actual_connect_target_is_the_validated_numeric_ip(db):
    delivery_id = _delivery_fixture(db)
    dns_answers = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )
    connected_targets = []

    def rebinding_resolver(*_args, **_kwargs):
        return next(dns_answers)

    def timeout_once(target, *_args, **_kwargs):
        connected_targets.append(target.ip)
        raise socket.timeout("rebind")

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=timeout_once,
        resolver=rebinding_resolver,
        sleeper=lambda _delay: None,
    )
    assert connected_targets == [PUBLIC_IP]
    assert outcome.error_code == "address_not_public"


def test_private_dns_rebind_on_retry_never_reaches_transport(db):
    delivery_id = _delivery_fixture(db)
    dns_answers = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )
    transport_calls = []

    def timeout_once(*_args, **_kwargs):
        transport_calls.append(1)
        raise socket.timeout("rebind")

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=timeout_once,
        resolver=lambda *_args, **_kwargs: next(dns_answers),
        sleeper=lambda _delay: None,
    )
    assert len(transport_calls) == 1
    assert outcome.error_code == "address_not_public"


# --- delivery, retry, audit и отсутствие утечек -----------------------------


def test_delivery_executor_has_a_bounded_pending_queue():
    executor = BoundedDeliveryExecutor(max_workers=1, max_pending=2)
    started = threading.Event()
    release = threading.Event()
    executor.submit(lambda: (started.set(), release.wait(1)))
    assert started.wait(1)
    assert executor.submit(lambda: None) is True
    assert executor.submit(lambda: None) is False
    release.set()


def _delivery_fixture(db, secret="delivery-secret"):
    fernet = obtain_fernet()
    webhook = _webhook(db, secret=encrypt_secret(secret, fernet))
    delivery = create_delivery(
        db, webhook_id=webhook.id, event="card.created", tenant_id=None
    )
    return delivery.id


def test_delivery_signs_exact_body_and_persists_success(db):
    delivery_id = _delivery_fixture(db)
    captured = {}

    def transport(target, body, headers, timeout):
        captured.update(target=target, body=body, headers=headers, timeout=timeout)
        return 204

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {"id": 7}},
        transport=transport,
        resolver=_public_resolver,
        sleeper=lambda _delay: None,
    )
    expected = hmac.new(
        b"delivery-secret", captured["body"], hashlib.sha256
    ).hexdigest()
    assert captured["headers"]["X-Webhook-Signature"] == expected
    assert captured["target"].ip == PUBLIC_IP
    assert outcome.status == "succeeded"

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery.status == "succeeded"
    assert delivery.attempt_count == 1
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).count() == 1


def test_pinned_transport_closes_connection(monkeypatch):
    import webhook_delivery as delivery_module

    state = {"closed": False}

    class FakeConnection:
        def __init__(self, target, timeout):
            self.target = target

        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            return type("Response", (), {"status": 204})()

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(delivery_module, "_PinnedHTTPConnection", FakeConnection)
    target = resolve_public_target(
        "http://example.com/hook", resolver=_public_resolver
    )
    assert delivery_module.pinned_http_transport(target, b"{}", {}, 1.0) == 204
    assert state["closed"] is True


def test_timeout_retry_cap_backoff_and_dead_letter_are_bounded(db):
    delivery_id = _delivery_fixture(db)
    calls = []
    sleeps = []

    def timeout_transport(*_args, **_kwargs):
        calls.append(1)
        raise socket.timeout("credential-bearing internal text")

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=timeout_transport,
        resolver=_public_resolver,
        sleeper=sleeps.append,
        max_attempts=3,
    )
    assert isinstance(outcome, DeliveryOutcome)
    assert outcome.status == "dead_letter"
    assert outcome.error_code == "timeout"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]

    db.expire_all()
    assert db.get(models.WebhookDelivery, delivery_id).status == "dead_letter"
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).count() == 3


def test_redirect_is_rejected_without_following(db, caplog):
    delivery_id = _delivery_fixture(db)
    calls = []

    def redirect_transport(*_args, **_kwargs):
        calls.append(1)
        return 302

    with caplog.at_level("WARNING"):
        outcome = deliver_delivery(
            delivery_id,
            {"event": "card.created", "data": {}},
            transport=redirect_transport,
            resolver=_public_resolver,
            sleeper=lambda _delay: None,
        )
    assert outcome.correlation_id in caplog.text
    assert "redirect_rejected" in caplog.text
    assert outcome.status == "failed"
    assert outcome.error_code == "redirect_rejected"
    assert outcome.response_status == 302
    assert len(calls) == 1


def test_raw_transport_exception_is_replaced_and_never_logged(db, caplog):
    delivery_id = _delivery_fixture(db)
    sensitive = "https://user:password@example.com/hook?token=credential"

    def exploding_transport(*_args, **_kwargs):
        raise RuntimeError(sensitive)

    with caplog.at_level("WARNING"):
        outcome = deliver_delivery(
            delivery_id,
            {"event": "card.created", "data": {}},
            transport=exploding_transport,
            resolver=_public_resolver,
            sleeper=lambda _delay: None,
        )
    assert outcome.error_code == "transport_error"
    assert sensitive not in caplog.text
    assert "password" not in caplog.text
    assert "token=credential" not in caplog.text


def test_legacy_plaintext_fails_closed_for_delivery(db):
    delivery_id = _delivery_fixture(db, secret="legacy-at-rest")
    db.expire_all()
    webhook = db.query(models.Webhook).one()
    webhook.secret = "legacy-at-rest"
    db.commit()

    def must_not_call(*_args, **_kwargs):
        raise AssertionError("transport must not be called")

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=must_not_call,
        resolver=_public_resolver,
        sleeper=lambda _delay: None,
    )
    assert outcome.status == "failed"
    assert outcome.error_code == "secret_legacy_plaintext"


def test_classifier_distinguishes_ciphertext_plaintext_and_malformed():
    fernet = obtain_fernet()
    ciphertext = encrypt_secret("known-secret", fernet)
    assert classify_stored_secret(ciphertext, fernet).state is (
        WebhookSecretState.VALID_CIPHERTEXT
    )
    assert classify_stored_secret("plain", fernet).state is (
        WebhookSecretState.LEGACY_PLAINTEXT
    )
    assert classify_stored_secret("gAAAAA" + "A" * 100, fernet).state is (
        WebhookSecretState.INVALID_CIPHERTEXT
    )
    damaged_prefix = "A" + ciphertext[1:]
    assert classify_stored_secret(damaged_prefix, fernet).state is (
        WebhookSecretState.INVALID_CIPHERTEXT
    )


def test_build_signed_body_returns_identical_bytes():
    body, headers = build_signed_body({"event": "test"}, "secret")
    assert body == b'{"event":"test"}'
    assert headers["X-Webhook-Signature"] == hmac.new(
        b"secret", body, hashlib.sha256
    ).hexdigest()
