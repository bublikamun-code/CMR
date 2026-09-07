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


# --- Правка даты выписки накладной из карточки (фидбек 07.09) -----------

def test_invoice_date_edit_syncs_document_copy(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест правки даты накладной", 300.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-9", "invoice_date": "2026-09-01",
        "amount": 300.0, "store_location": "Тестовый магазин",
    }).status_code == 200

    txs = _card_transactions(card_id)
    invoice = next(t for t in txs if not t.is_document)
    doc = next(t for t in txs if t.is_document)

    response = superadmin_client.patch(
        f"/payments/transactions/{invoice.id}", json={"invoice_date": "2026-09-07"})
    assert response.status_code == 200, response.text
    assert response.json()["invoice_date"] == "2026-09-07"
    doc_after = next(t for t in _card_transactions(card_id) if t.is_document)
    assert doc_after.invoice_date == "2026-09-07", "копия в «Документах» должна получить ту же дату"

    # правка с копии в «Документах» синхронизируется обратно на накладную
    response = superadmin_client.patch(
        f"/payments/transactions/{doc.id}", json={"invoice_date": "2026-08-30"})
    assert response.status_code == 200, response.text
    inv_after = next(t for t in _card_transactions(card_id) if not t.is_document)
    assert inv_after.invoice_date == "2026-08-30"

    # очистка даты тоже синхронизируется на пару
    assert superadmin_client.patch(
        f"/payments/transactions/{invoice.id}", json={"invoice_date": None}).status_code == 200
    pair = _card_transactions(card_id)
    assert next(t for t in pair if not t.is_document).invoice_date is None
    assert next(t for t in pair if t.is_document).invoice_date is None


# --- Н13 (регрессия унификации сессий): менеджер без tenant_id читает
# основную базу через resolve_tenant_db — бойлерплейт-ветки удалены.
def test_manager_reads_business_lists(manager_client):
    for ep in ("/kanban/cards", "/kanban/trash", "/clients", "/suppliers", "/tags", "/writeoffs/pending"):
        response = manager_client.get(ep)
        assert response.status_code == 200, f"{ep}: {response.status_code} {response.text[:200]}"


# --- Н19 (валидации) ----------------------------------------------------

