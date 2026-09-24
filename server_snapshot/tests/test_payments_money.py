"""Приоритет A — деньги и потеря данных.

Самые критичные инварианты: выписка накладных, остаток по сделке, возврат суммы
при удалении, автоопределение статуса оплаты.

Живых дефектов здесь не осталось: все xfail Фазы 2 закрыты 2026-09-12
(см. PROGRESS.md). Маркер known_bug в pytest.ini оставлен на будущее.

Проверяем через БД, а не только через JSON ответа: нас интересует, что реально
сохранилось, а не что сериализовалось.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

import models
import services.payments as payments

pytestmark = pytest.mark.money

STORE = "Матусевича 72"


def _reload(db):
    """Сбросить кэш сессии, чтобы увидеть изменения, сделанные через API."""
    db.expire_all()


def _txs(db, card_id, **flt):
    q = db.query(models.Transaction).filter(models.Transaction.card_id == card_id)
    for k, v in flt.items():
        q = q.filter(getattr(models.Transaction, k) == v)
    return q.order_by(models.Transaction.id).all()


def _trigger(client, headers, card_id, store=STORE):
    return client.post(f"/payments/trigger_from_card/{card_id}",
                       headers=headers, json={"store_location": store})


def _issue(client, headers, card_id, number, amount, date="2026-09-01"):
    return client.post(f"/payments/cards/{card_id}/issue-invoice", headers=headers,
                       json={"invoice_number": number, "amount": amount,
                             "invoice_date": date, "store_location": STORE})


def _add(client, headers, card_id, number, amount, date="2026-09-01"):
    return client.post(f"/payments/cards/{card_id}/invoices", headers=headers,
                       json={"invoice_number": number, "amount": amount,
                             "invoice_date": date, "store_location": STORE})


def _post_invoices_concurrently(headers, card_id, requests):
    """Два независимых HTTP-вызова; все клиенты закрыты до возврата."""
    import main

    def _worker(index, kind, number):
        payload = {
            "invoice_number": number,
            "amount": 60.0,
            "invoice_date": "2026-09-01",
            "store_location": STORE,
        }
        with TestClient(main.app, raise_server_exceptions=False) as worker_client:
            if kind == "issue":
                return worker_client.post(
                    f"/payments/cards/{card_id}/issue-invoice",
                    headers=headers,
                    json=payload,
                )
            return worker_client.post(
                f"/payments/cards/{card_id}/invoices",
                headers=headers,
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_worker, index, kind, number)
            for index, (kind, number) in enumerate(requests)
        ]
        return [future.result(timeout=20) for future in futures]


# ---------------------------------------------------------------------------
# Запись-остаток и идемпотентность
# ---------------------------------------------------------------------------

def test_trigger_creates_single_remainder(client, manager, db, make_card):
    _, h = manager
    card = make_card(title="Сделка А", total_amount=1000.0, status="Сборка")

    r = _trigger(client, h, card.id)
    assert r.status_code == 200, r.text

    _reload(db)
    rows = _txs(db, card.id)
    assert len(rows) == 1
    assert float(rows[0].amount) == 1000.0
    assert not rows[0].is_document
    assert not rows[0].invoice_number


def test_trigger_is_idempotent(client, manager, db, make_card):
    """Кнопки «В Сборку»/«В Списание» нажимают многократно — дублей быть не должно."""
    _, h = manager
    card = make_card(total_amount=500.0, status="Сборка")

    codes = [_trigger(client, h, card.id).status_code for _ in range(3)]
    assert codes == [200, 200, 200], codes

    _reload(db)
    assert len(_txs(db, card.id)) == 1


def test_trigger_blocked_for_card_in_writeoff_group(client, manager, db, make_card):
    _, h = manager
    group = models.WriteoffGroup(name="Группа 1", total_amount=0.0)
    db.add(group)
    db.commit()
    card = make_card(total_amount=300.0, writeoff_group_id=group.id)

    r = _trigger(client, h, card.id)
    assert r.status_code == 400
    assert "групп" in r.json()["detail"].lower()

    _reload(db)
    assert _txs(db, card.id) == []


def test_trigger_unknown_card_404(client, manager):
    _, h = manager
    assert _trigger(client, h, 999999).status_code == 404


# ---------------------------------------------------------------------------
# Выписка накладных
# ---------------------------------------------------------------------------

def test_issue_invoice_partial_reduces_remainder(client, manager, db, make_card):
    """Частичная отгрузка: остаток уменьшается ровно на сумму накладной.

    Важно: issue-invoice создаёт ДВЕ записи с одним invoice_number — саму
    накладную в реестре и её копию в «Документах» (is_document=True).
    Поэтому везде фильтруем копии отдельно.
    """
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    r = _issue(client, h, card.id, "ТТН0001", 400.0)
    assert r.status_code == 200, r.text

    _reload(db)
    rows = _txs(db, card.id)
    inv = [t for t in rows if t.invoice_number == "ТТН0001" and not t.is_document]
    docs = [t for t in rows if t.invoice_number == "ТТН0001" and t.is_document]
    rest = [t for t in rows if not t.invoice_number and not t.is_document]

    assert len(inv) == 1 and float(inv[0].amount) == 400.0
    assert len(docs) == 1, "копия в «Документах» — ровно одна"
    assert len(rest) == 1, f"должна остаться одна запись-остаток, найдено {len(rest)}"
    assert round(float(rest[0].amount), 2) == 600.0


def test_registry_reports_partial_invoiced_amount(client, manager, db, make_card):
    """Фидбек 18.09 («Рацио Домус»): галочка «Выписка» у строки на всю сумму
    счёта читалась как «выписана вся сумма», хотя покрыта лишь часть.
    Группированный реестр отдаёт invoiced_amount — сколько счёта закрыто
    накладными/складскими списаниями (фронт рисует по нему «част.»)."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТНчаст", 400.0).status_code == 200

    rows = client.get("/payments/transactions", headers=h).json()
    row = next(r for r in rows if r.get("card_id") == card.id)
    assert float(row["amount"]) == 1000.0, "сумма строки — сумма счёта"
    assert float(row["invoiced_amount"]) == 400.0
    assert row["is_invoice_issued"] is True, "факт накладной — галочкой"

    # докрываем остаток — счёт покрыт целиком, «част.» исчезнет
    assert _issue(client, h, card.id, "ТНостат", 600.0).status_code == 200
    rows = client.get("/payments/transactions", headers=h).json()
    row = next(r for r in rows if r.get("card_id") == card.id)
    assert abs(float(row["invoiced_amount"]) - 1000.0) < 0.01


