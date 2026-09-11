"""Приоритет B — накладные, дубли, НДС.

Семантика подтверждена данными боевой БД (запрос read-only, 2026-09-11)
и пояснением владельца: сценарии «2 карточки — 1 накладная» и «1 карточка —
2 накладных» внедрены и являются ШТАТНЫМИ, а не грязью.

Факты из прода:
- один invoice_number на нескольких карточках: 0002314 (2), ТН0002236 (3),
  ТТН 4881030 (2), ТТН4881017 (2), ТТН4881032 (2). Суммы разные и каждая
  равна итогу своей карточки — это доли одной физической накладной.
- несколько накладных на одной карточке: 493, 525, 751 — номера разные.
- дублей (card_id, invoice_number) — ноль.

Отсюда: уникальный индекс допустим ТОЛЬКО по (card_id, invoice_number).
Индекс по одному invoice_number сломал бы пять рабочих накладных.
"""
import pytest

import models

STORE = "Матусевича 72"


def _reload(db):
    db.expire_all()


def _trigger(client, headers, card_id):
    return client.post(f"/payments/trigger_from_card/{card_id}", headers=headers,
                       json={"store_location": STORE})


def _issue(client, headers, card_id, number, amount):
    return client.post(f"/payments/cards/{card_id}/issue-invoice", headers=headers,
                       json={"invoice_number": number, "amount": amount,
                             "invoice_date": "2026-09-01", "store_location": STORE})


def _ledger(db, card_id):
    """Записи реестра по сделке, без копий-документов."""
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,      # noqa: E712
    ).order_by(models.Transaction.id).all()


# ---------------------------------------------------------------------------
# Штатные сценарии — их нельзя ломать
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("amounts", [(7.44, 157.53), (31.20, 1722.39), (315.00, 6746.56)])
def test_one_invoice_across_two_cards_is_allowed(client, manager, db, make_card, amounts):
    """«2 карточки — 1 накладная»: один номер на две сделки, доли разные."""
    _, h = manager
    c1 = make_card(title="Сделка 1", total_amount=amounts[0], status="Сборка")
    c2 = make_card(title="Сделка 2", total_amount=amounts[1], status="Сборка")
    assert _trigger(client, h, c1.id).status_code == 200
    assert _trigger(client, h, c2.id).status_code == 200

    r1 = _issue(client, h, c1.id, "ТТН9990001", amounts[0])
    r2 = _issue(client, h, c2.id, "ТТН9990001", amounts[1])
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    _reload(db)
    assert len([t for t in _ledger(db, c1.id) if t.invoice_number == "ТТН9990001"]) == 1
    assert len([t for t in _ledger(db, c2.id) if t.invoice_number == "ТТН9990001"]) == 1


def test_one_card_with_two_invoices_is_allowed(client, manager, db, make_card):
    """«1 карточка — 2 накладных»: частичные отгрузки разными номерами."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=8577.30, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "0002306", 75.60).status_code == 200
    assert _issue(client, h, card.id, "ТН0002269", 6851.22).status_code == 200

    _reload(db)
    numbers = [t.invoice_number for t in _ledger(db, card.id) if t.invoice_number]
    assert numbers == ["0002306", "ТН0002269"]
    rest = [t for t in _ledger(db, card.id) if not t.invoice_number]
    assert len(rest) == 1
    assert round(float(rest[0].amount), 2) == 1650.48


# ---------------------------------------------------------------------------
# Настоящие дубли
# ---------------------------------------------------------------------------

@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: одна и та же накладная на ОДНОЙ сделке выписывается "
                          "сколько угодно раз — в реестре появляется вторая строка с тем же "
                          "номером. Копия в «Документах» при этом корректно не дублируется "
                          "(_issue_document), а вот запись реестра ничем не защищена: "
                          "уникальности по (card_id, invoice_number) нет ни в БД, ни в коде.")
def test_same_invoice_number_on_same_card_is_rejected(client, manager, db, make_card):
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "ТТН0001", 300.0).status_code == 200
    second = _issue(client, h, card.id, "ТТН0001", 200.0)
    assert second.status_code == 400, f"ожидался отказ, получено {second.status_code}"

    _reload(db)
    same = [t for t in _ledger(db, card.id) if t.invoice_number == "ТТН0001"]
    assert len(same) == 1


def test_invoice_number_matching_ignores_spaces_and_prefixes(client, manager, db, make_card):
    """«ТТН 4881030» и «ТТН4881030» — одна накладная: сравнение только по цифрам.

    В боевых данных оба написания уже встречаются, поэтому нормализация обязательна.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН4881030", 400.0).status_code == 200

    _reload(db)
    ledger = [t for t in _ledger(db, card.id) if t.invoice_number][0]
    docs_before = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == True,       # noqa: E712
    ).count()
    assert docs_before == 1

    # удаляем запись реестра — её копия в «Документах» должна уйти вместе с ней,
    # хотя номер у копии совпадает посимвольно; отдельная проверка нормализации ниже
    assert client.delete(f"/payments/transactions/{ledger.id}", headers=h).status_code == 200

    _reload(db)
    docs_after = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == True,       # noqa: E712
    ).count()
    assert docs_after == 0, "копия-документ должна удаляться вместе с накладной"


