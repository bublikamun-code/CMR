"""Tenant-инварианты накладных: создание, изоляция ручек, раздача фото.

Дефекты, которые закрывает файл (аудит 2026-09-26):

1. ``POST /nakladnye/bot/create`` создавал накладную вообще без tenant_id.
   BEFORE INSERT триггер миграции 0018 отбивает ``tenant_id IS NULL``, поэтому
   приём накладной ботом отдавал 500 на КАЖДЫЙ документ.
2. ``POST /nakladnye`` писал ``tenant_id=current_user.tenant_id``; у superadmin
   это ``None`` — тот же 500. Теперь 403 «Tenant пользователя не настроен».
3. В роутере не было ни одного tenant-фильтра, кроме этой строки: список,
   правка, удаление, фото, Excel и экспорт видели и меняли чужие накладные.
4. ``GET /nakladnye/photos/{filename}`` отдавал ЛЮБОЙ файл из UPLOAD_DIR по
   имени — IDOR (в т.ч. между tenant'ами) и Stored XSS, потому что ответ шёл
   без Content-Disposition и браузер рендерил содержимое inline.

Тесты ловят именно эти дефекты, поэтому для проверок «tenant обязан быть»
маскирующий слушатель conftest снимается (иначе он подставляет 101 всем
вставляемым объектам и прячет баг).
"""
import contextlib
import io
import json
import os
import sys
import uuid
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import event

import database
import models
import schemas
import routers.nakladnye_router as nak_router

# Pytest обычно загружает conftest как ``conftest``; при импорте как пакета
# он доступен как ``tests.conftest``. Берём уже загруженный модуль, иначе
# импорт второго экземпляра зарегистрирует ещё один маскирующий слушатель.
cft = sys.modules.get("conftest")
if cft is None:
    from tests import conftest as cft


def _without_test_tenant_listener():
    """Снять маскирующий слушатель conftest перед реальным запросом."""
    event.remove(models.Base, "before_insert", cft._set_test_tenant)


def _restore_test_tenant_listener():
    event.listen(models.Base, "before_insert", cft._set_test_tenant, propagate=True)


@contextlib.contextmanager
def _no_test_tenant_listener():
    """Контекст, в котором tenant_id не подставляется никем, кроме кода."""
    _without_test_tenant_listener()
    try:
        yield
    finally:
        _restore_test_tenant_listener()


@pytest.fixture
def upload_dir():
    """UPLOAD_DIR роутера с уборкой созданных тестом файлов."""
    created = []

    def _write(name: str, content: bytes = b"\xff\xd8\xff\xe0test-jpeg") -> str:
        path = os.path.join(nak_router.UPLOAD_DIR, name)
        with open(path, "wb") as stream:
            stream.write(content)
        created.append(path)
        return name

    yield _write
    for path in created:
        if os.path.isfile(path):
            os.remove(path)


def _make_nakladnaya(db, tenant_id, **kw):
    defaults = {
        "doc_type": "ТТН",
        "doc_series": "ТТН",
        "doc_number": str(uuid.uuid4().int)[:9],
        "doc_date": "2026-09-01",
        "amount": 100.0,
        "vat_amount": 10.0,
        "supplier_name": "Поставщик",
        "tenant_id": tenant_id,
    }
    defaults.update(kw)
    nak = models.Nakladnaya(**defaults)
    db.add(nak)
    db.commit()
    db.refresh(nak)
    return nak


# ---------------------------------------------------------------------------
# 1. Приём ботом: tenant обязателен и определён явно
# ---------------------------------------------------------------------------

def test_bot_create_writes_into_single_tenant(client, bot_headers, db):
    """Без фикса INSERT с NULL отбивается триггером миграции 0018 (500)."""
    with _no_test_tenant_listener():
        response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
            "doc_type": "ТТН",
            "doc_series": "ТТН",
            "doc_number": "4881030",
            "doc_date": "2026-09-25",
            "amount": 1000.0,
            "vat_amount": 100.0,
        })
    assert response.status_code == 200, response.text

    db.expire_all()
    nak = db.query(models.Nakladnaya).one()
    assert nak.tenant_id == cft.DEFAULT_TEST_TENANT_ID
    assert nak.created_by_bot is True


