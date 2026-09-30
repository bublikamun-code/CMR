"""Правила обновления сделки: PATCH /cards/{id} и PATCH /cards/{id}/payment.

Файл закрывает ветки `update_card` и `update_card_payment`
(`routers/card_details_router.py`), которые не накрывались тестами:

  * общий PATCH не умеет оплату (оплата живёт только в /payment) и не умеет
    статус доски — это осознанные запреты, а не «забытые» поля;
  * ссылки (owner_id/client_id/tag_ids/sender_email) проверяются ДО мутаций,
    поэтому отказ не должен оставлять карточку изменённой наполовину;
  * смена суммы каскадно трогает реестр оплат (запись-остаток), статус
    списания и итог группы списаний — но НЕ трогает выписанные накладные;
  * смена названия и склада каскадно обновляет транзакции карточки;
  * закрытие из баланса клиента не может увести кредит в минус.

Проверяем через БД, а не только по JSON ответа: интересует, что реально
сохранилось. Помечены маркерами pytest.ini:
  money     — денежные инварианты;
  integrity — каскады между таблицами;
  access    — границы доступа, тенантов и ролей.
"""
import datetime

import pytest

import models

STORE = "Матусевича 72"


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _reload(db, *objs):
    """Сбросить кэш сессии теста, чтобы увидеть правки, сделанные через API."""
    db.expire_all()
    for obj in objs:
        db.refresh(obj)


def _txs(db, card_id):
    """Все транзакции карточки по порядку id, свежие из БД."""
    db.expire_all()
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id
    ).order_by(models.Transaction.id).all()


def _remainder(db, card_id):
    """Запись-остаток реестра оплат: не документ, без номера накладной,
    не помечена как складское списание (инвариант миграции 0004)."""
    rows = [t for t in _txs(db, card_id)
            if not t.is_document
            and not t.is_warehouse_writeoff
            and not (t.invoice_number or "").strip()]
    assert len(rows) <= 1, f"записей-остатков больше одной: {len(rows)}"
    return rows[0] if rows else None


def _logs(db, card_id):
    """Лента активности карточки, свежая из БД."""
    db.expire_all()
    return db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card_id
    ).order_by(models.ActivityLog.id).all()


def _details_joined(db, card_id):
    return "; ".join(l.details or "" for l in _logs(db, card_id))


def _make_client(db, name, tenant_id=101):
    client = models.Client(name=name, tenant_id=tenant_id)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def _make_tag(db, name, tenant_id=101):
    tag = models.Tag(name=name, tenant_id=tenant_id)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _make_payment(db, client_id, amount):
    """Приход денег от клиента (client_payments) — источник баланса."""
    payment = models.ClientPayment(client_id=client_id, amount=amount, tenant_id=101)
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


# ---------------------------------------------------------------------------
# 1. Общий PATCH не умеет оплату
# ---------------------------------------------------------------------------

