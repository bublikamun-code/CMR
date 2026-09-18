"""Критичные сценарии (аудит 06.09, Н2): логин и кука, роли, пересчёт
остатка при правке суммы сделки (фикс Н1), идемпотентность trigger_from_card,
лимиты (С3), закрытая карта API (С6), заголовки безопасности, обобщённые
ошибки почтового синка (С7)."""
import glob
import os

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


def test_registry_reports_partial_invoiced_amount(superadmin_client):
    # Фидбек 18.09 («Рацио Домус»): у строки реестра на всю сумму счёта
    # стояла галочка «Выписка», хотя выписана лишь часть. Группированный
    # реестр теперь отдаёт invoiced_amount — сколько счёта покрыто
    # накладными/складскими списаниями.
    card_id = _create_card(superadmin_client, "Тест частичной выписки в реестре", 1000.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-част", "amount": 400.0, "store_location": "Тестовый магазин",
    }).status_code == 200

    rows = superadmin_client.get("/payments/transactions").json()
    row = next(r for r in rows if r.get("card_id") == card_id)
    assert float(row["amount"]) == 1000.0, "сумма строки — сумма счёта"
    assert float(row["invoiced_amount"]) == 400.0
    assert row["is_invoice_issued"] is True, "факт накладной по-прежнему отражается галочкой"

    # докрываем остаток второй накладной — счёт покрыт целиком
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-остат", "amount": 600.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    rows = superadmin_client.get("/payments/transactions").json()
    row = next(r for r in rows if r.get("card_id") == card_id)
    assert abs(float(row["invoiced_amount"]) - 1000.0) < 0.01


# --- Правка даты оплаты в реестре (фидбек 08.09) -------------------------

def test_transaction_date_edit(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест правки даты оплаты", 250.0)
    tx = superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).json()
    assert tx["date"], "у записи должна быть дата создания"
    old_time = tx["date"][10:]  # 'THH:MM:SS' либо хвост после даты

    response = superadmin_client.patch(
        f"/payments/transactions/{tx['id']}", json={"date": "2026-08-15"})
    assert response.status_code == 200, response.text
    new_date = response.json()["date"]
    assert new_date.startswith("2026-08-15"), "день оплаты заменён"
    assert new_date[10:] == old_time, "время исходной записи сохранено"

    # невалидный формат отвергается, запись не портится
    bad = superadmin_client.patch(
        f"/payments/transactions/{tx['id']}", json={"date": "15.08.2026"})
    assert bad.status_code == 422
    still = superadmin_client.get("/payments/transactions").json()
    kept = next(t for t in still if t["id"] == tx["id"])
    assert kept["date"].startswith("2026-08-15")


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


# --- Остаток считается от суммы сделки, а не от разошедшейся записи
# (фидбек 09.09: «остаток для выписки накладной некорректный», в записях
# двух сделок остались старые опечатки ×100) ------------------------------

def _drift_remainder(card_id, amount):
    """Имитирует запись-остаток, разошедшуюся с суммой сделки (сумму сделки
    правили до фикса Н1, либо при создании была опечатка)."""
    db = SessionLocal()
    try:
        row = db.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False,
        ).first()
        row.amount = amount
        db.commit()
    finally:
        db.close()


def _remainders(card_id):
    return [t for t in _card_transactions(card_id)
            if not t.is_document and not (t.invoice_number or "").strip()
            and not t.is_warehouse_writeoff]


def test_invoice_rest_ignores_drifted_remainder_row(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест битого остатка", 500.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    _drift_remainder(card_id, 50000.0)

    info = superadmin_client.get(f"/payments/cards/{card_id}/invoices").json()
    assert info["rest"] == 500.0, "в форме выписки остаток = сумме сделки, а не битой записи"

    response = superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-3", "amount": 500.0, "store_location": "Тестовый магазин",
    })
    assert response.status_code == 200, response.text
    assert response.json()["rest"] == 0.0
    assert _remainders(card_id) == [], "исчерпанная запись-остаток удалена"


def test_issue_invoice_partial_rewrites_drifted_remainder(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест правки битого остатка", 1000.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    _drift_remainder(card_id, 9999.0)

    response = superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-4", "amount": 400.0, "store_location": "Тестовый магазин",
    })
    assert response.status_code == 200, response.text
    remainders = _remainders(card_id)
    assert len(remainders) == 1 and float(remainders[0].amount) == 600.0, \
        "запись-остаток перезаписана корректным значением (1000 − 400)"


