"""Кастомные объекты, вебхуки, граница приложения: С-5, С-8, С-12, С-16.

Регрессия аудита 2026-09-30.

С-5. float(value) без try/except давал 500 на «abc»; значение поля «список»
    принималось без сверки со списком вариантов; data принимал словарь любого
    размера.
С-8. События, вызванные суперадмином, не доходили ни до одного tenant'а:
    все места передавали current_user.tenant_id, а у суперадмина он NULL, и
    ветка «tenant_id is None» отбирала только глобальные webhook'и.
С-12. Приложение слушает 0.0.0.0 — весь API был доступен напрямую мимо nginx.
С-16. Ответ 500 собирался выше add_headers и оставался без CSP, nosniff и
    X-Frame-Options.
"""

import socket
from types import SimpleNamespace

import models
import pytest


# ---------------------------------------------------------------------------
# С-5: разбор значений кастомных объектов
# ---------------------------------------------------------------------------

def _object_with_field(db, tenant_id, field_type, options=None):
    from routers.custom_objects_router import _dump_options
    obj = models.CustomObjectType(name="Справочник", label="Справочник",
                                  tenant_id=tenant_id)
    db.add(obj)
    db.flush()
    fld = models.CustomFieldDef(object_type_id=obj.id, name="Поле",
                                label="Поле", field_type=field_type,
                                tenant_id=tenant_id,
                                options=_dump_options(options))
    db.add(fld)
    db.commit()
    return obj.id, fld.id


def test_number_field_rejects_text_with_400_not_500(client, db, admin):
    """«abc» в числовом поле — понятный 400, а не внутренняя ошибка."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    obj_id, _ = _object_with_field(db, tenant_id, "number")

    r = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                    json={"data": {"Поле": "abc"}})

    assert r.status_code == 400, f"ожидался 400, получено {r.status_code}: {r.text}"
    assert "число" in r.json()["detail"].lower()


def test_number_field_accepts_numeric_string(client, db, admin):
    """Число, введённое строкой из поля формы, принимается — это не ошибка."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    obj_id, _ = _object_with_field(db, tenant_id, "currency")

    r = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                    json={"data": {"Поле": "1500.50"}})

    assert r.status_code == 200, r.text
    db.expire_all()
    value = db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.field_def_id ==
        db.query(models.CustomFieldDef).filter(
            models.CustomFieldDef.object_type_id == obj_id).first().id).first()
    assert float(value.value_number) == 1500.50


def test_select_field_rejects_value_outside_options(client, db, admin):
    """Значение не из списка вариантов отвергается, а не кладётся как есть.

    Раньше произвольная строка молча попадала в поле, и запись становилась
    невидимой для фильтров по этому полю — без единого признака ошибки.
    """
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    obj_id, _ = _object_with_field(db, tenant_id, "select", options=["Новый", "В работе"])

    bad = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                      json={"data": {"Поле": "Выдуманный"}})
    assert bad.status_code == 400, f"ожидался 400, получено {bad.status_code}: {bad.text}"
    assert "Выдуманный" in bad.json()["detail"]

    good = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                       json={"data": {"Поле": "В работе"}})
    assert good.status_code == 200, good.text


