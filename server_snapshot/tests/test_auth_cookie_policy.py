"""Единая cookie-security policy и её startup-валидация."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import auth
from runtime_config import (
    RuntimeConfigError,
    auth_cookie_secure,
    parse_strict_bool,
    validate_startup_config,
)


ROOT = Path(__file__).resolve().parent.parent


def _has_secure(set_cookie: str) -> bool:
    return any(
        part.strip().lower() == "secure"
        for part in set_cookie.split(";")
    )


@pytest.mark.parametrize("forwarded_proto", ["http", "https"])
@pytest.mark.parametrize(("policy", "expected_secure"), [
    ("true", True),
    ("false", False),
])
def test_login_cookie_uses_explicit_policy_not_forwarded_header(
        client, make_user, monkeypatch, policy, expected_secure, forwarded_proto):
    make_user(username="cookie_login", password="Passw0rd!23")
    monkeypatch.setenv("CRM_DEPLOYMENT", "test")
    monkeypatch.setenv("CRM_COOKIE_SECURE", policy)

    response = client.post(
        "/auth/login",
        data={"username": "cookie_login", "password": "Passw0rd!23"},
        headers={"X-Forwarded-Proto": forwarded_proto},
    )

    assert response.status_code == 200, response.text
    set_cookie = response.headers["set-cookie"]
    assert "crm_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert _has_secure(set_cookie) is expected_secure


@pytest.mark.parametrize("forwarded_proto", ["http", "https"])
@pytest.mark.parametrize(("policy", "expected_secure"), [
    ("true", True),
    ("false", False),
])
def test_sliding_renewal_uses_same_explicit_policy(
        client, make_user, monkeypatch, policy, expected_secure, forwarded_proto):
    user, _ = make_user(username="cookie_renewal")
    monkeypatch.setenv("CRM_DEPLOYMENT", "test")
    monkeypatch.setenv("CRM_COOKIE_SECURE", policy)
    token = auth.create_access_token(
        {
            "sub": user.username,
            "tenant_id": user.tenant_id,
            "pv": auth.password_version(user.hashed_password),
        },
        expires_minutes=0.5,
    )

    response = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-Proto": forwarded_proto,
        },
    )

    assert response.status_code == 200, response.text
    set_cookie = response.headers["set-cookie"]
    assert "crm_token=" in set_cookie
    assert _has_secure(set_cookie) is expected_secure


@pytest.mark.parametrize(("raw", "expected"), [
    ("true", True), ("TRUE", True), ("1", True), ("on", True),
    ("false", False), ("FALSE", False), ("0", False), ("off", False),
])
def test_parse_strict_bool_accepts_documented_values(raw, expected):
    assert parse_strict_bool("TEST_FLAG", raw) is expected


@pytest.mark.parametrize("raw", ["", "yes please", "2", "none", "https"])
def test_parse_strict_bool_rejects_invalid_values(raw):
    with pytest.raises(RuntimeConfigError, match="true или false"):
        parse_strict_bool("TEST_FLAG", raw)


def test_local_default_is_false_for_http_tests():
    assert auth_cookie_secure({"CRM_DEPLOYMENT": "development"}) is False


@pytest.mark.parametrize("environ", [
    {"CRM_DEPLOYMENT": "production"},
    {"CRM_DEPLOYMENT": "production", "CRM_COOKIE_SECURE": ""},
    {"CRM_DEPLOYMENT": "production", "CRM_COOKIE_SECURE": "maybe"},
    {"CRM_DEPLOYMENT": "production", "CRM_COOKIE_SECURE": "false"},
])
def test_production_fails_closed_for_missing_invalid_or_insecure_policy(environ):
    with pytest.raises(RuntimeConfigError, match="CRM_COOKIE_SECURE"):
        validate_startup_config(environ)


def test_production_accepts_explicit_secure_policy():
    assert validate_startup_config({
        "CRM_DEPLOYMENT": "production",
        "CRM_COOKIE_SECURE": "true",
    }) is True


def test_main_startup_preflight_reports_invalid_production_policy(tmp_path):
    env = os.environ.copy()
    env.update({
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "production",
        "CRM_COOKIE_SECURE": "false",
    })

    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert "CRM_COOKIE_SECURE=false запрещён в production" in result.stderr