@pytest.mark.parametrize("status", ["В работе", "Сборка", "На списание"])
def test_twin_found_by_digits_when_spelling_differs(client, manager, db, make_card, status):
    """Прямая проверка _norm: номер с пробелом находит близнеца без пробела.

    Проверяется во всех трёх статусах: раньше в «Сборке» тест был невозможен,
    потому что удаление падало в 500 на второй вставке остатка (починено в Фазе 2).
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status=status)
    card_id = card.id

    ledger_tx = models.Transaction(card_id=card_id, company_name="Сделка", amount=400.0,
                                   is_document=False, invoice_number="ТТН4881030")
    doc_tx = models.Transaction(card_id=card_id, company_name="Сделка", amount=400.0,
                                is_document=True, invoice_number="ТТН 4881030")
    db.add_all([ledger_tx, doc_tx])
    db.commit()
    ledger_id, doc_id = ledger_tx.id, doc_tx.id

    assert client.delete(f"/payments/transactions/{ledger_id}", headers=h).status_code == 200

    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.id == doc_id).first() is None, \
        "документ с тем же номером в другом написании должен определиться как близнец"


def test_delete_invoice_from_card_in_assembly_does_not_crash(client, manager, db, make_card):
    """Починено в Фазе 2 (2026-09-11).

    SessionLocal работает с autoflush=False, поэтому при удалении накладной
    со сделки в «Сборке» срабатывали две вставки остатка подряд: ветка возврата
    суммы делала session.add(...), затем SELECT в ensure_registry_remainder
    не видел незафиксированную строку и добавлял вторую. Частичный unique-индекс
    uq_remainder_per_card (миграция 0004) отвечал IntegrityError → 500.
    Теперь ensure_registry_remainder делает flush() перед SELECT.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    card_id = card.id

    tx = models.Transaction(card_id=card_id, company_name="Сделка", amount=400.0,
                            is_document=False, invoice_number="ТТН4881099")
    db.add(tx)
    db.commit()
    tx_id = tx.id

    r = client.delete(f"/payments/transactions/{tx_id}", headers=h)
    assert r.status_code == 200, f"500 при удалении накладной со сделки в «Сборке»: {r.text}"

    _reload(db)
    rests = [t for t in _ledger(db, card_id) if not t.invoice_number]
    assert len(rests) == 1, f"записей-остатков должно быть ровно одна, найдено {len(rests)}"


# ---------------------------------------------------------------------------
# Бот: приём накладных и проверка дублей
# ---------------------------------------------------------------------------

def test_bot_endpoints_reject_missing_and_wrong_token(client):
    # значение заголовка обязано быть ASCII: httpx не кодирует кириллицу в заголовках
    assert client.get("/nakladnye/bot/check-duplicate?doc_number=1").status_code == 403
    assert client.get("/nakladnye/bot/check-duplicate?doc_number=1",
                      headers={"X-Bot-Token": "wrong-token"}).status_code == 403
    assert client.post("/nakladnye/bot/create", json={}).status_code == 403


