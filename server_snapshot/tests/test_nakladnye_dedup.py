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
- в nakladnye (14 записей) дублей по нормализованному номеру — ноль.

Отсюда две РАЗНЫЕ схемы ограничений, и их нельзя путать:

- transactions (записи реестра по сделке): дубль — это тот же номер на той же
  сделке. Защищается проверкой в issue_invoice/add_invoice по цифрам
  (_norm_invoice_number), а НЕ индексом: правило «только цифры» в выражение
  индекса SQLite не оформляется, а индекс по сырой строке пропустил бы
  «ТТН 4881030» против «ТТН4881030». Индекс по одному invoice_number
  недопустим — он сломал бы пять рабочих накладных.
- nakladnye (приём документов от магазинов): уникальность на уровне БД,
  частичный индекс uq_nakladnye_doc_key по нормализованным
  doc_series_norm/doc_number_norm (миграция 0005). Здесь гонка реальна
  (check-then-insert двумя запросами из telegram-бота), поэтому кода мало.
  Индекс частичный: записи без номера не ограничены, иначе fallback-ветка бота
  «Не распознано» сработала бы ровно один раз.
"""
import pytest
from sqlalchemy import text

import database
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
        models.Transaction.is_document == False,
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

def test_same_invoice_number_on_same_card_is_rejected(client, manager, db, make_card):
    """Починено в Фазе 2 (2026-09-12, дефект 6).

    Одна и та же накладная на ОДНОЙ сделке выписывалась сколько угодно раз:
    в реестре появлялась вторая строка с тем же номером, а issued_total
    суммировал все записи с номером, поэтому остаток к выписке считался дважды
    и сделка закрывалась раньше времени. Копия в «Документах» при этом
    корректно не дублировалась (_issue_document) — защищена теперь и запись
    реестра. Проверка в issue_invoice, до создания строки, отказ 400.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "ТТН0001", 300.0).status_code == 200
    second = _issue(client, h, card.id, "ТТН0001", 200.0)
    assert second.status_code == 400, f"ожидался отказ, получено {second.status_code}"

    _reload(db)
    same = [t for t in _ledger(db, card.id) if t.invoice_number == "ТТН0001"]
    assert len(same) == 1


def test_same_invoice_in_other_spelling_on_same_card_is_rejected(client, manager, db, make_card):
    """Дубль ловится и при другом написании того же номера.

    В боевых данных соседствуют «ТТН 4881030» и «ТТН4881030», поэтому сравнение
    идёт только по цифрам (_norm_invoice_number). Проверка по сырой строке
    пропустила бы ровно тот дубль, который встречается в жизни.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "ТТН4881030", 300.0).status_code == 200
    for variant in ("ТТН 4881030", "4881030", " ттн-4881030 "):
        r = _issue(client, h, card.id, variant, 100.0)
        assert r.status_code == 400, f"{variant!r} принят как новая накладная"

    _reload(db)
    assert len([t for t in _ledger(db, card.id) if t.invoice_number]) == 1


def test_duplicate_rejection_does_not_disturb_the_remainder(client, manager, db, make_card):
    """Отказ по дублю не портит состояние: остаток и сумма сделки на месте.

    Проверка стоит до любых мутаций, но утвердить это тестом дешевле, чем
    рассуждать: отказ в середине money-пути уже стоил нам 500 в «Сборке»
    (коммит 9bc3532).
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0002", 300.0).status_code == 200

    _reload(db)
    before = [(t.id, round(float(t.amount), 2), t.invoice_number) for t in _ledger(db, card.id)]

    assert _issue(client, h, card.id, "ТТН0002", 300.0).status_code == 400

    _reload(db)
    after = [(t.id, round(float(t.amount), 2), t.invoice_number) for t in _ledger(db, card.id)]
    assert after == before
    db.refresh(card)
    assert round(float(card.total_amount), 2) == 1000.0


