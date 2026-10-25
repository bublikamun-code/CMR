"""Восстановление очереди вебхуков после рестарта (волна D, 0024).

Сценарий: процесс умер, когда доставки уже стояли в webhook_deliveries.
Старое поведение — строки 'queued' висели вечно: payload существовал только
в памяти воркера. Новое: тело хранится в колонке payload, старт приложения
переподчиняет незавершённые доставки, нумерация попыток продолжается
(индекс ux_webhook_attempts_delivery_number уникален).

Доставка подменена заглушкой — сеть тесты не трогают.
"""
import json

import pytest

import models
import webhook_delivery
from webhook_delivery import (
    create_delivery,
    deliver_delivery,
    recover_pending_deliveries,
)
from webhook_security import encrypt_secret, obtain_fernet


@pytest.fixture
def hook(db):
    """Активный webhook с расшифровываемым секретом (tenant 101 из conftest)."""
    from tests.conftest import DEFAULT_TEST_TENANT_ID

    fernet = obtain_fernet()
    webhook = models.Webhook(
        url="https://example.com/hook",
        events='["card.created"]',
        is_active=True,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        secret=encrypt_secret("sec", fernet),
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


def _queued(db, hook, *, attempt_count=0, payload=None, status="queued"):
    delivery = create_delivery(
        db, webhook_id=hook.id, event="card.created",
        tenant_id=hook.tenant_id, payload=payload,
    )
    if attempt_count or status != "queued":
        delivery.attempt_count = attempt_count
        delivery.status = status
        db.commit()
    return delivery


@pytest.fixture
def sync_submit(monkeypatch):
    """submit_delivery выполняется синхронно: recovery в тесте детерминирован,
    иначе ассерты гоняются с воркерами executor'а."""
    monkeypatch.setattr(
        webhook_delivery, "submit_delivery",
        lambda fn, *a, **kw: (fn(*a, **kw), True)[1],
    )


def test_create_delivery_persists_payload(db, hook):
    delivery = _queued(db, hook, payload={"event": "card.created", "data": {"id": 5}})
    assert json.loads(delivery.payload) == {"event": "card.created", "data": {"id": 5}}


def test_recovery_resubmits_queued_and_resets_delivering(db, monkeypatch, hook, sync_submit):
    """И 'queued', и 'delivering' от умершего процесса уходят заново."""
    first = _queued(db, hook, payload={"event": "card.created", "data": {"id": 1}})
    second = _queued(db, hook, payload={"event": "card.created", "data": {"id": 2}},
                     status="delivering")
    resubmitted = []

    def stub(delivery_id, payload, *a, **kw):
        resubmitted.append((delivery_id, payload))
        return None

    monkeypatch.setattr(webhook_delivery, "deliver_delivery", stub)

    stats = recover_pending_deliveries()

    assert stats["resubmitted"] == 2
    assert {pid for pid, _ in resubmitted} == {first.id, second.id}
    payloads = {p["data"]["id"] for _, p in resubmitted}
    assert payloads == {1, 2}
    db.expire_all()
    for row in (first, second):
        fresh = db.query(models.WebhookDelivery).filter_by(id=row.id).one()
        assert fresh.status == "queued"


def test_recovery_closes_legacy_rows_without_payload(db, hook):
    """Строки до 0024 тела не имеют — им failed/restart_lost_payload,
    а не вечный 'queued'."""
    legacy = _queued(db, hook, payload=None)
    stats = recover_pending_deliveries()
    assert stats["lost_payload"] == 1 and stats["resubmitted"] == 0
    db.expire_all()
    fresh = db.query(models.WebhookDelivery).filter_by(id=legacy.id).one()
    assert fresh.status == "failed"
    assert fresh.last_error_code == "restart_lost_payload"
    assert fresh.completed_at is not None


def test_recovery_closes_corrupt_payload_row(db, hook):
    row = _queued(db, hook, payload={"x": 1})
    row.payload = "{не json"
    db.commit()
    stats = recover_pending_deliveries()
    assert stats["corrupt_payload"] == 1
    db.expire_all()
    fresh = db.query(models.WebhookDelivery).filter_by(id=row.id).one()
    assert fresh.status == "failed"
    assert fresh.last_error_code == "restart_payload_corrupt"


def test_attempt_numbering_continues_after_restart(db, monkeypatch, hook, sync_submit):
    """Доставка упала на второй попытке, процесс рестартнул: новая попытка —
    №3, не №1 (уникальный индекс попыток иначе даёт IntegrityError)."""
    row = _queued(db, hook, attempt_count=2, payload={"event": "card.created",
                                                      "data": {"id": 9}})

    def ok_transport(target, body, headers, timeout):
        return 200

    monkeypatch.setattr(
        webhook_delivery, "deliver_delivery",
        lambda delivery_id, payload, **kw: deliver_delivery(
            delivery_id, payload, transport=ok_transport, sleeper=lambda s: None),
    )
    stats = recover_pending_deliveries()

    assert stats["resubmitted"] == 1
    attempt_numbers = [a.attempt_number for a in
                       db.query(models.WebhookAttempt)
                       .filter_by(delivery_id=row.id).all()]
    assert attempt_numbers == [3]  # продолжение, не новый отсчёт с 1
    db.expire_all()
    fresh = db.query(models.WebhookDelivery).filter_by(id=row.id).one()
    assert fresh.status == "succeeded"
    assert fresh.attempt_count == 3


def test_budget_spent_delivery_gets_exactly_one_more_attempt(db, hook):
    """Бюджет (3) потрачен, доставка не завершилась — рестарт даёт ровно одну
    дополнительную попытку №4, не полный новый бюджет."""
    row = _queued(db, hook, attempt_count=3, payload={"event": "card.created",
                                                      "data": {"id": 10}})

    # Доставка падает отказом 400 (non-retryable) без сети: попытка будет
    # ровно одна — №4.
    def reject(target, body, headers, timeout):
        return 400

    outcome = deliver_delivery(row.id, json.loads(row.payload),
                               transport=reject, sleeper=lambda s: None)

    assert outcome.status == "failed"
    assert outcome.attempts == 4
