"""Пункт 16 плана V2-WORKPLAN-2026-09-22 — серверные страницы.

Главный инвариант модуля: **без параметров ответ прежний**. Доска (v2-boot)
и реестр оплат грузят списки целиком и ждут плоский массив, поэтому режим
страницы включается только явным limit/offset. Каждый тест ниже либо
проверяет обратную совместимость, либо то, что страницы не перекрываются
и не теряют строки на стыках.
"""
from datetime import datetime, timedelta, timezone

import pytest
from db_utils import PAGE_DEFAULT_SIZE, PAGE_MAX_SIZE


def _ids(rows):
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# /kanban/cards — обратная совместимость
# ---------------------------------------------------------------------------

def test_cards_without_params_still_plain_list(client, manager, make_card):
    """РЕГРЕССИЯ: без limit/offset роут обязан вернуть массив, как раньше."""
    _, h = manager
    for i in range(3):
        make_card(title=f"Сделка {i}")

    r = client.get("/kanban/cards", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list), "без параметров ответ должен оставаться списком"
    assert len(body) == 3
    # карточка целиком, а не «страница»: ключей items/total быть не должно
    assert "items" not in body[0] and "total" not in body[0]


def test_cards_without_params_keeps_money_fields(client, manager, make_card, make_transaction):
    """A12 не должен сломаться от пагинации: денежные поля считаются и в массиве."""
    _, h = manager
    card = make_card(title="С деньгами", total_amount=1000.0)
    make_transaction(card, amount=1000.0)

    body = client.get("/kanban/cards", headers=h).json()
    row = next(c for c in body if c["id"] == card.id)
    assert row["remaining"] is not None
    assert row["writeoff_status"] in {"not_written", "partially_written", "written"}


def test_cards_without_params_does_not_advertise_page_headers(client, manager, make_card):
    """cap_list ставит X-Total-Count только при усечении — на 3 карточках его нет."""
    _, h = manager
    make_card()
    r = client.get("/kanban/cards", headers=h)
    assert "x-total-count" not in r.headers
    assert "x-truncated" not in r.headers


# ---------------------------------------------------------------------------
# /kanban/cards — режим страницы
# ---------------------------------------------------------------------------

def test_cards_page_shape_and_total(client, manager, make_card):
    _, h = manager
    for i in range(5):
        make_card(title=f"Сделка {i}")

    r = client.get("/kanban/cards?limit=2", headers=h)
    assert r.status_code == 200, r.text
    page = r.json()
    assert isinstance(page, dict)
    assert set(page) == {"items", "total", "limit", "offset"}
    assert page["total"] == 5, "total — всё число сделок, а не размер страницы"
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert len(page["items"]) == 2
    # заголовок совпадает с телом: клиент уже умеет читать X-Total-Count
    assert r.headers["x-total-count"] == "5"
    assert "x-truncated" not in r.headers, "в режиме страницы усечения нет"


def test_cards_page_keeps_money_fields(client, manager, make_card, make_transaction):
    """_attach_money_fields обязан считаться и для строк страницы."""
    _, h = manager
    card = make_card(title="С деньгами", total_amount=500.0)
    make_transaction(card, amount=200.0)

    page = client.get("/kanban/cards?limit=10", headers=h).json()
    row = next(c for c in page["items"] if c["id"] == card.id)
    assert row["remaining"] is not None
    assert row["issued_total"] is not None


def test_cards_pages_tile_the_full_list_exactly(client, manager, make_card):
    """Ключевое: склейка всех страниц == ответу без пагинации, строка в строку."""
    _, h = manager
    for i in range(7):
        make_card(title=f"Сделка {i}")

    full = client.get("/kanban/cards", headers=h).json()
    collected = []
    offset = 0
    while True:
        page = client.get(f"/kanban/cards?limit=3&offset={offset}", headers=h).json()
        assert page["total"] == len(full)
        collected.extend(page["items"])
        if not page["items"] or offset + page["limit"] >= page["total"]:
            break
        offset += page["limit"]

    assert _ids(collected) == _ids(full), "страницы перекрываются или теряют сделки"
    assert len(collected) == len(full)


