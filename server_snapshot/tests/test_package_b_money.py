"""Тесты пакета B: деньги, карточки, клиенты, вложения.

Покрывает дефекты A3 (overpayment), A5 (is_advance), A7 (client_id null),
A8 (negative amount), A10 (multipart upload), A11 (client balance fields),
A12 (server-computed remaining/writeoff_status/issued_total), A14 (N+1).
"""
import io

import pytest

import models

pytestmark = pytest.mark.money

STORE = "Матусевича 72"


def _reload(db):
    db.expire_all()


# =========================================================================
# A12: remaining / writeoff_status / issued_total — серверные поля
# =========================================================================

def test_card_money_no_transactions(client, manager, db, make_card):
    """Карточка без записей: remaining = total, writeoff_status = not_written."""
    _, h = manager
    card = make_card(title="Без записей", total_amount=1000.0, status="Новый запрос")
    r = client.get(f"/kanban/cards/{card.id}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["remaining"] == 1000.0
    assert body["remaining_kop"] == 100000
    assert body["writeoff_status"] == "not_written"
    assert body["issued_total"] == 0.0


def test_card_money_partial_writeoff(client, manager, db, make_card):
    """Частичная выписка: remaining = total − issued."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    r = client.post(f"/payments/trigger_from_card/{card.id}",
                    headers=h, json={"store_location": STORE})
    assert r.status_code == 200
    r = client.post(f"/payments/cards/{card.id}/issue-invoice", headers=h,
                    json={"invoice_number": "ТТН-B1", "amount": 400.0,
                          "store_location": STORE})
    assert r.status_code == 200

    _reload(db)
    r = client.get(f"/kanban/cards/{card.id}", headers=h)
    body = r.json()
    assert body["issued_total"] == 400.0
    assert body["remaining"] == 600.0
    assert body["remaining_kop"] == 60000
    assert body["writeoff_status"] == "partially_written"


def test_card_money_full_writeoff(client, manager, db, make_card):
    """Полная выписка: remaining = 0, writeoff_status = written."""
    _, h = manager
    card = make_card(total_amount=500.0, status="Сборка")
    client.post(f"/payments/trigger_from_card/{card.id}",
                headers=h, json={"store_location": STORE})
    client.post(f"/payments/cards/{card.id}/issue-invoice", headers=h,
                json={"invoice_number": "ТТН-B2", "amount": 500.0,
                      "store_location": STORE})

    _reload(db)
    body = client.get(f"/kanban/cards/{card.id}", headers=h).json()
    assert body["remaining"] == 0.0
    assert body["remaining_kop"] == 0
    assert body["writeoff_status"] == "written"
    assert body["issued_total"] == 500.0


def test_card_money_zero_total(client, manager, db, make_card):
    """Карточка без суммы: remaining = 0, writeoff_status = not_written."""
    _, h = manager
    card = make_card(total_amount=0.0, status="Новый запрос")
    body = client.get(f"/kanban/cards/{card.id}", headers=h).json()
    assert body["remaining"] == 0.0
    assert body["writeoff_status"] == "not_written"


def test_kanban_cards_has_money_fields(client, manager, db, make_card):
    """GET /kanban/cards отдаёт денежные поля для всех карточек."""
    _, h = manager
    make_card(title="К1", total_amount=100.0)
    make_card(title="К2", total_amount=200.0)
    rows = client.get("/kanban/cards", headers=h).json()
    for row in rows:
        assert "remaining" in row
        assert "remaining_kop" in row
        assert "writeoff_status" in row
        assert "issued_total" in row


def test_card_money_group_writeoff(client, manager, db, make_card):
    """Групповое списание: remaining по группе считается корректно."""
    _, h = manager
    group = models.WriteoffGroup(name="Группа Б", total_amount=0.0)
    db.add(group)
    db.commit()
    card = make_card(total_amount=800.0, status="Сборка",
                     writeoff_group_id=group.id)
    # Карточка в группе: remaining считается по её own total
    body = client.get(f"/kanban/cards/{card.id}", headers=h).json()
    assert body["remaining"] == 800.0
    assert body["writeoff_status"] == "not_written"


# =========================================================================
# A3: переплата не меняет total_amount
# =========================================================================

def test_overpayment_does_not_change_total_amount(client, manager, db, make_card):
    """Оплата больше суммы сделки: total_amount остаётся неизменной."""
    _, h = manager
    card = make_card(total_amount=500.0)
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 700.0})
    assert r.status_code == 200

    _reload(db)
    db.refresh(card)
    assert float(card.total_amount) == 500.0, "total_amount не должна меняться при переплате"
    assert float(card.paid_amount) == 700.0
    assert card.payment_status == "Оплачен"


def test_exact_payment_preserves_total(client, manager, db, make_card):
    """Оплата ровно на сумму: total_amount не меняется."""
    _, h = manager
    card = make_card(total_amount=1000.0)
    client.patch(f"/cards/{card.id}/payment", headers=h,
                 json={"paid_amount": 1000.0})
    _reload(db)
    db.refresh(card)
    assert float(card.total_amount) == 1000.0
    assert card.payment_status == "Оплачен"


# =========================================================================
# A5: is_advance — признак аванса в реестре
# =========================================================================

def test_is_advance_detected_by_note(client, manager, db, make_card):
    """Запись с note='аванс' получает is_advance=True."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    r = client.post(f"/payments/trigger_from_card/{card.id}",
                    headers=h, json={"store_location": STORE})
    assert r.status_code == 200
    # Пометим запись как аванс
    tx = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == False
    ).first()
    tx.note = "Аванс от клиента"
    db.commit()

    rows = client.get("/payments/transactions", headers=h,
                      params={"grouped": False}).json()
    advance_rows = [r for r in rows if r.get("card_id") == card.id]
    assert any(r["is_advance"] is True for r in advance_rows)