def test_card_status_rejects_unknown_value(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест валидации статуса", 50.0)
    response = superadmin_client.patch(f"/kanban/cards/{card_id}/status", json={"status": "Черновик"})
    assert response.status_code == 422, "опечатка в статусе не должна уходить в БД"


def test_transaction_amount_must_be_positive(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест валидации суммы", 80.0)
    tx = superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).json()
    for bad in (-5, 0):
        response = superadmin_client.patch(f"/payments/transactions/{tx['id']}", json={"amount": bad})
        assert response.status_code == 422, f"amount={bad} не должен приниматься"


# --- Н9 (гонка остатка) -------------------------------------------------

def test_trigger_from_card_returns_existing_under_unique_index(superadmin_client):
    # После миграции 0004 второй остаток невозможен физически: IntegrityError
    # ловится и возвращается существующая запись (идемпотентность из теста
    # выше — теперь подкреплена индексом на уровне БД).
    card_id = _create_card(superadmin_client, "Тест unique-остатка", 300.0)
    first = superadmin_client.post(f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"})
    second = superadmin_client.post(f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"})
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    txs = [t for t in _card_transactions(card_id) if not t.is_document]
    assert len(txs) == 1


# --- Н8 (глобальный обработчик IntegrityError) --------------------------

def test_integrity_error_handler_returns_409():
    import asyncio
    from types import SimpleNamespace
    from main import integrity_error_handler
    from sqlalchemy.exc import IntegrityError

    request = SimpleNamespace(url=SimpleNamespace(path="/tags"))
    exc = IntegrityError("INSERT INTO tags ...", {}, Exception("UNIQUE constraint failed: tags.name"))
    response = asyncio.run(integrity_error_handler(request, exc))
    assert response.status_code == 409
    assert "конфликт" in response.body.decode().lower()


# --- Просрочки сделок (фидбек 06.09: «На списание» — сделка выписана) --

def test_sync_overdue_skips_cards_in_writeoff(admin_client):
    # Просроченная дата окончания: сделка «В работе» даёт уведомление,
    # сделка «На списание» — нет (она фактически выписана; вопросы только
    # по оплатам, они считаются по payment_due_date отдельно).
    import models
    from database import SessionLocal
    from datetime import date

    db = SessionLocal()
    try:
        owner = db.query(models.User).filter(models.User.username == "test_admin").first()
        past = date.today().replace(year=date.today().year - 1)
        c_done = models.Card(title="Просрочка-тест: На списании", status="На списание",
                             total_amount=100, due_date=past, owner_id=owner.id)
        c_open = models.Card(title="Просрочка-тест: В работе", status="В работе",
                             total_amount=100, due_date=past, owner_id=owner.id)
        db.add(c_done); db.add(c_open); db.commit()
        done_id, open_id = c_done.id, c_open.id
    finally:
        db.close()

    import auth
    r = admin_client.post("/notifications/sync-overdue", headers={"X-Cron-Token": auth.CRON_TOKEN})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        notes = db.query(models.Notification).filter(
            models.Notification.entity_type == "card",
            models.Notification.type == "card_overdue",
            models.Notification.entity_id.in_([done_id, open_id]),
        ).all()
        notified = {n.entity_id for n in notes}
        assert open_id in notified, "сделка «В работе» должна получить просрочку"
        assert done_id not in notified, "сделка «На списании» не должна получать просрочку по дате окончания"
        # прибираем, чтобы не влиять на другие тесты
        for n in notes:
            db.delete(n)
        db.commit()
    finally:
        db.close()


# --- Лимиты (С3) -------------------------------------------------------

def test_login_rate_limited():
    client = TestClient(app)
    for _ in range(10):
        assert client.post("/auth/login", data={"username": "test_admin", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", data={"username": "test_admin", "password": "nope"}).status_code == 429


# --- «Запомнить меня» + скользящее продление (фидбек 07.09) -------------

def test_login_remember_me_30_days():
    client = TestClient(app)
    r = client.post("/auth/login", data={
        "username": "test_admin", "password": USERS["test_admin"][0], "remember": "1",
    })
    assert r.status_code == 200
    assert "Max-Age=2592000" in r.headers.get("set-cookie", ""), "кука «Запомнить меня» должна жить 30 дней"
    import jwt as pyjwt
    from auth import SECRET_KEY, ALGORITHM
    payload = pyjwt.decode(client.cookies.get("crm_token"), SECRET_KEY, algorithms=[ALGORITHM])
    assert payload.get("rm") == 1
    assert 29 < (payload["exp"] - payload["iat"]) / 86400 <= 30.01, "срок токена — 30 дней"


def test_login_default_is_24h():
    client = TestClient(app)
    r = client.post("/auth/login", data={"username": "test_admin", "password": USERS["test_admin"][0]})
    set_cookie = r.headers.get("set-cookie", "")
    assert "Max-Age=86400" in set_cookie, "без «Запомнить меня» — сутки"
    assert "Max-Age=2592000" not in set_cookie


def test_sliding_renewal_extends_cookie():
    # Токен «в работе» с половиной срока: /auth/me должен продлить куку
    # на полный срок (24ч) — активный пользователь больше не вылетает.
    import jwt as pyjwt
    from auth import SECRET_KEY, ALGORITHM, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
    from database import SessionLocal as _S
    import models as _m
    db = _S()
    try:
        user = db.query(_m.User).filter(_m.User.username == "test_admin").first()
        pv = user.hashed_password[:8]
    finally:
        db.close()
    token = create_access_token(
        {"sub": "test_admin", "tenant_id": None, "pv": pv},
        expires_minutes=int(ACCESS_TOKEN_EXPIRE_MINUTES / 2),
    )
    client = TestClient(app)
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "Max-Age=86400" in r.headers.get("set-cookie", ""), "скользящее продление должно обновить куку"


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