@pytest.mark.money
def test_patch_rejects_paid_amount(client, db, manager, make_card):
    """paid_amount в общем PATCH → 400 и ссылка на /payment, карточка цела."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=500.0, description="исходная")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Переименованная", "paid_amount": 100})

    assert r.status_code == 400, r.text
    assert "Оплату меняйте" in r.json()["detail"]
    _reload(db, card)
    assert card.title == "Сделка", "запрет сработал после мутаций"
    assert card.description == "исходная"
    assert float(card.paid_amount) == 0.0


@pytest.mark.money
def test_patch_rejects_payment_status(client, db, manager, make_card):
    """payment_status в общем PATCH → 400 даже единственным полем."""
    _, h = manager
    card = make_card(title="Сделка", payment_status="Не оплачен")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"payment_status": "Оплачен"})

    assert r.status_code == 400, r.text
    assert "Оплату меняйте" in r.json()["detail"]
    _reload(db, card)
    assert card.payment_status == "Не оплачен"


@pytest.mark.access
def test_general_patch_ignores_status_field(client, db, manager, make_card):
    """Статус доски в общем PATCH не поле схемы: значение игнорируется,
    карточка остаётся в своей колонке (у доски свой эндпоинт статуса)."""
    _, h = manager
    card = make_card(status="В работе")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"status": "Сборка"})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.status == "В работе"


# ---------------------------------------------------------------------------
# 2. owner_id
# ---------------------------------------------------------------------------

@pytest.mark.access
def test_owner_id_assigned(client, db, manager, make_user, make_card):
    """Назначение владельца — пользователь своего тенанта."""
    _, h = manager
    new_owner, _ = make_user("manager", username="новый_владелец")
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"owner_id": new_owner.id})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.owner_id == new_owner.id
    assert r.json()["owner"]["username"] == "новый_владелец"


@pytest.mark.access
def test_owner_id_unknown_returns_404(client, db, manager, make_card):
    """Несуществующий владелец → 404, карточка не тронута."""
    _, h = manager
    card = make_card(title="Сделка", owner_id=None)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"owner_id": 987654, "title": "Новое"})

    assert r.status_code == 404, r.text
    assert "не найден" in r.json()["detail"]
    _reload(db, card)
    assert card.title == "Сделка"
    assert card.owner_id is None


@pytest.mark.access
def test_owner_id_foreign_tenant_returns_404(client, db, manager, make_card,
                                             two_tenant_users):
    """Владелец чужого тенанта не виден: 404, а не 500 по внешнему ключу."""
    _, h = manager
    card = make_card(title="Сделка", owner_id=None)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"owner_id": two_tenant_users["second_user"].id})

    assert r.status_code == 404, r.text
    _reload(db, card)
    assert card.owner_id is None


@pytest.mark.access
def test_owner_id_null_clears_owner(client, db, manager, make_user, make_card):
    """owner_id=null — штатное «отпустить сделку»."""
    _, h = manager
    owner, _ = make_user("manager", username="прежний")
    card = make_card(title="Сделка", owner_id=owner.id)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"owner_id": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.owner_id is None


# ---------------------------------------------------------------------------
# 3. client_id
# ---------------------------------------------------------------------------

@pytest.mark.access
def test_client_id_assigned(client, db, manager, make_card):
    """Клиент своего тенанта ставится."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"client_id": cl.id})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.client_id == cl.id


@pytest.mark.access
def test_client_id_unknown_returns_404(client, db, manager, make_card):
    """Несуществующий клиент → 404 без мутаций."""
    _, h = manager
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"client_id": 987654, "total_amount": 700.0})

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Клиент не найден"
    _reload(db, card)
    assert card.client_id is None
    assert float(card.total_amount) == 0.0


@pytest.mark.access
def test_client_id_foreign_tenant_returns_404(client, db, manager, make_card,
                                              two_tenant_users):
    """Клиент чужого тенанта → 404 (тот же ответ, что на несуществующего)."""
    _, h = manager
    foreign = _make_client(db, "Чужой клиент",
                           tenant_id=two_tenant_users["second_tenant_id"])
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"client_id": foreign.id})

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Клиент не найден"
    _reload(db, card)
    assert card.client_id is None


# ---------------------------------------------------------------------------
# 4. tag_ids
# ---------------------------------------------------------------------------

@pytest.mark.access
def test_tag_ids_applied(client, db, manager, make_card):
    """Корректные теги применяются, и ответ их возвращает."""
    _, h = manager
    first = _make_tag(db, "Срочно")
    second = _make_tag(db, "Оптовый")
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"tag_ids": [first.id, second.id]})

    assert r.status_code == 200, r.text
    assert sorted(t["name"] for t in r.json()["tags"]) == ["Оптовый", "Срочно"]
    _reload(db, card)
    assert sorted(t.id for t in card.tags) == sorted([first.id, second.id])


@pytest.mark.access
def test_tag_ids_cleared_by_empty_list(client, db, manager, make_card):
    """Пустой список тегов — штатное снятие меток."""
    _, h = manager
    tag = _make_tag(db, "Срочно")
    card = make_card(title="Сделка")
    db.query(models.CardTag).filter(models.CardTag.card_id == card.id).delete()
    db.add(models.CardTag(card_id=card.id, tag_id=tag.id))
    db.commit()

    r = client.patch(f"/cards/{card.id}", headers=h, json={"tag_ids": []})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.tags == []