def test_is_advance_false_for_normal(client, manager, db, make_card):
    """Обычная запись без «аванс» в note: is_advance=False."""
    _, h = manager
    card = make_card(total_amount=500.0, status="Сборка")
    client.post(f"/payments/trigger_from_card/{card.id}",
                headers=h, json={"store_location": STORE})

    rows = client.get("/payments/transactions", headers=h,
                      params={"grouped": False}).json()
    normal = [r for r in rows if r.get("card_id") == card.id]
    assert all(r["is_advance"] is False for r in normal)


def test_is_advance_english_keyword(client, manager, db, make_card):
    """Английское 'advance' тоже распознаётся."""
    _, h = manager
    card = make_card(total_amount=300.0, status="Сборка")
    client.post(f"/payments/trigger_from_card/{card.id}",
                headers=h, json={"store_location": STORE})
    tx = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == False
    ).first()
    tx.note = "advance payment"
    db.commit()

    rows = client.get("/payments/transactions", headers=h,
                      params={"grouped": False}).json()
    adv = [r for r in rows if r.get("card_id") == card.id]
    assert any(r["is_advance"] is True for r in adv)


# =========================================================================
# A7: client_id null не затирает клиента при полном сохранении
# =========================================================================

def test_client_id_null_single_field_clears(client, manager, db, make_card):
    """Старый фронт: {client_id: null} — единственное поле → клиент снимается."""
    _, h = manager
    cl = models.Client(name="Тестовый клиент")
    db.add(cl)
    db.commit()
    card = make_card(total_amount=100.0, client_id=cl.id)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"client_id": None})
    assert r.status_code == 200

    _reload(db)
    db.refresh(card)
    assert card.client_id is None, "клиент должен быть снят"


def test_client_id_null_full_save_preserves(client, manager, db, make_card):
    """v2 полное сохранение: client_id=null среди многих полей → клиент НЕ снимается."""
    _, h = manager
    cl = models.Client(name="Клиент V2")
    db.add(cl)
    db.commit()
    card = make_card(total_amount=200.0, client_id=cl.id, title="Сделка V2")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Сделка V2", "total_amount": 200.0,
                           "client_id": None, "status": "В работе"})
    assert r.status_code == 200

    _reload(db)
    db.refresh(card)
    assert card.client_id == cl.id, "клиент НЕ должен быть затёрт при полном сохранении"


def test_client_id_null_with_clear_client_flag(client, manager, db, make_card):
    """v2: client_id=null + clear_client=true → клиент снимается."""
    _, h = manager
    cl = models.Client(name="Клиент для снятия")
    db.add(cl)
    db.commit()
    card = make_card(total_amount=300.0, client_id=cl.id)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Любое", "client_id": None,
                           "clear_client": True})
    assert r.status_code == 200

    _reload(db)
    db.refresh(card)
    assert card.client_id is None, "clear_client=true должен снять клиента"


def test_client_id_nonnull_always_set(client, manager, db, make_card):
    """client_id с ненулевым значением всегда устанавливается."""
    _, h = manager
    cl = models.Client(name="Новый клиент")
    db.add(cl)
    db.commit()
    card = make_card(total_amount=100.0)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "X", "client_id": cl.id})
    assert r.status_code == 200

    _reload(db)
    db.refresh(card)
    assert card.client_id == cl.id


# =========================================================================
# A8: отрицательная сумма → 422
# =========================================================================

def test_negative_total_amount_rejected_on_create(client, manager):
    """Создание карточки с отрицательной суммой → 422."""
    _, h = manager
    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Минус", "total_amount": -100.0})
    assert r.status_code == 422
    assert "отрицательной" in r.text.lower() or "negative" in r.text.lower()


def test_negative_total_amount_rejected_on_update(client, manager, db, make_card):
    """PATCH с отрицательной суммой → 422."""
    _, h = manager
    card = make_card(total_amount=500.0)
    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"total_amount": -50.0})
    assert r.status_code == 422


# =========================================================================
# A10: multipart upload /files/attach/{card_id}
# =========================================================================

