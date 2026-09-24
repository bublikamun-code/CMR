"""Проверки серверного отзыва JWT-токенов после logout."""
from datetime import datetime, timedelta, timezone
import hashlib

import jwt

import auth
import models


def _token_for(user, **extra):
    data = {
        "sub": user.username,
        "tenant_id": None,
        "pv": user.hashed_password[:8],
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
        {"sub": user.username, "tenant_id": None, "pv": user.hashed_password[:8]},
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
        {"sub": user.username, "tenant_id": None, "pv": user.hashed_password[:8]},
        expires_minutes=0.5,
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/auth/logout", headers=headers).status_code == 200
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "crm_token=" not in response.headers.get("set-cookie", "")


def test_legacy_token_without_pv_uses_sha256_key_and_is_revoked(client, db, make_user):
    user, _ = make_user()
    token = jwt.encode(
        {
            "sub": user.username,
            "tenant_id": None,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401

    expected_key = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert db.query(models.RevokedAuthToken).filter(
        models.RevokedAuthToken.token_key == expected_key
    ).count() == 1
