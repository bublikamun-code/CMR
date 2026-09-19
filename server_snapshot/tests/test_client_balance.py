"""Баланс клиента (фидбек 18.09): касса client_payments, счета-карточки,
закрытие из баланса.

Модель: баланс = Σ кассы клиента − Σ total_amount его неудалённых
карточек. Плюс — аванс (клиент может «добрать»), минус — долг.
paid_amount карточки — покрытие счёта, кассу не двигает; закрытие из
баланса (from_balance=1) помечается в журнале карточки.
"""
import pytest

import models

pytestmark = pytest.mark.money


@pytest.fixture()
def make_client(db):
    def _factory(name="Клиент баланса"):
        c = models.Client(name=name)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c
    return _factory


def test_advance_and_deal_payment(client, manager, db, make_card, make_client):
    """Аванс 789 при счёте 1000: баланс −211; доплата 1000 деньгами —
    баланс +789 (аванс остался у клиента, ждёт добора)."""
    _, h = manager
    cl = make_client()
    card = make_card(title="Сделка", total_amount=1000.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)

    r = client.post(f"/clients/{cl.id}/payments", headers=h, json={"amount": 789.0, "note": "аванс"})
    assert r.status_code == 200, r.text
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 789.0 and bal["deals_total"] == 1000.0
    assert bal["balance"] == -211.0, "клиент должен 211 до закрытия счёта"

    # Закрытие счёта НОВОЙ оплатой: касса +1000 и покрытие счёта.
    assert client.post(f"/clients/{cl.id}/payments", headers=h,
                       json={"amount": 1000.0, "card_id": card.id, "note": "оплата счёта"}).status_code == 200
    assert client.patch(f"/cards/{card.id}/payment", headers=h,
                        json={"paid_amount": 1000.0, "payment_status": "Оплачен"}).status_code == 200
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 1789.0, "новые деньги в кассе"
    assert bal["balance"] == 789.0, "аванс остался: клиент забрал не всё"


def test_close_from_balance_draws_credit(client, manager, db, make_card, make_client):
    """Сценарий «Рацио Домус»: клиент внёс 1200, счёт А 1000 закрыт,
    остаток 200 — кредит; счёт Б 789 частично закрывается ИЗ БАЛАНСА:
    касса не меняется, paid растёт, в журнале пометка."""
    _, h = manager
    cl = make_client()
    a = make_card(title="Счёт А", total_amount=1000.0, paid_amount=0.0,
                  payment_status="Не оплачен", client_id=cl.id)

    # Клиент внёс 1200 предоплатой; счёт А закрыт из этой суммы.
    assert client.post(f"/clients/{cl.id}/payments", headers=h,
                       json={"amount": 1200.0, "note": "предоплата"}).status_code == 200
    assert client.patch(f"/cards/{a.id}/payment", headers=h,
                        json={"paid_amount": 1000.0, "payment_status": "Оплачен"}).status_code == 200
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["balance"] == 200.0, "кредит клиента 200"

    # Новый счёт Б на 789: баланс падает на сумму счёта сразу при создании.
    b = make_card(title="Счёт Б", total_amount=789.0, paid_amount=0.0,
                  payment_status="Не оплачен", client_id=cl.id)
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["balance"] == -589.0, "счёт Б выставлен — клиент должен"

    before = bal["payments_total"]
    r = client.patch(f"/cards/{b.id}/payment", headers=h,
                     json={"paid_amount": 200.0, "payment_status": "Частично",
                           "from_balance": True})
    assert r.status_code == 200, r.text

    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == before, "касса не двигается"
    assert bal["balance"] == -589.0, "закрытие из баланса не меняет кассу"
    assert bal["paid_on_cards"] == 1200.0, "счёт Б покрыт на 200"


def test_zeroing_paid_does_not_touch_cash(client, manager, db, make_card, make_client):
    """Обнуление paid — это правка покрытия, касса клиента неизменна."""
    _, h = manager
    cl = make_client()
    card = make_card(title="Сделка", total_amount=500.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)
    assert client.post(f"/clients/{cl.id}/payments", headers=h,
                       json={"amount": 500.0, "card_id": card.id}).status_code == 200
    assert client.patch(f"/cards/{card.id}/payment", headers=h,
                        json={"paid_amount": 500.0, "payment_status": "Оплачен"}).status_code == 200

    assert client.patch(f"/cards/{card.id}/payment", headers=h,
                        json={"paid_amount": 0.0, "payment_status": "Не оплачен"}).status_code == 200

    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 500.0, "деньги у клиента не пропадают"
    assert bal["balance"] == 0.0


