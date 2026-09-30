"""Фото накладных и бот-ручки nakladnye_router: загрузка, удаление, правка.

Модуль закрывает контракты, которых нет в соседних файлах
(``test_nakladnye_tenant.py`` — изоляция по tenant, ``test_nakladnye_dedup.py``
— дубли и суммы):

1. ``POST /nakladnye/{id}/photos`` (CRM): отказ по недопустимому расширению
   (400), отказ по размеру (413) и имя файла по умолчанию ``photo.jpg``, когда
   клиент не прислал имя. Отдельно и явно — главный контраст модуля: при отказе
   на диске НЕ остаётся файла, потому что список фото читается ДО записи
   (nakladnye_router.py:465-476). Иначе каждая неудачная загрузка оставляла бы
   сироту, на которую никто не ссылается.
2. ``DELETE /nakladnye/{id}``: успешный путь стирает с диска реальные файлы
   фото. В существующих тестах при удалении накладной файлов на диске нет, то
   есть строки 432-435 (цикл os.remove) не исполнялись никогда.
3. ``POST /nakladnye/bot/{id}/photos``: асимметрия с CRM-ручкой — бот МОЛЧА
   подменяет невалидное расширение на ``.jpg`` (строка 772), тогда как CRM
   отвечает 400. Это намеренное поведение (бот получает фото из Telegram, где
   имя присылает оператор), и оно обязано быть зафиксировано тестом с обеих
   сторон, иначе «починка» бота на симметрию с CRM тихо отвергнет приём фото
   с телефона.
4. ``PATCH /nakladnye/bot/{id}``: успешная правка с сериализацией products,
   правило НДС по эффективным значениям (400) и 409 с ``existing_id`` на
   занятый ключ документа.
5. ``_bot_actor``: нечисловое значение ``CRM_BOT_TENANT_ID`` (503) и
   неположительный tenant (503). Существующий tenant и «tenant не настроен»
   закрыты в test_nakladnye_tenant.py — здесь только эти две ветки.
6. ``GET /nakladnye/bot/check-duplicate``: номер без цифр («б/н») даёт early
   return, а выборка кандидатов реально ограничена ``DUP_SCAN_LIMIT`` и
   ``order_by(id.desc())`` берёт последние записи — с обеих сторон границы.

Фикстура ``photos_dir`` подменяет ``nak_router.UPLOAD_DIR`` на tmp-каталог:
константа вычисляется НА ИМПОРТЕ (строка 38), поэтому ``monkeypatch.setenv``
её не меняет, а запись в настоящий uploads недопустима.
"""
import asyncio
import io
import json
import logging
import sys
import uuid
from datetime import datetime

import pytest
from fastapi import UploadFile
from sqlalchemy import text

import database
import models
import routers.nakladnye_router as nak_router

# Pytest обычно загружает conftest как ``conftest``; при импорте как пакета
# он доступен как ``tests.conftest``. Берём уже загруженный модуль (см.
# test_nakladnye_tenant.py) — второй экземпляр зарегистрировал бы ещё один
# слушатель before_insert.
cft = sys.modules.get("conftest")
if cft is None:
    from tests import conftest as cft

TENANT = cft.DEFAULT_TEST_TENANT_ID
JPEG = b"\xff\xd8\xff\xe0test-jpeg"


def _make_nakladnaya(db, tenant_id=TENANT, **kw):
    """Накладная с уникальным ключом документа (иначе сработает uq_nakladnye_doc_key)."""
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


@pytest.fixture
def photos_dir(monkeypatch, tmp_path):
    """UPLOAD_DIR роутера, подменённый на временный каталог теста."""
    monkeypatch.setattr(nak_router, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _files(directory):
    """Имена файлов в каталоге (для проверок «ничего не осталось»)."""
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def _photo_names(db, nak_id):
    db.expire_all()
    nak = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak_id).one()
    return json.loads(nak.photo_paths) if nak.photo_paths else []


def _upload(client, url, headers, filename="photo.jpg", content=JPEG, content_type="image/jpeg"):
    return client.post(url, headers=headers,
                       files={"file": (filename, content, content_type)})


