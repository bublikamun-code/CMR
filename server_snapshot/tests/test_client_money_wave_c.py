"""Волна C (24.10): касса-журнал, привязки, атомарная оплата, сторно.

Целевая механика (решение владельца):
  оплата из карточки = деньги сразу в кассу клиента + сразу привязка к этой
  карточке (одной операцией); внесённое сверх счёта — аванс, покрытие
  карточки не раздувается; возврат — сторно-строка, не удаление.
"""

import datetime

import models


def _mkclient(db):
    user = (db.query(models.User)
              .filter(models.User.role.in_(("manager", "admin"))).first())
    tenant_id = user.tenant_id
    c = models.Client(name="Клиент", tenant_id=tenant_id)
    db.add(c)
    db.commit()
    return c


def _card(db, tenant_id, client_id, total, paid=0.0):
    c = models.Card(title="Сделка", total_amount=total, paid_amount=paid,
                    status="В работе", client_id=client_id, tenant_id=tenant_id)
    db.add(c)
    db.commit()
    return c


def _alloc_active(db, card_id):
    rows = (db.query(models.ClientPaymentAllocation)
              .filter(models.ClientPaymentAllocation.card_id == card_id,
                      models.ClientPaymentAllocation.status == "active").all())
    return round(sum(float(a.amount or 0) for a in rows), 2)


def _cash(db, client_id):
    rows = db.query(models.ClientPayment).filter(
        models.ClientPayment.client_id == client_id).all()
    deposits = sum(float(p.amount) for p in rows if p.kind != "refund")
    refunds = sum(float(p.amount) for p in rows if p.kind == "refund")
    return round(deposits - refunds, 2)


# ---------------------------------------------------------------------------
# Атомарная оплата деньгами: касса + привязка одним запросом
# ---------------------------------------------------------------------------

def test_pay_cash_creates_payment_and_allocation_at_once(client, db, manager):
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 1000.0)

    r = client.post(f"/cards/{card.id}/pay", headers=h, json={
        "amount": 400.0, "source": "cash", "method": "наличные",
        "paid_at": "2026-10-20", "note": "первый взнос"})

    assert r.status_code == 200, r.text
    db.expire_all()
    stored = db.get(models.Card, card.id)
    assert float(stored.paid_amount) == 400.0
    assert stored.payment_status == "Частично"
    pay = (db.query(models.ClientPayment)
             .filter(models.ClientPayment.card_id == card.id).one())
    assert pay.kind == "deposit"
    assert pay.method == "наличные"
    assert pay.paid_at == datetime.datetime(2026, 10, 20)
    # Инвариант волны C: покрытие == Σ активных привязок, привязка знает приход
    assert _alloc_active(db, card.id) == 400.0
    alloc = (db.query(models.ClientPaymentAllocation)
               .filter(models.ClientPaymentAllocation.card_id == card.id).one())
    assert alloc.payment_id == pay.id
    assert alloc.source == "cash"
    assert alloc.status == "active"
    # Касса выросла, доступный остаток — НЕТ: деньги сразу заняты карточкой
    assert _cash(db, cl.id) == 400.0
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["payments_total"] == 400.0
    assert bal["available"] == 0.0
    # Журнал: приход записан с автором
    log = (db.query(models.ActivityLog)
             .filter(models.ActivityLog.action == "Приход денег").one())
    assert log.user_id is not None


def test_pay_balance_keeps_cash_and_binds(client, db, manager):
    """Закрытие из баланса: касса не двигается, привязка source=balance."""
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 1000.0)
    client.post(f"/clients/{cl.id}/payments", headers=h,
                json={"amount": 500.0, "note": "аванс"})

    r = client.post(f"/cards/{card.id}/pay", headers=h,
                    json={"amount": 300.0, "source": "balance"})

    assert r.status_code == 200, r.text
    db.expire_all()
    stored = db.get(models.Card, card.id)
    assert float(stored.paid_amount) == 300.0
    assert _cash(db, cl.id) == 500.0, "кассу закрытие из баланса не двигает"
    alloc = (db.query(models.ClientPaymentAllocation)
               .filter(models.ClientPaymentAllocation.card_id == card.id).one())
    assert alloc.source == "balance"
    assert alloc.payment_id is None
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["available"] == 200.0


def test_pay_over_total_is_rejected_with_clear_reason(client, db, manager):
    """Переплата на карточку запрещена: лишнее — аванс на балансе клиента."""
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 300.0)

    r = client.post(f"/cards/{card.id}/pay", headers=h,
                    json={"amount": 500.0, "source": "cash"})

    assert r.status_code == 400, r.text
    assert "превышает остаток счёта" in r.json()["detail"]
    assert "авансом" in r.json()["detail"]
    db.expire_all()
    assert float(db.get(models.Card, card.id).paid_amount) == 0.0
    assert _cash(db, cl.id) == 0.0, "отказ не оставляет денег в кассе"


def test_pay_cash_without_client_is_rejected(client, db, manager):
    """Некому записать приход — оплата недоступна, а не «оплачено в никуда»."""
    _, h = manager
    tenant_id = db.query(models.User).filter(models.User.role == "manager").first().tenant_id
    card = _card(db, tenant_id, None, 500.0)

    r = client.post(f"/cards/{card.id}/pay", headers=h,
                    json={"amount": 100.0, "source": "cash"})

    assert r.status_code == 400, r.text
    assert "клиентом" in r.json()["detail"]


