"""Пункт 17 плана v2 — V11: роли на деструктивных бизнес-операциях.

До этого гейта запись реестра оплат, поставщика, тег и входящую накладную
удалял каждый аутентифицированный пользователь, включая роли warehouse и
documents (дефект V11, V2-PLAN-RECON §6: «любой аутентифицированный удаляет
платежи»). Здесь фиксируется принятая матрица и проверяется, что гейты НЕ
задели операционную работу менеджера.

МАТРИЦА РОЛЕЙ (2026-09-23, пункт 17 плана V2-WORKPLAN-2026-09-22)

| Операция                                            | Роут                                 | manager | warehouse | documents | admin | superadmin |
| --------------------------------------------------- | ------------------------------------ | ------- | --------- | --------- | ----- | ---------- |
| Отменить накладную/оплату (удалить запись реестра)  | DELETE /payments/transactions/{id}   | 403     | 403       | 403       | 200   | 200        |
| Отменить списание сделки целиком                    | DELETE /payments/cards/{id}/writeoff | 403     | 403       | 403       | 200   | 200        |
| Удалить поставщика (справочник)                     | DELETE /suppliers/{id}               | 403     | 403       | 403       | 200   | 200        |
| Удалить тег (справочник, снимается со всех сделок)  | DELETE /tags/{id}                    | 403     | 403       | 403       | 200   | 200        |
| Удалить входящую накладную (стирает фото с диска)   | DELETE /nakladnye/{id}               | 403     | 403       | 403       | 200   | 200        |
| Удалить клиента (справочник)                        | DELETE /clients/{id}                 | 403     | 403       | 403       | 200   | 200        |
| Удалить приход клиента                              | DELETE /clients/payments/{id}        | 403     | 403       | 403       | 200   | 200        |
| Очистить корзину (сделка навсегда)                  | DELETE /kanban/cards/{id}/permanent  | 403     | 403       | 403       | 200   | 200        |
| Массовая починка списаний                           | POST /payments/repair-writeoffs      | 403     | 403       | 403       | 200   | 200        |
| Пользователи, справочники, вебхуки, воркфлоу,       | /auth/users, /dictionaries/*,        | 403     | 403       | 403       | 200   | 200        |
| кастомные объекты (были закрыты до пункта 17)       | /webhooks, /workflows, /custom       |         |           |           |       |            |

ОПЕРАЦИОННЫЕ действия остаются доступными любому аутентифицированному
(проверены в конце файла): перенос сделки в реестр, выписка накладной,
галочки реестра (PATCH), копия в «Документы», мягкое удаление сделки в корзину
и восстановление, приём и правка входящих накладных, теги на сделке, группы
списания и само списание.

Сознательно НЕ закрыто: менеджер по-прежнему правит суммы чужой сделки
(проверки владельца нет) — см. test_access_matrix.py. Это отдельное решение
владельца, а не часть V11: карточки в CRM правят и склад, и документооборот,
поэтому «только свои» пришлось бы вводить сразу на всех экранах.

Отдельно про DELETE /payments/cards/{id}/writeoff: ручку зовёт только legacy
(«Списание» — site/js/writeoffs.js:401, site/js/payments.js:297), v2 её не
использует. Если владелец решит вернуть складу право отмены списания, гейт
снимается с одного роута, правки матрицы и тестов — в этом файле.
"""
import pytest

import models

pytestmark = pytest.mark.access

NON_ADMIN_ROLES = ["manager", "warehouse", "documents"]
ADMIN_ROLES = ["admin", "superadmin"]

STORE = "Богдановича"


def _reload(db):
    db.expire_all()


def _headers(make_user, role):
    _, h = make_user(role)
    return h


# ---------------------------------------------------------------------------
# Заготовки данных
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_row(db, make_card):
    """Запись реестра оплат (не документ) на сделке в «Сборке».

    Именно такие строки удаляет DELETE /payments/transactions/{id} и
    DELETE /payments/cards/{id}/writeoff.
    """
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка",
                     store_location=STORE)
    tx = models.Transaction(card_id=card.id, company_name=card.title,
                            amount=1000.0, store_location=STORE,
                            is_document=False)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@pytest.fixture
def supplier(db):
    s = models.Supplier(name="Поставщик")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def tag(db):
    t = models.Tag(name="важно")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture
def nakladnaya(db):
    n = models.Nakladnaya(doc_series="АБ", doc_number="77", supplier_name="П")
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@pytest.fixture
def client_obj(db):
    c = models.Client(name="Клиент")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ---------------------------------------------------------------------------
