"""Критичные сценарии (аудит 06.09, Н2): логин и кука, роли, пересчёт
остатка при правке суммы сделки (фикс Н1), идемпотентность trigger_from_card,
лимиты (С3), закрытая карта API (С6), заголовки безопасности, обобщённые
ошибки почтового синка (С7)."""
import pytest
from fastapi.testclient import TestClient

from conftest import USERS, login
from main import app
from database import SessionLocal
import models


def _card_transactions(card_id):
    db = SessionLocal()
    try:
        return db.query(models.Transaction).filter(models.Transaction.card_id == card_id).all()
    finally:
        db.close()


# --- Логин -------------------------------------------------------------

def test_login_sets_httponly_cookie():
    client = TestClient(app)
    response = client.post("/auth/login", data={"username": "test_admin", "password": USERS["test_admin"][0]})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "crm_token=" in set_cookie
    assert "httponly" in set_cookie, "кука должна быть httpOnly"
    assert "samesite=lax" in set_cookie


def test_login_wrong_password_rejected():
    client = TestClient(app)
    response = client.post("/auth/login", data={"username": "test_admin", "password": "wrong-password"})
    assert response.status_code == 401


def test_api_requires_authentication():
    client = TestClient(app)
    response = client.get("/kanban/cards")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")


# --- Роли --------------------------------------------------------------

def test_role_guards_on_system_routers(manager_client, admin_client):
    # Вебхуки — системная интеграция, только админ (FIX 2026-08-30)
    assert manager_client.get("/webhooks/").status_code == 403
    # Бизнесовые данные менеджеру доступны
    assert manager_client.get("/tags").status_code == 200
    assert admin_client.get("/webhooks/").status_code == 200


# --- Сделка -> накладная -> правка суммы (фикс Н1) ---------------------

def _create_card(client, title, amount):
    response = client.post("/kanban/cards", json={
        "title": title,
        "status": "Новый запрос",
        "total_amount": amount,
        "store_location": "Тестовый магазин",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_trigger_from_card_is_idempotent(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест идемпотентности", 500.0)
    first = superadmin_client.post(f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"})
    assert first.status_code == 200, first.text
    second = superadmin_client.post(f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"})
    assert second.status_code == 200
    txs = [t for t in _card_transactions(card_id) if not t.is_document]
    assert len(txs) == 1, "повторный вызов не должен плодить дубль"
    assert float(txs[0].amount) == 500.0


def test_issue_invoice_partial_keeps_remainder(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест частичной накладной", 1000.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    response = superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-1", "amount": 400.0, "store_location": "Тестовый магазин",
    })
    assert response.status_code == 200, response.text
    txs = _card_transactions(card_id)
    invoices = [t for t in txs if not t.is_document and (t.invoice_number or "").strip()]
    docs = [t for t in txs if t.is_document]
    remainders = [t for t in txs if not t.is_document and not (t.invoice_number or "").strip() and not t.is_warehouse_writeoff]
    assert len(invoices) == 1 and float(invoices[0].amount) == 400.0
    assert len(docs) == 1 and float(docs[0].amount) == 400.0, "копия накладной уходит в «Документы»"
    assert len(remainders) == 1 and float(remainders[0].amount) == 600.0

    # Фикс Н1: правка суммы сделки НЕ переписывает выписанные накладные.
    # PATCH /cards/{card_id} — card_details_router без префикса.
    patch = superadmin_client.patch(f"/cards/{card_id}", json={"total_amount": 1200.0})
    assert patch.status_code == 200, patch.text
    assert float(patch.json()["total_amount"]) == 1200.0
    invoices = [t for t in _card_transactions(card_id) if not t.is_document and (t.invoice_number or "").strip()]
    docs = [t for t in _card_transactions(card_id) if t.is_document]
    remainders = [t for t in _card_transactions(card_id)
                  if not t.is_document and not (t.invoice_number or "").strip() and not t.is_warehouse_writeoff]
    assert len(invoices) == 1 and float(invoices[0].amount) == 400.0, "накладная не должна перезаписываться"
    assert len(docs) == 1 and float(docs[0].amount) == 400.0, "документ-копия не должна перезаписываться"
    assert len(remainders) == 1 and abs(float(remainders[0].amount) - 800.0) < 0.01, "остаток = 1200 − 400"


def test_issue_invoice_rejects_amount_over_remainder(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест превышения остатка", 100.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    response = superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-2", "amount": 500.0, "store_location": "Тестовый магазин",
    })
    assert response.status_code == 400
    assert "больше остатка" in response.json()["detail"]


# --- Лимиты (С3) -------------------------------------------------------

def test_login_rate_limited():
    client = TestClient(app)
    for _ in range(10):
        assert client.post("/auth/login", data={"username": "test_admin", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", data={"username": "test_admin", "password": "nope"}).status_code == 429


def test_password_change_rate_limited(manager_client):
    for _ in range(5):
        assert manager_client.put("/auth/me/password", json={
            "old_password": "wrong-old", "new_password": "new-pass-1234",
        }).status_code == 400
    assert manager_client.put("/auth/me/password", json={
        "old_password": "wrong-old", "new_password": "new-pass-1234",
    }).status_code == 429


# --- Карта API (С6) и заголовки ----------------------------------------

def test_api_docs_disabled():
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    # С5: инлайн-скрипты выпилены из фронтенда — unsafe-inline в script-src
    # больше не нужен и ослабляет защиту при XSS.
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0], "script-src не должен содержать unsafe-inline"


# --- Почтовый синк (С7) ------------------------------------------------

def test_email_sync_error_is_generic(admin_client, monkeypatch):
    import os
    import routers.email_parser_router as epr
    # save_settings требует путь внутри CRM_DATA_DIR (защита от записи наружу)
    settings_path = os.path.join(os.environ["CRM_DATA_DIR"], "email_settings_test.json")
    monkeypatch.setattr(epr, "_get_settings_path", lambda tenant_id=None: settings_path)
    saved = admin_client.post("/email-parser/settings", json={
        "email": "box@example.com", "password": "secret", "imap_server": "127.0.0.1",
    })
    assert saved.status_code == 200, saved.text
    response = admin_client.post("/email-parser/sync")
    assert response.status_code == 500
    assert response.json()["detail"] == "Ошибка подключения к почтовому серверу"
    assert "Errno" not in response.text and "refused" not in response.text.lower()