def _post_multipart(client, url, headers, disposition):
    """Загрузка файла с заданной строкой Content-Disposition (в т.ч. filename="")."""
    body = (b"--B\r\n" + disposition + b"Content-Type: image/jpeg\r\n\r\n"
            + JPEG + b"\r\n--B--\r\n")
    return client.post(url, headers={**headers,
                                     "Content-Type": "multipart/form-data; boundary=B"},
                       content=body)


# ---------------------------------------------------------------------------
# 1. POST /nakladnye/{id}/photos — CRM
# ---------------------------------------------------------------------------

def test_crm_upload_rejects_extension_outside_allowlist(client, manager, db, photos_dir):
    """Не-картинка отклоняется, и на диске не остаётся ни одного файла."""
    _, headers = manager
    nak = _make_nakladnaya(db)

    for bad in ("накладная.pdf", "документ.xls", "скрипт.html", "без-расширения"):
        response = _upload(client, f"/nakladnye/{nak.id}/photos", headers, filename=bad)
        assert response.status_code == 400, f"{bad!r} принят: {response.text}"
        assert ".jpg" in response.json()["detail"], response.json()["detail"]

    assert _files(photos_dir) == [], "отказ по расширению не должен оставлять файл"
    assert _photo_names(db, nak.id) == []


def test_crm_upload_rejects_file_over_limit(client, manager, db, photos_dir, monkeypatch):
    """Файл больше MAX_UPLOAD_BYTES отклоняется до записи.

    Порог подменяется: реальные 25 МБ ради теста грузить нельзя.
    В тексте ответа «макс. 25 МБ» зашито константой — это известная
    неточность, проверяется только смысл отказа.
    """
    _, headers = manager
    nak = _make_nakladnaya(db)
    monkeypatch.setattr(nak_router, "MAX_UPLOAD_BYTES", 1024)

    response = _upload(client, f"/nakladnye/{nak.id}/photos", headers,
                       content=b"x" * 2048)
    assert response.status_code == 413, response.text
    assert "слишком большой" in response.json()["detail"]

    assert _files(photos_dir) == [], "файл записан до проверки размера"
    assert _photo_names(db, nak.id) == [], "photo_paths изменён при отказе"

    # Граница проходит: ровно 1024 байта файл принимается.
    ok = _upload(client, f"/nakladnye/{nak.id}/photos", headers,
                 content=b"x" * 1024)
    assert ok.status_code == 200, ok.text
    assert _files(photos_dir), "файл на границе лимита должен записаться"


def test_crm_upload_uses_photo_jpg_when_filename_missing(client, manager, db, photos_dir):
    """Пустое имя файла даёт photo.jpg, а не имя вроде nak7_ab12 без расширения.

    Запрос собран вручную: httpx при filename="" не добавляет параметр filename
    в Content-Disposition вовсе, и FastAPI отвечает 422 (часть приходит строкой,
    а не UploadFile). Реальный пустой filename доходит только «ручным» multipart.
    """
    _, headers = manager
    nak = _make_nakladnaya(db)

    response = _post_multipart(client, f"/nakladnye/{nak.id}/photos", headers,
                               b'Content-Disposition: form-data; name="file"; filename=""\r\n')
    assert response.status_code == 200, response.text

    photos = response.json()["photo_paths"]
    assert len(photos) == 1
    name = photos[0]
    assert name.startswith(f"nak{nak.id}_")
    assert name.endswith(".jpg"), name
    assert (photos_dir / name).is_file()
    assert (photos_dir / name).read_bytes() == JPEG
    assert _photo_names(db, nak.id) == [name]


def test_upload_handler_treats_none_filename_as_photo_jpg(db, photos_dir):
    """Ветка ``file.filename or "photo.jpg"`` для настоящего filename=None.

    Прямой вызов хендлера: multipart-клиент может прислать пустую строку, но
    filename=None в объект UploadFile попадает не из любого клиента, и именно
    на нём держится дефолт в коде.
    """
    nak = _make_nakladnaya(db)
    session = database.SessionLocal()

    class _Viewer:
        role = "manager"
        tenant_id = TENANT

    try:
        result = asyncio.run(nak_router.upload_nakladnaya_photo(
            nak.id, UploadFile(filename=None, file=io.BytesIO(JPEG)),
            session, _Viewer(),
        ))
    finally:
        session.close()

    assert len(result["photo_paths"]) == 1
    assert result["photo_paths"][0].endswith(".jpg")
    assert _files(photos_dir) == [result["photo_paths"][0]]


