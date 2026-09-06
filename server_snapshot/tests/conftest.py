"""Общее окружение API-тестов (аудит 06.09, пункт Н2).

CRM_DATA_DIR / CRM_UPLOADS_DIR выставляются ДО импорта main/database:
оба модуля вычисляют пути из окружения на этапе импорта, поэтому тесты
работают с временной базой и временной папкой загрузок и не трогают
боевые файлы рядом с кодом.
"""
import os
import tempfile

_TEST_ROOT = tempfile.mkdtemp(prefix="crm_tests_")
os.environ["CRM_DATA_DIR"] = _TEST_ROOT
os.environ["CRM_UPLOADS_DIR"] = os.path.join(_TEST_ROOT, "uploads")

import pytest
from fastapi.testclient import TestClient

from main import app
from database import SessionLocal
import models
from auth import get_password_hash
from limiter_config import limiter

USERS = {
    "test_superadmin": ("superadmin-pass-1", "superadmin"),
    "test_admin": ("admin-pass-1234", "admin"),
    "test_manager": ("manager-pass-12", "manager"),
}


@pytest.fixture(scope="session", autouse=True)
def seed_users():
    db = SessionLocal()
    try:
        for username, (password, role) in USERS.items():
            db.add(models.User(
                username=username,
                hashed_password=get_password_hash(password),
                role=role,
                tenant_id=None,
            ))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    # Лимитер в памяти и ключится по адресу клиента (у TestClient он один на
    # всех) — без сброса логины из предыдущих тестов исчерпали бы лимит
    # 10/мин у последующих.
    limiter.reset()
    yield
    limiter.reset()


def login(username: str) -> TestClient:
    password, _ = USERS[username]
    client = TestClient(app)
    response = client.post("/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def superadmin_client():
    return login("test_superadmin")


@pytest.fixture()
def admin_client():
    return login("test_admin")


@pytest.fixture()
def manager_client():
    return login("test_manager")
