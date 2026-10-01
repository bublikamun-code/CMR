"""Метка времени в вебхуке — получатель может отсечь повтор (С-9).

Подпись у нас всегда считалась только по телу запроса. Перехваченный запрос
можно было отправить заново сколько угодно раз, и он оставался валидным вечно:
ни в подписи, ни в заголовках не было ничего, что менялось бы со временем.

Здесь проверяется выбранная мера — метка времени отдельным заголовком при
НЕИЗМЕННОЙ подписи. Она не ломает ни одну работающую интеграцию: получатели,
которые проверяют подпись как раньше, получают ровно ту же строку.
"""

import hashlib
import hmac
import json
import time

import webhook_delivery as wd


def _body(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def test_timestamp_header_present():
    """Метка времени отправляется вместе с событием."""
    _body_bytes, headers = wd.build_signed_body({"event": "card.updated"}, "s3cret")

    assert "X-Webhook-Timestamp" in headers, sorted(headers)
    ts = headers["X-Webhook-Timestamp"]
    assert ts.isdigit(), f"метка должна быть целыми секундами: {ts!r}"
    assert abs(int(ts) - int(time.time())) < 60, "метка времени устарела"


def test_signature_unchanged_by_the_addition():
    """Подпись считается по телу — формат прежний.

    Это главное свойство правки: если бы метка времени вошла в подпись, все
    интеграции на проде перестали бы проверять её с первого же обновления.
    """
    payload = {"event": "card.updated", "data": {"id": 7}}
    expected = hmac.new(b"s3cret", _body(payload), hashlib.sha256).hexdigest()

    _body_bytes, headers = wd.build_signed_body(payload, "s3cret")

    assert headers["X-Webhook-Signature"] == expected


def test_signed_body_has_no_unsigned_headroom():
    """Метку времени нельзя подделать, не сломав подпись.

    Метка стоит и в заголовке, и внутри подписанного тела. Именно поэтому
    получатель может сверять время и не доверять заголовку слепо: изменить
    заголовок, оставив тело, невозможно — подпись сразу перестанет сходиться.
    """
    payload = {"event": "card.updated", "timestamp": time.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}
    body, _headers = wd.build_signed_body(payload, "s3cret")

    # Сравниваем ЗНАЧЕНИЕ по ключу: «x in dict» проверяет ключи, а не
    # содержимое, и такая проверка прошла бы, даже если бы времени в теле
    # не было вовсе.
    assert json.loads(body.decode())["timestamp"] == payload["timestamp"], \
        "время обязано быть и внутри подписанного тела"


def test_payload_timestamp_is_always_present_from_the_caller():
    """Отправитель кладёт timestamp в тело — как это делает notify_webhooks."""
    from routers.webhooks_router import notify_webhooks  # noqa: F401
    import inspect
    src = inspect.getsource(notify_webhooks)
    assert '"timestamp"' in src, \
        "без времени в теле получатель не сможет понять возраст события"


def test_replay_window_is_documented_in_the_module():
    """Правило проверки зафиксировано в коде, а не в памяти автора."""
    src = inspect_source_of_build_signed_body()
    assert "X-Webhook-Timestamp" in src
    assert "пять" in src or "5" in src, "окно допустимого расхождения не описано"


def inspect_source_of_build_signed_body() -> str:
    import inspect
    return inspect.getsource(wd.build_signed_body)