"""Остаток и баланс: правка не игнорируется, формула одна (С-2, С-4).

Регрессия аудита 2026-09-30.

С-2. Правка суммы САМОЙ записи-остатка возвращала 200 и молча затиралась:
setattr применял присланное значение, а блок пересчёта тут же присваивал
остатку производную величину «сумма сделки − выписанное». Пользователь видел
«сохранено», реестр оплат показывал старое число.

С-4. «Позиция клиента» и «чем можно закрыть сделку» считались в двух местах
по-разному. Интерфейс прятал чекбокс закрытия из баланса по balance
(приходы − суммы сделок), а сервер считал доступным payments_total −
paid_on_cards. Клиент с настоящим авансом не мог закрыть сделку вообще.
"""

import models


def _client(db, tenant_id, name="Клиент"):
    c = models.Client(name=name, tenant_id=tenant_id)
    db.add(c)
    db.commit()
    return c


def _card(db, tenant_id, client_id, total, paid=0.0, status="Сборка"):
    card = models.Card(title="Сделка", total_amount=total, paid_amount=paid,
                       status=status, client_id=client_id, tenant_id=tenant_id)
    db.add(card)
    db.commit()
    return card


def _pay(db, tenant_id, client_id, amount):
    p = models.ClientPayment(client_id=client_id, amount=amount,
                             tenant_id=tenant_id)
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# С-2: правка суммы записи-остатка
# ---------------------------------------------------------------------------

def test_remainder_amount_edit_is_rejected_not_silently_dropped(client, db, admin):
    """Правка суммы остатка обязана быть отвергнута, а не проигнорирована."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _card(db, tenant_id, None, 500.0)
    rest = models.Transaction(card_id=card.id, amount=500.0,
                              company_name="Сделка", tenant_id=tenant_id)
    db.add(rest)
    db.commit()
    rest_id = rest.id

    r = client.patch(f"/payments/transactions/{rest_id}", headers=h,
                     json={"amount": 300.0})

    assert r.status_code == 400, (
        f"ожидался 400 с объяснением, получено {r.status_code}: {r.text}")
    assert "остаток" in r.json()["detail"].lower()
    db.expire_all()
    assert float(db.query(models.Transaction).filter(
        models.Transaction.id == rest_id).first().amount) == 500.0, \
        "сумма остатка обязана остаться прежней"


def test_remainder_amount_edit_never_answers_ok_with_old_value(client, db, admin):
    """Ключевой признак дефекта: 200 при неизменившемся значении.

    Именно это видел пользователь — зелёное «сохранено» и старое число в
    реестре. Тест фиксирует, что такое сочетание больше невозможно.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _card(db, tenant_id, None, 800.0)
    rest = models.Transaction(card_id=card.id, amount=800.0,
                              company_name="Сделка", tenant_id=tenant_id)
    db.add(rest)
    db.commit()
    rest_id = rest.id

    r = client.patch(f"/payments/transactions/{rest_id}", headers=h,
                     json={"amount": 123.45})
    assert r.status_code != 200, "правка остатка не должна проходить успешно"
    db.expire_all()
    assert float(db.query(models.Transaction).filter(
        models.Transaction.id == rest_id).first().amount) == 800.0


def test_invoice_amount_edit_still_allowed(client, db, admin):
    """Сумму НАКЛАДНОЙ править можно и нужно — гейт не должен задевать её."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _card(db, tenant_id, None, 1000.0)
    rest = models.Transaction(card_id=card.id, amount=1000.0,
                              company_name="Сделка", tenant_id=tenant_id)
    invoice = models.Transaction(card_id=card.id, amount=400.0, invoice_number="ТН-1",
                                 company_name="Поставщик", tenant_id=tenant_id)
    db.add_all([rest, invoice])
    db.commit()
    invoice_id = invoice.id

    r = client.patch(f"/payments/transactions/{invoice_id}", headers=h,
                     json={"amount": 350.0})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert float(db.query(models.Transaction).filter(
        models.Transaction.id == invoice_id).first().amount) == 350.0


# ---------------------------------------------------------------------------
# С-4: одна формула доступного баланса
# ---------------------------------------------------------------------------

def test_balance_and_available_are_different_and_both_present(client, db, admin):
    """Долг и доступный аванс — разные числа, оба приходят в ответе."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    c = _client(db, tenant_id)
    _pay(db, tenant_id, c.id, 5000.0)
    _card(db, tenant_id, c.id, 9000.0, paid=4000.0)
    _card(db, tenant_id, c.id, 1000.0, paid=0.0)

    r = client.get(f"/clients/{c.id}/balance", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    # Позиция: 5000 − 10000 = −5000 (долг).
    assert body["balance"] == -5000.0, body
    # Доступно закрыть: 5000 − 4000 = 1000, неоплаченная сделка аванс не съедает.
    assert body["available"] == 1000.0, body


def test_available_ignores_unpaid_deals(client, db, admin):
    """Неоплаченные сделки не уменьшают доступный аванс."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    c = _client(db, tenant_id)
    _pay(db, tenant_id, c.id, 2000.0)
    _card(db, tenant_id, c.id, 5000.0, paid=0.0)   # долг большой, аванс цел

    body = client.get(f"/clients/{c.id}/balance", headers=h).json()
    assert body["available"] == 2000.0, body
    assert body["balance"] == -3000.0, body


def test_payment_from_balance_uses_the_same_available_number(client, db, admin):
    """Серверный гейт и данные для интерфейса берут одну и ту же величину.

    Раньше интерфейс считал доступным balance, а сервер — payments_total минус
    paid_on_cards. Здесь сценарий, где они расходились: аванс есть, долг есть,
    и человек не мог закрыть сделку, хотя деньги в кассе были.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    c = _client(db, tenant_id)
    _pay(db, tenant_id, c.id, 5000.0)
    # Первая сделка закрыта частично, вторая открыта и крупнее аванса.
    closed_partly = _card(db, tenant_id, c.id, 4000.0, paid=4000.0)
    open_card = _card(db, tenant_id, c.id, 3000.0, paid=0.0)

    body = client.get(f"/clients/{c.id}/balance", headers=h).json()
    assert body["available"] == 1000.0, body
    assert body["balance"] < 0, "долг при этом остаётся, он другого поля"

    # Больше аванса нельзя, и причина называет РОВНО ту цифру, которую видит
    # интерфейс: 1000, а не −5000 из balance.
    over = client.patch(f"/cards/{open_card.id}/payment", headers=h,
                        json={"paid_amount": 1500.0, "from_balance": True})
    assert over.status_code == 400, over.text
    detail = over.json()["detail"]
    assert "1000" in detail, f"текст должен называть доступную сумму: {detail}"
    assert "5000" not in detail, f"в тексте не должно быть величины balance: {detail}"

    # Аванс ровно 1000 списывается, и после этого касса пуста.
    ok = client.patch(f"/cards/{open_card.id}/payment", headers=h,
                      json={"paid_amount": 1000.0, "from_balance": True})
    assert ok.status_code == 200, f"аванс должен закрывать сделку: {ok.text}"

    after = client.get(f"/clients/{c.id}/balance", headers=h).json()
    assert after["available"] == 0.0, after
    assert after["balance"] < 0, "долг никуда не делся, это другое поле"