# 1. Запись реестра оплат: отмена накладной/оплаты
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_transaction_delete_denied_for_non_admin(client, make_user, db, registry_row, role):
    """V11: ни одна не-админская роль больше не удаляет платежи из реестра."""
    h = _headers(make_user, role)
    r = client.delete(f"/payments/transactions/{registry_row.id}", headers=h)
    assert r.status_code == 403, f"роль {role}: {r.status_code} {r.text}"
    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.id == registry_row.id).first() is not None, \
        "после 403 запись реестра обязана остаться"


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_transaction_delete_allowed_for_admin_roles(client, make_user, db, make_card, role):
    """Сделка без суммы: запись-остаток после удаления не пересоздаётся
    (ensure_registry_remainder пропускает total_amount <= 0), поэтому в реестре
    пусто. На сделке с суммой остаток пересоздаётся, а SQLite переиспользует
    rowid удалённой строки — проверка «по id» там врала бы."""
    card = make_card(title="Сделка", total_amount=0.0, status="Сборка",
                     store_location=STORE)
    tx = models.Transaction(card_id=card.id, company_name=card.title, amount=0.0,
                            store_location=STORE, is_document=False)
    db.add(tx)
    db.commit()

    h = _headers(make_user, role)
    r = client.delete(f"/payments/transactions/{tx.id}", headers=h)
    assert r.status_code == 200, f"роль {role}: {r.text}"
    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id).count() == 0


def test_transaction_delete_requires_authentication(client, registry_row):
    """Без токена — 401/403, но не 200 и не 500."""
    assert client.delete(f"/payments/transactions/{registry_row.id}").status_code in (401, 403)


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_group_invoice_document_delete_denied(client, make_user, db, role):
    """Отмена групповой ТН идёт тем же роутом: документ группы — запись реестра
    с is_document=True и writeoff_group_id."""
    group = models.WriteoffGroup(name="Группа", store_location=STORE, total_amount=500.0)
    db.add(group)
    db.commit()
    doc = models.Transaction(company_name=group.name, amount=500.0,
                             store_location=STORE, invoice_number="ТТН9001",
                             is_document=True, writeoff_group_id=group.id)
    db.add(doc)
    db.commit()
    doc_id = doc.id

    h = _headers(make_user, role)
    assert client.delete(f"/payments/transactions/{doc_id}", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.id == doc_id).first() is not None


# ---------------------------------------------------------------------------
# 2. Отмена списания сделки целиком
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_writeoff_cancel_denied_for_non_admin(client, make_user, db, registry_row, role):
    h = _headers(make_user, role)
    r = client.delete(f"/payments/cards/{registry_row.card_id}/writeoff", headers=h)
    assert r.status_code == 403, f"роль {role}: {r.status_code} {r.text}"
    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == registry_row.card_id).count() == 1, \
        "после 403 записи сделки обязаны остаться"


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_writeoff_cancel_allowed_for_admin_roles(client, make_user, db, registry_row, role):
    h = _headers(make_user, role)
    r = client.delete(f"/payments/cards/{registry_row.card_id}/writeoff", headers=h)
    assert r.status_code == 200, f"роль {role}: {r.text}"