def test_crm_upload_appends_and_keeps_files(client, manager, db, photos_dir):
    """Второе фото дописывается, а первое не теряется ни в БД, ни на диске."""
    _, headers = manager
    nak = _make_nakladnaya(db)

    first = _upload(client, f"/nakladnye/{nak.id}/photos", headers, filename="one.jpg")
    second = _upload(client, f"/nakladnye/{nak.id}/photos", headers, filename="two.png")
    assert first.status_code == second.status_code == 200, second.text

    photos = second.json()["photo_paths"]
    assert photos == first.json()["photo_paths"] + [photos[-1]]
    assert len(photos) == 2
    assert _files(photos_dir) == sorted(photos)
    for name in photos:
        assert (photos_dir / name).read_bytes() == JPEG


def test_crm_upload_refused_on_broken_photo_paths_leaves_no_file(client, manager, db,
                                                                 photos_dir):
    """Битый photo_paths: 500 и НИКАКОГО файла на диске.

    Список читается до записи именно ради этого: при отказе не должен
    появляться файл, на который никто не ссылается.
    """
    _, headers = manager
    nak = _make_nakladnaya(db, photo_paths="{это не json")

    response = _upload(client, f"/nakladnye/{nak.id}/photos", headers)
    assert response.status_code == 500, response.text
    assert _files(photos_dir) == [], "при отказе по photo_paths файл записан"

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).one()
    assert stored.photo_paths == "{это не json", "битое значение затёрто"


# ---------------------------------------------------------------------------
# 2. DELETE /nakladnye/{id} — файлы фото стираются с диска
# ---------------------------------------------------------------------------

def test_delete_removes_photo_files_from_disk(client, admin, bot_headers, db, photos_dir):
    """Успешное удаление стирает файлы, перечисленные в photo_paths.

    В существующих тестах накладные удаляются без файлов, поэтому ветка с
    os.remove не проверялась. Роль — admin (гейт require_role), сама роль
    проверена в test_nakladnye_dedup.py.
    """
    _admin, headers = admin
    nak = _make_nakladnaya(db)
    nak_id = nak.id

    first = _upload(client, f"/nakladnye/{nak_id}/photos", headers, filename="a.jpg")
    second = _upload(client, f"/nakladnye/bot/{nak_id}/photos", bot_headers,
                     filename="b.png")
    assert first.status_code == second.status_code == 200, second.text
    photos = second.json()["photo_paths"]
    assert _files(photos_dir) == sorted(photos)

    # Файл, на который накладная не ссылается, удалением не трогается:
    # роутер идёт по списку имён, а не по каталогу.
    stranger = photos_dir / "card900_orphan.jpg"
    stranger.write_bytes(JPEG)

    response = client.delete(f"/nakladnye/{nak_id}", headers=headers)
    assert response.status_code == 200, response.text

    for name in photos:
        assert not (photos_dir / name).exists(), f"фото {name} осталось на диске"
    assert _files(photos_dir) == [stranger.name], "вынесенное из photo_paths затронуто"
    assert stranger.is_file()

    db.expunge_all()
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak_id).count() == 0


def test_delete_ignores_missing_file_but_removes_row(client, admin, db, photos_dir):
    """Ссылка на несуществующий файл не должна ломать удаление накладной."""
    _admin, headers = admin
    nak = _make_nakladnaya(db, photo_paths=json.dumps(["nak999_ffffffff.jpg"]))
    assert _files(photos_dir) == []

    response = client.delete(f"/nakladnye/{nak.id}", headers=headers)
    assert response.status_code == 200, response.text
    assert _files(photos_dir) == []

    db.expunge_all()
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).count() == 0


# ---------------------------------------------------------------------------
# 3. POST /nakladnye/bot/{id}/photos — бот
# ---------------------------------------------------------------------------