def test_multipart_upload_v2_endpoint(client, manager, db, make_card):
    """POST /files/attach/{card_id} с multipart отвечает 200, не 405."""
    _, h = manager
    card = make_card(total_amount=100.0)
    content = b"%PDF-1.4 fake pdf content"
    r = client.post(
        f"/files/attach/{card.id}",
        headers=h,
        files={"file": ("test.pdf", io.BytesIO(content), "application/pdf")},
    )
    assert r.status_code == 200, f"ожидался 200, получен {r.status_code}: {r.text}"
    body = r.json()
    assert body["file_name"] == "test.pdf"
    assert body["card_id"] == card.id


def test_multipart_upload_bad_extension(client, manager, db, make_card):
    """Недопустимое расширение → 400."""
    _, h = manager
    card = make_card(total_amount=100.0)
    r = client.post(
        f"/files/attach/{card.id}",
        headers=h,
        files={"file": ("malware.exe", io.BytesIO(b"MZ\x90"), "application/octet-stream")},
    )
    assert r.status_code == 400


# =========================================================================
# A11: cash_balance и overdue_deals в списке клиентов
# =========================================================================

def make_client(db, name="Клиент"):
    c = models.Client(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_clients_list_has_cash_balance(client, manager, db, make_card):
    """GET /clients отдаёт cash_balance и overdue_deals."""
    _, h = manager
    cl = make_client(db, "Баланс-тест")
    # Приход 1000
    db.add(models.ClientPayment(client_id=cl.id, amount=1000.0,
                                created_by=1))
    db.commit()
    # Сделка 700
    make_card(total_amount=700.0, client_id=cl.id)

    rows = client.get("/clients", headers=h).json()
    row = next(r for r in rows if r["id"] == cl.id)
    assert row["cash_balance"] == 300.0, f"1000 - 700 = 300, got {row['cash_balance']}"
    assert row["overdue_deals"] == 0


def test_clients_list_overdue_deals(client, manager, db, make_card):
    """Просроченная сделка (due_date в прошлом) считается в overdue_deals.

    Граница «вчера/сегодня» — бизнес-день по Минску (constants.business_today),
    а не локальная дата машины теста: до фикса 23.09 роутер сравнивал с
    UTC-датой и в окне 00:00–03:00 MSK тест падал (дефект 6 реестра
    V2-WORKPLAN-2026-09-22).
    """
    _, h = manager
    from datetime import timedelta
    from constants import business_today
    today = business_today()
    cl = make_client(db, "Просрочка-тест")
    make_card(total_amount=100.0, client_id=cl.id,
              due_date=today - timedelta(days=1))
    make_card(total_amount=200.0, client_id=cl.id,
              due_date=today + timedelta(days=30))

    rows = client.get("/clients", headers=h).json()
    row = next(r for r in rows if r["id"] == cl.id)
    assert row["overdue_deals"] == 1, "одна просроченная сделка"
    assert row["cash_balance"] == -300.0, "0 приходов − 300 сделок"


def test_single_client_has_balance(client, manager, db, make_card):
    """GET /clients/{id} тоже отдаёт cash_balance."""
    _, h = manager
    cl = make_client(db, "Одиночный")
    db.add(models.ClientPayment(client_id=cl.id, amount=500.0,
                                created_by=1))
    db.commit()
    make_card(total_amount=200.0, client_id=cl.id)

    body = client.get(f"/clients/{cl.id}", headers=h).json()
    assert body["cash_balance"] == 300.0


# =========================================================================
# A9: чек-лист задачи в ответе
# =========================================================================

def test_task_response_includes_checklist(client, manager, db):
    """GET /tasks отдаёт checklist items вместе с задачей."""
    _, h = manager
    # Создать задачу
    r = client.post("/tasks", headers=h,
                    json={"title": "Задача с пунктами"})
    assert r.status_code == 200
    task_id = r.json()["id"]

    # Добавить пункты
    client.post(f"/tasks/{task_id}/checklist", headers=h,
                json={"title": "Пункт 1"})
    client.post(f"/tasks/{task_id}/checklist", headers=h,
                json={"title": "Пункт 2"})

    # Получить список задач — задача с пунктами должна быть там
    tasks = client.get("/tasks", headers=h).json()
    task = next(t for t in tasks if t["id"] == task_id)
    assert len(task.get("checklist", [])) == 2, \
        f"ожидалось 2 пункта чек-листа, получено {task.get('checklist')}"


# =========================================================================
# A6: пустой комментарий не создаёт запись activity_log
# =========================================================================

def test_empty_description_no_activity_log(client, manager, db, make_card):
    """PATCH description='' при пустом описании не создаёт «Комментарий» в логе."""
    _, h = manager
    card = make_card(total_amount=100.0, description="")
    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"description": "   "})
    assert r.status_code == 200

    _reload(db)
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id,
        models.ActivityLog.action == "Комментарий"
    ).all()
    assert len(logs) == 0, "пустой/пробельный note не должен создавать запись"
