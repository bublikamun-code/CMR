"""Комментарии сделки: POST /activity (+ связь с существующей лентой).

Покрытые ветки:
- 200: менеджер создаёт комментарий на карточке своего тенанта; запись видна
  в GET /activity?card_id= с action="Комментарий" и именем автора;
- 422: пустой details; details длиннее 4000 символов;
- 404: карточка другого тенанта (запись не создаётся); несуществующий card_id;
- "card.commented" заявлен в AVAILABLE_EVENTS вебхуков.
"""
from conftest import DEFAULT_TEST_TENANT_ID
from routers.webhooks_router import AVAILABLE_EVENTS


def test_manager_creates_comment_and_it_is_listed(client, manager, make_card):
    card = make_card(title="Сделка с комментарием")
    _, h = manager
    resp = client.post(
        "/activity",
        json={"card_id": card.id, "details": "Позвонить после обеда"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "Комментарий"
    assert body["card_id"] == card.id
    assert body["details"] == "Позвонить после обеда"
    assert body["user_name"]

    listed = client.get(f"/activity?card_id={card.id}", headers=h).json()
    match = [e for e in listed if e["id"] == body["id"]]
    assert len(match) == 1
    assert match[0]["details"] == "Позвонить после обеда"
    assert match[0]["user_name"] == body["user_name"]


def test_comment_rejects_empty_and_oversized_details(client, manager, make_card):
    card = make_card()
    _, h = manager
    empty = client.post("/activity", json={"card_id": card.id, "details": ""}, headers=h)
    assert empty.status_code == 422
    oversized = client.post(
        "/activity", json={"card_id": card.id, "details": "x" * 4001}, headers=h
    )
    assert oversized.status_code == 422


def test_comment_404_for_foreign_tenant_card(client, two_tenant_users, make_card):
    card = make_card(title="Чужая сделка", tenant_id=two_tenant_users["second_tenant_id"])

    resp = client.post(
        "/activity",
        json={"card_id": card.id, "details": "попытка из другого тенанта"},
        headers=two_tenant_users["first_headers"],
    )
    assert resp.status_code == 404
    # Чужой комментарий не создан: лента тенанта карточки чиста.
    listed = client.get(
        f"/activity?card_id={card.id}", headers=two_tenant_users["second_headers"]
    ).json()
    assert listed == []


def test_comment_404_for_unknown_card(client, manager):
    _, h = manager
    resp = client.post(
        "/activity", json={"card_id": 999999, "details": "текст"}, headers=h
    )
    assert resp.status_code == 404


def test_card_commented_event_is_declared():
    assert "card.commented" in AVAILABLE_EVENTS
