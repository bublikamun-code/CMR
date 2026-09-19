"""Дефект V3 (аудит прода 19.09, docs/audits/V2-FIX-PLAN-2026-09-19.md).

«Удалить навсегда» раньше оставляла хвосты: FK transactions.card_id имеет
ondelete="SET NULL" (models.py), поэтому ORM не удалял транзакции, а занулял
card_id — записи оставались в «Реестре оплат» (GET /payments/transactions
показывает их как «старые записи без привязки»). Версии карточки
(record_versions) и её транзакций жили вечно; лента activity_log тоже не
удалялась — сироты без card_id были источником редких 500-х (чистка
закреплена миграцией 0010). Группа списания не пересчитывалась, и пустая
группа оставалась плиткой-призраком на доске списания.

Здесь проверяется КОНЕЧНОЕ СОСТОЯНИЕ после DELETE /kanban/cards/{id}/permanent:
собственность сделки (транзакции, версии, лента) удалена, касса клиента не
затронута, группа пересчитана или удалена, если опустела, нарушений FK нет.

Важная деталь (как в test_data_integrity): все id захватываются в локальные
переменные ДО удаления — после expire_all() обращение к атрибуту удалённого
ORM-объекта поднимает ObjectDeletedError.
"""
import pytest

import models
from versioning import save_version

pytestmark = pytest.mark.integrity


def _count(db, model, **flt):
    q = db.query(model)
    for k, v in flt.items():
        q = q.filter(getattr(model, k) == v)
    return q.count()


def test_hard_delete_cleans_transactions_log_versions_and_recomputes_group(
        client, admin, db, make_card, make_transaction, foreign_key_violations):
    """Транзакции/лента/версии карточки удалены, группа пересчитана по остатку."""
    _, h = admin

    group = models.WriteoffGroup(name="Группа А+Б", total_amount=1500.0)
    db.add(group)
    db.commit()
    group_id = group.id
    card_a = make_card(title="Сделка А", total_amount=1000.0, status="Сборка",
                       writeoff_group_id=group_id)
    card_b = make_card(title="Сделка Б", total_amount=500.0, status="Сборка",
                       writeoff_group_id=group_id)
    card_a_id, card_b_id = card_a.id, card_b.id

    remainder = make_transaction(card_a, amount=1000.0)  # запись-остаток
    invoice = make_transaction(card_a, amount=400.0, invoice_number="ТН-1")
    make_transaction(card_a, amount=400.0, invoice_number="ТН-1", is_document=True)
    remainder_id, invoice_id = remainder.id, invoice.id

    db.add(models.ActivityLog(card_id=card_a_id, action="Статус",
                              details="Новый запрос → Сборка"))
    db.commit()
    save_version(db, "cards", card_a_id,
                 {"title": "Сделка А", "total_amount": 1000.0}, change_type="update")
    save_version(db, "transactions", remainder_id,
                 {"amount": 1000.0}, change_type="create")

    r = client.delete(f"/kanban/cards/{card_a_id}/permanent", headers=h)
    assert r.status_code == 200, r.text

    db.expire_all()
    # Транзакции карточки удалены физически — реестр оплат не получает сирот
    assert _count(db, models.Transaction, card_id=card_a_id) == 0
    assert db.query(models.Transaction).filter(
        models.Transaction.id.in_([remainder_id, invoice_id])).count() == 0
    # Лента карточки удалена вместе с ней (сироты activity_log давали 500-е)
    assert _count(db, models.ActivityLog, card_id=card_a_id) == 0
    # История версий карточки и её транзакций тоже не должна переживать запись
    assert _count(db, models.RecordVersion,
                  table_name="cards", record_id=card_a_id) == 0
    assert _count(db, models.RecordVersion,
                  table_name="transactions", record_id=remainder_id) == 0
    # Группа пересчитана: осталась только сделка Б
    fresh_group = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert fresh_group is not None, "группа с непустым составом не удаляется"
    assert round(float(fresh_group.total_amount), 2) == 500.0
    # Сделка Б не задета
    assert db.query(models.Card).filter(models.Card.id == card_b_id).first() is not None
    assert foreign_key_violations() == []


def test_hard_delete_of_last_member_removes_group(client, admin, db, make_card):
    """Пустая группа не должна оставаться плиткой-призраком на доске списания."""
    _, h = admin
    group = models.WriteoffGroup(name="Одиночная группа", total_amount=300.0)
    db.add(group)
    db.commit()
    group_id = group.id
    card = make_card(title="Одинокая сделка", total_amount=300.0,
                     writeoff_group_id=group_id)
    card_id = card.id

    r = client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first() is None


def test_payments_registry_is_clean_after_hard_delete(
        client, admin, manager, db, make_card, make_transaction):
    """Реестр оплат не показывает ни записей удалённой сделки, ни сирот."""
    _, admin_h = admin
    _, manager_h = manager
    card = make_card(title="Реестр-сделка", total_amount=700.0)
    card_id = card.id
    make_transaction(card, amount=700.0)

    assert client.delete(f"/kanban/cards/{card_id}/permanent",
                         headers=admin_h).status_code == 200

    r = client.get("/payments/transactions", headers=manager_h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(row["company_name"] != "Реестр-сделка" for row in rows), \
        "записи удалённой сделки остались в реестре"
    assert all(row["card_id"] is not None for row in rows), \
        "в реестре остались сироты без привязки к сделке"


def test_hard_delete_not_found_and_manager_forbidden(client, admin, manager, db, make_card):
    """Чужой сценарий доступа: нет карточки — 404 (админ), менеджер — 403."""
    _, admin_h = admin
    _, manager_h = manager

    assert client.delete("/kanban/cards/999999999/permanent",
                         headers=admin_h).status_code == 404

    card = make_card()
    card_id = card.id
    assert client.delete(f"/kanban/cards/{card_id}/permanent",
                         headers=manager_h).status_code == 403
    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == card_id).first() is not None, \
        "403 не должен удалять карточку"