def test_bot_check_duplicate_finds_existing(client, bot_headers, db):
    db.add(models.Nakladnaya(doc_type="ТТН", doc_series="АБ", doc_number="4881030",
                             amount=100.0, supplier_name="Поставщик"))
    db.commit()

    r = client.get("/nakladnye/bot/check-duplicate",
                   params={"doc_series": "АБ", "doc_number": "4881030"},
                   headers=bot_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is True
    assert body["id"]


@pytest.mark.parametrize("series,number", [
    ("аб", "ТТН 4881030"),     # другой регистр серии + префикс и пробел в номере
    ("АБ", "4881030"),         # только цифры
    ("  АБ  ", "ТТН4881030"),  # пробелы в серии
])
def test_bot_check_duplicate_normalizes(client, bot_headers, db, series, number):
    """Серия приводится к strip+upper, номер — только цифры."""
    db.add(models.Nakladnaya(doc_type="ТТН", doc_series="АБ", doc_number="4881030",
                             amount=100.0, supplier_name="П"))
    db.commit()

    r = client.get("/nakladnye/bot/check-duplicate",
                   params={"doc_series": series, "doc_number": number}, headers=bot_headers)
    assert r.status_code == 200
    assert r.json()["duplicate"] is True, f"не распознал дубль для {series!r}/{number!r}"


def test_bot_check_duplicate_no_false_positive(client, bot_headers, db):
    db.add(models.Nakladnaya(doc_series="АБ", doc_number="4881030", supplier_name="П"))
    db.commit()

    r = client.get("/nakladnye/bot/check-duplicate",
                   params={"doc_series": "АБ", "doc_number": "4881031"}, headers=bot_headers)
    assert r.json()["duplicate"] is False

    r2 = client.get("/nakladnye/bot/check-duplicate",
                    params={"doc_series": "ВГ", "doc_number": "4881030"}, headers=bot_headers)
    assert r2.json()["duplicate"] is False, "та же цифровая часть, но другая серия — не дубль"


def test_bot_check_duplicate_without_number(client, bot_headers):
    r = client.get("/nakladnye/bot/check-duplicate", headers=bot_headers)
    assert r.status_code == 200
    assert r.json()["duplicate"] is False


@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ (TOCTOU): bot/create и POST /nakladnye не проверяют "
                          "дубли вообще, а бот делает check-duplicate и create двумя "
                          "отдельными запросами. Уникальности на уровне БД нет, поэтому "
                          "параллельная отправка одного документа плодит две записи.")
def test_bot_create_rejects_duplicate(client, bot_headers, db):
    payload = {"doc_type": "ТТН", "doc_series": "АБ", "doc_number": "4881030",
               "amount": 100.0, "vat_amount": 16.67, "supplier_name": "П", "store": STORE}
    assert client.post("/nakladnye/bot/create", json=payload, headers=bot_headers).status_code == 200

    r = client.post("/nakladnye/bot/create", json=payload, headers=bot_headers)
    assert r.status_code == 400, f"дубль принят со статусом {r.status_code}"

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_bot_create_stores_products(client, bot_headers, db):
    """Регрессия на коммит 3d109d6: NakladnayaUpdate/Create больше не теряют products."""
    products = [{"name": "Светильник", "qty": 2, "price_no_vat": 50.0, "total": 100.0}]
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "АБ", "doc_number": "777",
        "amount": 120.0, "vat_amount": 20.0, "supplier_name": "П", "products": products,
    })
    assert r.status_code == 200, r.text

    _reload(db)
    nak = db.query(models.Nakladnaya).first()
    assert nak.products_json, "products не сохранились"
    import json
    assert json.loads(nak.products_json) == products


# ---------------------------------------------------------------------------
# Валидация сумм и справочников — на сервере отсутствует
# ---------------------------------------------------------------------------

@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: проверки amount >= vat_amount на сервере нет — "
                          "она живёт только в telegram-боте и там лишь меняет значения местами. "
                          "CRM-эндпоинты принимают НДС больше суммы документа.")
def test_vat_greater_than_amount_rejected(client, bot_headers):
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "1", "amount": 100.0, "vat_amount": 500.0,
    })
    assert r.status_code == 422


@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: NAKLADNYE_STATUSES и NAKLADNYE_DOC_TYPES объявлены "
                          "в schemas.py, но ни одним валидатором не используются — "
                          "в накладную можно записать произвольный status и doc_type.")
def test_garbage_status_and_doc_type_rejected(client, bot_headers):
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "2", "status": "какая-то ерунда",
        "doc_type": "НЕВЕСТЬЧТО",
    })
    assert r.status_code == 422


@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: колонка nakladnye.amount_no_vat есть в боевом DDL, "
                          "но отсутствует и в models.py, и в NakladnayaBase — клиент "
                          "физически не может её заполнить. Тот же класс дефекта, "
                          "что и починенный в 3d109d6 NakladnayaUpdate.products.")
def test_amount_no_vat_is_settable(client, manager):
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "3", "amount": 120.0, "amount_no_vat": 100.0,
    })
    assert r.status_code == 200, r.text
    assert r.json().get("amount_no_vat") == 100.0


def test_nakladnye_delete_does_not_require_admin(client, manager, db):
    """Фиксируем текущую модель доступа: удалить накладную может любой менеджер,
    тогда как DELETE /clients требует admin. Несогласованность — см. Фазу 2."""
    _, h = manager
    nak = models.Nakladnaya(doc_series="АБ", doc_number="4", supplier_name="П")
    db.add(nak)
    db.commit()
    nak_id = nak.id

    assert client.delete(f"/nakladnye/{nak_id}", headers=h).status_code == 200
    _reload(db)
    assert db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first() is None
