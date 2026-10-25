"""Покрытие событий вебхуков: каждое заявленное событие реально стреляет.

Волна D (25.10): AVAILABLE_EVENTS в webhooks_router перечисляет 13 событий,
но до этой волны из мутаций стреляли только три (client.created, card.updated,
payment.created) — подписчик на остальные получал тишину. UI настройки вебхуков
при этом даёт отметить любой пункт списка, то есть обещает то, чего нет.

Здесь каждая бизнес-мутация прогоняется через API с одним вебхуком,
подписанным на все события, и доставка подменена заглушкой (как в
test_activity_and_webhooks.py): фиксируем, какие события ушли. Сеть тесты
не трогают.
"""
import json

import pytest

import models
import routers.webhooks_router as webhooks_router
from tests.conftest import DEFAULT_TEST_TENANT_ID

ALL_EVENTS = [
    "card.created", "card.updated", "card.deleted",
    "client.created", "client.updated", "client.deleted",
    "supplier.created", "supplier.updated", "supplier.deleted",
    "payment.created", "payment.updated",
    "document.created", "document.updated",
]


@pytest.fixture
def collector(db, monkeypatch):
    """Вебхук на все события + подменённая доставка. Возвращает список
    записанных payload'ов (словарей с ключами event/data/timestamp)."""
    webhook = models.Webhook(
        url="https://example.com/hook",
        events=json.dumps(ALL_EVENTS),
        is_active=True,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    db.add(webhook)
    db.commit()

    recorded = []

    def stub(delivery_id, payload, *args, **kwargs):
        recorded.append(payload)

    monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)
    return recorded


def _events(recorded):
    return [p["event"] for p in recorded]


def test_card_lifecycle_fires_created_updated_deleted(client, db, manager, collector):
    _, headers = manager
    r = client.post("/kanban/cards", headers=headers, json={
        "title": "Вебхук-сделка", "status": "Новый запрос", "total_amount": 100.0,
    })
    assert r.status_code == 200, r.text
    card_id = r.json()["id"]

    r = client.patch(f"/kanban/cards/{card_id}/status", headers=headers,
                     json={"status": "Сборка"})
    assert r.status_code == 200, r.text

    r = client.delete(f"/kanban/cards/{card_id}", headers=headers)
    assert r.status_code == 200, r.text

    assert _events(collector) == ["card.created", "card.updated", "card.deleted"]
    created = collector[0]["data"]
    assert created["id"] == card_id and created["title"] == "Вебхук-сделка"
    deleted = collector[2]["data"]
    assert deleted["id"] == card_id and deleted["permanent"] is False


def test_client_lifecycle_fires_all_three_events(client, db, manager, admin, collector):
    _, headers = manager
    _, admin_headers = admin
    r = client.post("/clients", headers=headers, json={"name": "Вебхук-клиент"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]

    r = client.patch(f"/clients/{cid}", headers=headers, json={"phone": "+375001112233"})
    assert r.status_code == 200, r.text

    r = client.delete(f"/clients/{cid}", headers=admin_headers)
    assert r.status_code == 200, r.text

    assert _events(collector) == ["client.created", "client.updated", "client.deleted"]


def test_supplier_lifecycle_fires_all_three_events(client, db, manager, admin, collector):
    _, headers = manager
    _, admin_headers = admin
    r = client.post("/suppliers", headers=headers, json={"name": "Вебхук-поставщик"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    r = client.patch(f"/suppliers/{sid}", headers=headers, json={"phone": "+375009998877"})
    assert r.status_code == 200, r.text

    r = client.delete(f"/suppliers/{sid}", headers=admin_headers)
    assert r.status_code == 200, r.text

    assert _events(collector) == ["supplier.created", "supplier.updated", "supplier.deleted"]


def test_payment_created_and_updated_fire(client, db, manager, make_card, collector):
    _, headers = manager
    card = make_card(status="Сборка", store_location="Склад")

    r = client.post(f"/payments/trigger_from_card/{card.id}", headers=headers,
                    json={"store_location": "Склад"})
    assert r.status_code == 200, r.text
    tx_id = r.json()["id"]

    r = client.patch(f"/payments/transactions/{tx_id}", headers=headers,
                     json={"is_calculated": True})
    assert r.status_code == 200, r.text

    assert _events(collector) == ["payment.created", "payment.updated"]
    assert collector[1]["data"]["id"] == tx_id


def test_document_created_and_updated_fire(client, db, manager, collector):
    _, headers = manager
    r = client.post("/nakladnye", headers=headers, json={
        "doc_series": "ВХ", "doc_number": "wh-001", "supplier_name": "Поставщик",
    })
    assert r.status_code == 200, r.text
    nak_id = r.json()["id"]

    r = client.patch(f"/nakladnye/{nak_id}", headers=headers,
                     json={"amount": 150.0})
    assert r.status_code == 200, r.text

    assert _events(collector) == ["document.created", "document.updated"]
    assert collector[0]["data"]["doc_number"] == "wh-001"
