"""Пункт 17 плана v2 — V7: «Оригинал ТН возвращён» == «ТН у нас» == is_invoice_doc.

Решение (2026-09-23): отдельной колонки `originals_returned` НЕ будет.
`transactions.is_invoice_doc` на записи-документе (is_document=True) объявлен
каноническим хранилищем ОДНОГО бизнес-факта «оригинал ТН у нас», у которого
в интерфейсах три подписи:

  * «Оригинал ТН возвращён» — карточка сделки v2
    (tools/mockups/shell-v2-board.js:918, запись через KBData.mutate('doc-flag')
    → tools/v2-boot-template.js:757-760);
  * «ТН у нас» — финансы v2 (site-v2/js/shell-v2-fin.js:50,198,418, чтение
    из того же is_invoice_doc: tools/v2-boot-template.js:271 и :559);
  * колонка «ТН» (title «Накладная (ТН)») — «Документы» legacy
    (site/index.html:299, site/js/documents.js:185,282).

Почему не колонка: в кодировке проекта «invoice» = накладная (invoice_number —
№ ТН, issue_invoice — выписать накладную), поэтому имя колонки читается как
«документ-накладная у нас» и не противоречит смыслу; боевые данные уже
заполнены пользователями через «Документы» именно в этом смысле, а новая
колонка потребовала бы переноса данных и второй правки тех же экранов
(board.js/boot.js сейчас заняты другими пунктами плана). Три подписи одного
флага синхронизированы на клиенте событием `fin:originals`
(site-v2/js/shell-v2-fin.js:422-423).

Что закрепляет этот файл на сервере:
  1. флаг живёт только на записи-документе — попытка поставить его на строку
     реестра оплат отклоняется 400 (раньше записывалась молча и не читалась
     ни одним экраном, то есть терялась);
  2. выписка накладной НЕ означает «оригинал у нас»: свежая копия-документ
     создаётся с флагом False;
  3. флаг круговой: PATCH → GET /payments/documents (реестр оплат документов
     не содержит вовсе, а строка накладной флаг не дублирует);
  4. документ групповой ТН принимает флаг так же (пробел «ТН у нас» для групп
     в финансах v2 — клиентский: у групповой строки нет docTxId);
  5. отмена накладной убирает документ вместе с флагом — факт не остаётся
     висеть на осиротевшей строке.
"""
import pytest

import models

pytestmark = pytest.mark.access

STORE = "Богдановича"


def _reload(db):
    db.expire_all()


def _issued_card(client, headers, make_card, number="ТТН7001", amount=400.0):
    """Сделка в «Сборке» с записью реестра и выписанной накладной."""
    card = make_card(title="Сделка", total_amount=1000.0, status="Сборка",
                     store_location=STORE)
    r = client.post(f"/payments/trigger_from_card/{card.id}", headers=headers,
                    json={"store_location": STORE})
    assert r.status_code == 200, r.text
    r = client.post(f"/payments/cards/{card.id}/issue-invoice", headers=headers,
                    json={"invoice_number": number, "amount": amount,
                          "invoice_date": "2026-09-01", "store_location": STORE})
    assert r.status_code == 200, r.text
    return card, r.json()


def _doc_row(db, card_id):
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == True,  # noqa: E712
    ).first()


def _ledger_row(db, card_id):
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,  # noqa: E712
        models.Transaction.invoice_number.isnot(None),
    ).first()


# ---------------------------------------------------------------------------
# 1. Выписка накладной не означает «оригинал у нас»
# ---------------------------------------------------------------------------

def test_fresh_document_copy_has_no_originals_flag(client, manager, db, make_card):
    _, h = manager
    card, issued = _issued_card(client, h, make_card)
    _reload(db)
    doc = _doc_row(db, card.id)
    assert doc is not None, "выписка обязана создать копию в «Документах»"
    assert bool(doc.is_invoice_doc) is False, \
        "свежая ТН ещё не возвращена: флаг ставит человек, а не выписка"
    assert bool(doc.is_bill_doc) is False
    # строка реестра (сама накладная) флаг не несёт вовсе
    ledger = _ledger_row(db, card.id)
    assert ledger.id != doc.id
    assert bool(ledger.is_invoice_doc) is False


# ---------------------------------------------------------------------------
# 2. Флаг ставится на запись-документ и читается обратно обоими реестрами
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["is_invoice_doc", "is_bill_doc"])
def test_originals_flag_roundtrip_on_document_row(client, manager, db, make_card, field):
    """PATCH (галочка «ТН у нас» / «Оригинал ТН возвращён») → оба GET.

    Именно этот ответ читает клиент: originalsReturned в карточке
    (v2-boot-template.js:271) и tnHere/billHere в финансах (:559-560).
    """
    _, h = manager
    card, _ = _issued_card(client, h, make_card)
    _reload(db)
    doc = _doc_row(db, card.id)

    r = client.patch(f"/payments/transactions/{doc.id}", headers=h, json={field: True})
    assert r.status_code == 200, r.text
    assert r.json()[field] is True

    _reload(db)
    assert bool(getattr(doc, field)) is True, "флаг обязан сохраниться, а не потеряться после F5"

    docs = client.get("/payments/documents", headers=h).json()
    row = next(d for d in docs if d["id"] == doc.id)
    assert row[field] is True, "«Документы» отдают флаг"

    # Реестр оплат документов не содержит вовсе (is_document=False в фильтре),
    # а строка накладной флаг не несёт: у факта одно каноническое место.
    registry = client.get("/payments/transactions?grouped=false", headers=h).json()
    assert all(t["is_document"] is False for t in registry)
    assert all(t[field] is False for t in registry if t["card_id"] == card.id), \
        "строки реестра не должны дублировать флаг документа"