def test_cards_page_order_is_position_then_id_desc(client, manager, make_card):
    """Порядок страницы совпадает с прежним: position ASC, затем id DESC."""
    _, h = manager
    a = make_card(title="A", position=0)
    b = make_card(title="B", position=0)
    c = make_card(title="C", position=1)
    d = make_card(title="D", position=1)

    full = client.get("/kanban/cards", headers=h).json()
    # сначала колонка с меньшей position, внутри неё — id по убыванию
    assert _ids(full) == [b.id, a.id, d.id, c.id]

    first = client.get("/kanban/cards?limit=2", headers=h).json()["items"]
    second = client.get("/kanban/cards?limit=2&offset=2", headers=h).json()["items"]
    assert _ids(first) == [b.id, a.id]
    assert _ids(second) == [d.id, c.id]


def test_cards_page_order_with_equal_positions_is_total(client, manager, make_card):
    """На доске position у всех карточек равен 0 — порядок держит id DESC.

    Без уникального второго ключа страницы перекрывались бы: SQLite не
    гарантирует порядок строк с равным значением ORDER BY между запросами.
    """
    _, h = manager
    made = [make_card(title=f"Сделка {i}") for i in range(6)]
    expected = [c.id for c in reversed(made)]

    full = client.get("/kanban/cards", headers=h).json()
    assert _ids(full) == expected

    seen = []
    for offset in (0, 2, 4):
        page = client.get(f"/kanban/cards?limit=2&offset={offset}", headers=h).json()
        seen.extend(_ids(page["items"]))
    assert seen == expected


def test_cards_page_excludes_deleted_from_items_and_total(client, manager, make_card):
    _, h = manager
    live = [make_card(title=f"Живая {i}") for i in range(3)]
    dead = make_card(title="В корзине", is_deleted=True)

    page = client.get("/kanban/cards?limit=50", headers=h).json()
    assert page["total"] == 3, "удалённые не должны попадать в total"
    assert dead.id not in _ids(page["items"])
    assert {c["id"] for c in page["items"]} == {c.id for c in live}


def test_cards_offset_without_limit_uses_default_page_size(client, manager, make_card):
    """offset без limit — тоже страница; молча отдать весь список нельзя."""
    _, h = manager
    for i in range(4):
        make_card(title=f"Сделка {i}")

    page = client.get("/kanban/cards?offset=2", headers=h).json()
    assert page["offset"] == 2
    assert page["limit"] == PAGE_DEFAULT_SIZE
    assert page["total"] == 4
    assert len(page["items"]) == 2


def test_cards_offset_beyond_total_returns_empty_page(client, manager, make_card):
    _, h = manager
    make_card()
    r = client.get("/kanban/cards?limit=10&offset=100", headers=h)
    assert r.status_code == 200
    page = r.json()
    assert page["items"] == []
    assert page["total"] == 1, "total не зависит от offset"


def test_cards_empty_board_page(client, manager):
    page = client.get("/kanban/cards?limit=10", headers=manager[1]).json()
    assert page == {"items": [], "total": 0, "limit": 10, "offset": 0}


@pytest.mark.parametrize("query", [
    "limit=0", "limit=-1", "limit=abc", f"limit={PAGE_MAX_SIZE + 1}",
    "offset=-1", "offset=abc", "limit=10&offset=-5",
])
def test_cards_invalid_page_params_rejected_422(client, manager, query):
    """Невалидные значения — 422 от Query(ge=/le=), не безопасный дефолт молча."""
    r = client.get(f"/kanban/cards?{query}", headers=manager[1])
    assert r.status_code == 422, f"{query}: {r.status_code} {r.text}"


