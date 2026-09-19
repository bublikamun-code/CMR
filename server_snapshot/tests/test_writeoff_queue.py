"""Дефект V5 (аудит прода 19.09): очередь списания показывала удалённые
карточки, а execute закрывал сделку из любого статуса без флагов
is_warehouse_writeoff и записи в ленту.

Здесь проверяется:
  - GET /writeoffs/pending не отдаёт карточки из корзины (is_deleted);
  - POST /writeoffs/{id}/execute проводится только из «На списание»
    и только для живой карточки;
  - при проведении помечаются ВСЕ не-документные записи реестра
    (включая запись-остаток), пишется запись «Списание» в ленту,
    сделка закрывается, и sync-writeoff-status больше её не трогает
    (инвариант согласован с writeoff_status_for).
"""
import models


# ---------------------------------------------------------------------------
# Очередь списания
# ---------------------------------------------------------------------------

def test_pending_filters_trash(client, manager, make_card):
    """Карточка в корзине не попадает в очередь списания (V5)."""
    _, h = manager
    alive = make_card(title="Живая", status="На списание")
    make_card(title="В корзине", status="На списание", is_deleted=True)

    r = client.get("/writeoffs/pending", headers=h)
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()]
    assert ids == [alive.id]


# ---------------------------------------------------------------------------
# Защита execute
# ---------------------------------------------------------------------------

def test_execute_only_from_listanie(client, db, manager, make_card):
    """execute не из очереди → 400, статус не меняется."""
    _, h = manager
    card = make_card(title="В сборке", status="Сборка")

    r = client.post(f"/writeoffs/{card.id}/execute", headers=h)
    assert r.status_code == 400, r.text
    db.refresh(card)
    assert card.status == "Сборка"


def test_execute_rejects_trash(client, db, manager, make_card):
    """Карточка в корзине не проводится, даже если стоит в «На списание»."""
    _, h = manager
    card = make_card(title="Удалена", status="На списание", is_deleted=True)

    r = client.post(f"/writeoffs/{card.id}/execute", headers=h)
    assert r.status_code == 400, r.text
    db.refresh(card)
    assert card.status == "На списание", "статус удалённой карточки не меняется"


# ---------------------------------------------------------------------------
# Счастливый путь
# ---------------------------------------------------------------------------

def test_execute_happy_path(client, db, manager, make_card, make_transaction):
    """Полное проведение: флаги всем не-документным записям, запись в
    ленту, закрытие, согласованность с writeoff_status_for."""
    _, h = manager
    card = make_card(title="Заказ со списанием", status="На списание",
                     total_amount=1000.0)
    remainder = make_transaction(card, amount=600.0)  # запись-остаток
    invoice = make_transaction(card, amount=400.0, invoice_number="ТН-9")

    r = client.post(f"/writeoffs/{card.id}/execute", headers=h)
    assert r.status_code == 200, r.text
    assert "закрыта" in r.json()["message"].lower()

    db.refresh(card)
    assert card.status == "Закрыто"

    db.refresh(remainder)
    db.refresh(invoice)
    assert remainder.is_warehouse_writeoff is True, "запись-остаток не помечена"
    assert invoice.is_warehouse_writeoff is True, "накладная не помечена"

    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id).all()
    assert any(l.action == "Списание" for l in logs), "нет записи в ленте"

    # Инвариант: закрытие согласовано с формулой writeoff_status_for —
    # синхронизация не должна «оживлять» только что закрытую сделку.
    r2 = client.post(f"/payments/cards/{card.id}/sync-writeoff-status", headers=h)
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["changed"] is False, data
    assert data["status"] == "Закрыто"
    db.refresh(card)
    assert card.status == "Закрыто"
