"""Фидбек 18.09 («почему пропадает синхронизация с сервером»).

При явном CRM_DATA_DIR легаси-файл настроек рядом с кодом перекрывал файл
каталога данных для ЧТЕНИЯ, а защитный guard запрещал туда ПИСАТЬ. Синк
успешно импортировал письма и падал уже на сохранении last_sync — каждую
итерацию watchdog уходил «Ошибка подключения к почтовому серверу».
"""
import json

import pytest

import routers.email_parser_router as epr


@pytest.fixture()
def legacy_shadow(monkeypatch, tmp_path):
    """CRM_DATA_DIR задан + легаси-файл «существует» рядом с кодом (как на проде)."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CRM_DATA_DIR", str(data_dir))
    monkeypatch.setattr("database.DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        epr.os.path, "exists",
        lambda p: str(p).endswith("email_settings.json"))
    return data_dir


def test_main_settings_path_ignores_legacy_when_data_dir_set(legacy_shadow):
    path = epr._get_settings_path(None)
    assert str(legacy_shadow) in path, "чтение и запись должны жить в CRM_DATA_DIR"
    assert path.endswith("email_settings.json")


def test_tenant_settings_path_ignores_legacy_when_data_dir_set(legacy_shadow):
    path = epr._get_settings_path(7)
    assert str(legacy_shadow / "tenants") in path
    assert path.endswith("email_settings_7.json")


def test_save_settings_writes_into_data_dir(legacy_shadow):
    # Корректный токен при сохранении служебных полей не шифруется повторно.
    token = epr.encrypt_credential("mailbox-secret", epr.obtain_fernet())
    settings = {"email": "sale@svetvdome.by", "password": token,
                "imap_server": "mailbe05.hoster.by", "last_sync": "2026-09-18"}
    epr.save_settings(
        dict(settings), None, preserve_encrypted_password=True
    )
    saved = (legacy_shadow / "email_settings.json").read_text(encoding="utf-8")
    assert "sale@svetvdome.by" in saved
    assert json.loads(saved)["password"] == token
    assert (legacy_shadow / "email_settings.json").stat().st_mode & 0o777 == 0o600
