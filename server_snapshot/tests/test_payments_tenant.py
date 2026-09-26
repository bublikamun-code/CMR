"""Регрессии платёжных ручек: tenant-изоляция, денежные пороги, откат.

Три независимых класса дефектов, найденных аудитом 2026-09-26 и закрытых
в routers/payments_router.py, services/payments.py, services/writeoffs.py и
services/invoices.py:

  1. Ручки реестра работали по ГЛОБАЛЬНОМУ id: чужую сделку можно было
     посмотреть, пробить накладной и даже починить статус. Теперь всё
     tenant-scoped, чужой объект неотличим от отсутствующего (404).
  2. MONEY_EPSILON (допуск СРАВНЕНИЯ) использовался как «остаток существует»:
     недобор в копейку закрывал сделку, а запись-остаток удалялась — деньги
     исчезали. Теперь «остаток = 0» — это округление до копеек.
  3. Ошибка на промежуточном шаге многошаговой операции оставляла сессию в
     pending-rollback, и все следующие обращения к базе в том же запросе
     падали каскадом.

ВАЖНО про conftest: там есть слушатель before_insert, который молча
проставляет tenant_id=101 и маскирует баги с tenant. Для тестов, где запрос
ПИШЕТ данные, слушатель снимается (паттерн test_email_sync_tenant.py и
test_tenant_required.py), а записи готовятся с явным tenant_id.
"""
import contextlib
import sys

import pytest
from sqlalchemy import event

import models
import services.payments as payments_service
from services.writeoffs import money_is_present, money_is_zero, writeoff_status_for

# Pytest обычно загружает conftest как ``conftest``; при импорте как пакета
# он доступен как ``tests.conftest``. Берём уже загруженный модуль, иначе
# импорт второго экземпляра зарегистрирует ещё один маскирующий слушатель.
cft = sys.modules.get("conftest")
if cft is None:
    from tests import conftest as cft


def _without_test_tenant_listener():
    """Снять маскирующий слушатель conftest перед реальным запросом."""
    event.remove(models.Base, "before_insert", cft._set_test_tenant)


def _restore_test_tenant_listener():
    event.listen(models.Base, "before_insert", cft._set_test_tenant, propagate=True)


def _foreign_card(db, tenant_id, **kw):
    """Сделка чужого tenant с записью реестра — как в бою после выписки."""
    defaults = {
        "title": "Сделка чужого tenant",
        "status": "На списание",
        "total_amount": 1000.0,
        "paid_amount": 0.0,
        "payment_status": "Не оплачен",
        "is_deleted": False,
        "store_location": "Магазин",
        "tenant_id": tenant_id,
    }
    defaults.update(kw)
    card = models.Card(**defaults)
    db.add(card)
    db.flush()
    remainder = models.Transaction(
        company_name=card.title, amount=1000.0,
        store_location=card.store_location, card_id=card.id,
        tenant_id=tenant_id,
    )
    db.add(remainder)
    db.commit()
    return card, remainder


# ---------------------------------------------------------------------------
# 1. Tenant-изоляция платёжных ручек
# ---------------------------------------------------------------------------

def test_writeoff_status_of_foreign_card_is_404(client, db, two_tenant_users):
    """Сводка по списанию чужой сделки не отдаётся (и не подтверждает её существование)."""
    _, first_headers = None, two_tenant_users["first_headers"]
    card, _ = _foreign_card(db, two_tenant_users["second_tenant_id"])

    response = client.get(
        f"/payments/cards/{card.id}/writeoff-status", headers=first_headers
    )
    assert response.status_code == 404, response.text


