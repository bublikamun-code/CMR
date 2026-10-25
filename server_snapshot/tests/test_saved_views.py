"""Сохранённые виды доски: сервер — источник правды (волна D).

До волны D таблица saved_views существовала без роутов, а фронт держал виды
в localStorage — смена браузера теряла их. Здесь проверен контракт, на который
переведён shell-v2-board.js: список только своих, upsert по имени
(перезапись фильтров), удаление чужого — 404.
"""
import pytest

import models


def _views(client, headers):
    return client.get("/kanban/saved-views", headers=headers)


def test_list_is_empty_and_auth_required(client, db, manager):
    _, headers = manager
    assert client.get("/kanban/saved-views").status_code == 401
    r = _views(client, headers)
    assert r.status_code == 200 and r.json() == []


def test_upsert_creates_then_overwrites_same_name(client, db, manager):
    _, headers = manager
    r = client.post("/kanban/saved-views", headers=headers, json={
        "name": "Мои сборки", "q": "сборка", "mode": "list", "priority": "2",
    })
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["name"] == "Мои сборки"
    assert first["q"] == "сборка" and first["mode"] == "list"

    r = client.post("/kanban/saved-views", headers=headers, json={
        "name": "Мои сборки", "q": "другой поиск", "mode": "board",
    })
    assert r.status_code == 200, r.text
    second = r.json()
    # Одно имя — один вид: перезапись, не дубль.
    assert second["id"] == first["id"]
    assert second["q"] == "другой поиск" and second["mode"] == "board"

    rows = _views(client, headers).json()
    assert len(rows) == 1 and rows[0]["id"] == first["id"]


def test_views_are_private_between_users(client, db, manager, make_user):
    _, headers = manager
    _, other_headers = make_user("manager")
    client.post("/kanban/saved-views", headers=headers, json={"name": "Вид А"})

    other = _views(client, other_headers).json()
    assert other == []

    # Чужой id не виден и не удаляем: фильтр по created_by.
    row_id = _views(client, headers).json()[0]["id"]
    assert client.delete(f"/kanban/saved-views/{row_id}",
                         headers=other_headers).status_code == 404


def test_delete_removes_own_view(client, db, manager):
    _, headers = manager
    view_id = client.post("/kanban/saved-views", headers=headers,
                          json={"name": "Секретарь"}).json()["id"]
    r = client.delete(f"/kanban/saved-views/{view_id}", headers=headers)
    assert r.status_code == 200
    assert _views(client, headers).json() == []
    assert client.delete(f"/kanban/saved-views/{view_id}",
                         headers=headers).status_code == 404


def test_name_is_required_and_bounded(client, db, manager):
    _, headers = manager
    assert client.post("/kanban/saved-views", headers=headers,
                       json={"name": ""}).status_code == 422
    assert client.post("/kanban/saved-views", headers=headers,
                       json={"name": "x" * 41}).status_code == 422
    # Пустое после trim — 400, а не тихая запись
    assert client.post("/kanban/saved-views", headers=headers,
                       json={"name": "   "}).status_code == 400


def test_row_shape_matches_model_expectations(client, db, manager):
    """target_page/view_type закреплены за доской: фронт v2 единственный
    потребитель, а колонки NOT NULL."""
    _, headers = manager
    client.post("/kanban/saved-views", headers=headers, json={"name": "Форма"})
    db.expire_all()
    row = db.query(models.SavedView).one()
    assert row.target_page == "kanban" and row.view_type == "kanban"
    assert row.created_by is not None and row.tenant_id is not None