def test_bot_create_without_tenant_setting_is_not_500(client, bot_headers, db):
    """Явный tenant из окружения — тоже контракт, а не подстановка 101."""
    with _no_test_tenant_listener():
        response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
            "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4881036",
        })
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.query(models.Nakladnaya).one().tenant_id == cft.DEFAULT_TEST_TENANT_ID


def test_bot_create_uses_explicit_tenant_setting(client, bot_headers, db,
                                                  two_tenant_users, monkeypatch):
    """CRM_BOT_TENANT_ID выбирает tenant явно — в том числе не первый."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV,
                       str(two_tenant_users["second_tenant_id"]))

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "УПД", "doc_series": "УПД", "doc_number": "5550001",
    })
    assert response.status_code == 200, response.text

    db.expire_all()
    nak = db.query(models.Nakladnaya).one()
    assert nak.tenant_id == two_tenant_users["second_tenant_id"]


def test_bot_create_refused_when_tenant_cannot_be_determined(
        client, bot_headers, db, two_tenant_users, monkeypatch):
    """Два tenant'а и молчаливая догадка — отказ, а не запись не в тот tenant.

    Раньше такой запрос просто доходил до INSERT без tenant_id.
    """
    monkeypatch.delenv(nak_router.BOT_TENANT_ENV, raising=False)

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4881031",
    })
    assert response.status_code == 503, response.text
    assert nak_router.BOT_TENANT_ENV in response.json()["detail"]
    assert db.query(models.Nakladnaya).count() == 0


def test_bot_create_refused_for_unknown_tenant_setting(client, bot_headers, db,
                                                        monkeypatch):
    """Опечатка в CRM_BOT_TENANT_ID не превращается в запись «в никуда»."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, "999999")

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4881032",
    })
    assert response.status_code == 503, response.text
    assert db.query(models.Nakladnaya).count() == 0


# ---------------------------------------------------------------------------
# 2. Обычное создание: superadmin без tenant — 403, а не 500
# ---------------------------------------------------------------------------

def test_superadmin_create_returns_403_not_500(db):
    """У superadmin tenant_id IS NULL: пишем 500-ю запись было нельзя.

    Пользователя с пустым tenant в БД не бывает — триггер миграции 0018 стоит
    и на users, поэтому ветка проверяется прямым вызовом роутера (та же
    схема, что в test_custom_objects_tenant.py).
    """
    class _NoTenantUser:
        id = 1
        role = "superadmin"
        tenant_id = None

    with _no_test_tenant_listener(), pytest.raises(HTTPException) as exc:
        nak_router.create_nakladnaya(
            schemas.NakladnayaCreate(doc_type="ТТН", doc_series="ТТН",
                                     doc_number="4881033"),
            db, _NoTenantUser(),
        )
    assert exc.value.status_code == 403, exc.value.detail
    assert db.query(models.Nakladnaya).count() == 0