def test_card_invoice_list_of_foreign_card_is_404(client, db, two_tenant_users):
    """Чек-лист накладных чужой сделки с её суммами не отдаётся."""
    card, _ = _foreign_card(db, two_tenant_users["second_tenant_id"])

    response = client.get(
        f"/payments/cards/{card.id}/invoices", headers=two_tenant_users["first_headers"]
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("path,body", [
    ("/payments/cards/{card_id}/issue-invoice", {
        "invoice_number": "ТТН-ЧУЖАЯ", "invoice_date": "2026-09-26",
        "amount": 100.0, "store_location": "Магазин",
    }),
    ("/payments/cards/{card_id}/invoices", {
        "invoice_number": "ТТН-ЧУЖАЯ2", "amount": 100.0,
        "store_location": "Магазин",
    }),
])
def test_issue_invoice_on_foreign_card_is_404(
    client, db, two_tenant_users, path, body
):
    """Выписать или дописать накладную по ЧУЖОЙ сделке нельзя.

    ВАЖНО: слушатель conftest проставляет tenant_id=101 вставляемым записям,
    поэтому без его снятия тест подставил бы записи в чужую сделку «невидимо»
    и пропустил бы регрессию.
    """
    card, _ = _foreign_card(db, two_tenant_users["second_tenant_id"])
    card_id, tenant_id = card.id, card.tenant_id

    _without_test_tenant_listener()
    try:
        response = client.post(
            path.format(card_id=card_id),
            headers=two_tenant_users["first_headers"],
            json=body,
        )
    finally:
        _restore_test_tenant_listener()

    assert response.status_code == 404, response.text
    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id
    ).count() == 1, "у чужой сделки появилась запись реестра"
    assert db.query(models.Card).filter(models.Card.id == card_id).one().tenant_id == tenant_id


def test_sync_writeoff_status_does_not_write_foreign_card(
    client, db, two_tenant_users
):
    """Ручка не только читала чужую сделку, но и ПИСАЛА ей статус."""
    card, _ = _foreign_card(db, two_tenant_users["second_tenant_id"],
                            status="Закрыто")
    card_id = card.id

    response = client.post(
        f"/payments/cards/{card_id}/sync-writeoff-status",
        headers=two_tenant_users["first_headers"],
    )
    assert response.status_code == 404, response.text
    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == card_id).one().status == "Закрыто"


def test_remove_foreign_card_from_writeoff_is_404(client, db, two_tenant_users):
    """Массовое удаление записей реестра чужой сделки не выполняется."""
    card, remainder = _foreign_card(db, two_tenant_users["second_tenant_id"])
    card_id, remainder_id = card.id, remainder.id

    response = client.delete(
        f"/payments/cards/{card_id}/writeoff",
        headers=two_tenant_users["first_headers"],
    )
    assert response.status_code == 404, response.text
    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.id == remainder_id
    ).one_or_none() is not None
    assert db.query(models.Card).filter(models.Card.id == card_id).one().status == "На списание"


def test_delete_foreign_transaction_is_404(client, db, two_tenant_users):
    """DELETE /payments/transactions/{id} больше не работает по глобальному id."""
    card, remainder = _foreign_card(db, two_tenant_users["second_tenant_id"])
    remainder_id = remainder.id

    response = client.delete(
        f"/payments/transactions/{remainder_id}",
        headers=two_tenant_users["first_headers"],
    )
    assert response.status_code == 404, response.text
    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.id == remainder_id
    ).one_or_none() is not None


def test_repair_writeoffs_is_limited_to_calling_tenant(
    client, db, two_tenant_users
):
    """Массовая починка не правит чужие сделки (и не создаёт им записи реестра).

    Слушатель conftest снят: иначе записи-остатки, созданные починкой для
    чужих карточек, получили бы tenant 101 и тест ничего не заметил бы.
    """
    other = two_tenant_users["second_tenant_id"]
    # Сделка ВТОРОГО tenant без записи реестра: починка её не должна видеть.
    foreign = models.Card(
        title="Чужая сделка без реестра", status="Сборка", total_amount=700.0,
        is_deleted=False, tenant_id=other,
    )
    # Сделка ПЕРВОГО tenant без записи реестра — контрольная.
    own = models.Card(
        title="Своя сделка без реестра", status="Сборка", total_amount=500.0,
        is_deleted=False, tenant_id=two_tenant_users["first_tenant_id"],
    )
    db.add_all([foreign, own])
    db.commit()
    foreign_id, own_id = foreign.id, own.id

    _without_test_tenant_listener()
    try:
        response = client.post(
            "/payments/repair-writeoffs?dry_run=false",
            headers=two_tenant_users["first_headers"],
        )
    finally:
        _restore_test_tenant_listener()

    assert response.status_code == 200, response.text
    body = response.json()
    repaired = {row["card_id"] for row in body["missing_registry"]}
    assert foreign_id not in repaired, "починка задела чужую сделку"
    assert own_id in repaired, "починка обязана чинить сделку своего tenant"

    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == foreign_id
    ).count() == 0, "починка создала запись реестра в чужом tenant"
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == own_id
    ).count() == 1