def test_bot_replaces_bad_extension_where_crm_rejects_it(client, manager, bot_headers,
                                                          db, photos_dir):
    """Асимметрия зафиксирована с обеих сторон: CRM 400, бот молча пишет .jpg.

    Бот получает фото из Telegram, где имя прислал оператор («IMG_1234»,
    «document.pdf»), и отказ означал бы потерю снимка накладной целиком.
    CRM-ручка отказ сохраняет: там имя выбирает оператор из своего каталога,
    и молчаливая подмена скрыла бы опечатку.
    """
    _manager, headers = manager
    nak = _make_nakladnaya(db)

    crm = _upload(client, f"/nakladnye/{nak.id}/photos", headers,
                  filename="снимок.png.txt")
    assert crm.status_code == 400, crm.text
    assert _files(photos_dir) == [], "CRM-отказ не должен оставить файл"

    bot = _upload(client, f"/nakladnye/bot/{nak.id}/photos", bot_headers,
                  filename="снимок.png.txt")
    assert bot.status_code == 200, bot.text
    name = bot.json()["photo_paths"][0]
    assert name.endswith(".jpg"), name
    assert not name.endswith(".txt")
    assert (photos_dir / name).read_bytes() == JPEG
    assert _files(photos_dir) == [name]


def test_bot_upload_rejects_file_over_limit(client, bot_headers, db, photos_dir,
                                            monkeypatch):
    """Тот же порог размера, что и на CRM-пути, и тоже до записи файла."""
    nak = _make_nakladnaya(db)
    monkeypatch.setattr(nak_router, "MAX_UPLOAD_BYTES", 1024)

    response = _upload(client, f"/nakladnye/bot/{nak.id}/photos", bot_headers,
                       content=b"x" * 2048)
    assert response.status_code == 413, response.text
    assert _files(photos_dir) == [], "файл записан до проверки размера"
    assert _photo_names(db, nak.id) == []


def test_bot_upload_appends_to_existing_photos(client, bot_headers, db, photos_dir):
    """Второе фото бота дописывается к первому, а не заменяет список."""
    nak = _make_nakladnaya(db)

    first = _upload(client, f"/nakladnye/bot/{nak.id}/photos", bot_headers,
                    filename="one.jpg")
    assert first.status_code == 200, first.text
    second = _upload(client, f"/nakladnye/bot/{nak.id}/photos", bot_headers,
                     filename="two.heic")
    assert second.status_code == 200, second.text

    photos = second.json()["photo_paths"]
    assert photos == first.json()["photo_paths"] + [photos[-1]]
    assert len(photos) == 2
    assert photos[1].endswith(".heic")
    assert _files(photos_dir) == sorted(photos)
    assert _photo_names(db, nak.id) == photos


def test_bot_upload_refused_on_broken_photo_paths_leaves_no_file(client, bot_headers,
                                                                 db, photos_dir):
    """Битый photo_paths блокирует и бота — и тоже без следа на диске."""
    nak = _make_nakladnaya(db, photo_paths='{"не": "список"}')

    response = _upload(client, f"/nakladnye/bot/{nak.id}/photos", bot_headers)
    assert response.status_code == 500, response.text
    assert _files(photos_dir) == [], "при отказе по photo_paths файл записан"

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).one()
    assert stored.photo_paths == '{"не": "список"}'


# ---------------------------------------------------------------------------
# 4. PATCH /nakladnye/bot/{id}
# ---------------------------------------------------------------------------

def test_bot_patch_serializes_products(client, bot_headers, db):
    """products сохраняются в products_json и читаются обратно в ответе."""
    nak = _make_nakladnaya(db)
    products = [
        {"name": "Светильник", "qty": 2, "unit": "шт", "price_no_vat": 50.0},
        {"name": "Лампа LED", "qty": 10, "unit": "шт", "price_no_vat": 12.5},
    ]

    response = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                            json={"store": "Матусевича 72", "products": products})
    assert response.status_code == 200, response.text
    assert response.json()["products"] == products
    assert response.json()["store"] == "Матусевича 72"

    db.expire_all()
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    assert json.loads(fresh.products_json) == products


def test_bot_patch_with_empty_products_clears_the_list(client, bot_headers, db):
    """Пустой список товаров очищает products_json, а не пишет «[]»."""
    nak = _make_nakladnaya(db, products_json=json.dumps(
        [{"name": "Светильник", "qty": 1, "price_no_vat": 100.0}]))

    response = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                            json={"products": []})
    assert response.status_code == 200, response.text
    assert response.json()["products"] == []

    db.expire_all()
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    assert fresh.products_json is None


