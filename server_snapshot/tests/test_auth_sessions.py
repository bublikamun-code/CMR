"""Проверки серверного отзыва JWT-токенов после logout."""
from datetime import datetime, timedelta, timezone
import hashlib

import jwt
import pytest

import auth
import models


def _token_for(user, **extra):
    data = {
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "pv": auth.password_version(user.hashed_password),
    }
    data.update(extra)
    return auth.create_access_token(data)


def test_bearer_is_revoked_by_logout(client, make_user):
    user, headers = make_user()

    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_cookie_only_is_revoked_by_logout(client, make_user):
    user, _ = make_user()
    token = _token_for(user)
    client.cookies.set("crm_token", token)

    assert client.get("/auth/me", cookies={"crm_token": token}).status_code == 200
    assert client.post("/auth/logout", cookies={"crm_token": token}).status_code == 200
    assert client.get("/auth/me", cookies={"crm_token": token}).status_code == 401


def test_logout_revokes_bearer_and_different_cookie(client, make_user):
    user, _ = make_user()
    bearer = _token_for(user)
    cookie = _token_for(user)
    headers = {"Authorization": f"Bearer {bearer}"}
    client.cookies.set("crm_token", cookie)

    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/auth/me", cookies={"crm_token": cookie}).status_code == 401


def test_logout_only_revokes_the_presented_token(client, make_user):
    user, _ = make_user()
    revoked_token = _token_for(user)
    other_token = _token_for(user)
    headers = {"Authorization": f"Bearer {revoked_token}"}

    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {other_token}"}
    ).status_code == 200


def test_logout_is_idempotent_for_missing_malformed_and_expired_tokens(client, make_user):
    user, _ = make_user()
    expired = auth.create_access_token(
        {
            "sub": user.username,
            "tenant_id": user.tenant_id,
            "pv": auth.password_version(user.hashed_password),
        },
        expires_minutes=-1,
    )

    for _ in range(2):
        assert client.post("/auth/logout").status_code == 200
        assert client.post(
            "/auth/logout", headers={"Authorization": "Bearer not-a-jwt"}
        ).status_code == 200
        assert client.post(
            "/auth/logout", cookies={"crm_token": "not-a-jwt"}
        ).status_code == 200
        assert client.post(
            "/auth/logout", headers={"Authorization": f"Bearer {expired}"}
        ).status_code == 200


def test_revoked_token_is_rejected_before_sliding_cookie_renewal(client, make_user):
    user, _ = make_user()
    token = auth.create_access_token(
        {
            "sub": user.username,
            "tenant_id": user.tenant_id,
            "pv": auth.password_version(user.hashed_password),
        },
        expires_minutes=0.5,
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/auth/logout", headers=headers).status_code == 200
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "crm_token=" not in response.headers.get("set-cookie", "")


def test_missing_pv_rejected_via_cookie(client, make_token):
    user, headers = make_token(
        claims=lambda u: {"sub": u.username, "tenant_id": u.tenant_id}
    )
    token = headers["Authorization"].removeprefix("Bearer ")
    assert client.get(
        "/auth/me", cookies={"crm_token": token}
    ).status_code == 401


@pytest.mark.parametrize("pv", ["", "pv-☃", 123, True, [], {}], ids=[
    "empty", "unicode", "number", "bool", "list", "object",
])
def test_invalid_pv_rejected_via_bearer_and_cookie(client, make_token, pv):
    user, headers = make_token(
        claims=lambda u: {"sub": u.username, "tenant_id": u.tenant_id, "pv": pv}
    )
    assert client.get("/auth/me", headers=headers).status_code == 401

    token = headers["Authorization"].removeprefix("Bearer ")
    assert client.get(
        "/auth/me", cookies={"crm_token": token}
    ).status_code == 401


def test_shared_legacy_prefix_does_not_keep_old_password_version(
        client, db, make_user):
    """Короткий префикс bcrypt не является версией пароля."""
    user, _ = make_user(username="prefix_collision")
    old_hash = "$2b$12$sharedPrefixOldHashValue"
    new_hash = "$2b$12$sharedPrefixNewHashValue"
    assert old_hash[:8] == new_hash[:8]
    assert auth.password_version(old_hash) != auth.password_version(new_hash)

    old_token = auth.create_access_token({
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "pv": auth.password_version(old_hash),
    })
    user.hashed_password = new_hash
    db.commit()

    response = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {old_token}"}
    )
    assert response.status_code == 401


def test_password_change_rejects_old_bearer_and_issues_working_login(
        client, make_user):
    user, old_headers = make_user(username="password_rotation")
    old_password = "Passw0rd!23"
    new_password = "NewPassw0rd!45"

    response = client.put(
        "/auth/me/password",
        headers=old_headers,
        json={"old_password": old_password, "new_password": new_password},
    )
    assert response.status_code == 200, response.text
    assert client.get("/auth/me", headers=old_headers).status_code == 401

    login = client.post(
        "/auth/login",
        data={"username": user.username, "password": new_password},
    )
    assert login.status_code == 200, login.text
    new_token = login.json()["access_token"]
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {new_token}"}
    ).status_code == 200


def test_legacy_token_without_pv_is_rejected_but_logout_revokes_it(
        client, db, make_user):
    user, _ = make_user()
    token = jwt.encode(
        {
            "sub": user.username,
            "tenant_id": user.tenant_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401

    expected_key = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert db.query(models.RevokedAuthToken).filter(
        models.RevokedAuthToken.token_key == expected_key
    ).count() == 1