def test_superadmin_with_tenant_creates_into_its_own_tenant(client, make_user, db):
    """У superadmin с настроенным tenant запись идёт в ЕГО tenant, а не в чужой."""
    _user, headers = make_user("superadmin", username="sa_with_tenant")

    response = client.post("/nakladnye", headers=headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4881035",
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.query(models.Nakladnaya).one().tenant_id == cft.DEFAULT_TEST_TENANT_ID


def test_create_writes_into_callers_tenant(client, two_tenant_users, db):
    """Обычный пользователь пишет в свой tenant, а не в дефолтный."""
    response = client.post("/nakladnye", headers=two_tenant_users["second_headers"],
                           json={"doc_type": "ТТН", "doc_series": "ТТН",
                                 "doc_number": "4881034"})
    assert response.status_code == 200, response.text

    db.expire_all()
    assert db.query(models.Nakladnaya).one().tenant_id == \
        two_tenant_users["second_tenant_id"]


# ---------------------------------------------------------------------------
# 3. Изоляция ручек по tenant
# ---------------------------------------------------------------------------

def test_list_shows_only_own_tenant(client, two_tenant_users, db):
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"], supplier_name="АЛЬФА-T1")
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"], supplier_name="БЕТА-T2")

    first = client.get("/nakladnye", headers=two_tenant_users["first_headers"]).json()
    second = client.get("/nakladnye", headers=two_tenant_users["second_headers"]).json()

    assert [n["supplier_name"] for n in first] == ["АЛЬФА-T1"]
    assert [n["supplier_name"] for n in second] == ["БЕТА-T2"]


def test_list_search_does_not_leak_other_tenant(client, two_tenant_users, db):
    """Поиск по чужому поставщику не должен находить чужую накладную."""
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"], supplier_name="БЕТА-T2")

    found = client.get("/nakladnye", headers=two_tenant_users["first_headers"],
                       params={"q": "БЕТА"}).json()
    assert found == []


def test_foreign_nakladnaya_is_404_for_every_handle(client, two_tenant_users, db,
                                                     upload_dir):
    """Чужой объект неотличим от отсутствующего на всех ручках по id."""
    photo = upload_dir("nak900_aaaaaaaa.jpg")
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                           photo_paths=json.dumps([photo]))
    headers = two_tenant_users["second_headers"]

    assert client.patch(f"/nakladnye/{nak.id}", headers=headers,
                        json={"status": "verified"}).status_code == 404
    assert client.get(f"/nakladnye/{nak.id}/excel", headers=headers).status_code == 404
    assert client.get(f"/nakladnye/photos/{photo}", headers=headers).status_code == 404
    photo_response = client.post(
        f"/nakladnye/{nak.id}/photos", headers=headers,
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0x", "image/jpeg")},
    )
    assert photo_response.status_code == 404, photo_response.text
    assert client.delete(f"/nakladnye/{nak.id}", headers=headers).status_code == 404

    # Ничего не изменилось: чужая накладная на месте и нетронута.
    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).one()
    assert stored.tenant_id == two_tenant_users["first_tenant_id"]
    assert json.loads(stored.photo_paths) == [photo]
    assert stored.status == "new"