@pytest.mark.access
def test_tag_ids_unknown_returns_404(client, db, manager, make_card):
    """Несуществующий тег → 404, прежние метки остаются."""
    _, h = manager
    tag = _make_tag(db, "Срочно")
    card = make_card(title="Сделка")
    db.add(models.CardTag(card_id=card.id, tag_id=tag.id))
    db.commit()

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"tag_ids": [tag.id, 987654]})

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Тег не найден"
    _reload(db, card)
    assert [t.id for t in card.tags] == [tag.id]


@pytest.mark.access
def test_tag_ids_foreign_tenant_returns_404(client, db, manager, make_card,
                                            two_tenant_users):
    """Тег чужого тенанта → 404, метки карточки не меняются."""
    _, h = manager
    own = _make_tag(db, "Свой тег")
    foreign = _make_tag(db, "Чужой тег",
                        tenant_id=two_tenant_users["second_tenant_id"])
    card = make_card(title="Сделка")
    db.add(models.CardTag(card_id=card.id, tag_id=own.id))
    db.commit()

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"tag_ids": [own.id, foreign.id]})

    assert r.status_code == 404, r.text
    _reload(db, card)
    assert [t.id for t in card.tags] == [own.id]


# ---------------------------------------------------------------------------
# 5. sender_email
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "   "])
def test_sender_email_blank_saved_as_null(client, db, manager, make_card, value):
    """Пустая строка и строка из пробелов = «адреса нет» → NULL, не ''."""
    _, h = manager
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"sender_email": value})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.sender_email is None


def test_sender_email_normal_value_saved(client, db, manager, make_card):
    """Обычный адрес сохраняется как есть (формат сервер не режет)."""
    _, h = manager
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"sender_email": "  zakaz@example.by  "})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.sender_email == "zakaz@example.by"


def test_sender_email_too_long_rejected(client, db, manager, make_card):
    """Адрес длиннее, чем колонка, → 400 без мутаций (обрезать нельзя:
    сохранился бы заведомо чужой адрес)."""
    _, h = manager
    card = make_card(title="Сделка", description="исходная")
    limit = models.Card.__table__.columns["sender_email"].type.length
    too_long = "a" * (limit + 1)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Новое", "sender_email": too_long})

    assert r.status_code == 400, r.text
    assert str(limit) in r.json()["detail"]
    _reload(db, card)
    assert card.title == "Сделка"
    assert card.description == "исходная"
    assert card.sender_email is None


def test_sender_email_at_limit_accepted(client, db, manager, make_card):
    """Ровно 255 символов проходят: граница включительная."""
    _, h = manager
    card = make_card(title="Сделка")
    limit = models.Card.__table__.columns["sender_email"].type.length
    at_limit = "a" * limit

    r = client.patch(f"/cards/{card.id}", headers=h, json={"sender_email": at_limit})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.sender_email == at_limit


# ---------------------------------------------------------------------------
# 6, 8. Каскады по транзакциям карточки
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_title_change_cascades_to_transactions(client, db, manager, make_card,
                                              make_transaction):
    """Смена названия переписывает company_name у всех транзакций карточки."""
    _, h = manager
    card = make_card(title="Старое название")
    remainder = make_transaction(card, amount=600.0)
    invoice = make_transaction(card, amount=400.0, invoice_number="ТН-1")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"title": "Новое название"})

    assert r.status_code == 200, r.text
    _reload(db, card, remainder, invoice)
    assert card.title == "Новое название"
    assert remainder.company_name == "Новое название"
    assert invoice.company_name == "Новое название"


@pytest.mark.integrity
def test_store_location_change_cascades_to_transactions(client, db, manager,
                                                        make_card, make_transaction):
    """Смена склада сделки обновляет склад у всех её транзакций, включая
    выписанные накладные: сделка ведётся с одного склада (решение Н4)."""
    _, h = manager
    card = make_card(title="Сделка", store_location=STORE)
    remainder = make_transaction(card, amount=600.0, store_location=STORE)
    invoice = make_transaction(card, amount=400.0, invoice_number="ТН-1",
                               store_location=STORE)
    document = make_transaction(card, amount=50.0, is_document=True,
                                invoice_number="ТН-Д", store_location=STORE)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"store_location": "Независимость 42"})

    assert r.status_code == 200, r.text
    _reload(db, card, remainder, invoice, document)
    assert card.store_location == "Независимость 42"
    assert remainder.store_location == "Независимость 42"
    assert invoice.store_location == "Независимость 42"
    assert document.store_location == "Независимость 42"


