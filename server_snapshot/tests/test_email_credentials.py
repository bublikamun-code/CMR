"""BA-12: строгая Fernet-защита паролей почтового ящика."""
import base64
import json
import logging
import os

from cryptography.fernet import Fernet
import pytest

import email_credentials
from email_credentials import (
    EmailEncryptionConfigError,
    InvalidCredentialCiphertextError,
    LegacyPlaintextError,
    StoredCredentialState,
    classify_stored_credential,
    decrypt_credential,
    email_key_path,
    encrypt_credential,
    normalize_fernet_key,
    obtain_fernet,
)
from routers import email_parser_router as epr


RAW_KEY = bytes(range(32))
HEX_KEY = RAW_KEY.hex()
B64_KEY = base64.urlsafe_b64encode(RAW_KEY).decode("ascii")
PASSWORD = "mailbox-password-for-test"


@pytest.fixture()
def fernet():
    return Fernet(base64.urlsafe_b64encode(RAW_KEY))


def _isolated_env(tmp_path, *, production=False, key=HEX_KEY):
    env = {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "production" if production else "test",
    }
    if key is not None:
        env["CRM_EMAIL_SECRET_KEY"] = key
    return env


def test_key_formats_normalize_to_same_raw_bytes():
    assert normalize_fernet_key(HEX_KEY) == RAW_KEY
    assert normalize_fernet_key(B64_KEY) == RAW_KEY


@pytest.mark.parametrize(
    "value",
    [
        "short",
        "z" * 64,
        "0" * 63,
        "0" * 65,
        base64.urlsafe_b64encode(b"too short").decode("ascii"),
        base64.urlsafe_b64encode(bytes(33)).decode("ascii"),
    ],
)
def test_invalid_key_is_controlled_and_does_not_echo_value(value):
    with pytest.raises(EmailEncryptionConfigError) as caught:
        normalize_fernet_key(value)
    assert value not in str(caught.value)


def test_jwt_key_is_never_email_key_fallback(tmp_path):
    env = _isolated_env(tmp_path, production=True, key=None)
    env["CRM_SECRET_KEY"] = "jwt-only-value"

    with pytest.raises(EmailEncryptionConfigError) as caught:
        obtain_fernet(env)

    assert "jwt-only-value" not in str(caught.value)
    assert not (tmp_path / ".email_secret_key").exists()


def test_production_missing_key_fails_closed(tmp_path):
    with pytest.raises(EmailEncryptionConfigError, match="не настроено"):
        obtain_fernet(_isolated_env(tmp_path, production=True, key=None))


def test_generated_hex_key_reloads_without_process_cache(tmp_path):
    env = _isolated_env(tmp_path, key=None)
    first = obtain_fernet(env)
    second = obtain_fernet(env)
    token = first.encrypt(b"reload-value")

    assert second.decrypt(token) == b"reload-value"
    key_file = tmp_path / ".email_secret_key"
    assert len(key_file.read_text(encoding="ascii").strip()) == 64
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert email_key_path(env) == key_file


def test_canonical_file_is_used_when_env_missing(tmp_path):
    key_file = tmp_path / ".email_secret_key"
    key_file.write_text(B64_KEY + "\n", encoding="ascii")
    key_file.chmod(0o600)
    fernet = obtain_fernet(_isolated_env(tmp_path, key=None))

    assert decrypt_credential(encrypt_credential(PASSWORD, fernet), fernet) == PASSWORD


def test_fernet_unavailable_is_controlled(monkeypatch, tmp_path):
    monkeypatch.setattr(email_credentials, "Fernet", None)
    with pytest.raises(EmailEncryptionConfigError, match="недоступно"):
        obtain_fernet(_isolated_env(tmp_path))


def test_valid_decrypt_and_invalid_token(fernet):
    token = encrypt_credential(PASSWORD, fernet)
    assert token.startswith("gAAAA")
    assert decrypt_credential(token, fernet) == PASSWORD
    assert classify_stored_credential(token, fernet).state is StoredCredentialState.VALID_CIPHERTEXT

    broken = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidCredentialCiphertextError):
        decrypt_credential(broken, fernet)


def test_malformed_gAAAA_is_not_accepted_as_valid(fernet):
    malformed = "gAAAA-not-a-real-fernet-token"
    with pytest.raises(InvalidCredentialCiphertextError) as caught:
        decrypt_credential(malformed, fernet)
    assert malformed not in str(caught.value)


def test_legacy_plaintext_is_classified_but_never_returned_by_sync(fernet, monkeypatch):
    assert classify_stored_credential(PASSWORD, fernet).state is StoredCredentialState.LEGACY_PLAINTEXT
    monkeypatch.setattr(epr, "obtain_fernet", lambda: fernet)
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "operator-override")
    with pytest.raises(LegacyPlaintextError):
        epr._get_smtp_password({"password": PASSWORD})