def test_superadmin_keeps_global_scope_on_payments_routes(
    client, db, two_tenant_users, superadmin
):
    """has_global_scope не сломан: superadmin видит сделку любого tenant."""
    import auth

    superadmin_user, _ = superadmin
    card, _ = _foreign_card(db, two_tenant_users["second_tenant_id"])
    token = auth.create_access_token({
        "sub": superadmin_user.username,
        "tenant_id": superadmin_user.tenant_id,
        "pv": auth.password_version(superadmin_user.hashed_password),
    })
    headers = {"Authorization": f"Bearer {token}"}

    status_response = client.get(
        f"/payments/cards/{card.id}/writeoff-status", headers=headers
    )
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["card_id"] == card.id

    invoices_response = client.get(
        f"/payments/cards/{card.id}/invoices", headers=headers
    )
    assert invoices_response.status_code == 200, invoices_response.text
    assert invoices_response.json()["card_amount"] == 1000.0


# ---------------------------------------------------------------------------
# 2. Денежные пороги: копейка — деньги, двоичный хвост — нет
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,is_zero", [
    (0.0, True),
    (0.0, True),
    (1e-15, True),               # хвост float после вычитаний
    (0.004, True),               # меньше копейки
    (0.01, False),               # копейка — валидная сумма
    (0.006, False),              # округление вверх до копейки
    (0.005, False),              # round(0.005, 2) == 0.01 — тоже деньги
    (0.1 - 0.1, True),
    (0.1 + 0.2 - 0.3, True),     # хвост 5.55e-17 после трёх вычитаний
    (1.01, False),
])
def test_money_zero_predicate(value, is_zero):
    assert money_is_zero(value) is is_zero
    assert money_is_present(value) is (not is_zero)


def test_writeoff_status_keeps_penny_remainder(db, make_card, make_transaction):
    """Остаток в копейку оставляет сделку «На списание», а не «Закрыто»."""
    card = make_card(total_amount=100.01, status="На списание")
    make_transaction(card, amount=100.0, invoice_number="ТТН-100")
    make_transaction(card, amount=0.01)

    assert writeoff_status_for(card, db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id
    ).all()) == "На списание"


def test_writeoff_status_closes_float_dust(db, make_card, make_transaction):
    """Микродробь после вычитаний закрывает сделку как ноль.

    0.1 + 0.2 - 0.3 == 5.55e-17 — двоичный мусор, а не деньги.
    """
    card = make_card(total_amount=0.3, status="На списание")
    make_transaction(card, amount=0.1, invoice_number="ТТН-1")
    make_transaction(card, amount=0.2, invoice_number="ТТН-2")

    assert writeoff_status_for(card, db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id
    ).all()) == "Закрыто"


