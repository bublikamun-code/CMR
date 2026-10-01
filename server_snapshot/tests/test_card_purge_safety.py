"""Удаление сделки навсегда: гейт, снимок содержимого и след в ленте (Д-1).

Регрессия аудита 2026-09-30. Три вещи, которых раньше не было:

  * гейт — сделка с записями в реестре оплат отвечает 409 и требует force;
  * снимок содержимого пишется ДО разрушения;
  * само удаление попадает в ленту, хотя лента сделки только что стёрта.

И отдельный порядок «сначала база, потом файлы»: раньше файлы сносились до
коммита, и любой сбой между этими точками оставлял ссылку в CRM на файл,
которого уже нет.
"""

import json
import os

import models
import pytest


def _seed_card_with_transaction(db, tenant_id, owner_id):
    card = models.Card(
        title="Сделка с накладной", total_amount=100, paid_amount=0,
        status="new", tenant_id=tenant_id, owner_id=owner_id,
    )
    db.add(card)
    db.flush()
    tx = models.Transaction(
        card_id=card.id, amount=100, invoice_number="НН-77",
        company_name="Поставщик ООО", is_invoice_issued=True,
        is_document=True, tenant_id=tenant_id,
    )
    db.add(tx)
    db.commit()
    return card, tx


def _seed_plain_card(db, tenant_id, owner_id):
    card = models.Card(
        title="Пустая сделка", total_amount=0, status="new",
        tenant_id=tenant_id, owner_id=owner_id,
    )
    db.add(card)
    db.commit()
    return card


def test_card_with_transactions_is_gated(client, db, admin):
    """Сделка с записями в реестре не удаляется молча."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card, _ = _seed_card_with_transaction(db, tenant_id, None)

    r = client.delete(f"/kanban/cards/{card.id}/permanent", headers=h)

    assert r.status_code == 409, f"ожидался гейт 409, получено {r.status_code}: {r.text}"
    assert "реестр" in r.json()["detail"].lower() or "запис" in r.json()["detail"].lower()
    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == card.id).count() == 1, \
        "карточка обязана остаться на месте"
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id).count() == 1, \
        "транзакции обязаны остаться на месте"


def test_force_deletes_card_with_transactions(client, db, admin):
    """С подтверждением сделка с накладными удаляется — гейт не тупик."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card, _ = _seed_card_with_transaction(db, tenant_id, None)
    card_id = card.id

    r = client.delete(f"/kanban/cards/{card_id}/permanent?force=1", headers=h)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transactions_removed"] == 1, body
    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == card_id).count() == 0
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id).count() == 0


def test_plain_card_deleted_without_force(client, db, admin):
    """Сделка без денег удаляется как раньше — без лишнего подтверждения."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _seed_plain_card(db, tenant_id, None)
    card_id = card.id

    r = client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)

    assert r.status_code == 200, r.text
    assert r.json()["transactions_removed"] == 0
    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == card_id).count() == 0


def test_deletion_is_written_to_activity_log(client, db, admin):
    """Само удаление остаётся в ленте: раньше след был нулевой."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _seed_plain_card(db, tenant_id, None)
    card_id = card.id

    client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)

    db.expire_all()
    entries = db.query(models.ActivityLog).filter(
        models.ActivityLog.action == "Удаление сделки навсегда").all()
    assert len(entries) == 1, "удаление должно попасть в ленту"
    entry = entries[0]
    assert entry.card_id is None, "card_id обнуляется — самой сделки уже нет"
    assert "Пустая сделка" in entry.details, entry.details
    assert f"id={card_id}" in entry.details, entry.details


def test_deletion_log_names_the_administrator(client, db, admin):
    """В записи есть кто удалил — иначе след бесполезен."""
    _, h = admin
    username = client.get("/auth/me", headers=h).json()["username"]
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = _seed_plain_card(db, tenant_id, None)
    card_id = card.id

    client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)

    db.expire_all()
    entry = db.query(models.ActivityLog).filter(
        models.ActivityLog.action == "Удаление сделки навсегда").first()
    assert username in entry.details, entry.details


def test_content_snapshot_is_written(client, db, admin):
    """Перед разрушением пишется снимок содержимого сделки."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card, _ = _seed_card_with_transaction(db, tenant_id, None)
    card_id = card.id

    r = client.delete(f"/kanban/cards/{card_id}/permanent?force=1", headers=h)

    path = r.json()["backup"]
    assert path, "ответ обязан называть файл снимка"
    assert os.path.exists(path), path
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["card_id"] == card_id
    assert payload["card"]["title"] == "Сделка с накладной"
    assert len(payload["transactions"]) == 1
    assert payload["transactions"][0]["invoice_number"] == "НН-77"
    assert payload["transactions"][0]["amount"] is not None


def test_snapshot_records_deleted_count_in_log(client, db, admin):
    """Запись в ленте говорит, сколько записей реестра пропало."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card, _ = _seed_card_with_transaction(db, tenant_id, None)

    client.delete(f"/kanban/cards/{card.id}/permanent?force=1", headers=h)

    db.expire_all()
    entry = db.query(models.ActivityLog).filter(
        models.ActivityLog.action == "Удаление сделки навсегда").first()
    assert "1" in entry.details, entry.details
    assert "100.00" in entry.details, entry.details


def test_gated_request_leaves_no_snapshot(client, db, admin, tmp_path):
    """Отказ по гейту не создаёт снимок: ничего ведь не удалено."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card, _ = _seed_card_with_transaction(db, tenant_id, None)

    before = set(os.listdir(_backups_dir(db)))
    client.delete(f"/kanban/cards/{card.id}/permanent", headers=h)
    after = set(os.listdir(_backups_dir(db)))

    assert before == after, f"снимок создан вопреки отказу: {after - before}"


def _backups_dir(db):
    rows = db.connection().exec_driver_sql("PRAGMA database_list").fetchall()
    db_path = next((r[2] for r in rows if r[1] == "main" and r[2]), None)
    return os.path.join(os.path.dirname(db_path), "backups")
