"""Интеграции и уведомления: регрессии аудита 2026-09-30.

  * Ф-7 — бот Telegram подтверждал «✅ Сохранено в CRM», не проверив ответ на
    загрузку фото: снимок тяжелее 25 МБ отбивался 413 молча.
  * Ф-8 — сбой на одном webhook'е прерывал цикл и оставлял остальные webhook'и
    tenant'а без доставки и без записи в журнал.
  * С-11 — дедупликация уведомлений сравнивала entity_type с константой
    "client" вместо переданного значения.
  * С-7 — POST /workflows не проверял имя, в отличие от PATCH.
"""

from types import SimpleNamespace

import pytest

import models
import notify

# Бот разворачивается ОТДЕЛЬНО от CRM: python-telegram-bot нет ни в
# requirements.txt, ни в её venv, а модуль на отсутствии пакета вызывает
# sys.exit(1) прямо на импорте. Поэтому он подключается мягко, а тесты Ф-7
# пропускаются с внятной причиной, а не роняют сбор всего набора.
try:
    from telegram_nakladnye_bot import upload_photos
    _BOT_IMPORT_ERROR = None
except SystemExit as exc:  # бот сам уходит через sys.exit при ImportError
    upload_photos = None
    _BOT_IMPORT_ERROR = exc
except ImportError as exc:
    upload_photos = None
    _BOT_IMPORT_ERROR = exc

bot_required = pytest.mark.skipif(
    upload_photos is None,
    reason=(
        "python-telegram-bot не установлен — бот разворачивается отдельно "
        "от CRM и его зависимости не входят в requirements.txt"
    ),
)


# ---------------------------------------------------------------------------
# Ф-7: загрузка фото ботом
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _PhotoClient:
    """Заглушка httpx-клиента с заранее заданными ответами."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def post(self, url, headers=None, files=None, json=None):
        self.calls += 1
        if self.calls <= len(self.statuses):
            return _Response(self.statuses.pop(0))
        return _Response(200)


def _photos(n):
    return [{"filename": f"page-{i}.jpg", "data": b"x"} for i in range(n)]


async def _run(statuses, n_photos):
    client = _PhotoClient(statuses)
    failed = await upload_photos(client, 42, _photos(n_photos))
    return failed, client.calls


@bot_required
def test_all_photos_uploaded_returns_no_failures():
    import asyncio

    failed, calls = asyncio.run(_run([200, 200, 200], 3))
    assert calls == 3
    assert failed == [], failed


@bot_required
def test_oversized_photo_is_reported_as_failed():
    """413 на тяжёлом фото обязан попасть в список неудавшихся, а не исчезнуть."""
    import asyncio

    failed, calls = asyncio.run(_run([200, 413, 200], 3))
    assert calls == 3, "остальные фото всё равно грузятся"
    assert len(failed) == 1, failed
    assert "page-1.jpg" in failed[0], failed


@bot_required
def test_one_failure_does_not_stop_others():
    """Одно битое фото не отменяет загрузку остальных."""
    import asyncio

    failed, calls = asyncio.run(_run([500, 200, 200], 3))
    assert calls == 3
    assert failed == ["page-0.jpg"], failed


@bot_required
def test_network_error_is_reported_as_failed():
    """Обрыв сети — тоже потерянное фото, а не повод упасть всему пакету."""
    import asyncio
    import telegram_nakladnye_bot

    class Broken:
        def __init__(self):
            self.calls = 0

        async def post(self, *a, **kw):
            self.calls += 1
            raise TimeoutError("соединение сброшено")

    client = Broken()
    import asyncio as _a
    failed = _a.get_event_loop().run_until_complete(
        upload_photos(client, 42, _photos(2))
    ) if False else _a.new_event_loop().run_until_complete(
        upload_photos(client, 42, _photos(2))
    )
    assert client.calls == 2, "второе фото тоже попытались отправить"
    assert len(failed) == 2, failed


# ---------------------------------------------------------------------------
# Ф-8: один сбойный webhook не выключает остальные
# ---------------------------------------------------------------------------

def test_one_failing_webhook_does_not_block_others(client, db, admin, monkeypatch):
    """Сбой на первом webhook'е не должен оставить второй без доставки."""
    import asyncio
    import socket

    from routers import webhooks_router

    # URL webhook'а проходит SSRF-проверку с реальным резолвом хоста, поэтому
    # сеть подменяется — ровно как это делает фикстура public_dns в тестах
    # схемы, иначе тест зависел бы от выхода в интернет.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **kw: [(2, 1, 6, "", ("93.184.216.34", port or 80))],
    )

    _, h = admin
    for n in (1, 2):
        r = client.post("/webhooks/", headers=h, json={
            "url": f"https://example.com/hook{n}", "events": ["card_created"],
            "secret": "s3cret",
        })
        assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(models.Webhook).count() == 2

    created = []

    def fake_create_delivery(db_, webhook_id, event, tenant_id, payload=None):
        # Первый webhook падает так, как падал бы OperationalError из commit.
        # Второй — просто фиксируется. Строка доставки здесь НЕ создаётся
        # намеренно: тест проверяет, что цикл не прервался, а не устройство
        # таблицы webhook_deliveries — у неё обязательные correlation_id и
        # tenant_id, и заполнять их в заглушке значило бы тащить в тест
        # лишнюю хрупкость.
        if webhook_id == 1:
            raise Exception("simulated OperationalError from commit")
        created.append(webhook_id)
        return SimpleNamespace(id=1000 + webhook_id)

    monkeypatch.setattr(
        webhooks_router, "create_delivery", fake_create_delivery)
    monkeypatch.setattr(
        webhooks_router, "submit_delivery", lambda *a, **kw: True)

    tenant_id = db.query(models.User).filter(
        models.User.role == "admin").first().tenant_id
    asyncio.run(webhooks_router.notify_webhooks(tenant_id, "card_created", {"id": 1}))

    assert created == [2], (
        f"второй webhook должен получить доставку, получено {created}"
    )