def test_cards_max_page_size_accepted(client, manager, make_card):
    make_card()
    r = client.get(f"/kanban/cards?limit={PAGE_MAX_SIZE}", headers=manager[1])
    assert r.status_code == 200
    assert r.json()["limit"] == PAGE_MAX_SIZE


def test_cards_page_requires_auth(client, make_card):
    make_card()
    assert client.get("/kanban/cards?limit=10").status_code in (401, 403)


# ---------------------------------------------------------------------------
# /payments/transactions — обратная совместимость
# ---------------------------------------------------------------------------

def _registry(client, h, **params):
    return client.get("/payments/transactions", headers=h, params=params)


def test_transactions_without_params_still_plain_list(client, manager, make_card, make_transaction):
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    make_transaction(card, amount=100.0)

    for params in ({}, {"grouped": "false"}):
        r = _registry(client, h, **params)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list), f"без limit/offset ответ должен быть списком: {params}"


def test_transactions_without_params_keeps_legacy_total_header(client, manager, make_card, make_transaction):
    """Прежний контракт: X-Total-Count = число СЫРЫХ строк, даже в grouped."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=300.0)
    make_transaction(card, amount=200.0)
    make_transaction(card, amount=100.0, invoice_number="ТН-1")

    r = _registry(client, h)
    assert len(r.json()) == 1, "grouped схлопывает две записи в одну строку сделки"
    assert r.headers["x-total-count"] == "2", "заголовок по-прежнему про сырые строки"


# ---------------------------------------------------------------------------
# /payments/transactions — режим страницы
# ---------------------------------------------------------------------------

def _seed_registry(make_card, make_transaction, cards=6):
    """Сделки с РАЗНЫМИ датами: порядок grouped-реестра — сортировка по дате."""
    base = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    cards_made = []
    for i in range(cards):
        card = make_card(title=f"Сделка {i}", total_amount=100.0 * (i + 1))
        make_transaction(card, amount=100.0 * (i + 1), date=base + timedelta(days=i))
        cards_made.append(card)
    return cards_made


def test_transactions_grouped_page_shape(client, manager, make_card, make_transaction):
    _, h = manager
    _seed_registry(make_card, make_transaction)
    full = _registry(client, h).json()

    r = _registry(client, h, limit=2)
    assert r.status_code == 200, r.text
    page = r.json()
    assert set(page) == {"items", "total", "limit", "offset"}
    assert page["total"] == len(full), "total считает СТРОКИ СДЕЛОК, как и items"
    assert page["limit"] == 2 and page["offset"] == 0
    assert len(page["items"]) == 2
    assert r.headers["x-total-count"] == str(len(full))


def test_transactions_grouped_pages_tile_the_full_list(client, manager, make_card, make_transaction):
    _, h = manager
    _seed_registry(make_card, make_transaction, cards=7)
    full = _registry(client, h).json()

    collected, offset = [], 0
    while offset < len(full):
        page = _registry(client, h, limit=3, offset=offset).json()
        assert page["total"] == len(full)
        collected.extend(page["items"])
        offset += 3

    assert _ids(collected) == _ids(full), "страницы группового реестра разъехались"


def test_transactions_grouped_page_preserves_date_order(client, manager, make_card, make_transaction):
    """Порядок страницы — тот же, что без пагинации (дата по убыванию)."""
    _, h = manager
    _seed_registry(make_card, make_transaction, cards=5)
    full = _registry(client, h).json()

    page = _registry(client, h, limit=2).json()
    assert _ids(page["items"]) == _ids(full[:2])
    dates = [row["date"] for row in page["items"]]
    assert dates == sorted(dates, reverse=True)


def test_transactions_raw_page_shape_and_sql_slice(client, manager, make_card, make_transaction):
    """grouped=false: элемент = сырая запись, total = число сырых записей."""
    _, h = manager
    cards = _seed_registry(make_card, make_transaction, cards=4)
    make_transaction(cards[0], amount=50.0, invoice_number="ТН-1")

    full = _registry(client, h, grouped="false").json()
    page = _registry(client, h, grouped="false", limit=2).json()
    assert page["total"] == len(full)
    assert len(page["items"]) == 2
    assert _ids(page["items"]) == _ids(full[:2])

    tail = _registry(client, h, grouped="false", limit=2, offset=len(full) - 1).json()
    assert tail["total"] == len(full)
    assert _ids(tail["items"]) == _ids(full[-1:])


def test_transactions_raw_pages_tile_the_full_list(client, manager, make_card, make_transaction):
    _, h = manager
    cards = _seed_registry(make_card, make_transaction, cards=5)
    for c in cards[:2]:
        make_transaction(c, amount=10.0, invoice_number="ТН-2")
    full = _registry(client, h, grouped="false").json()

    collected, offset = [], 0
    while offset < len(full):
        collected.extend(_registry(client, h, grouped="false", limit=2, offset=offset).json()["items"])
        offset += 2
    assert _ids(collected) == _ids(full)


def test_transactions_grouped_flag_still_works_with_pagination(client, manager, make_card, make_transaction):
    """Единственный существующий фильтр (grouped) обязан сочетаться со страницей."""
    _, h = manager
    cards = _seed_registry(make_card, make_transaction, cards=3)
    make_transaction(cards[0], amount=10.0, invoice_number="ТН-1")  # дробит сделку

    grouped = _registry(client, h, limit=50).json()
    raw = _registry(client, h, grouped="false", limit=50).json()
    assert grouped["total"] == 3, "одна строка на сделку"
    assert raw["total"] == 4, "сырые записи: 3 остатка + 1 накладная"


def test_transactions_page_excludes_deleted_card(client, manager, make_card, make_transaction):
    """Фидбек 19.09 жив и в режиме страницы: корзина не попадает ни в items, ни в total."""
    _, h = manager
    live = make_card(title="Живая", total_amount=100.0)
    make_transaction(live, amount=100.0)
    dead = make_card(title="В корзине", total_amount=200.0, is_deleted=True)
    make_transaction(dead, amount=200.0)

    grouped = _registry(client, h, limit=50).json()
    assert grouped["total"] == 1
    # id строки группового реестра — это id базовой транзакции, не карточки
    assert [r["card_id"] for r in grouped["items"]] == [live.id]

    raw = _registry(client, h, grouped="false", limit=50).json()
    assert raw["total"] == 1, "запись удалённой сделки не должна считаться"
    assert [r["card_id"] for r in raw["items"]] == [live.id]


def test_transactions_document_rows_excluded_in_page_mode(client, manager, make_card, make_transaction):
    """is_document=True — это копии документов, в реестр они не входят."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    make_transaction(card, amount=100.0)
    make_transaction(card, amount=100.0, is_document=True)

    page = _registry(client, h, grouped="false", limit=50).json()
    assert page["total"] == 1


def test_transactions_offset_beyond_total_returns_empty_page(client, manager, make_card, make_transaction):
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    make_transaction(card, amount=100.0)

    for params in ({"limit": 10, "offset": 99}, {"grouped": "false", "limit": 10, "offset": 99}):
        page = _registry(client, h, **params).json()
        assert page["items"] == [], params
        assert page["total"] == 1, params


def test_transactions_empty_registry_page(client, manager):
    page = _registry(client, manager[1], limit=10).json()
    assert page == {"items": [], "total": 0, "limit": 10, "offset": 0}


@pytest.mark.parametrize("query", [
    {"limit": 0}, {"limit": -1}, {"limit": PAGE_MAX_SIZE + 1},
    {"offset": -1}, {"limit": 10, "offset": -5},
])
def test_transactions_invalid_page_params_rejected_422(client, manager, query):
    r = _registry(client, manager[1], **query)
    assert r.status_code == 422, f"{query}: {r.status_code} {r.text}"


def test_transactions_page_requires_auth(client):
    assert client.get("/payments/transactions?limit=10").status_code in (401, 403)
