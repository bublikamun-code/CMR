"""Приоритет C — статусы сделок, канбан, группы списаний.

Здесь же регрессия на главный дефект Фазы 0: перевод в «Сборку» падал с
ImportError, потому что ensure_registry_remainder отсутствовала в репозитории.
"""
import pytest

import models

STORE = "Матусевича 72"
VALID_STATUSES = {"Новый запрос", "В работе", "Ждет оплаты", "Сборка",
                  "На списание", "Закрыто"}


def _reload(db):
    db.expire_all()


def _set_status(client, headers, card_id, status):
    return client.patch(f"/kanban/cards/{card_id}/status", headers=headers,
                        json={"status": status})


def _ledger(db, card_id):
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).order_by(models.Transaction.id).all()


# ---------------------------------------------------------------------------
# Перевод статусов через канбан
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", sorted(VALID_STATUSES))
def test_all_valid_statuses_accepted(client, manager, db, make_card, status):
    _, h = manager
    card = make_card(total_amount=1000.0)
    r = _set_status(client, h, card.id, status)
    assert r.status_code == 200, f"{status}: {r.text}"
    _reload(db)
    db.refresh(card)
    assert card.status == status


def test_status_to_assembly_creates_registry_remainder(client, manager, db, make_card):
    """РЕГРЕССИЯ на дефект Фазы 0: ensure_registry_remainder отсутствовала,
    и перевод в «Сборку» падал с ImportError → 500."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=1234.56, status="Новый запрос")
    card_id = card.id

    r = _set_status(client, h, card_id, "Сборка")
    assert r.status_code == 200, r.text

    _reload(db)
    rows = _ledger(db, card_id)
    assert len(rows) == 1, "сделка в «Сборке» обязана иметь запись в реестре оплат"
    assert round(float(rows[0].amount), 2) == 1234.56


def test_status_to_assembly_is_idempotent(client, manager, db, make_card):
    _, h = manager
    card = make_card(total_amount=500.0)
    card_id = card.id
    for _ in range(3):
        assert _set_status(client, h, card_id, "Сборка").status_code == 200
    _reload(db)
    assert len(_ledger(db, card_id)) == 1


def test_status_change_without_amount_creates_no_remainder(client, manager, db, make_card):
    """Сделка без суммы запись реестра не получает."""
    _, h = manager
    card = make_card(total_amount=0.0)
    card_id = card.id
    assert _set_status(client, h, card_id, "Сборка").status_code == 200
    _reload(db)
    assert _ledger(db, card_id) == []


def test_garbage_status_rejected(client, manager, db, make_card):
    """Починено в Фазе 2 (2026-09-12, дефект 10).

    CardUpdateStatus получила валидатор по CARD_STATUSES — тому же множеству,
    что проверяют CardBase и CardReorder. Мусорный статус теперь отклоняется
    на входе (422), а не сохраняется в cards.status: раньше карточка с таким
    статусом не попадала ни в одну колонку канбана и исчезала из интерфейса.
    """
    _, h = manager
    card = make_card(total_amount=100.0)
    card_id = card.id
    r = _set_status(client, h, card_id, "Совершенно левый статус")
    assert r.status_code == 422, f"мусорный статус принят со статусом {r.status_code}"
    _reload(db)
    db.refresh(card)
    assert card.status in VALID_STATUSES


# ---------------------------------------------------------------------------
# Списание
# ---------------------------------------------------------------------------

def test_finish_assembly_moves_to_writeoff(client, manager, db, make_card):
    _, h = manager
    card = make_card(total_amount=100.0, status="Сборка")
    card_id = card.id
    r = client.post(f"/writeoffs/{card_id}/finish_assembly", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    db.refresh(card)
    assert card.status == "На списание"


@pytest.mark.parametrize("status", ["Новый запрос", "В работе", "Ждет оплаты",
                                    "На списание", "Закрыто"])
def test_finish_assembly_requires_assembly_status(client, manager, db, make_card, status):
    _, h = manager
    card = make_card(total_amount=100.0, status=status)
    r = client.post(f"/writeoffs/{card.id}/finish_assembly", headers=h)
    assert r.status_code == 400


def test_execute_closes_from_any_status(client, manager, db, make_card):
    """Фиксируем текущее поведение: /execute не проверяет предусловий и не создаёт
    транзакций — просто ставит «Закрыто»."""
    _, h = manager
    card = make_card(total_amount=100.0, status="Новый запрос")
    card_id = card.id
    r = client.post(f"/writeoffs/{card_id}/execute", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    db.refresh(card)
    assert card.status == "Закрыто"
    assert _ledger(db, card_id) == []


def test_remove_from_writeoff_clears_everything(client, manager, db, make_card):
    """DELETE /payments/cards/{id}/writeoff сносит накладные и их копии.

    Запись-остаток при этом СОЗНАТЕЛЬНО пересоздаётся: «Сборка» означает,
    что сделка обязана быть видна в реестре оплат (иначе она исчезала оттуда,
    пока её не «пнули» вручную). Поэтому ожидаем ровно одну свежую запись
    на полную сумму сделки, а не ноль.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    card_id = card.id
    assert client.post(f"/payments/trigger_from_card/{card_id}", headers=h,
                       json={"store_location": STORE}).status_code == 200
    assert client.post(f"/payments/cards/{card_id}/issue-invoice", headers=h,
                       json={"invoice_number": "ТТН1", "amount": 300.0,
                             "invoice_date": "2026-09-01",
                             "store_location": STORE}).status_code == 200

    _reload(db)
    before = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id).count()
    assert before >= 3, "накладная + копия-документ + остаток"

    r = client.delete(f"/payments/cards/{card_id}/writeoff", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    db.refresh(card)
    assert card.status == "Сборка"
    left = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id).all()
    assert len(left) == 1, f"остаться должна только свежая запись-остаток, найдено {len(left)}"
    assert not left[0].invoice_number
    assert not left[0].is_document
    assert round(float(left[0].amount), 2) == 1000.0


