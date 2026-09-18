"""Справочники магазинов и статусов (Этап 2.3 плана замены фронта).

Границы: чтение — любому авторизованному; создание и правка — только админам;
дубликаты имён — 400, а не 500; unknown id — 404.
"""


def test_stores_read_requires_auth(client):
    assert client.get("/dictionaries/stores").status_code == 401


def test_store_crud_by_admin(client, admin):
    _, headers = admin
    r = client.post("/dictionaries/stores", json={"name": "Матусевича", "address": "ул. Матусевича, 10", "phone": "+375 17 200-11-22"}, headers=headers)
    assert r.status_code == 200, r.text
    store = r.json()
    assert store["is_active"] is True

    r = client.get("/dictionaries/stores", headers=headers)
    assert r.status_code == 200
    assert any(s["name"] == "Матусевича" for s in r.json())

    r = client.post("/dictionaries/stores", json={"name": "Матусевича"}, headers=headers)
    assert r.status_code == 400, "дубликат названия — 400"

    r = client.patch(f"/dictionaries/stores/{store['id']}", json={"phone": "+375 17 200-99-99"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["phone"] == "+375 17 200-99-99"

    r = client.patch("/dictionaries/stores/999999", json={"name": "Чужой"}, headers=headers)
    assert r.status_code == 404


def test_store_write_forbidden_for_manager(client, make_user):
    _, headers = make_user("manager")
    assert client.post("/dictionaries/stores", json={"name": "Тест"}, headers=headers).status_code == 403
    assert client.patch("/dictionaries/stores/1", json={"name": "Тест"}, headers=headers).status_code == 403


def test_status_crud_by_admin_and_ordering(client, admin):
    _, headers = admin
    for name, position in [("Новый запрос", 0), ("На списание", 4)]:
        r = client.post("/dictionaries/statuses", json={"name": name, "position": position, "color": "#4f46e5"}, headers=headers)
        assert r.status_code == 200, r.text

    r = client.get("/dictionaries/statuses", headers=headers)
    names = [s["name"] for s in r.json()]
    assert names.index("Новый запрос") < names.index("На списание"), "порядок по position"

    r = client.post("/dictionaries/statuses", json={"name": "Новый запрос"}, headers=headers)
    assert r.status_code == 400, "дубликат названия — 400"

    status = client.get("/dictionaries/statuses", headers=headers).json()[0]
    r = client.patch(f"/dictionaries/statuses/{status['id']}", json={"color": "#00aa00"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["color"] == "#00aa00"


def test_status_write_forbidden_for_manager(client, make_user):
    _, headers = make_user("manager")
    assert client.post("/dictionaries/statuses", json={"name": "Левый статус"}, headers=headers).status_code == 403
    assert client.patch("/dictionaries/statuses/1", json={"name": "Левый"}, headers=headers).status_code == 403