# ---------------------------------------------------------------------------
# 7. Смена total_amount
# ---------------------------------------------------------------------------

@pytest.mark.money
def test_total_change_recomputes_only_remainder(client, db, manager, make_card,
                                                make_transaction):
    """Пересчитывается только запись-остаток: накладные и документы
    сохраняют свои суммы (иначе реестр расходился бы с напечатанными ТН)."""
    _, h = manager
    card = make_card(title="Сделка", status="Сборка", total_amount=1000.0)
    remainder = make_transaction(card, amount=600.0)
    invoice = make_transaction(card, amount=400.0, invoice_number="ТН-1")
    document = make_transaction(card, amount=50.0, is_document=True,
                                invoice_number="ТН-Д")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 1200.0})

    assert r.status_code == 200, r.text
    _reload(db, card, remainder, invoice, document)
    assert float(card.total_amount) == 1200.0
    assert float(remainder.amount) == 800.0, "остаток = сумма сделки − выписанное"
    assert float(invoice.amount) == 400.0, "накладная не пересчитывается"
    assert float(document.amount) == 50.0, "документ не пересчитывается"


@pytest.mark.money
def test_total_equal_issued_deletes_remainder(client, db, manager, make_card,
                                              make_transaction):
    """Новая сумма равна уже выписанному → остатка больше нет, запись
    удаляется, а не обнуляется."""
    _, h = manager
    card = make_card(title="Сделка", status="Сборка", total_amount=1000.0)
    remainder = make_transaction(card, amount=600.0)
    make_transaction(card, amount=400.0, invoice_number="ТН-1")
    remainder_id = remainder.id

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 400.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert _remainder(db, card.id) is None
    assert db.query(models.Transaction).filter(
        models.Transaction.id == remainder_id).first() is None


@pytest.mark.money
def test_total_creates_remainder_in_sborka(client, db, manager, make_card):
    """В «Сборке» правка суммы создаёт запись-остаток: сделка появляется
    в реестре оплат, статус «Сборка» решение менеджера не трогает."""
    _, h = manager
    card = make_card(title="Сделка", status="Сборка", total_amount=0.0,
                     store_location=STORE)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 500.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    rest = _remainder(db, card.id)
    assert rest is not None, "запись-остаток не создана"
    assert float(rest.amount) == 500.0
    assert rest.company_name == card.title
    assert rest.store_location == STORE
    assert card.status == "Сборка"


@pytest.mark.money
def test_total_does_not_create_remainder_in_new_request(client, db, manager,
                                                        make_card):
    """В «Новом запросе» остаток не создаётся: правка суммы не должна
    протаскивать сделку в реестр оплат."""
    _, h = manager
    card = make_card(title="Сделка", status="Новый запрос", total_amount=0.0)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 500.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert _txs(db, card.id) == []
    assert card.status == "Новый запрос"


@pytest.mark.money
def test_total_recomputes_status_for_na_spisanie(client, db, manager, make_card,
                                                 make_transaction):
    """«На списание» → «Закрыто», когда остатка к выписке не осталось."""
    _, h = manager
    card = make_card(title="Сделка", status="На списание", total_amount=1000.0)
    make_transaction(card, amount=600.0)                      # запись-остаток
    make_transaction(card, amount=400.0, invoice_number="ТН-1")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 400.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.status == "Закрыто"
    assert _remainder(db, card.id) is None


@pytest.mark.money
def test_total_recomputes_status_for_closed(client, db, manager, make_card,
                                            make_transaction):
    """«Закрыто» → правка суммы вверх возвращает сделку в «На списание»
    и создаёт запись-остаток; «Сборку» не трогаем."""
    _, h = manager
    card = make_card(title="Сделка", status="Закрыто", total_amount=1000.0)
    make_transaction(card, amount=1000.0, invoice_number="ТН-1")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 1500.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    rest = _remainder(db, card.id)
    assert rest is not None, "остаток не создан"
    assert float(rest.amount) == 500.0
    assert card.status == "На списание"