def test_originals_flag_can_be_unset(client, manager, db, make_card):
    """Снятие галочки — тот же контракт (возврат оригинала клиенту)."""
    _, h = manager
    card, _ = _issued_card(client, h, make_card)
    _reload(db)
    doc = _doc_row(db, card.id)

    assert client.patch(f"/payments/transactions/{doc.id}", headers=h,
                        json={"is_invoice_doc": True}).status_code == 200
    r = client.patch(f"/payments/transactions/{doc.id}", headers=h,
                     json={"is_invoice_doc": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_invoice_doc"] is False


# ---------------------------------------------------------------------------
# 3. Строка реестра оплат флаг не принимает
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["is_invoice_doc", "is_bill_doc"])
def test_originals_flag_rejected_on_registry_row(client, manager, db, make_card, field):
    """400 вместо молчаливой записи в никуда.

    Строка реестра (оплата/остаток) не читается ни одним экраном как «ТН у нас»:
    карточка и финансы берут флаг с записи-документа. Раньше PATCH отвечал 200,
    флаг оседал на строке реестра и бесследно терялся для пользователя.
    """
    _, h = manager
    card, _ = _issued_card(client, h, make_card)
    _reload(db)
    ledger = _ledger_row(db, card.id)

    r = client.patch(f"/payments/transactions/{ledger.id}", headers=h, json={field: True})
    assert r.status_code == 400, f"ожидали 400, получили {r.status_code}: {r.text}"
    assert "Документы" in r.json()["detail"]

    _reload(db)
    assert bool(getattr(ledger, field)) is False, "флаг не должен был записаться"


def test_registry_row_keeps_its_own_flags(client, manager, db, make_card):
    """Гейт V7 точечный: остальные поля строки реестра правятся как раньше."""
    _, h = manager
    card, _ = _issued_card(client, h, make_card)
    _reload(db)
    ledger = _ledger_row(db, card.id)

    r = client.patch(f"/payments/transactions/{ledger.id}", headers=h,
                     json={"is_written_off": True, "is_calculated": True,
                           "note": "оплата по доверенности"})
    assert r.status_code == 200, r.text
    assert r.json()["is_written_off"] is True
    assert r.json()["is_calculated"] is True


# ---------------------------------------------------------------------------
# 4. Групповая ТН
# ---------------------------------------------------------------------------

def test_group_document_accepts_originals_flag(client, manager, db, make_card):
    """Документ групповой накладной — такая же запись-документ.

    Сервер флаг принимает; то, что «ТН у нас» для групп не редактируется в
    финансах v2, — клиентский пробел (у групповой строки нет docTxId,
    site-v2/js/shell-v2-fin.js:218), а не серверный.
    """
    _, h = manager
    a = make_card(title="A", total_amount=100.0, status="Сборка", store_location=STORE)
    b = make_card(title="B", total_amount=200.0, status="Сборка", store_location=STORE)
    r = client.post("/writeoffs/groups/", headers=h, json={"card_ids": [a.id, b.id]})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]

    r = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=h,
                    json={"invoice_number": "ТТН8001", "amount": 300.0,
                          "invoice_date": "2026-09-02"})
    assert r.status_code == 200, r.text

    _reload(db)
    doc = db.query(models.Transaction).filter(
        models.Transaction.writeoff_group_id == group_id,
        models.Transaction.is_document == True,  # noqa: E712
    ).first()
    assert doc is not None, "групповая выписка создаёт документ"

    r = client.patch(f"/payments/transactions/{doc.id}", headers=h,
                     json={"is_invoice_doc": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_invoice_doc"] is True


# ---------------------------------------------------------------------------
# 5. Отмена накладной убирает документ вместе с флагом
# ---------------------------------------------------------------------------

def test_invoice_cancel_removes_document_and_its_flag(client, admin, db, make_card):
    """Удаляет админ (V11): вместе с накладной уходит и её копия-документ,
    поэтому флаг «оригинал у нас» не остаётся на осиротевшей строке."""
    _, h = admin
    card, _ = _issued_card(client, h, make_card, number="ТТН7002")
    _reload(db)
    doc = _doc_row(db, card.id)
    ledger = _ledger_row(db, card.id)
    assert client.patch(f"/payments/transactions/{doc.id}", headers=h,
                        json={"is_invoice_doc": True}).status_code == 200

    r = client.delete(f"/payments/transactions/{ledger.id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == True,  # noqa: E712
    ).count() == 0, "копия-документ удалена вместе с накладной"