def test_issue_invoice_leaving_penny_keeps_remainder(
    client, db, make_card, manager
):
    """Накладная на всю сумму минус копейка НЕ закрывает сделку и НЕ удаляет остаток."""
    _, headers = manager
    card = make_card(title="Сделка с копейкой", total_amount=10.01,
                     status="Сборка", store_location="Магазин")
    client.post(f"/payments/trigger_from_card/{card.id}", headers=headers,
                json={"store_location": "Магазин"})
    card_id = card.id

    response = client.post(
        f"/payments/cards/{card_id}/issue-invoice",
        headers=headers,
        json={"invoice_number": "ТТН-0011", "invoice_date": "2026-09-26",
              "amount": 10.0, "store_location": "Магазин"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["card_closed"] is False
    assert response.json()["rest"] == 0.01

    db.expire_all()
    ledger = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    remainders = [t for t in ledger if not (t.invoice_number or "").strip()]
    assert len(remainders) == 1, "запись-остаток с копейкой удалена — деньги исчезли"
    assert round(float(remainders[0].amount), 2) == 0.01
    assert db.query(models.Card).filter(models.Card.id == card_id).one().status == "На списание"


def test_issue_invoice_closing_float_dust_removes_remainder(
    client, db, make_card, manager
):
    """Остаток 4e-17 после вычитаний — ноль: сделка закрывается, остаток сносится."""
    _, headers = manager
    card = make_card(title="Сделка с дробью", total_amount=0.30000000000000004,
                     status="Сборка", store_location="Магазин")
    client.post(f"/payments/trigger_from_card/{card.id}", headers=headers,
                json={"store_location": "Магазин"})
    card_id = card.id

    response = client.post(
        f"/payments/cards/{card_id}/issue-invoice",
        headers=headers,
        json={"invoice_number": "ТТН-0012", "invoice_date": "2026-09-26",
              "amount": 0.3, "store_location": "Магазин"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["card_closed"] is True

    db.expire_all()
    ledger = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    assert [t for t in ledger if not (t.invoice_number or "").strip()] == []
    assert db.query(models.Card).filter(models.Card.id == card_id).one().status == "Закрыто"


def test_repair_writeoffs_keeps_penny_remainder(client, db, admin, make_card,
                                                make_transaction):
    """Починка тоже не «закрывает» копейку: запись-остатка 0.01 остаётся."""
    _, headers = admin
    card = make_card(title="Копейка на починку", total_amount=10.01,
                     status="На списание", store_location="Магазин")
    make_transaction(card, amount=10.0, invoice_number="ТТН-REPAIR")
    # Остаток-ошибка (опечатка) 1.00: правильный остаток сделки — копейка.
    make_transaction(card, amount=1.0)
    card_id = card.id

    response = client.post("/payments/repair-writeoffs?dry_run=false", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(row["card_id"] != card_id for row in body["zero_rows_removed"]), body
    assert card_id in {row["card_id"] for row in body["remainder_fixes"]}, body

    db.expire_all()
    ledger = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    remainders = [t for t in ledger if not (t.invoice_number or "").strip()]
    assert len(remainders) == 1
    assert round(float(remainders[0].amount), 2) == 0.01


def test_repair_writeoffs_drops_zero_dust_remainder(client, db, admin, make_card,
                                                    make_transaction):
    """А вот двоичный мусор (< копейки) починка по-прежнему убирает."""
    _, headers = admin
    card = make_card(title="Мусор в остатке", total_amount=10.0,
                     status="На списание", store_location="Магазин")
    make_transaction(card, amount=10.0, invoice_number="ТТН-MUSOR")
    make_transaction(card, amount=0.004)
    card_id = card.id

    response = client.post("/payments/repair-writeoffs?dry_run=false", headers=headers)
    assert response.status_code == 200, response.text
    assert card_id in {row["card_id"] for row in response.json()["zero_rows_removed"]}

    db.expire_all()
    ledger = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    assert [t for t in ledger if not (t.invoice_number or "").strip()] == []


# ---------------------------------------------------------------------------
# 3. Ложный «близнец» счёта
# ---------------------------------------------------------------------------

def test_document_without_number_does_not_take_foreign_row_as_twin(
    db, make_card, manager
):
    """Документ с пустым ключом не объявляет «близнецом» чужую запись счёта.

    Сценарий: копия накладной в «Документах» без номера и складская запись
    без номера той же суммы. Раньше совпадение суммы при пустых ключах
    засчитывалось как пара, и удаление копии сносило складскую запись.
    """
    user, _ = manager
    card = make_card(title="Сделка с безномерными записями", total_amount=100.0)
    warehouse = models.Transaction(
        company_name=card.title, amount=100.0, store_location="Магазин",
        card_id=card.id, is_warehouse_writeoff=True, invoice_number=None,
        tenant_id=card.tenant_id,
    )
    doc = models.Transaction(
        company_name=card.title, amount=100.0, store_location="Магазин",
        card_id=card.id, is_document=True, invoice_number=None,
        tenant_id=card.tenant_id,
    )
    db.add_all([warehouse, doc])
    db.commit()
    warehouse_id = warehouse.id

    from services.invoices import find_invoice_twins

    twins = find_invoice_twins(db, doc)
    assert twins == [], f"запись #{warehouse_id} объявлена близнецом без номера"

    payments_service.delete_transaction(db, doc.id, user)
    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.id == warehouse_id
    ).one_or_none() is not None, "складская запись снесена вместе с копией"


def test_twins_still_paired_by_number_despite_odd_spelling(db, make_card):
    """Штатная пара «ТТН 4881006» / «ТТН4881006» не сломана новым правилом."""
    card = make_card(title="Сделка с парой накладной", total_amount=100.0)
    live = models.Transaction(
        company_name=card.title, amount=100.0, store_location="Магазин",
        card_id=card.id, invoice_number="ТТН 4881006", is_warehouse_writeoff=True,
        tenant_id=card.tenant_id,
    )
    doc = models.Transaction(
        company_name=card.title, amount=100.0, store_location="Магазин",
        card_id=card.id, invoice_number="ТТН4881006", is_document=True,
        tenant_id=card.tenant_id,
    )
    db.add_all([live, doc])
    db.commit()
    live_id = live.id

    from services.invoices import find_invoice_twins

    assert [t.id for t in find_invoice_twins(db, doc)] == [live_id]


# ---------------------------------------------------------------------------
# 4. Откат незавершённой операции
# ---------------------------------------------------------------------------

def test_multistep_operation_rolls_back_and_rethrows(db, make_card, manager,
                                                      monkeypatch):
    """Сбой на промежуточном шаге: откат + исходная ошибка, сессия остаётся рабочей.

    Без отката SQLAlchemy оставляет сессию в pending-rollback, и следующий
    же запрос в том же request падает каскадом, скрывая настоящую причину.
    """
    user, _ = manager
    card = make_card(title="Сделка для отката", total_amount=1000.0,
                     status="На списание", store_location="Магазин")
    db.add(models.Transaction(
        company_name=card.title, amount=1000.0, store_location="Магазин",
        card_id=card.id, tenant_id=card.tenant_id,
    ))
    db.commit()
    card_id = card.id

    def _boom(*args, **kwargs):
        raise RuntimeError("сбой на промежуточном шаге")

    # Точка сбоя — уже ПОСЛЕ flush() новой накладной: половина операции
    # в базе, и без отката она осталась бы висеть.
    import routers.payments_router as payments_router

    monkeypatch.setattr(payments_router, "_issue_document", _boom)

    with pytest.raises(RuntimeError, match="сбой на промежуточном шаге"):
        payments_service.issue_invoice(
            db, card_id,
            type("P", (), {
                "amount": 400.0, "invoice_number": "ТТН-ROLLBACK",
                "invoice_date": "2026-09-26", "store_location": "Магазин",
            })(),
            user,
        )

    # Сессия обязана остаться пригодной: запрос проходит, а незаписанные
    # строки не «всплывают» при следующем autoflush.
    rows = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id
    ).all()
    assert [t for t in rows if (t.invoice_number or "").strip()] == []
    assert len([t for t in rows if not t.is_document]) == 1


# ---------------------------------------------------------------------------
# 5. N+1 и пагинация grouped-реестра
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _count_sql(pattern):
    """Считать SELECT'ы, содержащие подстроку SQL, во время блока."""
    import sqlalchemy
    from database import engine

    state = {"count": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and pattern in statement:
            state["count"] += 1

    sqlalchemy.event.listen(engine, "before_cursor_execute", _before)
    try:
        yield state
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", _before)


def test_group_annul_reads_all_card_ledgers_in_one_query(client, db, two_tenant_users):
    """Отмена групповой накладной не делает SELECT на каждую карточку группы.

    Проверяем инвариант, а не реализацию: число запросов к реестру не растёт
    вместе с числом сделок группы.
    """
    tenant = two_tenant_users["first_tenant_id"]
    headers = two_tenant_users["first_headers"]
    group = models.WriteoffGroup(name="Группа для N+1", tenant_id=tenant)
    db.add(group)
    db.flush()
    for i in range(6):
        card = models.Card(
            title=f"Сделка группы {i}", status="Закрыто", total_amount=100.0,
            is_deleted=False, store_location="Магазин", writeoff_group_id=group.id,
            tenant_id=tenant,
        )
        db.add(card)
        db.flush()
        db.add(models.Transaction(
            company_name=card.title, amount=100.0, store_location="Магазин",
            card_id=card.id, is_warehouse_writeoff=True,
            tenant_id=tenant,
        ))
    doc = models.Transaction(
        company_name="Группа", amount=600.0, store_location="Магазин",
        is_document=True, writeoff_group_id=group.id, is_invoice_issued=True,
        tenant_id=tenant,
    )
    db.add(doc)
    group.invoice_number = "ТТН-ГРУППА"
    group.written_off = True
    db.commit()

    _without_test_tenant_listener()
    try:
        with _count_sql("FROM transactions") as state:
            response = client.post(
                f"/writeoffs/groups/{group.id}/annul", headers=headers
            )
    finally:
        _restore_test_tenant_listener()

    assert response.status_code == 200, response.text
    # 6 сделок = 6 карточек. Старый код делал по одному запросу на карточку
    # плюс общие; новая схема — общий запрос с IN(...) на все карточки.
    assert state["count"] <= 2, (
        f"отмена группы сделала {state['count']} запросов к реестру — вернулся N+1"
    )


def test_grouped_registry_page_does_not_load_whole_registry(
    client, db, manager, make_card, make_transaction, monkeypatch
):
    """Страница grouped-реестра не материализует весь реестр.

    Считаем реально загруженные ORM-записи реестра: «лёгкий» проход страницы
    берёт только колонки id/card_id/date и объекты Transaction не создаёт.
    Раньше любая страница читала до REGISTRY_HARD_LIMIT полных записей.
    """
    _, headers = manager
    for i in range(40):
        card = make_card(title=f"Сделка {i}", total_amount=100.0 * (i + 1))
        make_transaction(card, amount=100.0 * (i + 1))

    loaded = {"count": 0}

    def _count_load(target, context):
        loaded["count"] += 1

    event.listen(models.Transaction, "load", _count_load)
    try:
        response = client.get(
            "/payments/transactions", headers=headers,
            params={"limit": 3, "offset": 0},
        )
    finally:
        event.remove(models.Transaction, "load", _count_load)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 40
    assert len(body["items"]) == 3
    # 3 сделки — по одной записи каждая; старая реализация грузила все 40.
    assert loaded["count"] <= 3 * 2, (
        f"страница из 3 сделок загрузила {loaded['count']} записей реестра"
    )


def test_grouped_registry_page_tiles_full_list(client, manager, make_card,
                                               make_transaction):
    """Постраничный grouped-реестр совпадает с нарезкой полного ответа."""
    _, headers = manager
    for i in range(5):
        card = make_card(title=f"Сделка {i}", total_amount=100.0 * (i + 1))
        make_transaction(card, amount=100.0 * (i + 1))
    full = client.get("/payments/transactions", headers=headers).json()
    collected = []
    offset = 0
    while offset < len(full):
        page = client.get(
            "/payments/transactions", headers=headers,
            params={"limit": 2, "offset": offset},
        ).json()
        assert page["total"] == len(full)
        collected.extend(page["items"])
        offset += 2
    assert [row["id"] for row in collected] == [row["id"] for row in full]