@pytest.mark.money
def test_total_recomputes_writeoff_group_total(client, db, manager, make_card):
    """Правка суммы сделки пересчитывает итог группы списаний: на доске
    списания и в акте должно висеть actual'ное число, а не старое."""
    _, h = manager
    group = models.WriteoffGroup(name="Группа", total_amount=0.0, tenant_id=101)
    db.add(group)
    db.commit()
    db.refresh(group)
    first = make_card(title="Сделка 1", total_amount=100.0, writeoff_group_id=group.id)
    second = make_card(title="Сделка 2", total_amount=200.0, writeoff_group_id=group.id)

    r = client.patch(f"/cards/{second.id}", headers=h, json={"total_amount": 500.0})

    assert r.status_code == 200, r.text
    _reload(db, group)
    assert float(group.total_amount) == 600.0


# ---------------------------------------------------------------------------
# 9. description → «Комментарий»
# ---------------------------------------------------------------------------

def test_description_change_logs_comment(client, db, manager, make_card):
    """Новый текст заметки попадает в ленту отдельной записью «Комментарий»."""
    _, h = manager
    card = make_card(title="Сделка", description="старое")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"description": "Клиент перезвонит в 15:00"})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.description == "Клиент перезвонит в 15:00"
    logs = _logs(db, card.id)
    assert [l.action for l in logs] == ["Комментарий"]
    assert logs[0].details == "Клиент перезвонит в 15:00"


def test_description_unchanged_makes_no_comment_log(client, db, manager, make_card):
    """Тот же текст заново не логируется: лента не засоряется."""
    _, h = manager
    card = make_card(title="Сделка", description="без изменений")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"description": "без изменений"})

    assert r.status_code == 200, r.text
    assert _logs(db, card.id) == []


# ---------------------------------------------------------------------------
# 10. Защита клиента от затирания
# ---------------------------------------------------------------------------

def test_client_id_null_alone_clears_client(client, db, manager, make_card):
    """Старый фронт снимает клиента, посылая ТОЛЬКО {client_id: null}."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    card = make_card(title="Сделка", client_id=cl.id)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"client_id": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.client_id is None


def test_client_id_null_in_full_save_ignored(client, db, manager, make_card):
    """null в составе полного сохранения без clear_client игнорируется —
    v2 не должен затирать клиента «по умолчанию»."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    card = make_card(title="Сделка", client_id=cl.id, total_amount=100.0)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Переименованная", "total_amount": 300.0,
                           "client_id": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.client_id == cl.id, "клиент затёрт null-ом из полного сохранения"
    assert card.title == "Переименованная"
    assert float(card.total_amount) == 300.0


def test_clear_client_true_clears_client_in_full_save(client, db, manager, make_card):
    """Явный флаг clear_client=true снимает клиента даже в полном сохранении."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    card = make_card(title="Сделка", client_id=cl.id)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"title": "Переименованная", "clear_client": True,
                           "client_id": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.client_id is None
    assert card.title == "Переименованная"


# ---------------------------------------------------------------------------
# 11. Лента активности по общему PATCH
# ---------------------------------------------------------------------------

def test_activity_log_details_are_readable(client, db, manager, make_user, make_card):
    """Смена суммы, владельца и адреса отправителя попадает в одну запись
    «Изменение» с читаемыми подписями."""
    _, h = manager
    owner, _ = make_user("manager", username="владелец_сделки")
    card = make_card(title="Сделка", total_amount=100.0)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"total_amount": 500.0, "owner_id": owner.id,
                           "sender_email": "zakaz@example.by"})

    assert r.status_code == 200, r.text
    logs = _logs(db, card.id)
    assert [l.action for l in logs] == ["Изменение"]
    details = logs[0].details
    assert "Сумма: 100,00 → 500,00 BYN" in details
    assert "Ответственный: — → владелец_сделки" in details
    assert "Почта отправителя: — → zakaz@example.by" in details


def test_activity_log_records_client_and_store_change(client, db, manager, make_card):
    """Смена клиента и магазина видна в ленте именами, а не id."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    card = make_card(title="Сделка", store_location=STORE)

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"client_id": cl.id, "store_location": "Независимость 42"})

    assert r.status_code == 200, r.text
    details = _details_joined(db, card.id)
    assert f"Клиент: — → Клиент А" in details
    assert f"Магазин: {STORE} → Независимость 42" in details


