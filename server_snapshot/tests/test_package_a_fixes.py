"""Пакет A (план 2026-09-19): три серверных фикса контрактов.

A1 — справочник статусов: при пустой таблице GET /dictionaries/statuses
     возвращает пустой список, а не хардкод.
A2 — doc_type: неканоническое значение → 422, тихой подмены нет.
A4 — дубль накладной: 409 + existing_id в теле, а не 201/400 без тела.

Каждый тест проверяет именно контракт, который чинил пакет; смежные
сценарии (нормализация регистра, пустой doc_type, бот-дубль через
check-duplicate) покрыты в test_nakladnye_dedup.py.
"""
import pytest

import models


# ---------------------------------------------------------------------------
# A1: пустой справочник статусов → пустой список
# ---------------------------------------------------------------------------

def test_empty_statuses_returns_empty_list(client, admin, db):
    """Сервер — источник истины. Если в БД нет статусов, клиент получает [].

    Раньше (до фикса A1) сервер подставлял хардкод ['Новый','В работе','Закрыто'],
    и клиент показывал статусы, которые нельзя назначить (PATCH валидирует по БД).
    """
    _, headers = admin
    # Удаляем все статусы, если они есть (в prod-схеме таблица может быть
    # непустой из fixtures; для теста важна именно реакция на пустую таблицу).
    db.query(models.DealStatus).delete()
    db.commit()

    r = client.get("/dictionaries/statuses", headers=headers)
    assert r.status_code == 200
    assert r.json() == []


def test_statuses_returns_only_db_rows(client, admin, db):
    """GET /dictionaries/statuses отдаёт ровно то, что в БД, без добавленных
    «по умолчанию» строк.
    """
    _, headers = admin
    db.query(models.DealStatus).delete()
    db.commit()

    # Добавляем один статус — и только его видим в ответе.
    r = client.post("/dictionaries/statuses",
                    json={"name": "Новый запрос", "position": 0, "color": "#4f46e5"},
                    headers=headers)
    assert r.status_code == 200

    r = client.get("/dictionaries/statuses", headers=headers)
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Новый запрос"


# ---------------------------------------------------------------------------
# A2: doc_type — валидация по канону
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_type", ["Накладная", "Счёт", "Акт", "СЧФ", "foo"])
def test_invalid_doc_type_rejected_with_422(client, manager, bad_type):
    """Неканонический doc_type → 422, а не тихая подмена в «Накладная».

    Фильтр UI (board.js) сравнивает r.doc_type === 'ТН'|'ТТН'|'УПД'; если
    сервер молча подменяет, фильтр никогда не совпадает и записи выглядят
    пропущенными.
    """
    _, h = manager
    r = client.post("/nakladnye", headers=h, json={
        "doc_series": "АБ", "doc_number": "100", "doc_type": bad_type,
        "supplier_name": "Тест",
    })
    assert r.status_code == 422, f"doc_type={bad_type!r} должен быть отклонён"


def test_invalid_doc_type_rejected_in_bot_endpoint(client, bot_headers):
    """Бот-эндпоинт тоже валидирует doc_type (схема NakladnayaCreate общая)."""
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "МК", "doc_number": "200", "doc_type": "Акт приёма",
        "supplier_name": "Тест",
    })
    assert r.status_code == 422


@pytest.mark.parametrize("good_type", ["ТН", "ТТН", "УПД"])
def test_canon_doc_type_accepted(client, bot_headers, good_type):
    """Канонические значения проходят без ошибок."""
    r = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "МК", "doc_number": "300", "doc_type": good_type,
        "supplier_name": "Тест",
    })
    assert r.status_code == 200, r.text
    assert r.json()["doc_type"] == good_type


# ---------------------------------------------------------------------------
# A4: дубль накладной → 409 + existing_id
# ---------------------------------------------------------------------------

def test_duplicate_returns_409_with_existing_id(client, manager, db):
    """CRM-создание дубля: 409 + тело с existing_id.

    Раньше сервер отвечал 201 (ничего не создав) или 400 без structured body.
    Клиент показывал «Сохранено», а после reload запись исчезала.
    """
    _, h = manager
    payload = {"doc_series": "АБ", "doc_number": "500", "supplier_name": "Тест"}
    r1 = client.post("/nakladnye", headers=h, json=payload)
    assert r1.status_code == 200
    existing_id = r1.json()["id"]

    r2 = client.post("/nakladnye", headers=h, json=payload)
    assert r2.status_code == 409
    body = r2.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), "detail обязан быть dict с existing_id и message"
    assert detail["existing_id"] == existing_id
    assert "message" in detail
    assert str(existing_id) in detail["message"]


def test_bot_duplicate_returns_409_with_existing_id(client, bot_headers, db):
    """Бот-создание дубля: тот же контракт 409 + existing_id."""
    payload = {"doc_series": "МК", "doc_number": "600", "supplier_name": "Тест",
               "doc_type": "ТН"}
    r1 = client.post("/nakladnye/bot/create", headers=bot_headers, json=payload)
    assert r1.status_code == 200
    existing_id = r1.json()["id"]

    r2 = client.post("/nakladnye/bot/create", headers=bot_headers, json=payload)
    assert r2.status_code == 409
    detail = r2.json().get("detail")
    assert isinstance(detail, dict)
    assert detail["existing_id"] == existing_id


def test_duplicate_normalised_key_returns_409(client, bot_headers, db):
    """Дубль по нормализованному ключу (другой регистр серии, пробелы в номере)
    тоже 409 с existing_id.
    """
    r1 = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "АБ", "doc_number": "700", "supplier_name": "Т",
    })
    assert r1.status_code == 200
    existing_id = r1.json()["id"]

    # Другое написание того же ключа
    r2 = client.post("/nakladnye/bot/create", headers=bot_headers, json={
        "doc_series": "аб", "doc_number": "ТТН 700", "supplier_name": "Т",
    })
    assert r2.status_code == 409
    detail = r2.json().get("detail")
    assert isinstance(detail, dict)
    assert detail["existing_id"] == existing_id