def test_issue_invoice_creates_exactly_one_document_copy(client, manager, db, make_card):
    """Инвариант: одна накладная = одна запись в реестре + одна копия в «Документах»."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0011", 250.0).status_code == 200

    _reload(db)
    rows = _txs(db, card.id)
    assert len([t for t in rows if t.is_document]) == 1
    assert len([t for t in rows if not t.is_document]) == 2, "накладная + остаток"


def test_issue_invoice_full_remainder_closes_card(client, manager, db, make_card):
    """Накладная на весь остаток: остаток превращается в накладную, сделка закрывается."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    r = _issue(client, h, card.id, "ТТН0002", 1000.0)
    assert r.status_code == 200, r.text

    _reload(db)
    db.refresh(card)
    rows = _txs(db, card.id)
    assert not [t for t in rows if not t.invoice_number and not t.is_document], \
        "запись-остаток должна исчезнуть после полного закрытия"
    assert card.status == "Закрыто"


def test_issue_invoice_over_remainder_rejected(client, manager, db, make_card):
    """Сумма больше остатка — 400 и никаких записей."""
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    r = _issue(client, h, card.id, "ТТН0003", 1500.0)
    assert r.status_code == 400

    _reload(db)
    assert len(_txs(db, card.id)) == 1, "отклонённая выписка не должна ничего создать"


@pytest.mark.parametrize("amount", [0, -1, -0.01])
def test_issue_invoice_nonpositive_rejected(client, manager, db, make_card, amount):
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "ТТН0004", amount).status_code == 400
    _reload(db)
    assert len(_txs(db, card.id)) == 1