def test_no_activity_log_when_nothing_changed(client, db, manager, make_card):
    """Патч без фактических изменений ленту не засоряет."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 100.0})

    assert r.status_code == 200, r.text
    assert _logs(db, card.id) == []


# ---------------------------------------------------------------------------
# 12. Версионирование
# ---------------------------------------------------------------------------

def test_version_created_after_patch(client, db, manager, make_card):
    """После PATCH в истории карточки появляется запись change_type=update
    со снимком актуальных значений."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)

    r = client.patch(f"/cards/{card.id}", headers=h, json={"title": "Новое название"})

    assert r.status_code == 200, r.text
    rv = client.get(f"/kanban/cards/{card.id}/versions", headers=h)
    assert rv.status_code == 200, rv.text
    versions = rv.json()
    assert versions, "версия не записана"
    latest = versions[0]
    assert latest["change_type"] == "update"
    assert latest["data"]["title"] == "Новое название"
    assert latest["version"] == 1


# ---------------------------------------------------------------------------
# 13-14. Закрытие из баланса: проверки до мутаций
# ---------------------------------------------------------------------------

@pytest.mark.money
def test_from_balance_requires_client(client, db, manager, make_card):
    """Закрытие из баланса сделки без клиента невозможно."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=300.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 300.0, "from_balance": True})

    assert r.status_code == 400, r.text
    assert "только для сделок с клиентом" in r.json()["detail"]
    _reload(db, card)
    assert float(card.paid_amount) == 0.0


@pytest.mark.money
def test_from_balance_over_available_rejected(client, db, manager, make_card):
    """Списание сверх кредита клиента → 400 с суммами; карточка и баланс
    не изменились (иначе кредит уходит в минус)."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    _make_payment(db, cl.id, 100.0)
    card = make_card(title="Сделка", client_id=cl.id, total_amount=500.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 150.0, "from_balance": True})

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Баланса клиента не хватает" in detail
    assert "100.0 BYN" in detail, detail
    assert "150.0 BYN" in detail, detail

    _reload(db, card)
    assert float(card.paid_amount) == 0.0
    assert card.payment_status == "Не оплачен"

    balance = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert float(balance["payments_total"]) == 100.0, "приход не изменился"
    assert float(balance["paid_on_cards"]) == 0.0, "покрытие не выросло"
    # balance в ответе = приход − сумма сделок клиента (deals_total = 500).
    assert float(balance["balance"]) == round(
        float(balance["payments_total"]) - float(balance["deals_total"]), 2)


# ---------------------------------------------------------------------------
# 15. Успешное закрытие из баланса
# ---------------------------------------------------------------------------

@pytest.mark.money
def test_from_balance_closes_card(client, db, manager, make_card):
    """Успешное закрытие из баланса: покрытие выросло, статус «Оплачен»,
    а в ленте видно и сам статус, и сумму, и источник денег."""
    _, h = manager
    cl = _make_client(db, "Клиент А")
    _make_payment(db, cl.id, 500.0)
    card = make_card(title="Сделка", client_id=cl.id, total_amount=300.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 300.0, "from_balance": True})

    assert r.status_code == 200, r.text
    assert r.json()["payment_status"] == "Оплачен"
    _reload(db, card)
    assert float(card.paid_amount) == 300.0
    assert card.payment_status == "Оплачен"

    details = _details_joined(db, card.id)
    assert "Оплата: Не оплачен → Оплачен" in details
    assert "Оплачено: 0,00 → 300,00 BYN" in details
    assert "Закрыто из баланса клиента на 300,00 BYN" in details

    # Приход в кассе закрытием из баланса НЕ меняется: покрытие — не приход.
    balance = client.get(f"/clients/{cl.id}/balance", headers=h).json()
    assert float(balance["payments_total"]) == 500.0
    assert float(balance["paid_on_cards"]) == 300.0


# ---------------------------------------------------------------------------
# 16-17. Статус оплаты
# ---------------------------------------------------------------------------

