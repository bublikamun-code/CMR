"""Волна фиксов аудита 2026-10-05, 24.10: Д-3, Д-4, А-3.

Д-3. ``is_document`` больше не поле TransactionUpdate: раньше PATCH снимал
флаг у строки реестра молча — строка исчезала из реестра и с доски списания,
из-под частичного уникального индекса остатка, а trigger_from_card создавал
запись-дубль на полную сумму.

Д-4. ``max(0.0, …)`` при пересчёте остатка: уменьшение суммы сделки ниже
уже выписанного писало отрицательный остаток, сделка уезжала в «На
списание» и не возвращалась (repair-writeoffs это не чинил).

А-3. Индексы горячих фильтров (миграция 0021 + index=True в models):
cards.due_date, cards.payment_due_date, activity_log.card_id,
notifications.read_at.
"""

import models

from sqlalchemy import text


def _tenant_id(db):
    return db.query(models.User).filter(models.User.role == "admin").first().tenant_id


def _card(db, tenant_id, total, status="Сборка"):
    card = models.Card(title="Сделка", total_amount=total, paid_amount=0.0,
                       status=status, tenant_id=tenant_id)
    db.add(card)
    db.commit()
    return card


def _tx(db, tenant_id, card_id, amount, invoice_number=None):
    t = models.Transaction(card_id=card_id, amount=amount,
                           company_name="Сделка",
                           invoice_number=invoice_number,
                           tenant_id=tenant_id)
    db.add(t)
    db.commit()
    return t


# ---------------------------------------------------------------------------
# Д-3: is_document непатчится
# ---------------------------------------------------------------------------

def test_is_document_is_ignored_on_registry_row(client, db, admin):
    """is_document в теле PATCH игнорируется: флаг у строки реестра не снимается."""
    _, h = admin
    tid = _tenant_id(db)
    card = _card(db, tid, 1000.0)
    rest = _tx(db, tid, card.id, 1000.0)

    r = client.patch(f"/payments/transactions/{rest.id}", headers=h,
                     json={"is_document": True})

    # Поле исчезло из схемы: старые клиенты, продолжающие его слать,
    # получают 200 без эффекта, а не 422 — контракт не ломается.
    assert r.status_code == 200, r.text
    db.expire_all()
    stored = db.get(models.Transaction, rest.id)
    assert stored is not None, "строка реестра обязана остаться"
    assert stored.is_document is False, (
        "флаг is_document управляется только серверными путями (выписка/возврат ТН)")


# ---------------------------------------------------------------------------
# Д-4: отрицательный остаток
# ---------------------------------------------------------------------------

def test_total_below_issued_clamps_remainder_to_zero(client, db, admin):
    """Сумма сделки ниже выписанного даёт остаток 0 (удаление), а не минус."""
    _, h = admin
    tid = _tenant_id(db)
    card = _card(db, tid, 1000.0)
    _tx(db, tid, card.id, 400.0)                 # запись-остаток
    _tx(db, tid, card.id, 600.0, invoice_number="ТН-1")  # выписанное

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"total_amount": 500.0})

    assert r.status_code == 200, r.text
    db.expire_all()
    rows = (db.query(models.Transaction)
              .filter(models.Transaction.card_id == card.id).all())
    assert rows, "выписанная накладная остаётся при любом раскладе"
    for t in rows:
        assert float(t.amount or 0) >= 0, (
            f"отрицательный остаток вернулся: {t.amount}")
    # new_rest = max(0, 500 − 600) = 0 → запись-остаток удаляется,
    # выписанная накладная не трогается.
    leftovers = [t for t in rows if not t.invoice_number]
    assert leftovers == [], (
        "нулевой остаток должен удаляться, а не записываться нулём/минусом")


# ---------------------------------------------------------------------------
# А-3: индексы горячих фильтров
# ---------------------------------------------------------------------------

def _indexed_columns(db, table):
    """PRAGMA-обход индексов таблицы: {колонка} для любого имени индекса."""
    cols = set()
    for row in db.execute(text(f"PRAGMA index_list('{table}')")).fetchall():
        idx_name = row[1]
        for info in db.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall():
            cols.add(info[2])
    return cols


def test_hot_filter_indexes_exist(db):
    """Все четыре горячие колонки покрыты индексом (миграция 0021 / create_all)."""
    for table, column in (
        ("cards", "due_date"),
        ("cards", "payment_due_date"),
        ("activity_log", "card_id"),
        ("notifications", "read_at"),
    ):
        assert column in _indexed_columns(db, table), (
            f"у {table}.{column} нет индекса: скан просрочек/ленты деградирует "
            f"линейно с ростом таблицы (А-3 аудита 2026-10-05)")
