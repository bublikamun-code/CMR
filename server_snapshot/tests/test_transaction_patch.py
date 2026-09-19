"""V4 (аудит прода 19.09): PATCH /payments/transactions/{id} и инварианты сделки.

Дефект: правка суммы ТН не пересчитывала запись-остаток и статус карточки,
а правка номера не проверяла дубли — реестр оплат расходился с напечатанными
накладными.

Здесь закреплено поведение после починки:
  - инвариант «запись-остаток = сумма сделки − выписанное» держится и на
    правке суммы (формула как в add_invoice и в PATCH /cards/{id});
  - дубль номера на одной сделке — 409, в том числе в другом написании
    (сравнение по цифрам, как в issue_invoice/add_invoice);
  - документы (is_document) и записи без карточки правятся как раньше,
    без пересчёта: номер документа повторяет оригинал по дизайну.
"""
import pytest

import models

pytestmark = pytest.mark.money


def _reload(db):
    """Сбросить кэш сессии, чтобы увидеть изменения, сделанные через API."""
    db.expire_all()


def _patch(client, headers, tx_id, payload):
    return client.patch(f"/payments/transactions/{tx_id}", headers=headers, json=payload)


def _ledger(db, card_id):
    """Записи реестра по сделке, без копий-документов."""
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).order_by(models.Transaction.id).all()


def _base_layout(make_card, make_transaction, total=1000.0):
    """Базовая раскладка: карточка «На списание», накладная ТН-1 + остаток."""
    card = make_card(title="Сделка V4", total_amount=total, status="На списание")
    tx1 = make_transaction(card, amount=400.0, invoice_number="ТН-1")
    tx2 = make_transaction(card, amount=round(total - 400.0, 2))
    return card, tx1, tx2


# ---------------------------------------------------------------------------
# Дубль номера на правке
# ---------------------------------------------------------------------------

def test_duplicate_number_on_patch_rejected_with_409(client, manager, db,
                                                     make_card, make_transaction):
    """Поставить запись-остатку номер уже выписанной накладной — 409.

    Тот же замок, что в issue_invoice/add_invoice: без него дубль номера
    обходился через соседнюю дверь — правку записи. Сравнение по цифрам:
    «ттн 1» и «ТН-1» — одна накладная.
    """
    _, h = manager
    card, _tx1, tx2 = _base_layout(make_card, make_transaction)

    r = _patch(client, h, tx2.id, {"invoice_number": "ТН-1"})
    assert r.status_code == 409, r.text

    r2 = _patch(client, h, tx2.id, {"invoice_number": " ттн 1 "})
    assert r2.status_code == 409, r2.text

    _reload(db)
    fresh = db.query(models.Transaction).filter(
        models.Transaction.id == tx2.id).first()
    assert not (fresh.invoice_number or "").strip(), \
        "отклонённая правка всё равно записалась"


# ---------------------------------------------------------------------------
# Правка суммы и пересчёт остатка
# ---------------------------------------------------------------------------

def test_amount_over_available_rejected_with_400(client, manager, db,
                                                 make_card, make_transaction):
    """Сумма накладной больше непокрытого остатка сделки — 400 (паритет с add_invoice).

    Доступно = сумма сделки − выписанное по ДРУГИМ записям: карточка на 500,
    других накладных нет → 600 не проходит.
    """
    _, h = manager
    card, tx1, _tx2 = _base_layout(make_card, make_transaction, total=500.0)

    r = _patch(client, h, tx1.id, {"amount": 600.0})
    assert r.status_code == 400, r.text
    assert "больше остатка" in r.json()["detail"]


def test_amount_decrease_recomputes_remainder(client, manager, db,
                                              make_card, make_transaction):
    """Меньшая сумма накладной → остаток пересчитан (1000 − 100 = 900)."""
    _, h = manager
    card, tx1, _tx2 = _base_layout(make_card, make_transaction)

    r = _patch(client, h, tx1.id, {"amount": 100.0})
    assert r.status_code == 200, r.text

    _reload(db)
    fresh_card = db.query(models.Card).filter(models.Card.id == card.id).first()
    remainder = next(t for t in _ledger(db, card.id)
                     if not (t.invoice_number or "").strip())
    assert round(float(remainder.amount), 2) == 900.0
    assert fresh_card.status == "На списание"