def test_bot_patch_enforces_vat_rule_on_effective_amounts(client, bot_headers, db):
    """Частичная правка НДС сверяется с сохранённой суммой — отказ 400.

    Схема видит только присланное, поэтому правило работает по эффективным
    значениям (nak + update_data); отклонённая правка в базу не попадает.
    """
    nak = _make_nakladnaya(db, amount=100.0, vat_amount=10.0)

    over_vat = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                            json={"vat_amount": 500.0})
    assert over_vat.status_code == 400, over_vat.text
    assert "НДС" in over_vat.json()["detail"]

    low_amount = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                              json={"amount": 5.0})
    assert low_amount.status_code == 400, low_amount.text

    db.expire_all()
    fresh = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == nak.id).one()
    assert round(float(fresh.amount), 2) == 100.0
    assert round(float(fresh.vat_amount), 2) == 10.0

    # Законная правка проходит.
    ok = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                      json={"amount": 200.0, "vat_amount": 33.34})
    assert ok.status_code == 200, ok.text


def test_bot_patch_onto_occupied_doc_key_returns_409_with_existing_id(
        client, bot_headers, db):
    """Правка номера на занятый ключ: 409 и existing_id существующей записи."""
    victim = _make_nakladnaya(db, doc_series="АБ", doc_number="4882001")
    target = _make_nakladnaya(db, doc_series="АБ", doc_number="4882002")

    response = client.patch(f"/nakladnye/bot/{target.id}", headers=bot_headers,
                            json={"doc_number": "ТТН 4882001"})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict) and "existing_id" in detail
    assert detail["existing_id"] == victim.id

    db.expire_all()
    fresh = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == target.id).one()
    assert fresh.doc_number == "4882002", "отклонённая правка записалась"
    untouched = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == victim.id).one()
    assert untouched.doc_number == "4882001", "посторонняя запись изменилась"


def test_bot_patch_updates_updated_at(client, bot_headers, db):
    """Правка проставляет updated_at — по нему видно, что запись трогали.

    Проверяется поведение, а не конкретная строка роутера: у модели Nakladnaya
    есть ``onupdate``, поэтому штатно обновляет время и ORM сам (проверено
    мутацией — удаление явного присваивания тест не ловит). Контракт, который
    важен, — «после правки updated_at новее прежнего», а не кто именно его
    проставил.
    """
    nak = _make_nakladnaya(db)
    nak.updated_at = datetime(2020, 1, 1)
    db.commit()
    assert _naive(_photo_updated_at(db, nak.id)) == datetime(2020, 1, 1)

    response = client.patch(f"/nakladnye/bot/{nak.id}", headers=bot_headers,
                            json={"is_verified": True})
    assert response.status_code == 200, response.text
    assert response.json()["is_verified"] is True

    assert _naive(_photo_updated_at(db, nak.id)) > datetime(2020, 1, 1)


def _photo_updated_at(db, nak_id):
    db.expire_all()
    return db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak_id).one().updated_at


def _naive(value):
    """SQLite отдаёт updated_at без tzinfo, а код пишет aware-datetime."""
    return value.replace(tzinfo=None) if value.tzinfo else value


# ---------------------------------------------------------------------------
# 5. _bot_actor: конфигурация tenant'а бота
# ---------------------------------------------------------------------------

def test_bot_refused_when_tenant_setting_is_not_a_number(client, bot_headers, db,
                                                         monkeypatch):
    """Нечисловая CRM_BOT_TENANT_ID — отказ 503 с именем переменной.

    Молчаливый fallback на «единственный tenant в базе» здесь невозможен:
    конфигурация задана и задана неверно, это надо видять сразу.
    """
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, "сто первый")

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4883001",
    })
    assert response.status_code == 503, response.text
    assert nak_router.BOT_TENANT_ENV in response.json()["detail"]
    assert db.query(models.Nakladnaya).count() == 0


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_bot_refused_for_non_positive_tenant_setting(client, bot_headers, db,
                                                      monkeypatch, raw):
    """Ноль и отрицательное значение — тот же отказ 503, записи нет."""
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, raw)

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4883002",
    })
    assert response.status_code == 503, response.text
    assert nak_router.BOT_TENANT_ENV in response.json()["detail"]
    assert db.query(models.Nakladnaya).count() == 0