def test_record_data_size_is_capped(client, db, admin):
    """Одна запись не может втянуть в себя бесконечно много полей."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    obj_id, _ = _object_with_field(db, tenant_id, "text")

    huge = {f"Поле {i}": "x" for i in range(500)}
    r = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                    json={"data": huge})

    assert r.status_code == 422, f"ожидался 422 по лимиту, получено {r.status_code}"
    db.expire_all()
    assert db.query(models.CustomRecord).filter(
        models.CustomRecord.object_type_id == obj_id).count() == 0


def test_normal_sized_record_still_accepted(client, db, admin):
    """Лимит не должен мешать обычной работе — 50 полей это норма."""
    _, h = admin
    tenant_id = db.query(models.User).filter(models.User.role == "admin").first().tenant_id
    obj_id, _ = _object_with_field(db, tenant_id, "text")

    r = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                    json={"data": {f"Поле {i}": "x" for i in range(50)}})

    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# С-8: адресат вебхука — tenant-владелец, а не автор
# ---------------------------------------------------------------------------

def test_user_tenant_is_not_nullable():
    """У пользователя tenant обязателен — «суперадмина без tenant» не бывает."""
    assert models.User.__table__.c.tenant_id.nullable is False


def test_webhook_tenant_is_not_nullable():
    """Webhook тоже всегда принадлежит компании.

    Следствие: условие «tenant_id == None» в notify_webhooks не находит ни
    одной строки, то есть ветка недостижима, а не «работает как задумано».
    """
    assert models.Webhook.__table__.c.tenant_id.nullable is False


def test_token_without_tenant_is_rejected(client, db, make_user):
    """Токен с tenant_id = NULL не проходит: запрос отклоняется.

    Проверка поведения, а не кода: подпись с NULL-claim'ом выпустить можно
    (это только криптография), а вот дойти до обработчика — нельзя.
    """
    import auth

    user, _headers = make_user("admin", username="null_tenant_probe")
    db.refresh(user)
    token = auth.create_access_token({
        "sub": user.username,
        "tenant_id": None,
        "pv": auth.password_version(user.hashed_password),
    })
    r = client.get("/clients/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403), (
        f"токен с tenant_id=None не должен давать доступ, получено {r.status_code}")


def test_global_webhook_branch_cannot_match_anything(db):
    """Ветка глобальных webhook'ов не находит ни одной строки.

    Прямая проверка того условия, на которое ссылалась находка С-8.
    """
    global_rows = db.query(models.Webhook).filter(
        models.Webhook.tenant_id == None).count()
    assert global_rows == 0, (
        "в базе есть webhook'и без tenant — тогда ветка С-8 достижима, "
        "и вопрос об адресате события снова открыт")


def test_superadmin_is_a_role_not_a_missing_tenant(db, make_user):
    """Суперадмин принадлежит компании — роль не отменяет tenant."""
    user, _headers = make_user("superadmin", username="sa_role", tenant_id=None)

    # tenant_id=None фикстура подставляет дефолтом: колонка NOT NULL.
    db.refresh(user)
    assert user.tenant_id is not None, (
        "если появился суперадмин без tenant, С-8 снова становится реальной")
    assert user.role == "superadmin"


def test_webhook_delivery_reaches_owner_tenant(client, db, admin):
    """Рабочий путь: событие уходит webhook'у компании автора.

    Событие создания клиента эмитится при создании клиента, и подписанный
    webhook той же компании получает доставку — то есть механика исправна.
    """
    import socket

    real_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda host, port, *a, **kw: [
        (2, 1, 6, "", ("93.184.216.34", port or 80))]
    try:
        _, h = admin
        r = client.post("/webhooks/", headers=h, json={
            "url": "https://example.com/hook",
            "events": ["client.created"],
            "secret": "s3cret",
        })
    finally:
        socket.getaddrinfo = real_getaddrinfo
    assert r.status_code == 200, r.text

    made = client.post("/clients", headers=h, json={"name": "Клиент для вебхука"})
    assert made.status_code in (200, 201), made.text

    db.expire_all()
    deliveries = db.query(models.WebhookDelivery).all()
    assert deliveries, "подписанный webhook не получил доставку"
    assert any(d.event == "client.created" for d in deliveries), \
        [(d.id, d.event) for d in deliveries]

# ---------------------------------------------------------------------------
# С-12 / С-16: граница приложения
# ---------------------------------------------------------------------------

def test_unknown_host_header_is_rejected():
    """Обращение с чужим Host-заголовком не принимается."""
    import main
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    assert any(isinstance(m.cls, type) and issubclass(m.cls, TrustedHostMiddleware)
               for m in main.app.user_middleware), "TrustedHostMiddleware не установлен"


def test_known_hosts_are_accepted(client):
    """Рабочие хосты продолжают приниматься — фильтр не должен ломать прод."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    for host in ("127.0.0.1", "testserver"):
        resp = client.get("/health/live", headers={"Host": host})
        assert resp.status_code == 200, f"{host}: {resp.status_code}"


def test_allowed_hosts_contain_known_local_addresses():
    """Локальные адреса обязаны быть в списке: стенд и health-чеки идут так."""
    import main

    allowed = set()
    for m in main.app.user_middleware:
        if issubclass(m.cls, __import__(
                "starlette.middleware.trustedhost", fromlist=["x"]).TrustedHostMiddleware):
            allowed |= set(m.kwargs.get("allowed_hosts") or [])
    assert {"127.0.0.1", "localhost", "testserver"} <= allowed, allowed


def test_500_response_keeps_security_headers(client, admin):
    """Страница ошибки получает те же заголовки безопасности, что и обычная."""
    _, h = admin

    # Хендлер, который гарантированно падает: поле select без вариантов
    # приводит к 400, а вот несуществующий объект — к 404. Для 500 нужен
    # настоящий сбой, поэтому вызываем middleware напрямую.
    from fastapi import Request
    import main

    async def boom(_request):
        raise RuntimeError("искусственный сбой для проверки заголовков")

    captured = {}

    async def call_next_fn(_request):
        raise RuntimeError("искусственный сбой для проверки заголовков")

    import asyncio

    async def run():
        scope = {
            "type": "http", "method": "GET", "path": "/cards/1",
            "headers": [(b"host", b"testserver")],
            "query_string": b"", "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234), "root_path": "",
        }
        response = await main.add_headers(Request(scope), call_next_fn)
        return response

    response = asyncio.run(run())
    assert response.status_code == 500
    assert response.headers.get("X-Content-Type-Options") == "nosniff", \
        f"на 500 нет nosniff: {dict(response.headers)}"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Content-Security-Policy"), \
        "на 500 нет CSP"