def test_issue_invoice_rejects_over_rest_despite_drifted_row(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест лимита по сделке", 100.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    _drift_remainder(card_id, 9999.0)

    response = superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-5", "amount": 500.0, "store_location": "Тестовый магазин",
    })
    assert response.status_code == 400, "разбитая запись не должна позволять выписать больше сделки"
    assert "больше остатка" in response.json()["detail"]


def test_repair_writeoffs_reports_and_heals(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест починки остатка", 800.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    _drift_remainder(card_id, 12345.0)

    report = superadmin_client.post("/payments/repair-writeoffs?dry_run=true").json()
    assert report["dry_run"] is True
    assert any(f["card_id"] == card_id and f["will_be"] == 800.0 for f in report["remainder_fixes"]), \
        "dry-run показывает расходление, ничего не меняя"
    assert float(_remainders(card_id)[0].amount) == 12345.0

    applied = superadmin_client.post("/payments/repair-writeoffs?dry_run=false").json()
    assert any(f["card_id"] == card_id for f in applied["remainder_fixes"])
    assert float(_remainders(card_id)[0].amount) == 800.0, "остаток приведён к сумме сделки"


# --- Группы списания без участников не отдаются (группа-призрак с пыла) --

def test_list_groups_skips_empty_groups(superadmin_client):
    c1 = _create_card(superadmin_client, "Тест группы 1", 300.0)
    c2 = _create_card(superadmin_client, "Тест группы 2", 200.0)
    for cid in (c1, c2):
        assert superadmin_client.patch(
            f"/kanban/cards/{cid}/status", json={"status": "Сборка"}
        ).status_code == 200
    created = superadmin_client.post("/writeoffs/groups/", json={"card_ids": [c1, c2]})
    assert created.status_code == 200, created.text
    group_id = created.json()["id"]

    assert len(superadmin_client.get("/writeoffs/groups/").json()) >= 1

    # Участники выведены в обход API — именно так на бою появилась группа
    # без карточек, рисовавшая плитку с устаревшей суммой.
    db = SessionLocal()
    try:
        for cid in (c1, c2):
            db.query(models.Card).filter(models.Card.id == cid).update({"writeoff_group_id": None})
        db.commit()
    finally:
        db.close()

    ids = [g["id"] for g in superadmin_client.get("/writeoffs/groups/").json()]
    assert group_id not in ids, "группа без участников не попадает в выдачу"


# --- Сделка в «Сборке» всегда в реестре оплат (фидбек 09.09, кейс
# «ТрансЛИДИЯсервис»: карточка в «Сборке», в реестре её нет) --------------

def test_moving_to_assembly_creates_registry_entry(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест реестра при сборке", 1409.81)
    # перетаскивание/кнопки переноса меняют статус этим же эндпоинтом
    response = superadmin_client.patch(
        f"/kanban/cards/{card_id}/status", json={"status": "Сборка"})
    assert response.status_code == 200, response.text

    rows = [t for t in superadmin_client.get("/payments/transactions").json()
            if t["card_id"] == card_id and not t["is_document"]]
    assert len(rows) == 1, "сделка в «Сборке» появилась в реестре оплат"
    assert abs(float(rows[0]["amount"]) - 1409.81) < 0.01

    # повторная смена статуса не плодит дубли
    superadmin_client.patch(f"/kanban/cards/{card_id}/status", json={"status": "Сборка"})
    rows = [t for t in superadmin_client.get("/payments/transactions").json()
            if t["card_id"] == card_id and not t["is_document"]]
    assert len(rows) == 1, "запись-остаток одна"


def test_remove_from_writeoff_restores_registry_entry(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест возврата в сборку", 700.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.delete(f"/payments/cards/{card_id}/writeoff").status_code == 200

    card = superadmin_client.get(f"/kanban/cards/{card_id}").json()
    assert card["status"] == "Сборка"
    rows = [t for t in superadmin_client.get("/payments/transactions").json()
            if t["card_id"] == card_id and not t["is_document"]]
    assert len(rows) == 1 and abs(float(rows[0]["amount"]) - 700.0) < 0.01, \
        "после «Убрать из списания» запись-остаток восстановлена"


def test_moving_left_of_assembly_keeps_new_status(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест переноса влево", 250.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    tx_id = superadmin_client.get("/payments/transactions?grouped=false").json()[0]["id"]

    # фронт при переносе влево сначала меняет статус, потом удаляет запись —
    # удаление не должно откатывать статус обратно в «Сборку»
    assert superadmin_client.patch(
        f"/kanban/cards/{card_id}/status", json={"status": "Ждет оплаты"}).status_code == 200
    assert superadmin_client.delete(f"/payments/transactions/{tx_id}").status_code == 200

    card = superadmin_client.get(f"/kanban/cards/{card_id}").json()
    assert card["status"] == "Ждет оплаты", "перенос влево не отскакивает в «Сборку»"


def test_repair_reports_assembly_cards_without_registry(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест починки реестра", 333.0)
    superadmin_client.patch(f"/kanban/cards/{card_id}/status", json={"status": "Сборка"})
    # убираем запись в обход API — имитируем карточку, «застрявшую» до фикса
    db = SessionLocal()
    try:
        db.query(models.Transaction).filter(models.Transaction.card_id == card_id).delete()
        db.commit()
    finally:
        db.close()

    report = superadmin_client.post("/payments/repair-writeoffs?dry_run=true").json()
    assert any(m["card_id"] == card_id and m["amount"] == 333.0 for m in report["missing_registry"])

    applied = superadmin_client.post("/payments/repair-writeoffs?dry_run=false").json()
    assert any(m["card_id"] == card_id for m in applied["missing_registry"])
    rows = [t for t in superadmin_client.get("/payments/transactions").json()
            if t["card_id"] == card_id and not t["is_document"]]
    assert len(rows) == 1 and abs(float(rows[0]["amount"]) - 333.0) < 0.01

    # страховка отката: перед применением сохранена копия базы
    import glob
    import database
    assert glob.glob(os.path.join(database.DATA_DIR, "backups", "before_repair_*.db")), \
        "копия базы перед применением не создана"


def test_repair_removes_zero_remainder_rows(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест нулевого остатка", 800.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-Д", "amount": 800.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    # хвост после старых багов: нулевая запись-остаток
    db = SessionLocal()
    try:
        db.add(models.Transaction(company_name="Тест нулевого остатка", amount=0.0,
                                  card_id=card_id, store_location="Тестовый магазин"))
        db.commit()
    finally:
        db.close()

    report = superadmin_client.post("/payments/repair-writeoffs?dry_run=true").json()
    assert any(z["card_id"] == card_id for z in report["zero_rows_removed"]), \
        "нулевой остаток замечен в отчёте"

    superadmin_client.post("/payments/repair-writeoffs?dry_run=false")
    leftovers = [t for t in _card_transactions(card_id)
                 if not t.is_document and float(t.amount or 0) <= 0.005]
    assert leftovers == [], "нулевая запись-остаток удалена"


def test_add_invoice_keeps_remainder_and_status_consistent(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест дописывания по API", 1000.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    # переводим в зону списания — только там статус следует за остатком
    assert superadmin_client.patch(
        f"/kanban/cards/{card_id}/status", json={"status": "На списание"}
    ).status_code == 200
    # дописали 400 по API — остаток должен пересчитаться до 600
    added = superadmin_client.post(f"/payments/cards/{card_id}/invoices", json={
        "amount": 400.0, "invoice_number": "ТН-API-1", "store_location": "Тестовый магазин",
    })
    assert added.status_code == 200, added.text
    info = superadmin_client.get(f"/payments/cards/{card_id}/invoices").json()
    assert info["rest"] == 600.0, "запись-остаток пересчитана после дописывания"
    assert superadmin_client.get(f"/kanban/cards/{card_id}").json()["status"] == "На списание"

    # дописали остальное — остаток закрыт, сделка закрыта
    added2 = superadmin_client.post(f"/payments/cards/{card_id}/invoices", json={
        "amount": 600.0, "invoice_number": "ТН-API-2", "store_location": "Тестовый магазин",
    })
    assert added2.status_code == 200, added2.text
    info = superadmin_client.get(f"/payments/cards/{card_id}/invoices").json()
    assert info["rest"] == 0.0
    assert superadmin_client.get(f"/kanban/cards/{card_id}").json()["status"] == "Закрыто"

    # без суммы дописывание предлагает непокрытый остаток, а не «всё покрыто»
    card2 = _create_card(superadmin_client, "Тест дописывания без суммы", 300.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card2}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    partial = superadmin_client.post(f"/payments/cards/{card2}/invoices", json={
        "invoice_number": "ТН-API-3", "amount": 100.0, "store_location": "Тестовый магазин",
    })
    assert partial.status_code == 200
    auto = superadmin_client.post(f"/payments/cards/{card2}/invoices", json={
        "invoice_number": "ТН-API-4", "store_location": "Тестовый магазин",
    })
    assert auto.status_code == 200 and abs(float(auto.json()["amount"]) - 200.0) < 0.01, \
        "без суммы дописывается ровно непокрытый остаток"


# --- Выписано полностью = сделка закрыта, складские флажки статус не
# держат (фидбек 09.09: «Свидеал выписан полностью, но висит в "На
# списание"») ---------------------------------------------------------------

def _unset_warehouse_flags(card_id):
    db = SessionLocal()
    try:
        db.query(models.Transaction).filter(models.Transaction.card_id == card_id).update(
            {"is_warehouse_writeoff": False}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_fully_invoiced_deal_stays_closed_without_warehouse_flags(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест полного покрытия", 600.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-А", "amount": 250.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-Б", "amount": 350.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    assert superadmin_client.get(f"/kanban/cards/{card_id}").json()["status"] == "Закрыто"

    # складские флажки не проставлены — статус «Закрыто» это не меняет
    _unset_warehouse_flags(card_id)
    sync = superadmin_client.post(f"/payments/cards/{card_id}/sync-writeoff-status").json()
    assert sync["status"] == "Закрыто", "полное покрытие выпиской = сделка закрыта"
    assert sync["changed"] is False

    report = superadmin_client.post("/payments/repair-writeoffs?dry_run=true").json()
    assert not any(f["card_id"] == card_id for f in report["status_fixes"]), \
        "починка не должна возвращать выписанные сделки в «На списание»"


def test_repair_closes_issued_but_unwritten_deal(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест закрытия починкой", 500.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-В", "amount": 500.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    # имитируем состояние до фикса: статус «На списание», флажков нет
    db = SessionLocal()
    try:
        db.query(models.Card).filter(models.Card.id == card_id).update({"status": "На списание"})
        db.query(models.Transaction).filter(models.Transaction.card_id == card_id).update(
            {"is_warehouse_writeoff": False}, synchronize_session=False)
        db.commit()
    finally:
        db.close()

    report = superadmin_client.post("/payments/repair-writeoffs?dry_run=true").json()
    fix = next(f for f in report["status_fixes"] if f["card_id"] == card_id)
    assert fix["from"] == "На списание" and fix["to"] == "Закрыто"

    superadmin_client.post("/payments/repair-writeoffs?dry_run=false")
    assert superadmin_client.get(f"/kanban/cards/{card_id}").json()["status"] == "Закрыто"


def test_total_increase_reopens_closed_deal(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест возврата при правке суммы", 500.0)
    assert superadmin_client.post(
        f"/payments/trigger_from_card/{card_id}", json={"store_location": "Тестовый магазин"}
    ).status_code == 200
    assert superadmin_client.post(f"/payments/cards/{card_id}/issue-invoice", json={
        "invoice_number": "ТН-Г", "amount": 500.0, "store_location": "Тестовый магазин",
    }).status_code == 200
    assert superadmin_client.get(f"/kanban/cards/{card_id}").json()["status"] == "Закрыто"

    # сумму сделки увеличили — появился остаток к выписке, сделка должна
    # вернуться в «На списание»
    patch = superadmin_client.patch(f"/cards/{card_id}", json={"total_amount": 700.0})
    assert patch.status_code == 200, patch.text
    card = superadmin_client.get(f"/kanban/cards/{card_id}").json()
    assert card["status"] == "На списание", "правка суммы с остатком возвращает сделку на доску"
    info = superadmin_client.get(f"/payments/cards/{card_id}/invoices").json()
    assert info["rest"] == 200.0


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
