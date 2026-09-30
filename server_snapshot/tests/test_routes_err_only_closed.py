"""Маршруты, которые набор касался, но ни разу не довёл до успеха.

Гейт ``--route-coverage-strict-success`` (tests/route_coverage.py) вскрыл
три ручки, у которых был только негативный путь:

* ``GET /clients/{client_id}/cards`` — звали с несуществующим клиентом, то
  есть сработал только 404 ветки ``get_tenant_object``. Сам список сделок
  клиента (с selectinload вложенных связей) не выполнялся ни разу;
* ``DELETE /custom/fields/{field_id}`` — звали только с чужим/несуществующим
  id. Удаление поля с каскадным сносом значений не проверялось;
* ``POST /email-parser/sync`` — восемь вызовов, все с ошибкой: настройки не
  заполнены, ключ шифрования не настроен, IMAP недоступен. Успешный прогон
  синка через HTTP не делал никто: он требует живого IMAP-сервера, а
  набор его не касается.

Все три ветки проверяются здесь локально: IMAP подменён фейком из
test_email_sync_failures.py, сетевых обращений и subprocess нет.
"""
import models
import pytest

import routers.email_parser_router as email_parser_router
from tests.test_email_sync_failures import _ImapHarness, _mail, _write_settings_file


# --- GET /clients/{client_id}/cards ----------------------------------------


def test_client_cards_lists_cards_of_client(client, db, admin, make_card):
    """Сделки клиента возвращаются списком, а не пустым ответом.

    Ответ — CardResponse, который тянет вложенные связи (вложения,
    чек-листы, исполнитель, клиент, теги). Именно эти selectinload и
    никогда не выполнялись, поэтому проверяем не только код, но и то,
    что сериализация реально проходит.
    """
    _user, h = admin
    r = client.post("/clients", headers=h, json={"name": "ООО «Свет»"})
    assert r.status_code == 200, r.text
    client_id = r.json()["id"]

    card = make_card(title="Люстра в гостиную", client_id=client_id)

    response = client.get(f"/clients/{client_id}/cards", headers=h)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [c["id"] for c in body] == [card.id], body
    # вложенные связи сериализуются без 500
    assert body[0]["title"] == "Люстра в гостиную"
    assert body[0]["client"]["id"] == client_id


def test_client_cards_omits_soft_deleted_and_other_clients(
        client, db, admin, make_card):
    """В списке только живые сделки ЭТОГО клиента.

    Фильтр ``is_deleted == False`` и ``client_id ==`` проверяются вместе:
    по отдельности каждый из них закрывается чужим тестом, а вместе
    ветка «сделка удалена в корзину остаётся в выдаче» замечается сразу.
    """
    _user, h = admin
    first = client.post("/clients", headers=h, json={"name": "Первый"}).json()["id"]
    second = client.post("/clients", headers=h, json={"name": "Второй"}).json()["id"]

    mine = make_card(title="Моя", client_id=first)
    make_card(title="Удалённая", client_id=first, is_deleted=True)
    make_card(title="Чужая", client_id=second)

    body = client.get(f"/clients/{first}/cards", headers=h).json()

    assert [c["id"] for c in body] == [mine.id], body


def test_client_cards_of_client_without_deals_is_empty(client, admin):
    """Клиент без сделок — 200 и пустой список, а не 404."""
    _user, h = admin
    client_id = client.post(
        "/clients", headers=h, json={"name": "Без сделок"}
    ).json()["id"]

    response = client.get(f"/clients/{client_id}/cards", headers=h)

    assert response.status_code == 200, response.text
    assert response.json() == []


# --- DELETE /custom/fields/{field_id} --------------------------------------


def _make_type(client, h, name="equipment", label="Оборудование"):
    r = client.post("/custom/objects", headers=h,
                    json={"name": name, "label": label})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_field(client, h, obj_id, name="power", label="Мощность",
                field_type="text"):
    r = client.post(f"/custom/objects/{obj_id}/fields", headers=h,
                    json={"name": name, "label": label, "field_type": field_type})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_delete_custom_field_removes_field_and_its_values(client, db, admin):
    """Поле удаляется вместе со значениями записей.

    Ручка сносит значения вручную (db.query(...).delete()), а не каскадом
    БД, поэтому каскад надо проверить: если забудут эту строку, значения
    осиротеют и займут место, оставаясь невидимыми для API.
    """
    _user, h = admin
    obj_id = _make_type(client, h)
    field_id = _make_field(client, h, obj_id)
    r = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                    json={"data": {"power": "120 Вт"}})
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.field_def_id == field_id).count() == 1

    response = client.delete(f"/custom/fields/{field_id}", headers=h)

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Удалено"}
    db.expire_all()
    assert db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.id == field_id).count() == 0
    assert db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.field_def_id == field_id).count() == 0, \
        "значения удалённого поля остались в базе"
    # поле исчезло и из списка полей объекта
    fields = client.get(f"/custom/objects/{obj_id}/fields", headers=h).json()
    assert field_id not in [f["id"] for f in fields], fields