def test_pay_balance_without_money_is_rejected(client, db, manager):
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 1000.0)

    r = client.post(f"/cards/{card.id}/pay", headers=h,
                    json={"amount": 100.0, "source": "balance"})

    assert r.status_code == 400, r.text
    assert "не хватает" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Гейт Д-1 на старом пути PATCH
# ---------------------------------------------------------------------------

def test_patch_paid_over_total_is_rejected(client, db, manager):
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 300.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 500.0})

    assert r.status_code == 400, r.text
    assert "превышает счёт" in r.json()["detail"]
    db.expire_all()
    assert float(db.get(models.Card, card.id).paid_amount) == 0.0


# ---------------------------------------------------------------------------
# Привязки: снижение покрытия аннулирует, а не теряет
# ---------------------------------------------------------------------------

def test_lowering_paid_annuls_allocations_and_keeps_history(client, db, manager):
    _, h = manager
    cl = _mkclient(db)
    card = _card(db, cl.tenant_id, cl.id, 1000.0)
    client.post(f"/cards/{card.id}/pay", headers=h, json={"amount": 400.0, "source": "cash"})

    r = client.patch(f"/cards/{card.id}/payment", headers=h, json={"paid_amount": 150.0})

    assert r.status_code == 200, r.text
    db.expire_all()
    assert float(db.get(models.Card, card.id).paid_amount) == 150.0
    assert _alloc_active(db, card.id) == 150.0, "инвариант: paid == Σ активных привязок"
    annulled = (db.query(models.ClientPaymentAllocation)
                  .filter(models.ClientPaymentAllocation.card_id == card.id,
                          models.ClientPaymentAllocation.status == "annulled").all())
    assert round(sum(float(a.amount) for a in annulled), 2) == 250.0, (
        "аннулированная часть видна историей, касса при этом не меняется")
    assert _cash(db, cl.id) == 400.0


# ---------------------------------------------------------------------------
# Сторно-возврат
# ---------------------------------------------------------------------------

def test_refund_is_a_storno_row_not_a_delete(client, db, admin):
    _, h = admin
    cl = _mkclient(db)
    pay = client.post(f"/clients/{cl.id}/payments", headers=h,
                      json={"amount": 300.0}).json()

    r = client.post(f"/clients/{cl.id}/payments/{pay['id']}/refund", headers=h,
                    json={"amount": 100.0, "note": "вернули наличными"})

    assert r.status_code == 200, r.text
    db.expire_all()
    rows = db.query(models.ClientPayment).filter(
        models.ClientPayment.client_id == cl.id).all()
    deposits = [p for p in rows if p.kind == "deposit"]
    refunds = [p for p in rows if p.kind == "refund"]
    assert len(deposits) == 1, "исходный приход не удаляется"
    assert len(refunds) == 1 and float(refunds[0].amount) == 100.0
    assert f"#{pay['id']}" in (refunds[0].note or "")
    assert _cash(db, cl.id) == 200.0
    bal = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert bal["refunds_total"] == 100.0
    assert bal["payments_total"] == 200.0


def test_refund_cannot_exceed_available(client, db, admin, manager):
    """Возвращать можно только свободные деньги: привязанное — не трожь."""
    _, ha = admin
    _, hm = manager
    cl = _mkclient(db)
    pay = client.post(f"/clients/{cl.id}/payments", headers=ha,
                      json={"amount": 500.0}).json()
    card = _card(db, cl.tenant_id, cl.id, 1000.0)
    client.post(f"/cards/{card.id}/pay", headers=hm,
                json={"amount": 400.0, "source": "balance"})

    r = client.post(f"/clients/{cl.id}/payments/{pay['id']}/refund", headers=ha,
                    json={"amount": 300.0})

    assert r.status_code == 400, r.text
    assert "свободного остатка" in r.json()["detail"]
    # 100 ещё свободно — проходит
    r2 = client.post(f"/clients/{cl.id}/payments/{pay['id']}/refund", headers=ha,
                     json={"amount": 100.0})
    assert r2.status_code == 200, r2.text


def test_refund_is_admin_only(client, db, manager):
    _, h = manager
    cl = _mkclient(db)
    pay = client.post(f"/clients/{cl.id}/payments", headers=h,
                      json={"amount": 100.0}).json()

    r = client.post(f"/clients/{cl.id}/payments/{pay['id']}/refund", headers=h,
                    json={"amount": 50.0})

    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Приход: журнал, резервы, дата/способ
# ---------------------------------------------------------------------------

def test_deposit_logs_activity_and_accepts_backdating(client, db, manager):
    _, h = manager
    cl = _mkclient(db)

    r = client.post(f"/clients/{cl.id}/payments", headers=h, json={
        "amount": 250.0, "method": "карта", "paid_at": "2026-10-18",
        "note": "взнос вчера"})

    assert r.status_code == 200, r.text
    db.expire_all()
    pay = db.query(models.ClientPayment).filter(
        models.ClientPayment.client_id == cl.id).one()
    assert pay.paid_at == datetime.datetime(2026, 10, 18)
    assert pay.method == "карта"
    log = (db.query(models.ActivityLog)
             .filter(models.ActivityLog.action == "Приход денег").one())
    assert "250.0 BYN" in (log.details or "") or "250 BYN" in (log.details or "")