# ---------------------------------------------------------------------------
# 3. Справочники: поставщики и теги
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_supplier_delete_denied_for_non_admin(client, make_user, db, supplier, role):
    h = _headers(make_user, role)
    assert client.delete(f"/suppliers/{supplier.id}", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Supplier).filter(
        models.Supplier.id == supplier.id).first() is not None


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_supplier_delete_allowed_for_admin_roles(client, make_user, supplier, role):
    h = _headers(make_user, role)
    assert client.delete(f"/suppliers/{supplier.id}", headers=h).status_code == 200


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_tag_delete_denied_for_non_admin(client, make_user, db, tag, role):
    h = _headers(make_user, role)
    assert client.delete(f"/tags/{tag.id}", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Tag).filter(models.Tag.id == tag.id).first() is not None


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_tag_delete_allowed_for_admin_roles(client, make_user, tag, role):
    h = _headers(make_user, role)
    assert client.delete(f"/tags/{tag.id}", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# 4. Входящие накладные
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_nakladnaya_delete_denied_for_non_admin(client, make_user, db, nakladnaya, role):
    h = _headers(make_user, role)
    assert client.delete(f"/nakladnye/{nakladnaya.id}", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nakladnaya.id).first() is not None


@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_nakladnaya_delete_allowed_for_admin_roles(client, make_user, nakladnaya, role):
    h = _headers(make_user, role)
    assert client.delete(f"/nakladnye/{nakladnaya.id}", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# 5. Уже закрытые роуты — регрессия на матрицу
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_client_delete_still_denied(client, make_user, client_obj, role):
    h = _headers(make_user, role)
    assert client.delete(f"/clients/{client_obj.id}", headers=h).status_code == 403


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_client_payment_delete_still_denied(client, make_user, db, client_obj, role):
    pay = models.ClientPayment(client_id=client_obj.id, amount=100.0)
    db.add(pay)
    db.commit()
    h = _headers(make_user, role)
    assert client.delete(f"/clients/payments/{pay.id}", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.ClientPayment).filter(
        models.ClientPayment.id == pay.id).first() is not None


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_permanent_card_delete_still_denied(client, make_user, db, make_card, role):
    card = make_card(title="В корзине", is_deleted=True)
    h = _headers(make_user, role)
    assert client.delete(f"/kanban/cards/{card.id}/permanent", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Card).filter(models.Card.id == card.id).first() is not None


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_repair_writeoffs_still_denied(client, make_user, role):
    h = _headers(make_user, role)
    r = client.post("/payments/repair-writeoffs?dry_run=true", headers=h)
    assert r.status_code == 403, f"роль {role}: {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# 6. Гейты не задели операционную работу менеджера
# ---------------------------------------------------------------------------

def test_manager_keeps_full_writeoff_cycle(client, manager, make_card):
    """Перенос в реестр → выписка ТН → галочка реестра → копия в «Документы»."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка",
                     store_location=STORE)

    assert client.post(f"/payments/trigger_from_card/{card.id}", headers=h,
                       json={"store_location": STORE}).status_code == 200
    r = client.post(f"/payments/cards/{card.id}/issue-invoice", headers=h,
                    json={"invoice_number": "ТТН5001", "amount": 400.0,
                          "invoice_date": "2026-09-01", "store_location": STORE})
    assert r.status_code == 200, r.text
    invoice_id = r.json()["invoice_id"]

    assert client.patch(f"/payments/transactions/{invoice_id}", headers=h,
                        json={"is_written_off": True}).status_code == 200
    assert client.post(
        f"/payments/transactions/{invoice_id}/duplicate_as_document",
        headers=h).status_code == 200


def test_manager_keeps_card_trash_and_restore(client, manager, make_card):
    _, h = manager
    card = make_card(title="Сделка")
    assert client.delete(f"/kanban/cards/{card.id}", headers=h).status_code == 200
    assert client.patch(f"/kanban/cards/{card.id}/restore", headers=h).status_code == 200


def test_manager_keeps_incoming_nakladnye_work(client, manager):
    """Приём и правка входящих накладных — операционное действие склада."""
    _, h = manager
    r = client.post("/nakladnye", headers=h,
                    json={"doc_series": "АБ", "doc_number": "78", "amount": 120.0})
    assert r.status_code == 200, r.text
    nak_id = r.json()["id"]
    assert client.patch(f"/nakladnye/{nak_id}", headers=h,
                        json={"is_verified": True}).status_code == 200


def test_manager_keeps_tagging_cards(client, manager, db, tag, make_card):
    """Удаление тега из справочника — админское, работа тегом на сделке — нет."""
    _, h = manager
    card = make_card(title="Сделка")
    assert client.post(f"/tags/cards/{card.id}/tags/{tag.id}", headers=h).status_code == 200
    assert client.delete(f"/tags/cards/{card.id}/tags/{tag.id}", headers=h).status_code == 200
    assert client.post("/tags", headers=h, json={"name": "срочно"}).status_code == 200


def test_manager_keeps_writeoff_groups(client, manager, make_card):
    _, h = manager
    a = make_card(title="A", total_amount=100.0, status="Сборка", store_location=STORE)
    b = make_card(title="B", total_amount=200.0, status="Сборка", store_location=STORE)
    r = client.post("/writeoffs/groups/", headers=h, json={"card_ids": [a.id, b.id]})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]
    assert client.delete(f"/writeoffs/groups/{group_id}/cards/{b.id}",
                         headers=h).status_code == 200


def test_warehouse_keeps_writeoff_execution(client, make_user, db, make_card):
    """Списание со склада — операционное действие роли warehouse."""
    _, h = make_user("warehouse")
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка",
                     store_location=STORE)
    assert client.post(f"/payments/trigger_from_card/{card.id}", headers=h,
                       json={"store_location": STORE}).status_code == 200
    assert client.post(f"/writeoffs/{card.id}/finish_assembly", headers=h).status_code == 200
    assert client.post(f"/writeoffs/{card.id}/execute", headers=h).status_code == 200