def test_full_coverage_deletes_remainder_and_closes_card(client, manager, db,
                                                         make_card, make_transaction):
    """Накладная покрыла сделку целиком → запись-остаток удалена, статус «Закрыто».

    Нулевая запись-остаток — мусор в реестре, поэтому удаляется, а не остаётся.
    """
    _, h = manager
    card, tx1, tx2 = _base_layout(make_card, make_transaction)

    r = _patch(client, h, tx1.id, {"amount": 1000.0})
    assert r.status_code == 200, r.text

    _reload(db)
    ledger = _ledger(db, card.id)
    assert [t.id for t in ledger] == [tx1.id], "запись-остаток должна быть удалена"
    fresh_card = db.query(models.Card).filter(models.Card.id == card.id).first()
    assert fresh_card.status == "Закрыто"


# ---------------------------------------------------------------------------
# Случаи, которые пересчёт не должен затрагивать
# ---------------------------------------------------------------------------

def test_document_amount_patch_does_not_touch_card(client, manager, db,
                                                   make_card, make_transaction):
    """Правка суммы КОПИИ-документа не трогает сделку: документ — копия, не деньги.

    Его номер повторяет оригинал по дизайну, сумма его ни на что не влияет.
    """
    _, h = manager
    card, tx1, tx2 = _base_layout(make_card, make_transaction)
    doc = make_transaction(card, is_document=True, invoice_number="ТН-1",
                           amount=100.0)

    r = _patch(client, h, doc.id, {"amount": 250.0})
    assert r.status_code == 200, r.text

    _reload(db)
    fresh_card = db.query(models.Card).filter(models.Card.id == card.id).first()
    assert fresh_card.status == "На списание"
    ledger = {t.id: t for t in _ledger(db, card.id)}
    assert round(float(ledger[tx1.id].amount), 2) == 400.0
    assert round(float(ledger[tx2.id].amount), 2) == 600.0


def test_row_without_card_is_patched_without_recalc(client, manager, db,
                                                    make_card, make_transaction):
    """Запись без карточки (card_id IS NULL) правится как раньше — ничего не падает."""
    _, h = manager
    card, tx1, tx2 = _base_layout(make_card, make_transaction)
    orphan = make_transaction(card, card_id=None, company_name="Свободная запись",
                              amount=300.0)

    r = _patch(client, h, orphan.id, {"amount": 150.0})
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 150.0

    _reload(db)
    fresh_card = db.query(models.Card).filter(models.Card.id == card.id).first()
    assert fresh_card.status == "На списание"
    ledger = {t.id: t for t in _ledger(db, card.id)}
    assert round(float(ledger[tx1.id].amount), 2) == 400.0
    assert round(float(ledger[tx2.id].amount), 2) == 600.0


# ---------------------------------------------------------------------------
# Регресс: прежнее поведение правки номера сохраняется
# ---------------------------------------------------------------------------

def test_unique_renumber_syncs_document_copy(client, manager, db,
                                             make_card, make_transaction):
    """Законная перенумеровка проходит, копия в «Документах» подтягивает номер.

    Регресс-тест прежнего поведения: пара «накладная + копия» — одна сущность,
    правка номера с любой стороны касается обеих.
    """
    _, h = manager
    card, tx1, _tx2 = _base_layout(make_card, make_transaction)
    doc = make_transaction(card, is_document=True, invoice_number="ТН-1",
                           amount=400.0)

    r = _patch(client, h, tx1.id, {"invoice_number": "ТН-2"})
    assert r.status_code == 200, r.text

    _reload(db)
    fresh_tx1 = db.query(models.Transaction).filter(
        models.Transaction.id == tx1.id).first()
    fresh_doc = db.query(models.Transaction).filter(
        models.Transaction.id == doc.id).first()
    assert fresh_tx1.invoice_number == "ТН-2"
    assert fresh_doc.invoice_number == "ТН-2", "копия-документ должна подтянуть номер"
