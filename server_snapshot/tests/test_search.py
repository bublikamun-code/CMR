"""Глобальный поиск GET /search: клиенты, сделки, поставщики.

Покрытые ветки:
- кириллический регистр: клиент и карточка находятся запросом в другом
  регистре (главная причина существования Python-side casefold);
- клиент по фрагменту телефона и по УНП;
- карточка по строковому id и по имени привязанного клиента;
- тенант-изоляция: чужие клиенты/карточки не возвращаются;
- кап: 7 совпадающих клиентов → ровно 5;
- пустой/отсутствующий q → все группы пустые;
- поставщик по имени.
"""
from conftest import DEFAULT_TEST_TENANT_ID
import models


def _add_client(db, name, tenant_id=DEFAULT_TEST_TENANT_ID, **kw):
    c = models.Client(name=name, tenant_id=tenant_id, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_supplier(db, name, tenant_id=DEFAULT_TEST_TENANT_ID, **kw):
    s = models.Supplier(name=name, tenant_id=tenant_id, **kw)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_cyrillic_casefold_finds_client_and_card(client, manager, db, make_card):
    _add_client(db, "Мармеладова Ольга")
    make_card(title="Люстра Задание")
    _, h = manager

    body = client.get("/search", params={"q": "мармеладова"}, headers=h).json()
    assert [c["name"] for c in body["clients"]] == ["Мармеладова Ольга"]

    body = client.get("/search", params={"q": "ЛЮСТРА"}, headers=h).json()
    assert [c["title"] for c in body["cards"]] == ["Люстра Задание"]


def test_client_found_by_phone_fragment_and_unp(client, manager, db):
    _add_client(db, "Иванов", phone="+375 29 111-22-33", unp="191456321")
    _, h = manager

    body = client.get("/search", params={"q": "111-22"}, headers=h).json()
    assert [c["name"] for c in body["clients"]] == ["Иванов"]

    body = client.get("/search", params={"q": "191456"}, headers=h).json()
    assert [c["name"] for c in body["clients"]] == ["Иванов"]


def test_card_found_by_string_id_and_client_name(client, manager, db, make_card):
    _add_client(db, "Клиент Поиска")
    cl = db.query(models.Client).filter(models.Client.name == "Клиент Поиска").first()
    card = make_card(title="Сделка без сюрпризов", client_id=cl.id)
    _, h = manager

    body = client.get("/search", params={"q": str(card.id)}, headers=h).json()
    assert any(c["id"] == card.id for c in body["cards"])

    body = client.get("/search", params={"q": "клиент поиска"}, headers=h).json()
    assert any(c["id"] == card.id and c["client_name"] == "Клиент Поиска" for c in body["cards"])


def test_search_is_tenant_scoped(client, two_tenant_users, db, make_card):
    _add_client(db, "Свой клиент", tenant_id=two_tenant_users["first_tenant_id"])
    make_card(title="Сделка своя", tenant_id=two_tenant_users["first_tenant_id"])
    _add_client(db, "Чужой клиент", tenant_id=two_tenant_users["second_tenant_id"])
    make_card(title="Чужая сделка", tenant_id=two_tenant_users["second_tenant_id"])

    # «й кл» входит и в «свой клиент», и в «чужой клиент» — но чужой
    # клиент другого тенанта не возвращается.
    body = client.get(
        "/search", params={"q": "й кл"}, headers=two_tenant_users["first_headers"]
    ).json()
    assert [c["name"] for c in body["clients"]] == ["Свой клиент"]
    assert body["cards"] == []

    body = client.get(
        "/search", params={"q": "сделка"}, headers=two_tenant_users["first_headers"]
    ).json()
    assert [c["title"] for c in body["cards"]] == ["Сделка своя"]


def test_search_caps_each_group_at_five(client, manager, db):
    for i in range(7):
        _add_client(db, f"Повторец {i}")
    _, h = manager

    body = client.get("/search", params={"q": "повторец"}, headers=h).json()
    assert len(body["clients"]) == 5


def test_empty_query_returns_empty_groups(client, manager):
    _, h = manager
    for q in (None, "", "   "):
        params = {} if q is None else {"q": q}
        resp = client.get("/search", params=params, headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"q": q or "", "clients": [], "cards": [], "suppliers": []}


def test_supplier_found_by_name(client, manager, db):
    _add_supplier(db, "Огни Провинции", unp="190000111")
    _, h = manager

    body = client.get("/search", params={"q": "огни пр"}, headers=h).json()
    assert [s["name"] for s in body["suppliers"]] == ["Огни Провинции"]