def test_missing_and_foreign_nakladnaya_give_same_404(client, two_tenant_users, db):
    """Ответ не должен подтверждать существование чужого id."""
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"])
    headers = two_tenant_users["second_headers"]

    foreign = client.patch(f"/nakladnye/{nak.id}", headers=headers, json={"store": "X"})
    missing = client.patch("/nakladnye/999999", headers=headers, json={"store": "X"})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_bot_endpoints_are_scoped_to_bot_tenant(client, bot_headers, db,
                                                two_tenant_users, monkeypatch):
    """Бот правит и грузит фото только в свой tenant."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV,
                       str(two_tenant_users["first_tenant_id"]))
    nak = _make_nakladnaya(db, two_tenant_users["second_tenant_id"])

    assert client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                        json={"store": "Взломано"}).status_code == 404
    assert client.post(f"/nakladnye/bot/{nak.id}/photos", headers=bot_headers,
                       files={"file": ("p.jpg", b"\xff\xd8\xff\xe0x", "image/jpeg")}
                       ).status_code == 404

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    assert stored.store is None
    assert stored.photo_paths is None


def test_superadmin_keeps_global_scope(client, two_tenant_users, db, make_user):
    """Глобальная видимость superadmin — намеренный контракт, не ломаем."""
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"], supplier_name="АЛЬФА-T1")
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"], supplier_name="БЕТА-T2")

    _user, headers = make_user("superadmin", username="sa_global", tenant_id=None)
    names = {n["supplier_name"]
             for n in client.get("/nakladnye", headers=headers).json()}
    assert names == {"АЛЬФА-T1", "БЕТА-T2"}


# ---------------------------------------------------------------------------
# 4. Раздача фото: только своё, только как вложение, только изображение
# ---------------------------------------------------------------------------

def test_own_photo_is_served_as_attachment(client, two_tenant_users, db, upload_dir):
    """Штатный сценарий фронта (бот загрузил фото, менеджер его смотрит)."""
    photo = upload_dir("nak901_bbbbbbbb.jpg")
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                     photo_paths=json.dumps([photo]))

    response = client.get(f"/nakladnye/photos/{photo}",
                          headers=two_tenant_users["first_headers"])
    assert response.status_code == 200, response.text
    assert response.content == b"\xff\xd8\xff\xe0test-jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == f'attachment; filename="{photo}"'
    assert response.headers["x-content-type-options"] == "nosniff"


def test_unreferenced_upload_is_not_served(client, two_tenant_users, db, upload_dir):
    """Файл в UPLOAD_DIR сам по себе не делается доступным.

    Раньше ручка отдавала что угодно из каталога; сюда попадают и вложения
    карточек, и скриншоты — всё это закрыто.
    """
    stranger = upload_dir("card900_cccccccc.jpg", "чужое содержимое".encode("utf-8"))
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"])

    response = client.get(f"/nakladnye/photos/{stranger}",
                          headers=two_tenant_users["first_headers"])
    assert response.status_code == 404
    assert "чужое содержимое".encode() not in response.content


def test_html_upload_cannot_be_rendered_inline(client, two_tenant_users, db, upload_dir):
    """Content-Type по белому списку + attachment + nosniff: Stored XSS закрыт."""
    evil = upload_dir("nak902_dddddddd.jpg", b"<script>alert(1)</script>")
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                           photo_paths=json.dumps([evil]))
    headers = two_tenant_users["first_headers"]

    response = client.get(f"/nakladnye/photos/{evil}", headers=headers)
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"].startswith("attachment;")

    # Не-картинка под именем .jpg всё равно не должна отдаваться как документ.
    html = upload_dir("nak903_eeeeeeee.html", b"<script>alert(1)</script>")
    nak.photo_paths = json.dumps([html])
    db.commit()
    response = client.get(f"/nakladnye/photos/{html}", headers=headers)
    assert response.status_code == 404
    assert b"<script>" not in response.content


@pytest.mark.parametrize("evil_name", [
    "../../../../etc/hosts",
    "..%2f..%2f..%2fetc%2fhosts",
    "sub/dir/nak904_ffffffff.jpg",
    "/etc/hosts",
    "nak904_ffffffff.jpg/../../../../etc/hosts",
])
def test_path_traversal_is_refused(client, two_tenant_users, db, upload_dir,
                                   evil_name):
    """Выход за пределы UPLOAD_DIR невозможен ни через путь, ни через symlink."""
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                     photo_paths=json.dumps([evil_name]))
    response = client.get(
        "/nakladnye/photos/" + evil_name,
        headers=two_tenant_users["first_headers"],
    )
    assert response.status_code == 404, response.text
    assert b"localhost" not in response.content


def test_symlink_out_of_upload_dir_is_refused(client, two_tenant_users, db,
                                              upload_dir, tmp_path):
    """realpath-фильтр закрывает и симлинк за пределы каталога."""
    outside = tmp_path / "secret.jpg"
    outside.write_bytes("секрет за пределами uploads".encode("utf-8"))
    link_name = "nak906_gggggggg.jpg"
    link_path = os.path.join(nak_router.UPLOAD_DIR, link_name)
    if os.path.lexists(link_path):
        os.remove(link_path)
    os.symlink(str(outside), link_path)
    try:
        _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                         photo_paths=json.dumps([link_name]))
        response = client.get(f"/nakladnye/photos/{link_name}",
                              headers=two_tenant_users["first_headers"])
        assert response.status_code == 404, response.text
        assert b"\xd1\x81\xd0\xb5\xd0\xba\xd1\x80\xd0\xb5\xd1\x82" not in response.content
    finally:
        os.remove(link_path)


def test_photo_lookup_helper_rejects_unsafe_names(upload_dir):
    """Юнит-регресс на нормализацию пути (os.path.basename и symlink)."""
    upload_dir("nak907_hhhhhhhh.jpg")
    assert nak_router._photo_file_in_uploads("nak907_hhhhhhhh.jpg")
    assert nak_router._photo_file_in_uploads("../nak907_hhhhhhhh.jpg") is None
    assert nak_router._photo_file_in_uploads("sub/nak907_hhhhhhhh.jpg") is None
    assert nak_router._photo_file_in_uploads("nak907_hhhhhhhh.sh") is None
    assert nak_router._photo_file_in_uploads("") is None
    assert nak_router._photo_file_in_uploads("..") is None
    # Имя без расширения-из-белого-списка и с кавычкой/CRLF в заголовок.
    assert nak_router._photo_file_in_uploads('a";x=.jpg') is None
    assert nak_router._photo_file_in_uploads("a\r\nX-Evil: 1.jpg") is None
    assert nak_router._photo_file_in_uploads("nak907_hhhhhhhh.jpg\x00") is None


# ---------------------------------------------------------------------------
# 5. bot/check-duplicate: не полный скан и не чужие tenant'ы
# ---------------------------------------------------------------------------

def test_check_duplicate_is_not_a_full_table_scan(client, bot_headers, db,
                                                  monkeypatch):
    """Запрос к БД обязан быть tenant-scoped и ограниченным.

    Раньше здесь был ``db.query(models.Nakladnaya).all()`` — вся таблица
    накладных грузилась в память на каждый вызов бота.
    """
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, str(cft.DEFAULT_TEST_TENANT_ID))
    _make_nakladnaya(db, cft.DEFAULT_TEST_TENANT_ID)

    statements = []

    def _capture(_conn, _cursor, statement, _params, _ctx, _many):
        if "FROM nakladnye" in statement:
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", _capture)
    try:
        response = client.get("/nakladnye/bot/check-duplicate",
                              params={"doc_series": "ТТН", "doc_number": "4881030"},
                              headers=bot_headers)
    finally:
        event.remove(database.engine, "before_cursor_execute", _capture)

    assert response.status_code == 200, response.text
    assert statements, "запрос к nakladnye не зафиксирован"
    for statement in statements:
        assert "tenant_id" in statement, statement
        assert "LIMIT" in statement.upper(), statement


def test_check_duplicate_ignores_other_tenant(client, bot_headers, db,
                                              two_tenant_users, monkeypatch):
    """Дубль чужого tenant'а не должен останавливать приём."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV,
                       str(two_tenant_users["first_tenant_id"]))
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"],
                     doc_series="ТТН", doc_number="4881030")

    response = client.get("/nakladnye/bot/check-duplicate",
                          params={"doc_series": "ТТН", "doc_number": "4881030"},
                          headers=bot_headers)
    assert response.json() == {"duplicate": False}


