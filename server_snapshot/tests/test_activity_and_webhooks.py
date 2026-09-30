"""Тесты ленты активности (/activity) и вебхуков (/webhooks).

Маршруты `DELETE /activity/{entry_id}`, `GET /webhooks/events`,
`DELETE /webhooks/{wh_id}` и `POST /webhooks/{wh_id}/test` до этого файла
не дёргались ни одним тестом (видно в MISS-прогоне
`pytest -q -p tests.route_coverage`).

Особенности подготовки данных:

* авторизация берётся из фикстур (`make_user` возвращает готовые заголовки),
  `/auth/login` не дёргается — slowapi режет частые логины;
* записи ленты и вебхуки заводятся прямо в БД через ORM: в HTTP-API нет
  способа создать запись с произвольным `action` (служебная) и тенантом;
* `POST /webhooks/{id}/test` ОБЯЗАТЕЛЬНО идёт с подменённой доставкой
  (`monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)`) —
  реальный `deliver_delivery` ходит в сеть, в автотестах сети быть не должно.
  Счётчик вызовов stub'а проверяется, чтобы тест не прошёл «молча».
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import models
import routers.webhooks_router as webhooks_router
from webhook_delivery import DeliveryOutcome
from webhook_security import encrypt_secret, obtain_fernet


# ---------------------------------------------------------------------------
# Фабрики данных
# ---------------------------------------------------------------------------


def _entry(db, tenant_id, **overrides):
    """Запись ленты активности. По умолчанию — редактируемый комментарий."""
    values = {"action": "Комментарий", "details": "текст комментария"}
    values.update(overrides)
    entry = models.ActivityLog(tenant_id=tenant_id, **values)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _webhook(db, tenant_id, **overrides):
    """Webhook напрямую в БД (как в tests/test_webhook_security.py).

    URL остаётся невалидируемым по DNS, но и не используется: доставка в этих
    тестах всегда подменена, а DELETE/GET/список секрета сеть не трогают.
    """
    values = {
        "url": "https://example.com/hook",
        "events": '["card.created"]',
        "is_active": True,
    }
    values.update(overrides)
    webhook = models.Webhook(tenant_id=tenant_id, **values)
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


def _row_count(db, model, row_id):
    """Сколько строк с таким id осталось в таблице.

    Через count(), а не db.get(): удалённая запросом строка остаётся в
    identity map тестовой сессии, и get() на ней падает ObjectDeletedError
    вместо того, чтобы вернуть None.
    """
    db.expire_all()
    return db.query(model).filter(model.id == row_id).count()


def _stub_delivery(outcome, calls=None):
    """Подмена deliver_delivery: ничего не отправляет, только считает вызовы."""
    recorded = calls if calls is not None else []

    def stub(delivery_id, payload, *args, **kwargs):
        recorded.append((delivery_id, payload))
        return outcome

    return stub, recorded


# ---------------------------------------------------------------------------
# A. Лента активности
# ---------------------------------------------------------------------------


def test_activity_returns_entries_newest_first_with_real_user_name(
    client, db, make_user
):
    """GET /activity: сортировка по убыванию created_at и реальное имя автора."""
    first, first_headers = make_user("manager", username="автор_первый")
    second, _ = make_user("manager", username="автор_второй")
    base = datetime(2026, 1, 1, 12, 0, 0)

    old = _entry(
        db, first.tenant_id, user_id=first.id,
        details="самый старый", created_at=base,
    )
    new = _entry(
        db, first.tenant_id, user_id=second.id,
        details="самый новый", created_at=base + timedelta(hours=2),
    )
    middle = _entry(
        db, first.tenant_id, user_id=first.id,
        details="средний", created_at=base + timedelta(hours=1),
    )

    response = client.get("/activity", headers=first_headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [new.id, middle.id, old.id]
    # user_name заполняется реальным username автора, а не инициалами из action
    names = {item["id"]: item["user_name"] for item in body}
    assert names[new.id] == "автор_второй"
    assert names[middle.id] == names[old.id] == "автор_первый"
    assert "user_name" in body[0]


def test_activity_filter_by_card_id_returns_only_that_card(
    client, db, make_user, make_card
):
    """Фильтр ?card_id= оставляет только записи указанной карточки."""
    author, headers = make_user("manager")
    wanted = make_card(title="Нужная сделка")
    other = make_card(title="Другая сделка")

    first_wanted = _entry(db, author.tenant_id, user_id=author.id,
                          card_id=wanted.id, details="по нужной")
    _entry(db, author.tenant_id, user_id=author.id, card_id=other.id,
           details="по другой")
    second_wanted = _entry(db, author.tenant_id, user_id=author.id,
                           card_id=wanted.id, details="вторая по нужной")

    response = client.get(f"/activity?card_id={wanted.id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert {item["id"] for item in body} == {first_wanted.id, second_wanted.id}
    assert {item["details"] for item in body} == {"по нужной", "вторая по нужной"}


@pytest.mark.access
def test_activity_filter_by_foreign_tenant_card_returns_404(
    client, db, make_user, make_card, two_tenant_users
):
    """Карточка чужого тенанта не видна: 404, а не пустая выдача."""
    _, headers = make_user("admin", username="админ_своего_тенанта")
    foreign_card = make_card(
        title="Сделка чужого тенанта",
        tenant_id=two_tenant_users["second_tenant_id"],
    )

    response = client.get(f"/activity?card_id={foreign_card.id}", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Карточка не найдена"


def test_activity_limit_caps_number_of_entries(client, db, make_user):
    """?limit= ограничивает размер выдачи."""
    author, headers = make_user("manager")
    base = datetime(2026, 2, 1, 9, 0, 0)
    created = [
        _entry(db, author.tenant_id, user_id=author.id, details=f"запись {i}",
               created_at=base + timedelta(minutes=i))
        for i in range(5)
    ]

    limited = client.get("/activity?limit=2", headers=headers)

    assert limited.status_code == 200
    body = limited.json()
    assert len(body) == 2
    # при обрезке остаются самые свежие записи
    assert [item["details"] for item in body] == ["запись 4", "запись 3"]
    assert {item["id"] for item in body} == {created[4].id, created[3].id}


def test_activity_requires_authentication(client, db, make_user):
    """Без токена лента недоступна."""
    author, _ = make_user("manager")
    _entry(db, author.tenant_id, user_id=author.id)

    assert client.get("/activity").status_code == 401
    assert client.get("/activity?limit=1").status_code == 401


def test_author_can_patch_own_comment_and_details_are_stripped(
    client, db, make_user
):
    """Автор правит свой комментарий; details сохраняется с обрезкой пробелов."""
    author, headers = make_user("manager")
    entry = _entry(db, author.tenant_id, user_id=author.id,
                   details="исходный комментарий")

    response = client.patch(
        f"/activity/{entry.id}", headers=headers,
        json={"details": "   исправленный комментарий   "},
    )

    assert response.status_code == 200
    assert response.json()["details"] == "исправленный комментарий"
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id).details == "исправленный комментарий"


def test_patch_activity_rejects_empty_details(client, db, make_user):
    """Пустой details не проходит валидацию ActivityDetailsUpdate(min_length=1)."""
    author, headers = make_user("manager")
    entry = _entry(db, author.tenant_id, user_id=author.id, details="было")

    response = client.patch(
        f"/activity/{entry.id}", headers=headers, json={"details": ""}
    )

    assert response.status_code == 422
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id).details == "было"


@pytest.mark.access
def test_manager_cannot_patch_foreign_comment(client, db, make_user):
    """Чужой комментарий менеджеру не по силам: 403 с понятным текстом."""
    author, _ = make_user("manager", username="автор_комментария")
    other, other_headers = make_user("manager", username="чужой_менеджер")
    entry = _entry(db, author.tenant_id, user_id=author.id,
                   details="чужой комментарий")

    response = client.patch(
        f"/activity/{entry.id}", headers=other_headers, json={"details": "правка"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Можно изменять только свои комментарии"
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id).details == "чужой комментарий"


@pytest.mark.access
def test_admin_can_patch_foreign_comment(client, db, make_user):
    """Администратор правит комментарий любого автора своего тенанта."""
    author, _ = make_user("manager", username="автор_комментария")
    _, admin_headers = make_user("admin", username="админ_редактор")
    entry = _entry(db, author.tenant_id, user_id=author.id,
                   details="чужой комментарий")

    response = client.patch(
        f"/activity/{entry.id}", headers=admin_headers,
        json={"details": "правка админа"},
    )

    assert response.status_code == 200
    assert response.json()["details"] == "правка админа"


@pytest.mark.parametrize(
    "action", ["Статус", "Создание карточки", "Импорт почты"],
)
def test_service_entry_cannot_be_patched(client, db, make_user, action):
    """Записи с action != 'Комментарий' не редактируются даже автором."""
    author, headers = make_user("manager")
    entry = _entry(db, author.tenant_id, user_id=author.id, action=action,
                   details="служебная запись")

    response = client.patch(
        f"/activity/{entry.id}", headers=headers, json={"details": "правка"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Эту запись нельзя изменять"
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id).details == "служебная запись"


@pytest.mark.access
def test_patch_activity_entry_of_foreign_tenant_returns_404(
    client, db, make_user, two_tenant_users
):
    """Запись чужого тенанта не патчится даже администратором."""
    _, admin_headers = make_user("admin", username="админ_своего_тенанта")
    foreign_entry = _entry(
        db, two_tenant_users["second_tenant_id"],
        user_id=two_tenant_users["second_user"].id, details="чужой тенант",
    )

    response = client.patch(
        f"/activity/{foreign_entry.id}", headers=admin_headers,
        json={"details": "правка"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Запись не найден"


def test_author_deletes_own_comment(client, db, make_user, make_card):
    """DELETE /activity/{id}: автор удаляет комментарий, записи больше нет."""
    author, headers = make_user("manager")
    card = make_card(title="Сделка для комментария")
    keep = _entry(db, author.tenant_id, user_id=author.id, card_id=card.id,
                  details="остаётся")
    doomed = _entry(db, author.tenant_id, user_id=author.id, card_id=card.id,
                    details="удаляется")

    response = client.delete(f"/activity/{doomed.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["detail"] == "Комментарий удалён"
    listed = client.get(f"/activity?card_id={card.id}", headers=headers)
    assert [item["id"] for item in listed.json()] == [keep.id]
    assert _row_count(db, models.ActivityLog, doomed.id) == 0


@pytest.mark.access
def test_admin_deletes_foreign_comment(client, db, make_user):
    """Администратор удаляет чужой комментарий."""
    author, _ = make_user("manager", username="автор_комментария")
    _, admin_headers = make_user("admin", username="админ_удаляет")
    entry = _entry(db, author.tenant_id, user_id=author.id,
                   details="чужой комментарий")

    response = client.delete(f"/activity/{entry.id}", headers=admin_headers)

    assert response.status_code == 200
    assert _row_count(db, models.ActivityLog, entry.id) == 0


def test_delete_unknown_activity_entry_returns_404(client, make_user):
    """Несуществующий id — 404, а не 500."""
    _, headers = make_user("manager")

    assert client.delete("/activity/424242", headers=headers).status_code == 404


def test_delete_service_entry_returns_400(client, db, make_user):
    """Служебная запись (action != 'Комментарий') не удаляется."""
    author, headers = make_user("manager")
    entry = _entry(db, author.tenant_id, user_id=author.id, action="Статус",
                   details="служебная запись")

    response = client.delete(f"/activity/{entry.id}", headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Эту запись нельзя изменять"
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id) is not None


@pytest.mark.access
def test_manager_cannot_delete_foreign_comment(client, db, make_user):
    """DELETE чужого комментария менеджером — 403, запись остаётся."""
    author, _ = make_user("manager", username="автор_комментария")
    other, other_headers = make_user("manager", username="чужой_менеджер")
    entry = _entry(db, author.tenant_id, user_id=author.id,
                   details="чужой комментарий")

    response = client.delete(f"/activity/{entry.id}", headers=other_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Можно изменять только свои комментарии"
    db.expire_all()
    assert db.get(models.ActivityLog, entry.id) is not None


@pytest.mark.access
def test_delete_activity_entry_of_foreign_tenant_returns_404(
    client, db, make_user, two_tenant_users
):
    """Запись чужого тенанта не удаляется даже администратором."""
    _, admin_headers = make_user("admin", username="админ_своего_тенанта")
    foreign_entry = _entry(
        db, two_tenant_users["second_tenant_id"],
        user_id=two_tenant_users["second_user"].id, details="чужой тенант",
    )

    response = client.delete(f"/activity/{foreign_entry.id}", headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Запись не найден"
    db.expire_all()
    assert db.get(models.ActivityLog, foreign_entry.id) is not None


# ---------------------------------------------------------------------------
# B. Вебхуки
# ---------------------------------------------------------------------------


@pytest.mark.access
def test_webhook_events_listed_for_admin(client, admin, superadmin):
    """GET /webhooks/events отдаёт справочник событий админу и суперадмину."""
    _, admin_headers = admin
    _, superadmin_headers = superadmin

    for headers in (admin_headers, superadmin_headers):
        response = client.get("/webhooks/events", headers=headers)
        assert response.status_code == 200
        events = response.json()
        for expected in (
            "card.created", "card.updated", "card.deleted",
            "payment.created", "document.created",
        ):
            assert expected in events


@pytest.mark.access
def test_webhook_events_forbidden_for_manager(client, manager):
    """Справочник событий скрыт от менеджера."""
    _, headers = manager

    assert client.get("/webhooks/events", headers=headers).status_code == 403
    assert client.get("/webhooks/events").status_code == 401


def test_webhook_list_never_exposes_secret(client, db, admin):
    """GET /webhooks/ отдаёт только признак наличия секрета, не сам секрет."""
    _, headers = admin
    fernet = obtain_fernet()
    plaintext = "подпись-получателя"
    with_secret = _webhook(db, admin[0].tenant_id,
                           secret=encrypt_secret(plaintext, fernet))
    without_secret = _webhook(db, admin[0].tenant_id, secret=None)

    response = client.get("/webhooks/", headers=headers)

    assert response.status_code == 200
    body = {item["id"]: item for item in response.json()}
    assert set(body) == {with_secret.id, without_secret.id}
    assert body[with_secret.id]["has_secret"] is True
    assert body[without_secret.id]["has_secret"] is False
    # ни ключа secret, ни самого значения (и шифротекста) в ответе нет
    assert "secret" not in body[with_secret.id]
    assert plaintext not in response.text
    db.expire_all()
    stored = db.get(models.Webhook, with_secret.id).secret
    assert stored not in response.text


@pytest.mark.access
def test_admin_deletes_webhook_and_it_leaves_the_list(client, db, admin):
    """DELETE /webhooks/{id}: вебхук удаляется и исчезает из выдачи."""
    _, headers = admin
    doomed = _webhook(db, admin[0].tenant_id)
    keeper = _webhook(db, admin[0].tenant_id, events='["card.updated"]')

    response = client.delete(f"/webhooks/{doomed.id}", headers=headers)

    # Роутер отвечает общим сообщением «Удалено» (не «Webhook удалён»).
    assert response.status_code == 200
    assert response.json()["message"] == "Удалено"
    listed = client.get("/webhooks/", headers=headers)
    assert [item["id"] for item in listed.json()] == [keeper.id]
    assert _row_count(db, models.Webhook, doomed.id) == 0


def test_delete_unknown_webhook_returns_404(client, admin):
    """Несуществующий webhook — 404."""
    _, headers = admin

    response = client.delete("/webhooks/424242", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Webhook не найден"


@pytest.mark.access
def test_delete_webhook_of_foreign_tenant_returns_404(
    client, db, two_tenant_users
):
    """Вебхук чужого тенанта не удаляется: 404, запись остаётся."""
    foreign = _webhook(db, two_tenant_users["second_tenant_id"])

    response = client.delete(
        f"/webhooks/{foreign.id}", headers=two_tenant_users["first_headers"]
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Webhook не найден"
    db.expire_all()
    assert db.get(models.Webhook, foreign.id) is not None


@pytest.mark.access
def test_delete_webhook_forbidden_for_manager(client, db, admin, manager):
    """Менеджер не может удалить вебхук."""
    _, manager_headers = manager
    webhook = _webhook(db, admin[0].tenant_id)

    response = client.delete(f"/webhooks/{webhook.id}", headers=manager_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Только администратор"
    db.expire_all()
    assert db.get(models.Webhook, webhook.id) is not None


def test_test_webhook_success_records_delivery(client, db, admin, monkeypatch):
    """POST /webhooks/{id}/test: успешная доставка попадает в аудит.

    Сеть не используется: deliver_delivery подменена stub'ом, который обязан
    быть вызван ровно один раз с payload тестового события.
    """
    _, headers = admin
    webhook = _webhook(db, admin[0].tenant_id)
    outcome = DeliveryOutcome(
        correlation_id="corr-1", status="succeeded", attempts=1,
        response_status=200,
    )
    stub, calls = _stub_delivery(outcome)
    monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)

    response = client.post(f"/webhooks/{webhook.id}/test", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["success"] is True
    assert body["delivery_status"] == "succeeded"
    assert body["attempts"] == 1
    assert body["correlation_id"] == "corr-1"
    assert body["error_code"] is None

    # Доставка действительно была инициирована — тест не «прошёл молча».
    assert len(calls) == 1
    delivery_id, payload = calls[0]
    assert payload["event"] == "test"
    assert payload["data"] == {"message": "Тестовый webhook от CRM"}

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery is not None
    assert delivery.webhook_id == webhook.id
    assert delivery.event == "test"
    assert delivery.tenant_id == admin[0].tenant_id


def test_test_webhook_failure_is_reported_without_leaking(client, db, admin, monkeypatch):
    """Неудачная доставка: success=False и стабильный код ошибки."""
    _, headers = admin
    webhook = _webhook(db, admin[0].tenant_id)
    outcome = DeliveryOutcome(
        correlation_id="corr-2", status="failed", attempts=3,
        response_status=None, error_code="timeout",
    )
    stub, calls = _stub_delivery(outcome)
    monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)

    response = client.post(f"/webhooks/{webhook.id}/test", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["delivery_status"] == "failed"
    assert body["attempts"] == 3
    # response_status=None сериализуется как 0, а не как null
    assert body["status"] == 0
    assert body["error_code"] == "timeout"
    assert body["correlation_id"] == "corr-2"
    assert len(calls) == 1


def test_test_unknown_webhook_returns_404(client, admin, monkeypatch):
    """Тест несуществующего вебхука — 404, доставка не создаётся."""
    _, headers = admin
    stub, calls = _stub_delivery(
        DeliveryOutcome(correlation_id="corr-x", status="succeeded", attempts=1)
    )
    monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)

    response = client.post("/webhooks/424242/test", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Webhook не найден"
    assert calls == []


@pytest.mark.access
def test_test_webhook_of_foreign_tenant_returns_404(
    client, db, two_tenant_users, monkeypatch
):
    """Вебхук чужого тенанта не тестируется: 404, доставки нет."""
    foreign = _webhook(db, two_tenant_users["second_tenant_id"])
    stub, calls = _stub_delivery(
        DeliveryOutcome(correlation_id="corr-y", status="succeeded", attempts=1)
    )
    monkeypatch.setattr(webhooks_router, "deliver_delivery", stub)

    response = client.post(
        f"/webhooks/{foreign.id}/test",
        headers=two_tenant_users["first_headers"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Webhook не найден"
    assert calls == []


@pytest.mark.access
def test_test_webhook_forbidden_for_manager(client, db, admin, manager, monkeypatch):
    """Менеджер не может отправить тестовую доставку."""
    _, manager_headers = manager
    webhook = _webhook(db, admin[0].tenant_id)

    def must_not_call(*args, **kwargs):
        raise AssertionError("менеджер не должен инициировать доставку")

    monkeypatch.setattr(webhooks_router, "deliver_delivery", must_not_call)

    response = client.post(f"/webhooks/{webhook.id}/test", headers=manager_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Только администратор"