def test_remove_from_writeoff_does_not_resurrect_deleted_card(client, manager, db, make_card):
    """Карточка в корзине должна остаться в корзине."""
    _, h = manager
    card = make_card(title="В корзине", total_amount=100.0, status="Сборка",
                     is_deleted=True)
    card_id = card.id

    r = client.delete(f"/payments/cards/{card_id}/writeoff", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    db.refresh(card)
    assert card.is_deleted is True, "карточка воскрешена из корзины как побочный эффект"


@pytest.mark.parametrize("total,txs,expected", [
    # (сумма сделки, [(amount, invoice_number, is_warehouse_writeoff)], ожидаемый статус)
    (1000.0, [], "Сборка"),
    (1000.0, [(1000.0, None, False)], "На списание"),          # только остаток, ничего не выписано
    (1000.0, [(400.0, "ТТН1", False)], "На списание"),         # выписано частично
    (1000.0, [(1000.0, "ТТН1", False)], "Закрыто"),            # выписано полностью
    (1000.0, [(600.0, "ТТН1", False), (400.0, "ТТН2", False)], "Закрыто"),
    (1000.0, [(1000.0, None, True)], "Закрыто"),               # складское списание без номера
    (0.0, [(100.0, None, False)], "На списание"),              # нет суммы — правило по флагам
    (0.0, [(100.0, None, True)], "Закрыто"),
])
def test_sync_writeoff_status_derives_from_data(client, manager, db, make_card,
                                                total, txs, expected):
    """Статус вычисляется по данным, а не назначается вслепую.

    Важная деталь реальной логики (_writeoff_status_for): выписанным считается
    всё, у чего есть invoice_number ИЛИ is_warehouse_writeoff. Флаг
    is_written_off в расчёте НЕ участвует — выписка и складское списание
    намеренно раздельные действия (фидбек 04.09).
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=total, status="Новый запрос")
    card_id = card.id
    for amount, number, warehouse in txs:
        db.add(models.Transaction(card_id=card_id, company_name="Сделка", amount=amount,
                                  is_document=False, invoice_number=number,
                                  is_warehouse_writeoff=warehouse))
    db.commit()

    r = client.post(f"/payments/cards/{card_id}/sync-writeoff-status", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == expected

    _reload(db)
    fresh = db.query(models.Card).filter(models.Card.id == card_id).first()
    assert fresh.status == expected


def test_sync_writeoff_status_reports_changed_flag(client, manager, db, make_card):
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Закрыто")
    card_id = card.id

    r = client.post(f"/payments/cards/{card_id}/sync-writeoff-status", headers=h)
    body = r.json()
    assert body["changed"] is True, "не было записей — статус должен смениться на «Сборка»"
    assert body["status"] == "Сборка"

    r2 = client.post(f"/payments/cards/{card_id}/sync-writeoff-status", headers=h)
    assert r2.json()["changed"] is False, "повторный вызов ничего не меняет"


# ---------------------------------------------------------------------------
# Группы списаний
# ---------------------------------------------------------------------------

def _mk_group_cards(db, make_card, n=2, **kw):
    return [make_card(title=f"Сделка {i}", total_amount=100.0 * (i + 1),
                      status="Сборка", **kw) for i in range(n)]


def test_group_creation_requires_at_least_two_cards(client, manager, db, make_card):
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=1)
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [cards[0].id], "name": "Группа"})
    assert r.status_code == 400


def test_group_creation_rejects_wrong_status(client, manager, db, make_card):
    """В группу принимаются только «Сборка» и «На списание»."""
    _, h = manager
    c1 = make_card(title="A", total_amount=100.0, status="Сборка")
    c2 = make_card(title="B", total_amount=200.0, status="Новый запрос")
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c1.id, c2.id], "name": "Группа"})
    assert r.status_code == 400


def test_group_creation_rejects_card_already_in_group(client, manager, db, make_card):
    _, h = manager
    group = models.WriteoffGroup(name="Существующая", total_amount=0.0)
    db.add(group)
    db.commit()
    c1 = make_card(title="A", total_amount=100.0, status="Сборка",
                   writeoff_group_id=group.id)
    c2 = make_card(title="B", total_amount=200.0, status="Сборка")

    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c1.id, c2.id], "name": "Новая"})
    assert r.status_code == 400


def test_group_total_is_sum_of_cards(client, manager, db, make_card):
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    assert r.status_code == 200, r.text
    assert round(float(r.json()["total_amount"]), 2) == 300.0

    _reload(db)
    for c in cards:
        db.refresh(c)
        assert c.writeoff_group_id is not None


def test_removing_last_card_dissolves_group(client, manager, db, make_card):
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    group_id = r.json()["id"]

    assert client.delete(f"/writeoffs/groups/{group_id}/cards/{cards[0].id}",
                         headers=h).status_code == 200
    assert client.delete(f"/writeoffs/groups/{group_id}/cards/{cards[1].id}",
                         headers=h).status_code == 200

    _reload(db)
    assert db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first() is None, \
        "группа из одной карточки должна быть распущена"


def test_group_in_writeoff_is_frozen(client, manager, db, make_card):
    """После списания группу нельзя менять."""
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    group_id = r.json()["id"]

    inv = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=h,
                      json={"invoice_number": "ТТН-ГРУППА", "amount": 300.0})
    assert inv.status_code == 200, inv.text

    _reload(db)
    extra = make_card(title="C", total_amount=50.0, status="Сборка")
    add = client.post(f"/writeoffs/groups/{group_id}/cards/{extra.id}", headers=h,
                      json={"card_id": extra.id})
    assert add.status_code == 400, "в списанную группу нельзя добавлять карточки"

    rem = client.delete(f"/writeoffs/groups/{group_id}/cards/{cards[0].id}", headers=h)
    assert rem.status_code == 400, "из списанной группы нельзя убирать карточки"


def test_group_invoice_on_partial_amount_rejected(client, manager, db, make_card):
    """Групповая накладная выписывается только на полную сумму группы."""
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    group_id = r.json()["id"]

    bad = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=h,
                      json={"invoice_number": "ТТН-ЧАСТЬ", "amount": 100.0})
    assert bad.status_code == 400


def test_group_invoice_closes_all_cards(client, manager, db, make_card):
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    card_ids = [c.id for c in cards]
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": card_ids, "name": "Группа"})
    group_id = r.json()["id"]

    inv = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=h,
                      json={"invoice_number": "ТТН-ПОЛНАЯ", "amount": 300.0})
    assert inv.status_code == 200, inv.text

    _reload(db)
    for cid in card_ids:
        c = db.query(models.Card).filter(models.Card.id == cid).first()
        assert c.status == "Закрыто", f"карточка {cid} не закрыта"

    g = db.query(models.WriteoffGroup).filter(models.WriteoffGroup.id == group_id).first()
    assert g.written_off is True


def test_group_invoice_does_not_touch_manual_written_off(client, manager, db, make_card):
    """Фидбек 18.09: галочка «Списание» в реестре — ручная функция менеджера.

    Групповая накладная ставит складское списание (is_warehouse_writeoff)
    и «Выписку», но НЕ галочку «Списание» (is_written_off): та живёт
    своей жизнью только во вкладке «Реестр оплат»."""
    _, h = manager
    cards = _mk_group_cards(db, make_card, n=2)
    card_ids = [c.id for c in cards]
    for cid in card_ids:
        # записи реестра появляются при переносе в «Сборку»/«На списание»
        assert client.post(f"/payments/trigger_from_card/{cid}",
                           headers=h,
                           json={"store_location": "Матусевича 72"}).status_code == 200
    r = client.post("/writeoffs/groups/", headers=h,
                    json={"card_ids": card_ids, "name": "Группа"})
    group_id = r.json()["id"]

    inv = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=h,
                      json={"invoice_number": "ТТН-РУЧНАЯ", "amount": 300.0})
    assert inv.status_code == 200, inv.text

    _reload(db)
    rows = db.query(models.Transaction).filter(
        models.Transaction.card_id.in_(card_ids),
        models.Transaction.is_document == False,
    ).all()
    assert rows, "у карточек группы должны быть записи реестра"
    for t in rows:
        assert t.is_warehouse_writeoff is True, "складское списание — да"
        assert t.is_written_off is False, "ручная галочка «Списание» не тронута"