def test_smtp_override_is_used_only_without_stored_secret(fernet, monkeypatch):
    monkeypatch.setattr(epr, "obtain_fernet", lambda: fernet)
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "operator-override")
    assert epr._get_smtp_password({"password": ""}) == "operator-override"

    token = encrypt_credential(PASSWORD, fernet)
    assert epr._get_smtp_password({"password": token}) == PASSWORD


def test_smtp_override_cannot_rescue_missing_fernet(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "operator-override")
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRM_DEPLOYMENT", "production")
    monkeypatch.delenv("CRM_EMAIL_SECRET_KEY", raising=False)
    with pytest.raises(EmailEncryptionConfigError):
        epr._get_smtp_password({"password": ""})


def _use_temp_email_data(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("database.DATA_DIR", str(tmp_path))


def _settings_path(tmp_path, user):
    path = tmp_path / "tenants" / f"email_settings_{user.tenant_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_new_settings_save_encrypts_and_uses_mode_0600(client, admin, monkeypatch, tmp_path):
    _use_temp_email_data(monkeypatch, tmp_path)
    user, headers = admin
    payload = {
        "email": "mailbox@example.test",
        "password": PASSWORD,
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    }

    response = client.post("/email-parser/settings", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    settings_path = _settings_path(tmp_path, user)
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["password"] != PASSWORD
    fernet = obtain_fernet()
    assert decrypt_credential(stored["password"], fernet) == PASSWORD
    assert settings_path.stat().st_mode & 0o777 == 0o600


def test_masked_settings_preserve_ciphertext_without_double_encryption(
        client, admin, monkeypatch, tmp_path):
    _use_temp_email_data(monkeypatch, tmp_path)
    user, headers = admin
    base = {
        "email": "mailbox@example.test",
        "password": PASSWORD,
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    }
    assert client.post("/email-parser/settings", headers=headers, json=base).status_code == 200
    settings_path = _settings_path(tmp_path, user)
    original = json.loads(settings_path.read_text(encoding="utf-8"))["password"]

    base["password"] = "********"
    base["target_status"] = "В работе"
    assert client.post("/email-parser/settings", headers=headers, json=base).status_code == 200
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["password"] == original
    assert decrypt_credential(saved["password"], obtain_fernet()) == PASSWORD


def test_legacy_plaintext_normal_sync_is_rejected_without_network(
        client, admin, monkeypatch, tmp_path):
    _use_temp_email_data(monkeypatch, tmp_path)
    user, headers = admin
    settings_path = _settings_path(tmp_path, user)
    settings_path.write_text(json.dumps({
        "email": "mailbox@example.test",
        "password": PASSWORD,
        "imap_server": "impossible.example.test",
        "target_status": "Новый запрос",
    }), encoding="utf-8")

    response = client.post("/email-parser/sync", headers=headers)

    assert response.status_code == 409
    assert "конвертац" in response.json()["detail"]
    assert PASSWORD not in response.text


def test_missing_key_blocks_settings_save_and_sync(
        client, admin, monkeypatch, tmp_path):
    _use_temp_email_data(monkeypatch, tmp_path)
    monkeypatch.delenv("CRM_EMAIL_SECRET_KEY", raising=False)
    monkeypatch.setenv("CRM_DEPLOYMENT", "production")
    _, headers = admin
    payload = {
        "email": "mailbox@example.test",
        "password": PASSWORD,
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    }

    save = client.post("/email-parser/settings", headers=headers, json=payload)
    sync = client.post("/email-parser/sync", headers=headers)

    assert save.status_code == 503
    assert sync.status_code == 503
    assert not (tmp_path / "tenants" / f"email_settings_{admin[0].tenant_id}.json").exists()


def test_malformed_masked_stored_value_is_rejected(client, admin, monkeypatch, tmp_path):
    _use_temp_email_data(monkeypatch, tmp_path)
    user, headers = admin
    settings_path = _settings_path(tmp_path, user)
    settings_path.write_text(json.dumps({
        "email": "mailbox@example.test",
        "password": "gAAAA-fake",
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    }), encoding="utf-8")

    response = client.post("/email-parser/settings", headers=headers, json={
        "email": "mailbox@example.test",
        "password": "********",
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    })

    assert response.status_code == 409
    assert "gAAAA-fake" not in response.text


def test_secret_material_never_appears_in_errors_or_logs(
        client, admin, monkeypatch, tmp_path, caplog):
    _use_temp_email_data(monkeypatch, tmp_path)
    user, headers = admin
    malformed = "gAAAA-super-secret-token"
    settings_path = _settings_path(tmp_path, user)
    settings_path.write_text(json.dumps({
        "email": "mailbox@example.test",
        "password": malformed,
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
    }), encoding="utf-8")
    caplog.set_level(logging.DEBUG)

    response = client.post("/email-parser/sync", headers=headers)

    assert response.status_code == 409
    assert malformed not in response.text
    assert malformed not in caplog.text
    assert PASSWORD not in response.text