def test_number_without_digits_is_compared_literally(client, manager, db, make_card):
    """Номер без цифр («б/н») сравнивается строкой, а не по пустому ключу.

    Иначе любые две записи без цифр в номере выглядели бы дублями друг друга,
    и вторая законная накладная «б/н» на той же сделке оказалась бы заблокирована.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "б/н", 100.0).status_code == 200
    assert _issue(client, h, card.id, "б/н", 100.0).status_code == 400
    # другое текстовое обозначение — не дубль
    assert _issue(client, h, card.id, "без номера", 100.0).status_code == 200


def test_invoice_number_matching_ignores_spaces_and_prefixes(client, admin, db, make_card):
    """«ТТН 4881030» и «ТТН4881030» — одна накладная: сравнение только по цифрам.

    В боевых данных оба написания уже встречаются, поэтому нормализация обязательна.
    Роль — admin: удаление записи реестра с пункта 17 (V11) админское.
    """
    _, h = admin
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН4881030", 400.0).status_code == 200

    _reload(db)
    ledger = next(t for t in _ledger(db, card.id) if t.invoice_number)
    docs_before = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == True,
    ).count()
    assert docs_before == 1

    # удаляем запись реестра — её копия в «Документах» должна уйти вместе с ней,
    # хотя номер у копии совпадает посимвольно; отдельная проверка нормализации ниже
    assert client.delete(f"/payments/transactions/{ledger.id}", headers=h).status_code == 200

    _reload(db)
    docs_after = db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == True,
    ).count()
    assert docs_after == 0, "копия-документ должна удаляться вместе с накладной"


@pytest.mark.parametrize("status", ["В работе", "Сборка", "На списание"])
def test_twin_found_by_digits_when_spelling_differs(client, admin, db, make_card, status):
    """Прямая проверка _norm: номер с пробелом находит близнеца без пробела.

    Проверяется во всех трёх статусах: раньше в «Сборке» тест был невозможен,
    потому что удаление падало в 500 на второй вставке остатка (починено в Фазе 2).
    Роль — admin: удаление записи реестра с пункта 17 (V11) админское.
    """
    _, h = admin
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


def test_delete_invoice_from_card_in_assembly_does_not_crash(client, admin, db, make_card):
    """Починено в Фазе 2 (2026-09-11).

    SessionLocal работает с autoflush=False, поэтому при удалении накладной
    со сделки в «Сборке» срабатывали две вставки остатка подряд: ветка возврата
    суммы делала session.add(...), затем SELECT в ensure_registry_remainder
    не видел незафиксированную строку и добавлял вторую. Частичный unique-индекс
    uq_remainder_per_card (миграция 0004) отвечал IntegrityError → 500.
    Теперь ensure_registry_remainder делает flush() перед SELECT.
    Роль — admin: удаление записи реестра с пункта 17 (V11) админское.
    """
    _, h = admin
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


def test_bot_token_query_parameter_is_rejected(client, bot_headers):
    response = client.get(
        "/nakladnye/bot/check-duplicate",
        params={"doc_number": "1", "bot_token": bot_headers["X-Bot-Token"]},
    )
    assert response.status_code == 403


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


def test_bot_create_rejects_duplicate(client, bot_headers, db):
    """Починено в Фазе 2 (2026-09-12, дефект 7).

    Приём был check-then-insert двумя запросами: бот спрашивал
    GET /bot/check-duplicate, затем делал POST /bot/create, а сам create дубли
    не проверял вовсе. Гонка закрыта на уровне базы — частичным уникальным
    индексом uq_nakladnye_doc_key (миграция 0005) по нормализованным
    doc_series_norm/doc_number_norm, — а IntegrityError переводится в 400.
    """
    payload = {"doc_type": "ТТН", "doc_series": "АБ", "doc_number": "4881030",
               "amount": 100.0, "vat_amount": 16.67, "supplier_name": "П", "store": STORE}
    assert client.post("/nakladnye/bot/create", json=payload, headers=bot_headers).status_code == 200

    r = client.post("/nakladnye/bot/create", json=payload, headers=bot_headers)
    assert r.status_code == 409, f"дубль принят со статусом {r.status_code}"
    body = r.json()
    # FIX 2026-09-19 (A4): тело 409 содержит existing_id
    detail = body.get("detail") if isinstance(body, dict) else None
    assert isinstance(detail, dict), "detail должен быть объектом с existing_id"
    assert "existing_id" in detail
    assert detail["existing_id"] > 0

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_unique_doc_key_index_exists(db):
    """Индекс из миграции 0005 реально создан — иначе вся защита только в коде.

    Проверка нужна отдельным тестом: без неё тесты дедупликации зелёнели бы и
    на схеме, где миграцию не накатили (conftest применяет migrations/ поверх
    DDL, но если 0005 сломается, это должно быть видно сразу, а не через
    внезапно прошедший дубль).
    """
    with database.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_nakladnye_doc_key'")).fetchall()
    assert rows, "уникальный индекс uq_nakladnye_doc_key не создан"
    ddl = rows[0][0].upper()
    assert "UNIQUE" in ddl
    # частичный: строки без номера под индекс не попадают
    assert "DOC_NUMBER_NORM IS NOT NULL" in ddl


@pytest.mark.parametrize("series,number", [
    ("АБ", "ТТН 4881030"),     # префикс и пробел в номере
    ("АБ", "4881030"),         # только цифры
    ("аб", "ТТН4881030"),      # другой регистр серии
    ("  АБ  ", "ТТН-4881030"), # пробелы в серии и разделитель в номере
])
def test_bot_create_rejects_duplicate_in_other_spelling(client, bot_headers, db,
                                                        series, number):
    """Дубль ловится и при другом написании — индекс по НОРМАЛИЗОВАННОМУ ключу.

    Ровно тот случай, который пропустил бы индекс по сырым (doc_series,
    doc_number): в боевых данных соседствуют «ТТН 4881030» и «ТТН4881030».
    """
    first = {"doc_series": "АБ", "doc_number": "4881030", "amount": 100.0,
             "supplier_name": "П"}
    assert client.post("/nakladnye/bot/create", json=first,
                       headers=bot_headers).status_code == 200

    second = dict(first, doc_series=series, doc_number=number)
    r = client.post("/nakladnye/bot/create", json=second, headers=bot_headers)
    assert r.status_code == 409, f"{series!r}/{number!r} принят как новая накладная"

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_crm_create_rejects_duplicate_too(client, manager, db):
    """Бот не единственный источник: CRM-создание защищено тем же индексом."""
    _, h = manager
    payload = {"doc_series": "АБ", "doc_number": "4881031", "amount": 100.0,
               "supplier_name": "П"}
    assert client.post("/nakladnye", headers=h, json=payload).status_code == 200

    r = client.post("/nakladnye", headers=h, json=payload)
    assert r.status_code == 409, f"дубль принят со статусом {r.status_code}"
    # FIX 2026-09-19 (A4): 409 с existing_id
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert "existing_id" in detail

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_different_series_same_number_is_not_a_duplicate(client, bot_headers, db):
    """Та же цифровая часть при другой серии — другой документ.

    Симметрично bot_check_duplicate: серия входит в ключ, поэтому без этого
    теста индекс мог бы оказаться шире правила и блокировать законные документы.
    """
    assert client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "4881032", "supplier_name": "П",
    }).status_code == 200

    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "ВГ", "doc_number": "4881032", "supplier_name": "П",
    })
    assert r.status_code == 200, r.text

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 2


def test_nakladnye_without_number_are_unlimited(client, bot_headers, db):
    """Записи без номера не ограничены: индекс частичный.

    Это штатный сценарий, а не дыра — fallback-ветка telegram-бота при
    нераспознанном документе создаёт запись {"supplier_name": "Не распознано"}
    без серии и номера, и таких записей может быть несколько. Полная
    уникальность оставила бы магазин без приёма документа.
    """
    payload = {"supplier_name": "Не распознано", "store": STORE, "status": "new"}
    for _ in range(3):
        r = client.post("/nakladnye/bot/create", headers=bot_headers, json=payload)
        assert r.status_code == 200, r.text

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 3
    for nak in db.query(models.Nakladnaya).all():
        assert nak.doc_number_norm is None


def test_update_onto_existing_number_is_rejected(client, manager, db):
    """Правка номера на уже занятый — тоже 400, а не IntegrityError/500.

    Заодно это проверка слушателя before_update: производные ключи обязаны
    пересчитываться при правке, иначе индекс сравнивал бы устаревшие значения.
    """
    _, h = manager
    for _i, number in enumerate(["4881040", "4881041"]):
        r = client.post("/nakladnye", headers=h, json={
            "doc_series": "АБ", "doc_number": number, "supplier_name": "П"})
        assert r.status_code == 200, r.text

    _reload(db)
    rows = db.query(models.Nakladnaya).order_by(models.Nakladnaya.id).all()
    victim, target = rows[0], rows[1]

    r = client.patch(f"/nakladnye/{victim.id}", headers=h,
                     json={"doc_number": "ТТН 4881041"})
    assert r.status_code == 409, r.text

    _reload(db)
    fresh = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == victim.id).first()
    assert fresh.doc_number == "4881040", "отклонённая правка всё равно записалась"
    # target раньше вычислялся и не использовался — проверяем и его: отклонённая
    # правка не должна задеть запись, чей номер оказался «занятым».
    untouched = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == target.id).first()
    assert untouched.doc_number == "4881041", "посторонняя запись изменилась"


def test_update_renumbers_and_keeps_key_in_sync(client, manager, db):
    """Законная перенумеровка проходит, и производные ключи идут за сырыми полями."""
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "4881050", "supplier_name": "П"})
    assert r.status_code == 200, r.text
    nak_id = r.json()["id"]

    r2 = client.patch(f"/nakladnye/{nak_id}", headers=h,
                      json={"doc_series": "вг", "doc_number": "ТТН 999"})
    assert r2.status_code == 200, r2.text

    _reload(db)
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first()
    assert fresh.doc_series_norm == "ВГ"
    assert fresh.doc_number_norm == "999"


def test_doc_key_columns_are_filled_on_direct_insert(db):
    """Ключи заполняются и при прямой вставке через ORM, не только через API.

    Это делает слушатель в models.py: иначе запись, созданная скриптом или
    миграцией данных, осталась бы вне индекса и её можно было бы продублировать.
    """
    db.add(models.Nakladnaya(doc_series=" аб ", doc_number="ТТН-4881060",
                             supplier_name="П"))
    db.commit()

    _reload(db)
    nak = db.query(models.Nakladnaya).first()
    assert nak.doc_series_norm == "АБ"
    assert nak.doc_number_norm == "4881060"


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
# Валидация сумм и справочников на сервере (дефекты 11 и 12 — починены)
# ---------------------------------------------------------------------------

def test_vat_greater_than_amount_rejected(client, bot_headers):
    """Починено в Фазе 2 (2026-09-12, дефект 11).

    amount — «Стоимость с НДС», vat_amount — «Сумма НДС» внутри неё, поэтому
    НДС больше итога документа быть не может. Проверка переехала с бота
    (где она лишь молча меняла значения местами) на сервер: теперь её не
    обходит ни CRM-модалка, ни прямой запрос к API.
    """
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "1", "amount": 100.0, "vat_amount": 500.0,
    })
    assert r.status_code == 422


def test_vat_greater_than_amount_rejected_on_crm_create(client, manager):
    """То же правило на CRM-пути создания: бот не единственный источник."""
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "11", "amount": 100.0, "vat_amount": 500.0,
    })
    assert r.status_code == 422


def test_partial_update_cannot_break_amount_vs_vat(client, manager, db):
    """Частичная правка одного поля из пары проверяется по строке базы.

    NakladnayaUpdate видит только присланные поля: vat_amount=500 при
    сохранённом amount=100 схема сравнить не может в принципе, второе
    значение лежит в БД. Поэтому правило дублирует nakladnye_router,
    сравнивая эффективные значения. Отказ 400 (бизнес-правило), не 422.
    """
    _, h = manager
    nak = models.Nakladnaya(doc_series="АБ", doc_number="12", amount=100.0,
                            vat_amount=16.67, supplier_name="П")
    db.add(nak)
    db.commit()

    r = client.patch(f"/nakladnye/{nak.id}", headers=h, json={"vat_amount": 500.0})
    assert r.status_code == 400, r.text

    r2 = client.patch(f"/nakladnye/{nak.id}", headers=h, json={"amount": 5.0})
    assert r2.status_code == 400, r2.text

    _reload(db)
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).first()
    assert round(float(fresh.amount), 2) == 100.0
    assert round(float(fresh.vat_amount), 2) == 16.67


@pytest.mark.parametrize("payload", [
    {"doc_series": "АБ", "doc_number": "20", "amount": 100.0},
    {"doc_series": "АБ", "doc_number": "30", "vat_amount": 16.67},
    {"supplier_name": "Не распознано", "store": STORE, "status": "new"},
])
def test_bot_payload_with_one_amount_is_accepted(client, bot_headers, db, payload):
    """Отказ мягкий, когда задано только одно из двух полей (или ни одного).

    OCR распознаёт не всё: бот регулярно присылает amount без vat_amount
    и наоборот, а fallback-ветка при нераспознанном документе не шлёт ни того,
    ни другого (telegram_nakladnye_bot.py, «Не распознано»). Жёсткая проверка
    потеряла бы эти накладные.
    """
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json=payload)
    assert r.status_code == 200, r.text

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_equal_amount_and_vat_is_accepted(client, bot_headers):
    """Граница правила: amount == vat_amount — не нарушение (строгое «меньше»)."""
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "40", "amount": 100.0, "vat_amount": 100.0,
    })
    assert r.status_code == 200, r.text


def test_garbage_status_and_doc_type_rejected(client, bot_headers):
    """Починено в Фазе 2 (2026-09-12, дефект 12).

    NAKLADNYE_STATUSES и NAKLADNYE_DOC_TYPES наконец применяются валидаторами
    NakladnayaBase и NakladnayaUpdate. Мусор отклоняется на входе (422), а не
    оседает в базе записью, которую не берёт ни один фильтр таблицы накладных.
    """
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "2", "status": "какая-то ерунда",
        "doc_type": "НЕВЕСТЬЧТО",
    })
    assert r.status_code == 422


def test_garbage_status_rejected_on_crm_patch_too(client, manager, db):
    """Правило одинаково для создания и для правки.

    NakladnayaUpdate не наследует NakladnayaBase, поэтому валидаторы в нём
    отдельные. PATCH — рабочий путь переключателей «Проверена/Приехала/
    Оплачена» (js/nakladnye.js), и дыра там означала бы, что мусор проходит
    одним кликом по таблице.
    """
    _, h = manager
    nak = models.Nakladnaya(doc_series="АБ", doc_number="9", supplier_name="П")
    db.add(nak)
    db.commit()

    assert client.patch(f"/nakladnye/{nak.id}", headers=h,
                        json={"status": "оплачено-наверное"}).status_code == 422
    assert client.patch(f"/nakladnye/{nak.id}", headers=h,
                        json={"doc_type": "СЧФ"}).status_code == 422
    assert client.patch(f"/nakladnye/{nak.id}", headers=h,
                        json={"status": "paid", "doc_type": "УПД"}).status_code == 200


@pytest.mark.parametrize("doc_type", ["", None])
def test_bot_doc_type_blank_is_accepted(client, bot_headers, db, doc_type):
    """Отказ по doc_type обязан быть мягким для пустого значения.

    Telegram-бот собирает payload как first.get("doc_type", "") и присылает
    пустую строку, когда OCR тип не распознал; в модалке CRM селект типа
    содержит пункт «—» с пустым значением. Строгая проверка означала бы, что
    накладная с нераспознанным типом не сохраняется вовсе, — потеря документа
    вместо защиты справочника. Пустое значение нормализуется в None.
    """
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": doc_type, "doc_series": "АБ", "doc_number": "5",
        "amount": 100.0, "supplier_name": "П", "status": "new",
    })
    assert r.status_code == 200, r.text
    assert r.json()["doc_type"] is None

    _reload(db)
    assert db.query(models.Nakladnaya).count() == 1


def test_doc_type_case_and_spaces_are_normalized(client, bot_headers, db):
    """Значение приходит из OCR, а фронт сравнивает строки посимвольно.

    «ттн » и «ТТН» — один тип: без нормализации запись не совпадала бы ни с
    фильтром таблицы, ни с пунктом селекта в модалке и выглядела бы пустой.
    """
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": " ттн ", "doc_series": "АБ", "doc_number": "6",
        "supplier_name": "П",
    })
    assert r.status_code == 200, r.text
    assert r.json()["doc_type"] == "ТТН"


def test_amount_no_vat_is_settable(client, manager):
    """Починено в Фазе 2 (2026-09-12, дефект 14).

    Колонка nakladnye.amount_no_vat есть в боевом DDL (добавлена
    migrate_add_nakladnye.py), но отсутствовала и в models.py, и в
    NakladnayaBase — клиент физически не мог её заполнить, а сервер не мог
    отдать. Сумма без НДС нужна для сверки с поставщиком и для Excel-выгрузки,
    где товары идут в ценах без НДС (price_no_vat).
    """
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "3", "amount": 120.0, "amount_no_vat": 100.0,
    })
    assert r.status_code == 200, r.text
    assert r.json().get("amount_no_vat") == 100.0


def test_amount_no_vat_roundtrips_through_patch_and_db(client, manager, db):
    """Поле доступно и на правку, и читается из базы: NakladnayaUpdate —
    отдельная схема, она NakladnayaBase не наследует."""
    _, h = manager
    nak = models.Nakladnaya(doc_series="АБ", doc_number="31", amount=120.0,
                            supplier_name="П")
    db.add(nak)
    db.commit()

    r = client.patch(f"/nakladnye/{nak.id}", headers=h, json={"amount_no_vat": 100.0})
    assert r.status_code == 200, r.text
    assert r.json()["amount_no_vat"] == 100.0

    _reload(db)
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).first()
    assert round(float(fresh.amount_no_vat), 2) == 100.0


def test_amount_no_vat_is_optional(client, manager):
    """Поле не обязательное: OCR его часто не распознаёт.

    Накладная без суммы без НДС создаётся и отдаётся с None, а не с 0.0 —
    ноль выглядел бы как реальная сумма и попал бы в итоги сверки.
    """
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "32", "amount": 120.0,
    })
    assert r.status_code == 200, r.text
    assert r.json().get("amount_no_vat") is None


def test_nakladnye_delete_requires_admin(client, manager, admin, db):
    """РЕГРЕССИЯ на пункт 17 (V11): входящую накладную удаляет только админ.

    Прежнее поведение (удаляет любой менеджер) тест фиксировал как
    несогласованность модели доступа с DELETE /clients. С пункта 17
    несогласованности нет: удаление необратимо (вместе со строкой стираются
    фото с диска), поэтому оно админское. Создание и правка остались
    операционными — склад и бот работают без админа.
    """
    nak = models.Nakladnaya(doc_series="АБ", doc_number="4", supplier_name="П")
    db.add(nak)
    db.commit()
    nak_id = nak.id

    _, mh = manager
    assert client.delete(f"/nakladnye/{nak_id}", headers=mh).status_code == 403
    _reload(db)
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak_id).first() is not None, \
        "после 403 накладная обязана остаться"

    _, ah = admin
    assert client.delete(f"/nakladnye/{nak_id}", headers=ah).status_code == 200
    _reload(db)
    assert db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).first() is None