def test_issue_invoice_same_number_is_rejected_and_document_stays_single(
        client, manager, db, make_card):
    """Копия в «Документах» — одна на номер накладной.

    Изменено в Фазе 2 (2026-09-12, дефект 6): раньше тест выписывал один номер
    дважды и проверял, что копия-документ не задвоилась. Повторная выписка того
    же номера на той же сделке теперь отклоняется (400), поэтому документ
    остаётся один уже потому, что запись реестра одна. Прежняя защита
    _issue_document проверена отдельным тестом ниже.
    """
    _, h = manager
    card = make_card(total_amount=2000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    assert _issue(client, h, card.id, "ТТН0005", 500.0).status_code == 200
    assert _issue(client, h, card.id, "ТТН0005", 500.0).status_code == 400

    _reload(db)
    docs = _txs(db, card.id, is_document=True)
    assert len(docs) == 1, f"ожидалась одна копия-документ, найдено {len(docs)}"
    ledger = [t for t in _txs(db, card.id, is_document=False)
              if (t.invoice_number or "").strip()]
    assert len(ledger) == 1, f"ожидалась одна запись реестра, найдено {len(ledger)}"


def test_issue_document_reuses_existing_document_copy(client, manager, db, make_card):
    """_issue_document не плодит вторую копию, если документ с таким номером уже есть.

    Сохранён исходный смысл прежнего test_issue_invoice_same_number_does_not_
    duplicate_document. Штатным путём рассогласование теперь недостижимо —
    дубль номера отклоняется раньше (дефект 6), — поэтому состояние
    готовится напрямую: копия-документ есть, записи реестра с этим номером нет
    (такое остаётся, например, после ручной правки базы).
    """
    _, h = manager
    card = make_card(total_amount=2000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    db.add(models.Transaction(card_id=card.id, company_name=card.title, amount=500.0,
                              store_location=STORE, invoice_number="ТТН0007",
                              is_document=True))
    db.commit()

    assert _issue(client, h, card.id, "ТТН0007", 500.0).status_code == 200

    _reload(db)
    docs = _txs(db, card.id, is_document=True)
    assert len(docs) == 1, f"копия-документ задвоилась: найдено {len(docs)}"


# ---------------------------------------------------------------------------
# Конкурентные выписки и добавления
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("invoice_requests", [
    pytest.param(
        [("issue", "ТТН6001"), ("issue", "ТТН6002")],
        id="issue-issue-different",
    ),
    pytest.param(
        [("add", "ТТН6003"), ("add", "ТТН6004")],
        id="add-add",
    ),
    pytest.param(
        [("issue", "ТТН 6005"), ("add", "ТТН6005")],
        id="issue-add-same-normalized",
    ),
    pytest.param(
        [("issue", "ТТН6006"), ("add", "ТТН6007")],
        id="issue-add-different",
    ),
])
def test_concurrent_invoice_writes_are_serialized(
        invoice_requests, client, manager, db, make_card, monkeypatch):
    """Два HTTP-запроса не могут одновременно вычислить один остаток.

    Барьер после чтения реестра детерминированно вскрывает старое окно
    check-then-write. С резервированием карточки второй запрос до него не
    доходит; таймаут намеренно короткий, а BrokenBarrierError безопасен.
    """
    _, headers = manager
    card = make_card(total_amount=100.0, status="Сборка")
    card_id = card.id
    assert _trigger(client, headers, card_id).status_code == 200

    barrier = threading.Barrier(2)
    real_find_duplicate = payments.find_duplicate_invoice

    def synchronized_duplicate(rows, number):
        try:
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return real_find_duplicate(rows, number)

    monkeypatch.setattr(payments, "find_duplicate_invoice", synchronized_duplicate)
    responses = _post_invoices_concurrently(headers, card_id, invoice_requests)

    statuses = [response.status_code for response in responses]
    assert sorted(statuses) == [200, 400], [response.text for response in responses]
    assert all(status < 500 for status in statuses), statuses

    _reload(db)
    rows = _txs(db, card_id)
    ledger_invoices = [
        row for row in rows
        if not row.is_document and (row.invoice_number or "").strip()
    ]
    remainders = [
        row for row in rows
        if not row.is_document
        and not row.is_warehouse_writeoff
        and not (row.invoice_number or "").strip()
    ]
    documents = [row for row in rows if row.is_document]

    issued_total = round(sum(float(row.amount or 0) for row in ledger_invoices), 2)
    assert issued_total <= 100.0
    assert len(remainders) == 1
    assert float(remainders[0].amount) >= 0.0

    normalized_numbers = [
        "".join(ch for ch in row.invoice_number if ch.isdigit())
        for row in ledger_invoices
    ]
    assert len(normalized_numbers) == len(set(normalized_numbers))

    successful_issue_count = sum(
        1 for request, response in zip(invoice_requests, responses)
        if request[0] == "issue" and response.status_code == 200
    )
    assert len(documents) == successful_issue_count

    db.refresh(card)
    successful_action = next(
        request[0] for request, response in zip(invoice_requests, responses)
        if response.status_code == 200
    )
    expected_status = "На списание" if successful_action == "issue" else "Сборка"
    assert card.status == expected_status


# ---------------------------------------------------------------------------
# Удаление и возврат суммы
# ---------------------------------------------------------------------------

def test_delete_invoice_returns_amount_to_remainder(client, admin, db, make_card):
    """Удалили накладную — её сумма возвращается в остаток.

    Роль admin: удаление записи реестра с пункта 17 (V11) — админская операция.
    """
    _, h = admin
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0006", 400.0).status_code == 200

    _reload(db)
    inv = next(t for t in _txs(db, card.id) if t.invoice_number == "ТТН0006")
    r = client.delete(f"/payments/transactions/{inv.id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    rest = [t for t in _txs(db, card.id) if not t.invoice_number and not t.is_document]
    assert len(rest) == 1
    assert round(float(rest[0].amount), 2) == 1000.0, "остаток должен восстановиться до 1000"


def test_delete_last_transaction_returns_card_to_assembly(client, admin, db, make_card):
    """Удалили последнюю запись — сделка возвращается в «Сборку».

    И сразу пересоздаётся запись реестра: ensure_registry_remainder вызывается
    намеренно, иначе сделка в «Сборке» пропала бы из «Реестра оплат»
    (фикс от 09.09, кейс «ТрансЛИДИЯсервис»). Поэтому «записей нет» здесь
    НЕ является ожидаемым результатом — ожидаемо ровно одна свежая запись
    на полную сумму сделки. Роль admin — см. V11 (пункт 17).
    """
    _, h = admin
    card = make_card(total_amount=700.0, status="На списание")
    assert _trigger(client, h, card.id).status_code == 200

    _reload(db)
    rows = [t for t in _txs(db, card.id) if not t.is_document]
    assert len(rows) == 1
    assert client.delete(f"/payments/transactions/{rows[0].id}", headers=h).status_code == 200

    _reload(db)
    db.refresh(card)
    assert card.status == "Сборка"

    fresh = [t for t in _txs(db, card.id) if not t.is_document]
    assert len(fresh) == 1, "запись реестра пересоздаётся, чтобы сделка не пропала из реестра"
    assert round(float(fresh[0].amount), 2) == 700.0


def test_delete_last_transaction_card_without_amount_stays_empty(client, admin, db, make_card):
    """Сделка без суммы (total_amount <= 0) запись реестра не получает:
    ensure_registry_remainder такие пропускает. Роль admin — см. V11 (пункт 17)."""
    _, h = admin
    card = make_card(total_amount=0.0, status="На списание")
    tx = models.Transaction(card_id=card.id, company_name=card.title, amount=0.0,
                            is_document=False)
    db.add(tx)
    db.commit()

    assert client.delete(f"/payments/transactions/{tx.id}", headers=h).status_code == 200

    _reload(db)
    assert [t for t in _txs(db, card.id) if not t.is_document] == []


def test_delete_document_does_not_kill_unrelated_remainder(client, admin, db, make_card):
    """Документ с нераспознанным номером и чужой суммой не должен тянуть за собой остаток.

    Починено в Фазе 2 (2026-09-11): _find_invoice_twins больше не считает
    запись-остаток кандидатом в пары и не объявляет парой «единственного
    кандидата». Раньше при нераспознанном номере и несовпавшей сумме
    удалялась первая попавшаяся запись — так терялся остаток по сделке.
    Роль admin — см. V11 (пункт 17).
    """
    _, h = admin
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    # документ, у которого ни номер, ни сумма не совпадают с остатком
    doc = models.Transaction(card_id=card.id, company_name=card.title, amount=77.0,
                             is_document=True, invoice_number="НЕИЗВЕСТНО-999")
    db.add(doc)
    db.commit()

    r = client.delete(f"/payments/transactions/{doc.id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    rest = [t for t in _txs(db, card.id) if not t.is_document]
    assert len(rest) == 1, "запись-остаток не имеет отношения к удалённому документу"


def test_delete_one_invoice_does_not_delete_the_other(client, admin, db, make_card):
    """Вторая жертва убранного fallback'а «единственный кандидат — это пара».

    У сделки две накладные с разными номерами и суммами. Удаление одной не
    должно трогать вторую — раньше при нераспознанной паре оставшаяся
    накладная объявлялась близнецом и удалялась вместе с ней.
    Роль admin — см. V11 (пункт 17).
    """
    _, h = admin
    card = make_card(title="Сделка", total_amount=3000.0, status="Сборка")
    card_id = card.id
    assert _trigger(client, h, card_id).status_code == 200
    assert _issue(client, h, card_id, "ТТН1001", 1000.0).status_code == 200
    assert _issue(client, h, card_id, "ТТН1002", 800.0).status_code == 200

    _reload(db)
    first = next(t for t in _txs(db, card_id)
             if t.invoice_number == "ТТН1001" and not t.is_document)
    first_id = first.id

    assert client.delete(f"/payments/transactions/{first_id}", headers=h).status_code == 200

    _reload(db)
    ledger_numbers = sorted(t.invoice_number for t in _txs(db, card_id)
                            if t.invoice_number and not t.is_document)
    assert ledger_numbers == ["ТТН1002"], f"вторая накладная пострадала: {ledger_numbers}"
    doc_numbers = sorted(t.invoice_number for t in _txs(db, card_id) if t.is_document)
    assert doc_numbers == ["ТТН1002"], \
        f"копия удалённой накладной должна уйти, копия оставшейся — остаться: {doc_numbers}"


def test_delete_invoice_keeps_remainder_of_the_same_amount(client, admin, db, make_card):
    """Остаток, совпадающий по сумме с удаляемой накладной, не является её парой.

    Роль admin — см. V11 (пункт 17).
    """
    _, h = admin
    card = make_card(title="Сделка", total_amount=800.0, status="В работе")
    card_id = card.id
    db.add(models.Transaction(card_id=card_id, company_name="Сделка", amount=400.0,
                              is_document=False, invoice_number="ТТН2001"))
    db.add(models.Transaction(card_id=card_id, company_name="Сделка", amount=400.0,
                              is_document=False, invoice_number=None))
    db.commit()

    _reload(db)
    inv = next(t for t in _txs(db, card_id) if t.invoice_number == "ТТН2001")
    inv_id = inv.id
    assert client.delete(f"/payments/transactions/{inv_id}", headers=h).status_code == 200

    _reload(db)
    left = [t for t in _txs(db, card_id)
            if not t.invoice_number and not t.is_document and not t.is_warehouse_writeoff]
    assert len(left) == 1, "остаток совпадал по сумме, но парой накладной не является"


# ---------------------------------------------------------------------------
# Правка суммы сделки (регрессия на FIX 2026-09-06)
# ---------------------------------------------------------------------------

def test_patch_card_total_does_not_overwrite_issued_invoices(client, manager, db, make_card):
    """Главный баг потери данных, исправленный на проде 06.09.

    Раньше смена total_amount перезаписывала amount во ВСЕХ транзакциях,
    затирая суммы уже выписанных накладных — реестр расходился с напечатанными ТН.
    """
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0007", 300.0).status_code == 200
    assert _issue(client, h, card.id, "ТТН0008", 200.0).status_code == 200

    r = client.patch(f"/cards/{card.id}", headers=h, json={"total_amount": 1500.0})
    assert r.status_code == 200, r.text

    _reload(db)
    amounts = {t.invoice_number: round(float(t.amount), 2)
               for t in _txs(db, card.id) if t.invoice_number and not t.is_document}
    assert amounts == {"ТТН0007": 300.0, "ТТН0008": 200.0}, \
        f"суммы выписанных накладных затёрты: {amounts}"

    rest = [t for t in _txs(db, card.id) if not t.invoice_number and not t.is_document]
    assert len(rest) == 1
    assert round(float(rest[0].amount), 2) == 1000.0, "остаток = 1500 - 300 - 200"


# ---------------------------------------------------------------------------
# Статус оплаты
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("total,paid,expected", [
    (1000.0, 1000.0, "Оплачен"),
    (1000.0, 999.995, "Оплачен"),      # допуск 0.01
    (1000.0, 500.0, "Частично"),
    (1000.0, 0.011, "Частично"),
    (1000.0, 0.0, "Не оплачен"),
    (0.0, 0.0, "Не оплачен"),
    (0.0, 100.0, "Не оплачен"),         # total <= 0 — всегда «Не оплачен»
])
def test_payment_status_autodetect(client, manager, db, make_card, total, paid, expected):
    _, h = manager
    card = make_card(total_amount=total)
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": paid})
    assert r.status_code == 200, r.text

    _reload(db)
    db.refresh(card)
    assert card.payment_status == expected


def test_explicit_payment_status_wins(client, manager, db, make_card):
    """Явно присланный статус не пересчитывается."""
    _, h = manager
    card = make_card(total_amount=1000.0)
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"paid_amount": 1000.0, "payment_status": "Отсрочка"})
    assert r.status_code == 200, r.text
    _reload(db)
    db.refresh(card)
    assert card.payment_status == "Отсрочка"


def test_negative_paid_amount_clamped_to_zero(client, manager, db, make_card):
    _, h = manager
    card = make_card(total_amount=1000.0)
    r = client.patch(f"/cards/{card.id}/payment", headers=h, json={"paid_amount": -500.0})
    assert r.status_code == 200, r.text
    _reload(db)
    db.refresh(card)
    assert float(card.paid_amount) == 0.0


def test_invalid_payment_status_rejected(client, manager, make_card):
    """CardPaymentUpdate валидирует множество статусов."""
    _, h = manager
    card = make_card(total_amount=100.0)
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"payment_status": "Какой-то мусор"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Агрегаты
# ---------------------------------------------------------------------------

def test_writeoff_status_reflects_invoices(client, manager, db, make_card):
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0009", 400.0).status_code == 200

    r = client.get(f"/payments/cards/{card.id}/writeoff-status", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert round(body["card_amount"], 2) == 1000.0
    assert round(body["invoices_amount"], 2) == 400.0
    assert body["fully_covered"] is False


def test_writeoff_status_fully_covered(client, manager, db, make_card):
    _, h = manager
    card = make_card(total_amount=1000.0, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200
    assert _issue(client, h, card.id, "ТТН0010", 1000.0).status_code == 200

    body = client.get(f"/payments/cards/{card.id}/writeoff-status", headers=h).json()
    assert body["fully_covered"] is True


# ---------------------------------------------------------------------------
# Округление
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expected_rest", [
    (1000.0, 333.33, 666.67),
    (100.0, 33.33, 66.67),
    (0.3, 0.1, 0.2),          # классика float: 0.3 - 0.1 = 0.19999999999999998
    (10.0, 9.999, 0.0),       # разница меньше допуска 0.01 — остаток закрывается
])
def test_remainder_rounding(client, manager, db, make_card, a, b, expected_rest):
    """Деньги — float (решение владельца 2026-09-11), поэтому округление фиксируем явно."""
    _, h = manager
    card = make_card(total_amount=a, status="Сборка")
    assert _trigger(client, h, card.id).status_code == 200

    r = _issue(client, h, card.id, "ТТН-ROUND", b)
    assert r.status_code == 200, r.text

    _reload(db)
    rest = [t for t in _txs(db, card.id) if not t.invoice_number and not t.is_document]
    if expected_rest <= 0.01:
        assert rest == [], "остаток в пределах допуска должен закрыться"
    else:
        assert len(rest) == 1
        assert round(float(rest[0].amount), 2) == expected_rest


# ---------------------------------------------------------------------------
# Гарантия уровня БД (только профиль prod)
# ---------------------------------------------------------------------------

def test_unique_index_prevents_duplicate_remainder(db, make_card):
    """Миграция 0004: частичный unique-индекс делает гонку физически невозможной.

    В models.py этого индекса нет — он существует только в боевом DDL,
    поэтому тест осмыслен именно на профиле prod.
    """
    from conftest import SCHEMA_PROFILE
    if SCHEMA_PROFILE != "prod":
        pytest.skip("индекс uq_remainder_per_card есть только в DDL прода")

    card = make_card(total_amount=100.0)
    db.execute(text(
        "INSERT INTO transactions (card_id, company_name, amount, is_document, "
        "is_warehouse_writeoff, invoice_number) "
        "VALUES (:c, 'x', 100, 0, 0, NULL)"), {"c": card.id})
    db.commit()

    with pytest.raises(Exception) as exc:
        db.execute(text(
            "INSERT INTO transactions (card_id, company_name, amount, is_document, "
            "is_warehouse_writeoff, invoice_number) "
            "VALUES (:c, 'y', 50, 0, 0, NULL)"), {"c": card.id})
        db.commit()
    assert "UNIQUE" in str(exc.value).upper()
    db.rollback()