# ---------------------------------------------------------------------------
# С-11: дедупликация уведомлений
# ---------------------------------------------------------------------------

def test_dedupe_does_not_confuse_different_entity_types(db, make_user):
    """Однотипное уведомление по карточке не подавляется уведомлением по задаче.

    Условие старого кода `entity_type != "client"` срабатывало на ЛЮБОЙ тип
    сущности, кроме клиента. Чтобы это было видно, type у обеих записей должен
    совпадать: иначе отсекает первое же равенство по `type`, и тест проходит
    вхолостую — ровно так вышло с первой версией этого теста.

    Проявится ли это сегодня — вопрос отдельный: вызывающие пока держат пары
    один-к-одному ("task_overdue"+task, "card_overdue"+card), поэтому на
    текущем коде дефект латентный. Но условие неверно по существу, и первое
    же расширение (один type на две сущности) стало бы молча глотать уведомления
    о других сущностях.
    """
    user, _ = make_user("manager", username="dedupe_probe")
    uid = user.id

    db.add(models.Notification(
        user_id=uid, type="overdue", title="Задача", entity_type="task",
        entity_id=5, is_read=False, tenant_id=user.tenant_id,
    ))
    db.commit()

    result = notify.notify(
        db, [uid], type="overdue", title="Карточка",
        entity_type="card", entity_id=5, dedupe=True,
    )

    assert int(result) == 1, (
        f"уведомление по карточке обязано быть создано: {result}"
    )
    db.expire_all()
    kinds = {
        (n.type, n.entity_type)
        for n in db.query(models.Notification).filter(
            models.Notification.user_id == uid).all()
    }
    assert ("overdue", "card") in kinds, kinds


def test_dedupe_works_for_client_entity(db, make_user):
    """Для entity_type="client" дедупликация раньше не работала вовсе.

    Второе следствие того же условия: для клиента `!= "client"` не срабатывает
    никогда, поэтому повторные уведомления по клиенту проходили каждый раз.
    Сегодня dedupe=True с entity_type="client" не вызывается, но починка
    обязана покрывать и этот случай, а не только текущих вызывающих.
    """
    user, _ = make_user("manager", username="dedupe_client")
    uid = user.id

    first = notify.notify(db, [uid], type="client_event", title="Клиент",
                          entity_type="client", entity_id=7, dedupe=True)
    second = notify.notify(db, [uid], type="client_event", title="Клиент",
                           entity_type="client", entity_id=7, dedupe=True)

    assert int(first) == 1, first
    assert int(second) == 0, (
        f"повтор по той же сущности должен подавляться: {second}"
    )


def test_dedupe_still_works_for_same_entity(db, make_user):
    """Повторное уведомление по той же сущности подавляется — регрессии нет."""
    user, _ = make_user("manager", username="dedupe_same")
    uid = user.id

    first = notify.notify(db, [uid], type="card_overdue", title="Карточка",
                          entity_type="card", entity_id=9, dedupe=True)
    second = notify.notify(db, [uid], type="card_overdue", title="Карточка",
                           entity_type="card", entity_id=9, dedupe=True)

    assert int(first) == 1, first
    assert int(second) == 0, f"тот же тип и сущность должны дублироваться: {second}"


# ---------------------------------------------------------------------------
# С-7: имя воркфлоу при создании
# ---------------------------------------------------------------------------

def test_workflow_name_is_validated_on_create(client, admin, db):
    """POST отвергает пробельное имя так же, как это делает PATCH."""
    _, h = admin

    for bad in ("   ", "", "\t\n"):
        r = client.post("/workflows/", headers=h, json={"name": bad})
        assert r.status_code == 400, f"{bad!r}: {r.status_code} {r.text}"

    db.expire_all()
    assert db.query(models.Workflow).count() == 0, "мусорный воркфлоу не создан"


def test_workflow_name_is_trimmed_on_create(client, admin, db):
    """Полезное имя принимается, оканчивающие пробелы убираются."""
    _, h = admin
    r = client.post("/workflows/", headers=h, json={"name": "  Сборка  "})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Сборка", r.text