def test_delete_payment_requires_admin(client, manager, admin, db, make_client):
    _, h = manager
    cl = make_client()
    assert client.post(f"/clients/{cl.id}/payments", headers=h, json={"amount": 100.0}).status_code == 200
    pid = client.get(f"/clients/{cl.id}/balance", headers=h).json()["payments"][0]["id"]

    assert client.delete(f"/clients/payments/{pid}", headers=h).status_code == 403, "менеджер не удаляет кассу"
    assert client.delete(f"/clients/payments/{pid}", headers=admin[1]).status_code == 200
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 0.0


def test_payment_of_other_client_rejected(client, manager, db, make_card, make_client):
    _, h = manager
    cl_a = make_client("А")
    cl_b = make_client("Б")
    other_card = make_card(title="Чужая", total_amount=100.0, client_id=cl_b.id)
    r = client.post(f"/clients/{cl_a.id}/payments", headers=h,
                    json={"amount": 50.0, "card_id": other_card.id})
    assert r.status_code == 400, "чужая сделка не проходит"


def test_card_without_client_patches_normally(client, manager, db, make_card):
    """Карточка без клиента не задействует баланс вовсе."""
    _, h = manager
    card = make_card(title="Без клиента", total_amount=300.0, paid_amount=0.0,
                     payment_status="Не оплачен")
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 300.0, "payment_status": "Оплачен"})
    assert r.status_code == 200, r.text


def test_from_balance_without_money_rejected(client, manager, db, make_card, make_client):
    """V1 (аудит прода 19.09): закрытие из баланса при пустой кассе — 400,
    кредит клиента уходит в минус только с ведома сервера."""
    _, h = manager
    cl = make_client()
    card = make_card(title="Сделка", total_amount=1000.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 1000.0, "payment_status": "Оплачен",
                           "from_balance": True})
    assert r.status_code == 400, r.text
    assert "не хватает" in r.json()["detail"]


def test_from_balance_partial_credit(client, manager, db, make_card, make_client):
    """V1: кредита 500 не хватает на счёт 1000 (400), ровно 500 проходит (200);
    баланс после закрытия считается по прежней формуле, paid_on_cards = 500."""
    _, h = manager
    cl = make_client()
    card = make_card(title="Сделка", total_amount=1000.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)
    assert client.post(f"/clients/{cl.id}/payments", headers=h,
                       json={"amount": 500.0}).status_code == 200

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 1000.0, "payment_status": "Оплачен",
                           "from_balance": True})
    assert r.status_code == 400, r.text
    assert "не хватает" in r.json()["detail"]

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 500.0, "payment_status": "Частично",
                           "from_balance": True})
    assert r.status_code == 200, r.text

    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 500.0, "касса не двигается"
    assert bal["paid_on_cards"] == 500.0, "счёт покрыт на 500"
    assert bal["balance"] == -500.0, "баланс = касса − суммы счетов, как раньше"


def test_from_balance_writes_activity_log(client, manager, db, make_card, make_client):
    """V1: успешное закрытие из баланса создаёт запись в журнале карточки."""
    _, h = manager
    cl = make_client()
    card = make_card(title="Сделка", total_amount=400.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)
    assert client.post(f"/clients/{cl.id}/payments", headers=h,
                       json={"amount": 400.0}).status_code == 200
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 400.0, "payment_status": "Оплачен",
                           "from_balance": True})
    assert r.status_code == 200, r.text

    db.expire_all()
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id,
        models.ActivityLog.action == "Изменение",
    ).all()
    assert logs, "запись «Изменение» должна появиться"
    assert any("Закрыто из баланса" in (log.details or "") for log in logs), \
        "в журнале должна быть пометка о закрытии из баланса"


def test_from_balance_requires_client(client, manager, db, make_card):
    """V1: from_balance на сделке без клиента — 400: баланса, из которого
    можно списать, у такой сделки нет."""
    _, h = manager
    card = make_card(title="Без клиента", total_amount=300.0, paid_amount=0.0,
                     payment_status="Не оплачен")
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 300.0, "payment_status": "Оплачен",
                           "from_balance": True})
    assert r.status_code == 400, r.text
    assert "клиентом" in r.json()["detail"]