def test_delete_custom_field_keeps_other_fields_and_records(
        client, db, admin):
    """Снос одного поля не трогает остальные поля и записи объекта."""
    _user, h = admin
    obj_id = _make_type(client, h)
    doomed = _make_field(client, h, obj_id, name="power", label="Мощность")
    kept = _make_field(client, h, obj_id, name="weight", label="Вес",
                       field_type="number")
    record_id = client.post(f"/custom/objects/{obj_id}/records", headers=h,
                            json={"data": {"power": "120 Вт", "weight": 3}}
                            ).json()["id"]

    assert client.delete(f"/custom/fields/{doomed}", headers=h).status_code == 200

    fields = client.get(f"/custom/objects/{obj_id}/fields", headers=h).json()
    assert [f["id"] for f in fields] == [kept], fields
    records = client.get(f"/custom/objects/{obj_id}/records", headers=h).json()
    assert [r["id"] for r in records] == [record_id], records
    db.expire_all()
    # значение уцелевшего поля на месте
    assert db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.field_def_id == kept).count() == 1


def test_delete_custom_field_twice_gives_404(client, admin):
    """Повторное удаление поля — 404, а не 500 на отсутствующей строке."""
    _user, h = admin
    obj_id = _make_type(client, h)
    field_id = _make_field(client, h, obj_id)

    assert client.delete(f"/custom/fields/{field_id}", headers=h).status_code == 200
    assert client.delete(f"/custom/fields/{field_id}", headers=h).status_code == 404


# --- POST /email-parser/sync: успешный прогон -----------------------------


@pytest.fixture
def sync_mailbox(monkeypatch, tmp_path):
    """Подменить IMAP фейком и указать роутеру файл настроек в tmp.

    Отличается от фикстуры ``mailbox`` в test_email_sync_failures.py тем,
    что подменяет не только IMAP и save_settings, но и путь к файлу
    настроек: здесь синк запускается через HTTP-ручку, а она читает
    настройки сама.
    """
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "test-password")
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    monkeypatch.setattr(
        email_parser_router, "save_settings",
        lambda settings, tenant_id=None, **kwargs: None,
    )
    _write_settings_file(monkeypatch, tmp_path)

    def _factory(messages=(), **client_kwargs):
        harness = _ImapHarness(messages, **client_kwargs)
        monkeypatch.setattr(
            email_parser_router.imaplib, "IMAP4_SSL", harness.factory
        )
        return harness

    return _factory


def test_sync_endpoint_imports_letter_and_returns_200(
        client, db, admin, sync_mailbox):
    """Успешный синк через HTTP: письмо импортировано, ответ 200.

    Это единственный тест, который доводит POST /email-parser/sync до
    success=True: остальные восемь вызовов ручки проверяли только отказы
    (пустые настройки, сломанный ключ, недоступный IMAP).
    """
    _user, h = admin
    harness = sync_mailbox([_mail(subject="Люстра в гостиную")])

    response = client.post("/email-parser/sync", headers=h)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True, body
    assert body["count"] == 1, body
    assert len(body["cards"]) == 1, body
    # синк логинился под адресом из настроек и закрыл соединение
    assert harness.clients[0].login_calls == [("sale@example.com", "test-password")]
    assert harness.clients[0].logout_calls == 1
    db.expire_all()
    cards = db.query(models.Card).all()
    # тема письма попадает в заголовок карточки с префиксом «Письмо: »
    assert [c.title for c in cards] == ["Письмо: Люстра в гостиную"], \
        f"карточка из письма не создана: {[c.title for c in cards]}"
    # и она помечена прочитанной, чтобы не задваиваться при следующем прогоне
    assert harness.clients[0].store_calls == [(b"1", "+FLAGS", "\\Seen")]


def test_sync_endpoint_creates_card_in_tenant_of_calling_user(
        client, db, admin, sync_mailbox):
    """Карточка синка принадлежит tenant'у вызывающего, а не None.

    Регрессия 25.09 (импорт писем терял карточку из-за триггера 0018 на
    пустом tenant_id) жила именно здесь: синк должен определить tenant
    до создания карточки, иначе запись не проходит.
    """
    from tests.conftest import DEFAULT_TEST_TENANT_ID

    _user, h = admin
    sync_mailbox([_mail(subject="Сделка с авансом")])

    body = client.post("/email-parser/sync", headers=h).json()

    assert body["success"] is True, body
    db.expire_all()
    card = db.query(models.Card).one()
    assert card.tenant_id == DEFAULT_TEST_TENANT_ID
    assert body["tenant_id"] == DEFAULT_TEST_TENANT_ID


def test_sync_endpoint_reports_partial_failure_without_leaking_secret(
        client, admin, sync_mailbox, caplog):
    """Провал синка — 500 с обобщённым текстом, без хоста и пароля."""
    import imaplib
    import logging

    _user, h = admin
    sync_mailbox([], login_error=imaplib.IMAP4.error(
        "Login failed to mail.internal.example.com:993 user=box password=hunter2"
    ))

    with caplog.at_level(logging.ERROR):
        response = client.post("/email-parser/sync", headers=h)

    assert response.status_code == 500, response.text
    assert "hunter2" not in response.text
    assert "mail.internal.example.com" not in response.text
    assert "Ошибка авторизации на почтовом сервере" in response.json()["detail"]
