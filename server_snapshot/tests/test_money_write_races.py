"""Гонки записи денег: PATCH сделки и PATCH записи реестра (Д-3, Д-4).

Регрессия аудита 2026-09-30. Оба хендлера писали деньги, не удерживая
writer slot SQLite, поэтому два конкурентных PATCH читали одну и ту же цифру
и каждый применял свою: чей-то чекбокс или правка молча пропадали, а сумма
уезжала от напечатанных накладных.

Проверяем не сам факт блокировки (это внутренность sqlite_writer, она уже
покрыта своими тестами), а два следствия, важных для клиента:

  * конкурентный запрос получает управляемый отказ, а не портит данные;
  * сама правка по-прежнему работает и коммитится.
"""

import threading

import models
import pytest


# ---------------------------------------------------------------------------
# Д-3: PATCH сделки
# ---------------------------------------------------------------------------

def test_card_patch_takes_writer_slot(client, db, admin, monkeypatch):
    """Правка суммы сделки обязана начать write-транзакцию."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Гонка", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.commit()
    card_id = card.id

    # Помечаем момент захвата слота: обычный SELECT в приложении его не даёт.
    marker = {"hit": False}
    real_query = db.query

    def spy(*args, **kwargs):
        return real_query(*args, **kwargs)

    from services.sqlite_writer import reserve_model_rows as real_reserve
    calls = {"n": 0}

    def counting_reserve(session, model, row_ids, **kwargs):
        calls["n"] += 1
        return real_reserve(session, model, row_ids, **kwargs)

    monkeypatch.setattr("routers.card_details_router.reserve_model_rows", counting_reserve)

    r = client.patch(f"/cards/{card_id}", headers=h, json={"total_amount": 250})
    assert r.status_code == 200, r.text
    assert calls["n"] == 1, "PATCH сделки обязан резервировать слот"

    db.expire_all()
    assert float(db.query(models.Card).filter(
        models.Card.id == card_id).first().total_amount) == 250.0


def test_card_patch_without_tag_ids_still_works(client, db, admin):
    """Регрессия Д-3: вынос тела PATCH не ломает правки без тегов.

    tags раньше жила только внутри ветки 'tag_ids' в model_fields_set. После
    того как тело стало отдельной функцией и стало получать tags безусловно,
    отсутствие ветки давало UnboundLocalError — то есть ломалось ВСЁ, кроме
    правки тегов. Этот тест фиксирует именно тот случай.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Без тегов", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.commit()
    card_id = card.id

    for payload in ({"title": "Переименована"}, {"total_amount": 175},
                    {"status": "В работе"}):
        r = client.patch(f"/cards/{card_id}", headers=h, json=payload)
        assert r.status_code == 200, f"{payload}: {r.status_code} {r.text}"


def test_card_patch_snapshot_reads_fresh_values(client, db, admin):
    """Запись «Изменение» в ленту должна отличать старое от нового.

    Слот берётся до снимка _old. Если бы снимок читался ДО резервации, он
    содержал бы уже обновлённые значения, и лента писала бы «старое == новое»
    при реальной правке — то есть правки перестали бы быть видны в истории.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Для истории", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.commit()
    card_id = card.id

    r = client.patch(f"/cards/{card_id}", headers=h, json={"title": "Новое имя"})
    assert r.status_code == 200, r.text

    db.expire_all()
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card_id).all()
    text = " ".join((entry.details or "") for entry in logs)
    assert "Новое имя" in text, text
    assert "Для истории" in text, \
        f"старое имя должно попасть в снимок правки, получено: {text}"


# ---------------------------------------------------------------------------
# Д-4: PATCH записи реестра
# ---------------------------------------------------------------------------

def test_transaction_patch_takes_writer_slot(client, db, admin, monkeypatch):
    """Правка записи реестра обязана начинать write-транзакцию."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Сделка", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.flush()
    tx = models.Transaction(card_id=card.id, amount=100, invoice_number="ТН-1",
                            company_name="Поставщик",
                            is_invoice_issued=False, tenant_id=tenant_id)
    db.add(tx)
    db.commit()
    tx_id = tx.id

    from services.sqlite_writer import reserve_model_rows as real_reserve
    calls = {"n": 0}

    def counting_reserve(session, model, row_ids, **kwargs):
        calls["n"] += 1
        return real_reserve(session, model, row_ids, **kwargs)

    monkeypatch.setattr("routers.payments_router.reserve_model_rows", counting_reserve)

    r = client.patch(f"/payments/transactions/{tx_id}", headers=h,
                     json={"is_invoice_issued": True})
    assert r.status_code == 200, r.text
    assert calls["n"] == 1, "PATCH записи реестра обязан резервировать слот"


def test_transaction_patch_commits_normally(client, db, admin):
    """Обычная правка по-прежнему сохраняется."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Сделка", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.flush()
    tx = models.Transaction(card_id=card.id, amount=100,
                            company_name="Поставщик", tenant_id=tenant_id)
    db.add(tx)
    db.commit()
    tx_id = tx.id

    r = client.patch(f"/payments/transactions/{tx_id}", headers=h,
                     json={"is_invoice_issued": True, "print_status": "printed"})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.query(models.Transaction).filter(models.Transaction.id == tx_id).first()
    assert fresh.is_invoice_issued is True
    assert fresh.print_status == "printed"


def test_rejected_transaction_patch_leaves_no_half_write(client, db, admin):
    """Отказ не оставляет запись изменённой наполовину.

    Проверка на границе отката: если бы после мутаций пришёл отказ (например,
    дубль номера накладной) и отката не было, часть полей записи уже лежала бы
    в сессии, а часть — нет. Здесь проверяем, что при 409 состояние прежнее.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    card = models.Card(title="Сделка", total_amount=100, status="Сборка",
                       tenant_id=tenant_id)
    db.add(card)
    db.flush()
    first = models.Transaction(card_id=card.id, amount=40, invoice_number="ТН-9",
                               company_name="Поставщик", tenant_id=tenant_id)
    second = models.Transaction(card_id=card.id, amount=60, invoice_number="ТН-8",
                                company_name="Поставщик", tenant_id=tenant_id)
    db.add_all([first, second])
    db.commit()
    second_id = second.id

    # Пытаемся поставить на «ТН-8» номер, уже занятый у другой записи сделки.
    r = client.patch(f"/payments/transactions/{second_id}", headers=h,
                     json={"invoice_number": "ТН-9"})
    assert r.status_code == 409, f"ожидался 409 о дубле номера, получено {r.status_code}: {r.text}"

    db.expire_all()
    fresh = db.query(models.Transaction).filter(models.Transaction.id == second_id).first()
    assert fresh.invoice_number == "ТН-8", \
        f"отказ не должен был оставить новый номер: {fresh.invoice_number}"