def test_blank_tenant_setting_falls_back_to_single_tenant(client, bot_headers, db,
                                                          monkeypatch):
    """Пробелы в значении переменной считаются «не задано».

    В базе один tenant, поэтому конфигурация однозначна и приём работает.
    Отличается от пустой строки только trim'ом — но именно на этом отличается
    ветка «явно настроено» от ветки «настроить нечем».
    """
    monkeypatch.setenv(nak_router.BOT_TENANT_ENV, "   ")

    response = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_type": "ТТН", "doc_series": "ТТН", "doc_number": "4883003",
    })
    assert response.status_code == 200, response.text

    db.expire_all()
    assert db.query(models.Nakladnaya).one().tenant_id == TENANT


# ---------------------------------------------------------------------------
# 6. GET /nakladnye/bot/check-duplicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_number", ["б/н", "без номера", "   ", "---"])
def test_check_duplicate_returns_false_for_number_without_digits(
        client, bot_headers, db, doc_number):
    """Номер без цифр — ранний выход: сравнивать нечего.

    Раньше такие записи сравнивались по пустому ключу, и вторая законная
    накладная «б/н» выглядела бы дублем первой.
    """
    _make_nakladnaya(db, doc_series="АБ", doc_number=doc_number)

    response = client.get("/nakladnye/bot/check-duplicate",
                          params={"doc_series": "АБ", "doc_number": doc_number},
                          headers=bot_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"duplicate": False}


def _make_row_without_norm(db, doc_number, tenant_id=TENANT):
    """Строка, записанная мимо ORM: производные *_norm пустые (ветка IS NULL)."""
    nak = models.Nakladnaya(doc_type="ТТН", doc_series="СТАРЫЙ",
                            doc_number=doc_number, doc_date="2026-09-01",
                            supplier_name="П", tenant_id=tenant_id)
    db.add(nak)
    db.commit()
    db.refresh(nak)
    nak_id = nak.id
    # Слушатель before_update пересчитал бы ключи заново, поэтому мимо ORM.
    db.execute(text("UPDATE nakladnye SET doc_series_norm = NULL, "
                    "doc_number_norm = NULL WHERE id = :id"), {"id": nak_id})
    db.commit()
    db.expire_all()
    return nak_id


def test_check_duplicate_scan_is_limited_to_the_newest_rows(client, bot_headers, db,
                                                            monkeypatch, caplog):
    """DUP_SCAN_LIMIT режет выборку, и order_by(id.desc()) берёт последние.

    Найденный дубль — самаяя старая из кандидатов: он не попадает в первые
    DUP_SCAN_LIMIT строк, поэтому ручка отвечает «не дубль» и пишет
    предупреждение. Уникальный индекс всё равно не даст создать второй
    документ — предупреждение нужно именно для оператора.
    """
    monkeypatch.setattr(nak_router, "DUP_SCAN_LIMIT", 2)
    old_id = _make_row_without_norm(db, "4884001")
    for number in ("9990001", "9990002", "9990003"):
        _make_row_without_norm(db, number)

    with caplog.at_level(logging.WARNING, logger="routers.nakladnye_router"):
        response = client.get("/nakladnye/bot/check-duplicate",
                              params={"doc_series": "СТАРЫЙ", "doc_number": "4884001"},
                              headers=bot_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"duplicate": False}, "старый кандидат не должен попадать в срез"
    assert "кандидатов больше 2" in caplog.text, caplog.text
    # Запись жива: ответ «не дубль» — это свойство выборки, а не удаления.
    assert db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == old_id).count() == 1


def test_check_duplicate_finds_match_inside_the_limit(client, bot_headers, db,
                                                      monkeypatch):
    """Обратная сторона той же границы: свежий кандидат дубль находится.

    Тот же срез по id.desc(), но совпадение — последняя запись: без правильной
    сортировки (например, по возрастанию id) она была бы отрезана.
    """
    monkeypatch.setattr(nak_router, "DUP_SCAN_LIMIT", 2)
    for number in ("9990001", "9990002", "9990003"):
        _make_row_without_norm(db, number)
    fresh_id = _make_row_without_norm(db, "4884002")

    response = client.get("/nakladnye/bot/check-duplicate",
                          params={"doc_series": "СТАРЫЙ", "doc_number": "4884002"},
                          headers=bot_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"duplicate": True, "id": fresh_id}