@pytest.mark.money
@pytest.mark.parametrize("total, paid, expected", [
    (1000.0, 0.0, "Не оплачен"),
    (1000.0, 400.0, "Частично"),
    (1000.0, 1000.0, "Оплачен"),
    (1000.0, 1200.0, "Оплачен"),      # переплата — тоже оплачено
    (0.0, 0.0, "Не оплачен"),          # нулевая сумма сделки — эталона нет
])
def test_payment_status_auto(client, db, manager, make_card, total, paid, expected):
    """Без явного payment_status статус выводится из суммы сделки."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=total, paid_amount=paid)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": paid})

    assert r.status_code == 200, r.text
    assert r.json()["payment_status"] == expected


@pytest.mark.money
def test_explicit_payment_status_wins(client, db, manager, make_card):
    """Явный payment_status в запросе важнее автоопределения по сумме."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 1000.0, "payment_status": "Частично"})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.payment_status == "Частично"


# ---------------------------------------------------------------------------
# 18-19. Отсрочка и сумма оплаты
# ---------------------------------------------------------------------------

def test_payment_due_date_null_clears_deferral(client, db, manager, make_card):
    """Явный null стирает отсрочку — оба фронта шлют его при статусе
    «Не оплачен»."""
    _, h = manager
    card = make_card(title="Сделка",
                     payment_due_date=datetime.date(2026, 10, 1))

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"payment_due_date": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.payment_due_date is None
    assert "Отсрочка до: 01.10.2026 → —" in _details_joined(db, card.id)


def test_payment_due_date_absent_is_untouched(client, db, manager, make_card):
    """Поле не пришло в запросе — отсрочка не трогается."""
    _, h = manager
    card = make_card(title="Сделка",
                     payment_due_date=datetime.date(2026, 10, 1))

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 100.0})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert card.payment_due_date == datetime.date(2026, 10, 1)
    assert "Отсрочка до" not in _details_joined(db, card.id)


@pytest.mark.money
def test_negative_paid_amount_rejected(client, db, manager, make_card):
    """Отрицательная оплата отсекается схемой запроса (422) и в карточку
    не попадает: в минус оплата не пишется. Проверка max(0, ...) в роутере —
    вторая линия защиты для внутренних вызовов, через API недостижима."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, paid_amount=100.0)

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": -50.0})

    assert r.status_code == 422, r.text
    assert "не может быть отрицательной" in r.text
    _reload(db, card)
    assert float(card.paid_amount) == 100.0


@pytest.mark.money
def test_null_paid_amount_keeps_current(client, db, manager, make_card):
    """paid_amount=null — не команда «обнулить», текущее значение сохраняется."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, paid_amount=250.0,
                     payment_status="Частично")

    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": None})

    assert r.status_code == 200, r.text
    _reload(db, card)
    assert float(card.paid_amount) == 250.0
    assert card.payment_status == "Частично"


# ---------------------------------------------------------------------------
# 20. Авторизация и тенанты
# ---------------------------------------------------------------------------

def test_patch_without_token_401(client, db, make_card):
    """Без токена оба PATCH недоступны."""
    card = make_card(title="Сделка")

    assert client.patch(f"/cards/{card.id}", json={"title": "X"}).status_code == 401
    assert client.patch(f"/cards/{card.id}/payment",
                        json={"paid_amount": 1}).status_code == 401


def test_payment_patch_without_token_401(client, db, make_card):
    """Явная проверка /payment без токена (сообщение не путается с общим)."""
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}/payment", json={"paid_amount": 1.0})

    assert r.status_code == 401, r.text


def test_patch_foreign_tenant_card_404(client, db, manager, make_card,
                                       two_tenant_users):
    """Токен чужого тенанта не видит сделку: 404 на оба PATCH."""
    _, _ = manager
    card = make_card(title="Сделка")
    foreign = two_tenant_users["second_headers"]

    r = client.patch(f"/cards/{card.id}", headers=foreign, json={"title": "X"})
    assert r.status_code == 404, r.text

    r2 = client.patch(f"/cards/{card.id}/payment", headers=foreign,
                      json={"paid_amount": 100.0})
    assert r2.status_code == 404, r2.text

    _reload(db, card)
    assert card.title == "Сделка"
    assert float(card.paid_amount) == 0.0