def test_check_duplicate_still_finds_own_legacy_row_without_norm(
        client, bot_headers, db, monkeypatch):
    """Строка, записанная мимо ORM (пустой *_norm), всё ещё находится."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, str(cft.DEFAULT_TEST_TENANT_ID))
    nak = _make_nakladnaya(db, cft.DEFAULT_TEST_TENANT_ID,
                           doc_series="ТТН", doc_number="4881030")
    nak.doc_series_norm = None
    nak.doc_number_norm = None
    db.commit()

    response = client.get("/nakladnye/bot/check-duplicate",
                          params={"doc_series": " ттн ", "doc_number": "ТТН 4881030"},
                          headers=bot_headers)
    assert response.json() == {"duplicate": True, "id": nak.id}


# ---------------------------------------------------------------------------
# 6. photo_paths: порча данных не затирается молча
# ---------------------------------------------------------------------------

def test_upload_does_not_overwrite_broken_photo_paths(client, two_tenant_users, db):
    """Раньше битый photo_paths тихо заменялся одним новым именем."""
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                           photo_paths="{это не json")
    headers = two_tenant_users["first_headers"]

    response = client.post(
        f"/nakladnye/{nak.id}/photos", headers=headers,
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0x", "image/jpeg")},
    )
    assert response.status_code == 500, response.text

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    assert stored.photo_paths == "{это не json"


def test_upload_keeps_readable_entries_around_a_bad_one(
        client, two_tenant_users, db, upload_dir):
    """Читаемое сохраняется, нечитаемое отбрасывается с логом — не наоборот."""
    kept = upload_dir("nak908_iiiiiiii.jpg")
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                           photo_paths=json.dumps([kept, 42, None]))

    response = client.post(
        f"/nakladnye/{nak.id}/photos", headers=two_tenant_users["first_headers"],
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0x", "image/jpeg")},
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    photos = json.loads(stored.photo_paths)
    assert photos[0] == kept
    assert len(photos) == 2


def test_delete_survives_broken_photo_paths(client, two_tenant_users, db):
    """Читающий путь не падает: накладная удаляется, потеря залогирована."""
    nak = _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                           photo_paths="{это не json")
    nak_id = nak.id

    response = client.delete(f"/nakladnye/{nak_id}",
                             headers=two_tenant_users["first_headers"])
    assert response.status_code == 200, response.text
    db.expunge_all()
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak_id).count() == 0


# ---------------------------------------------------------------------------
# 3 (продолжение): выгрузки не текут за пределы tenant
# ---------------------------------------------------------------------------

def test_export_products_excel_contains_only_own_tenant(client, two_tenant_users, db):
    pytest.importorskip("openpyxl", reason="openpyxl не в requirements.txt venv")
    _make_nakladnaya(db, two_tenant_users["first_tenant_id"],
                     supplier_name="АЛЬФА-T1",
                     products_json=json.dumps([{"name": "ТОВАР-T1", "qty": 1,
                                                "price_no_vat": 10}]))
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"],
                     supplier_name="БЕТА-T2",
                     products_json=json.dumps([{"name": "ТОВАР-T2", "qty": 1,
                                                "price_no_vat": 10}]))

    response = client.get("/nakladnye/export/products-excel",
                          headers=two_tenant_users["first_headers"])
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert "АЛЬФА-T1" in text and "ТОВАР-T1" in text
    assert "БЕТА-T2" not in text and "ТОВАР-T2" not in text


def _xlsx_text(content: bytes) -> str:
    """Читаемый текст xlsx (это zip с XML внутри)."""
    parts = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                parts.append(archive.read(name).decode("utf-8", "replace"))
    return "\n".join(parts)


# --- 7. Поиск дубля по ключу документа не выходит за пределы tenant'а ------
#
# Уникальный индекс uq_nakladnye_doc_key построен по
# (tenant_id, doc_series_norm, doc_number_norm), то есть дублем считается
# совпадение ВНУТРИ компании. А вот поиск существующего документа для текста
# 409 шёл по всему ключу без tenant: сам дубль не создавался, зато номер
# накладной чужой компании попадал в ответ («уже принята (id=…)»).


def test_find_by_doc_key_does_not_see_other_tenant(db, two_tenant_users):
    from services import nakladnye as nak_service

    doc_series, doc_number = "ТТН", "7770001"
    _make_nakladnaya(db, two_tenant_users["second_tenant_id"],
                     doc_series=doc_series, doc_number=doc_number)

    found = nak_service.find_by_doc_key(
        db, doc_series, doc_number, two_tenant_users["first_tenant_id"])
    assert found is None, "ключ документа чужого tenant'а найден"

    own = nak_service.find_by_doc_key(
        db, doc_series, doc_number, two_tenant_users["second_tenant_id"])
    assert own is not None, "свой документ не найден"


def test_find_by_doc_key_requires_tenant_argument():
    """Без tenant параметра функция вызываться не должна: «искать по всем»
    — это ровно тот дефект, который закрыт выше."""
    import inspect

    from services import nakladnye as nak_service

    params = list(inspect.signature(nak_service.find_by_doc_key).parameters)
    assert params == ["session", "doc_series", "doc_number", "tenant_id"], params
