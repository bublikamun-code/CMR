"""Дозакрытие непокрытых веток в малых модулях бэкенда.

Собрано по принципу «одна находка — один тест»: в каждый модуль сюда попали
только те ветки, которых не касался ни один существующий тест. Поодиночке
они давали по 5-15 строк и не стоили отдельного файла, а вместе — почти
полтора десятка контрактов, часть из которых оказалась неочевидной
(фильтр ?q= у поставщиков вообще никем не вызывался, агрегация
/suppliers/{id}/purchases проверялась только по 404).

Что закрыто:

* routers/suppliers_router — поиск ?q= по трём полям, агрегация закупок,
  404 на несуществующий id, предохранитель cap_list (X-Truncated),
  асимметрия каскадов при переименовании, поведение PATCH с пустым именем,
  отсутствие UNIQUE на name/unp;
* secure_files — откат атомарной записи, отсутствующий файл, неизвестный uid,
  дедупликация в audit, symlink (lstat, а не stat);
* health_checks — несуществующий каталог миграций, невалидное имя миграции,
  пустой baseline, недоступный журнал миграций, отсутствующий uploads,
  OSError при создании временного файла, обрыв после успешной проверки БД;
* email_cleaner — ветки clean_email_body (URL, цитаты, подпись, обрезка) и
  link_repl в html_to_text;
* versioning — откат после неудачного коммита, битый data_snapshot;
* services/sqlite_writer — 503 на конкуренции и проброс не-конкуренции;
* tenant_policy — разбор CRM_LEGACY_TENANT_ID и модель без tenant_id;
* db_utils — cap_list с усечением; resolve_tenant_db/resolve_tenant_db_standalone
  не вызываются ни одним модулем (тенант-маршрутизация отключена решением
  владельца 2026-09-03/11, см. комментарий db_utils.py:4-8), поэтому тесты
  под них сознательно не пишутся;
* constants — окружение без tzdata;
* services/nakladnye — битые JSON-поля и пустой номер документа.

Попутно зафиксированы четыре живых дефекта (не чинились, тесты фиксируют
фактическое поведение, чтобы правка была осознанной):

* серверный поиск поставщиков не складывает регистр кириллицы — SQLite
  приводит регистр только для ASCII;
* PATCH /suppliers/{id} с {"name": null} отвечает 500 (NOT NULL), с
  {"name": ""} — 200 и молча отключает синхронизацию снимков;
* удаление поставщика снимает supplier_id и у чек-листов чужого tenant —
  это делает FK-действие БД (ON DELETE SET NULL), а не роутер;
* html_to_text меняет местами текст ссылки и её адрес.

Все проверки локальные: временные каталоги pytest, никакой сети и никаких
subprocess — в отличие от test_secret_file_security.py, здесь subprocess не
требуется, потому что проверяются сами функции, а не их CLI-обёртки.
"""
import importlib
import os
import zoneinfo
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

import constants
import database
import health_checks
import models
import secure_files
import services.nakladnye as nakladnye_service
from conftest import DEFAULT_TEST_TENANT_ID
from db_utils import LIST_HARD_LIMIT, cap_list
from email_cleaner import clean_email_body, html_to_text
from services.sqlite_writer import (
    reserve_model_rows,
    sqlite_write_transaction,
)
from tenant_policy import (
    DEFAULT_LEGACY_TENANT_ID,
    LEGACY_TENANT_ENV,
    TenantPolicyError,
    configured_legacy_tenant_id,
    get_tenant_child,
    tenant_predicate,
)
from versioning import get_versions, save_version


# ---------------------------------------------------------------------------
# Фикстуры данных
# ---------------------------------------------------------------------------

@pytest.fixture
def make_supplier(db):
    """Поставщик напрямую в ORM — справочник большой, а HTTP-Create тут ни при чём."""

    def _factory(name="ООО «Ромашка»", tenant_id=DEFAULT_TEST_TENANT_ID, **kw):
        supplier = models.Supplier(name=name, tenant_id=tenant_id, **kw)
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        return supplier

    return _factory


@pytest.fixture
def raw_client():
    """TestClient, не бросающий исключение наружу.

    Нужен ровно для одного контракта: PATCH с name=null должен вернуть
    500-ответ, а не поднять исключение тест-клиента на живом стеке.
    """
    import main

    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# routers/suppliers_router — фильтр ?q=
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("needle,expected", [
    ("Ромашка", "ООО «Ромашка»"),
    ("770708", "ЗАО «Второй»"),
    ("29-777", "ООО «Телефон»"),
])
def test_supplier_search_covers_name_unp_and_phone(
        client, manager, make_supplier, needle, expected
):
    """Поиск идёт сразу по трём полям.

    Ветка была мёртвой: ни один тест не передавал ?q=, поэтому весь or_
    из list_suppliers не исполнялся ни разу.
    """
    _, h = manager
    make_supplier(name="ООО «Ромашка»", unp="111111111", phone="00-00-00")
    make_supplier(name="ЗАО «Второй»", unp="770708123456", phone="00-00-11")
    make_supplier(name="ООО «Телефон»", unp="222222222", phone="+375 29-777-11-22")
    make_supplier(name="Несвязанный", unp="333333333", phone="00-00-33")

    r = client.get("/suppliers", params={"q": needle}, headers=h)

    assert r.status_code == 200, r.text
    assert [row["name"] for row in r.json()] == [expected]


def test_supplier_search_ignores_case_for_ascii_but_not_for_cyrillic(
        client, manager, make_supplier
):
    """ЖИВОЙ ДЕФЕКТ: ilike на SQLite не складывает кириллицу.

    `ilike` раскладывается в lower(колонка) LIKE lower(шаблон), а SQLite
    приводит регистр только для ASCII. Поэтому «ромашка» в lowercase не
    находит «Ромашка», хотя латиница в том же поиске находится — поведение
    расходится внутри одной строки запроса. Тест фиксирует оба факта, чтобы
    правка (например, на COLLATE NOCASE или русский lower в Python) упала
    здесь и была сделана осознанно.
    """
    _, h = manager
    make_supplier(name="ООО «Ромашка»")
    make_supplier(name="ООО «Romashka»")

    latin = client.get("/suppliers", params={"q": "romashka"}, headers=h).json()
    cyrillic = client.get("/suppliers", params={"q": "ромашка"}, headers=h).json()

    assert [row["name"] for row in latin] == ["ООО «Romashka»"], \
        "ASCII-регистр в этом же поиске складывается"
    assert cyrillic == [], "кириллица не складывается — искать надо с тем же регистром"


def test_supplier_search_is_tenant_scoped(client, manager, two_tenant_users, make_supplier):
    """Чужой поставщик не находится даже по точному совпадению строки."""
    _, h = manager
    make_supplier(name="ООО «Ромашка»")
    make_supplier(name="ООО «Ромашка»", tenant_id=two_tenant_users["second_tenant_id"])

    found = client.get("/suppliers", params={"q": "Ромашка"}, headers=h).json()

    assert len(found) == 1, "поиск обязан оставаться внутри tenant вызывающего"


def test_supplier_search_empty_q_keeps_full_list(client, manager, make_supplier):
    """Пустая строка фильтр не включает (q трактуется как «не задан»)."""
    _, h = manager
    make_supplier(name="Альфа")
    make_supplier(name="Бета")

    r = client.get("/suppliers", params={"q": ""}, headers=h)

    assert [row["name"] for row in r.json()] == ["Альфа", "Бета"]


# ---------------------------------------------------------------------------
# routers/suppliers_router — закупки поставщика
# ---------------------------------------------------------------------------

def test_supplier_purchases_aggregates_checklist_items(
        client, manager, make_card, make_supplier, db
):
    """Главная непокрытая ветка модуля: агрегация закупок целиком.

    До этого эндпоинт проверялся только по 404, поэтому ни суммы, ни
    признак оплаты, ни заголовок сделки не были проверены ни разу.
    """
    _, h = manager
    supplier = make_supplier(name="ООО «Ромашка»")
    other = make_supplier(name="ООО «Чужой»")
    card = make_card(title="Сделка со сборкой", status="Сборка")
    second_card = make_card(title="Сделка на закупке", status="Новый запрос")
    db.add_all([
        models.CardChecklist(
            card_id=card.id, company_name=supplier.name, supplier_id=supplier.id,
            amount=33.333, is_paid=True, note="оплачен полностью",
            invoice_file_path="uploads/invoice.pdf",
        ),
        models.CardChecklist(
            card_id=second_card.id, company_name=supplier.name,
            supplier_id=supplier.id, amount=66.667, is_secondary_check=True,
        ),
        # amount=None — вклад не должен ломать арифметику (float(it.amount or 0))
        models.CardChecklist(
            card_id=card.id, company_name=supplier.name, supplier_id=supplier.id,
            amount=None,
        ),
        # закупка ДРУГОГО поставщика в закупки этого не попадает
        models.CardChecklist(
            card_id=card.id, company_name=other.name, supplier_id=other.id,
            amount=999.0, is_paid=True,
        ),
    ])
    db.commit()

    r = client.get(f"/suppliers/{supplier.id}/purchases", headers=h)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supplier_id"] == supplier.id
    assert body["supplier_name"] == "ООО «Ромашка»"
    assert body["count"] == 3
    assert body["total_amount"] == 100.0
    assert body["paid_amount"] == 33.33
    assert body["unpaid_amount"] == 66.67

    # порядок — от свежих пунктов к старым (id desc)
    items = body["items"]
    assert [row["id"] for row in items] == sorted(
        (row["id"] for row in items), reverse=True
    )
    paid_item = next(row for row in items if row["is_paid"])
    assert paid_item["amount"] == 33.33
    assert paid_item["has_invoice"] is True
    assert paid_item["note"] == "оплачен полностью"
    assert paid_item["card_id"] == card.id
    assert paid_item["card_title"] == "Сделка со сборкой"
    assert paid_item["card_status"] == "Сборка"
    assert paid_item["is_secondary_check"] is False
    assert paid_item["created_at"] is not None

    secondary = next(row for row in items if row["is_secondary_check"])
    assert secondary["is_paid"] is False
    assert secondary["has_invoice"] is False
    assert secondary["note"] == "", "note=None обязан отдаваться пустой строкой"
    assert secondary["card_title"] == "Сделка на закупке"

    empty = [row for row in items if row["amount"] == 0.0]
    assert len(empty) == 1, "пункт без суммы учитывается как ноль"


def test_supplier_purchases_without_items_returns_zeroes(
        client, manager, make_supplier
):
    _, h = manager
    supplier = make_supplier(name="ООО «Пусто»")

    body = client.get(f"/suppliers/{supplier.id}/purchases", headers=h).json()

    assert body["items"] == []
    assert body["count"] == 0
    assert body["total_amount"] == 0.0
    assert body["paid_amount"] == 0.0
    assert body["unpaid_amount"] == 0.0


@pytest.mark.parametrize("method,path,body,headers_fixture", [
    ("get", "/suppliers/{sid}", None, "manager"),
    ("patch", "/suppliers/{sid}", {"name": "Неважно"}, "manager"),
    ("delete", "/suppliers/{sid}", None, "admin"),
    ("get", "/suppliers/{sid}/purchases", None, "manager"),
])
def test_supplier_routes_404_for_unknown_id(
        client, manager, admin, method, path, body, headers_fixture
):
    """Отсутствующий id обязан давать тот же 404, что и чужой tenant.

    В test_tenant_isolation.py закрыт только второй случай (объект есть,
    но принадлежит другому tenant'у) — с несуществующим id эти строки
    роутов не исполнялись вовсе.
    """
    headers = (admin if headers_fixture == "admin" else manager)[1]
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body

    r = client.request(method, path.format(sid=999999), **kwargs)

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Поставщик не найден"


# ---------------------------------------------------------------------------
# routers/suppliers_router — предохранитель cap_list
# ---------------------------------------------------------------------------

def test_supplier_list_without_truncation_has_no_page_headers(
        client, manager, make_supplier
):
    """На коротком списке заголовков усечения быть не должно."""
    _, h = manager
    make_supplier(name="Альфа")
    make_supplier(name="Бета")

    r = client.get("/suppliers", headers=h)

    assert r.status_code == 200
    assert len(r.json()) == 2
    assert "x-total-count" not in r.headers
    assert "x-truncated" not in r.headers


def test_supplier_list_truncates_over_hard_limit_with_headers(
        client, manager, db
):
    """Н11: список длиннее LIST_HARD_LIMIT обрезается и помечается.

    Строки заводятся одной пачкой напрямую в ORM: интересует контракт
    заголовков, а не 5001 HTTP-запроса.
    """
    _, h = manager
    total = LIST_HARD_LIMIT + 1
    db.bulk_save_objects([
        models.Supplier(name=f"Поставщик {i:05d}", tenant_id=DEFAULT_TEST_TENANT_ID)
        for i in range(total)
    ])
    db.commit()

    r = client.get("/suppliers", headers=h)

    assert r.status_code == 200, r.text
    assert len(r.json()) == LIST_HARD_LIMIT
    assert r.headers["x-total-count"] == str(total)
    assert r.headers["x-truncated"] == "true"


# ---------------------------------------------------------------------------
# routers/suppliers_router — асимметрия каскадов при переименовании
# ---------------------------------------------------------------------------

def test_rename_supplier_with_empty_old_name_keeps_checklist_snapshots(
        client, manager, make_card, make_supplier, db
):
    """Правила для двух таблиц РАЗНЫЕ, и это проверяется отдельно.

    nakladnye.supplier_name перезаписывается всегда, а вот снимок в
    card_checklists.company_name — только если он равнялся старому имени.
    Старого имени нет (поставщик заведён пустым), значит сравнивать не с чем
    и чек-листы обязаны остаться нетронутыми — иначе переименование
    уничтожило бы вписанные пользователем названия компаний закупки.
    """
    _, h = manager
    supplier = make_supplier(name="")
    card = make_card(title="Сделка")
    db.add_all([
        models.CardChecklist(
            card_id=card.id, company_name="Своё название закупки",
            supplier_id=supplier.id,
        ),
        models.Nakladnaya(
            supplier_id=supplier.id, supplier_name="", doc_number="1",
        ),
    ])
    db.commit()

    r = client.patch(
        f"/suppliers/{supplier.id}", headers=h, json={"name": "ООО «Ромашка»"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["name"] == "ООО «Ромашка»"
    db.expire_all()
    assert db.query(models.Nakladnaya).one().supplier_name == "ООО «Ромашка»", \
        "накладная — снимок из справочника, она обновляется всегда"
    assert db.query(models.CardChecklist).one().company_name == "Своё название закупки", \
        "у чек-листа нет старого имени для сравнения — трогать его нельзя"


def test_patch_supplier_with_empty_name_turns_off_snapshot_sync(
        client, manager, make_card, make_supplier, db
):
    """ФАКТИЧЕСКОЕ ПОВЕДЕНИЕ (не «правильное»): пустое имя молча отключает
    синхронизацию снимков, а запрос всё равно отвечает 200.

    Роутер не различает «пользователь стёр имя» и «имя не задано»: условие
    синхронизации — `if new_name and ...`, поэтому переименование в пустоту
    не трогает ни накладные, ни чек-листы. Тест фиксирует это как контракт,
    чтобы будущий рефакторинг увидел, что здесь было решение, а не случайность.
    """
    _, h = manager
    supplier = make_supplier(name="Старое имя")
    card = make_card(title="Сделка")
    db.add_all([
        models.CardChecklist(
            card_id=card.id, company_name="Старое имя", supplier_id=supplier.id,
        ),
        models.Nakladnaya(
            supplier_id=supplier.id, supplier_name="Старое имя", doc_number="1",
        ),
    ])
    db.commit()

    r = client.patch(f"/suppliers/{supplier.id}", headers=h, json={"name": ""})

    assert r.status_code == 200, r.text
    assert r.json()["name"] == ""
    db.expire_all()
    assert db.query(models.Nakladnaya).one().supplier_name == "Старое имя"
    assert db.query(models.CardChecklist).one().company_name == "Старое имя"


def _suppliers_name_is_not_null():
    """Схема боевой базы и профиль create_all расходятся по suppliers.name."""
    with database.engine.connect() as conn:
        columns = conn.execute(text("PRAGMA table_info(suppliers)")).fetchall()
    return any(row[1] == "name" and row[3] for row in columns)


def test_patch_supplier_name_null_ends_in_500(
        raw_client, manager, make_supplier, db
):
    """ФАКТИЧЕСКОЕ ПОВЕДЕНИЕ: name=null не проходит ни на одной схеме.

    Причины расходятся, а ответ одинаковый — 500 без валидации и без 422:
    * прод-DDL (tests/fixtures/prod_schema.sql) объявляет suppliers.name
      NOT NULL, поэтому обрыв случается на записи, а строка не меняется;
    * профиль CRM_TEST_SCHEMA=models строит колонку пустой, запись проходит,
      но response_model (SupplierResponse.name: str) отвергает ответ.

    Проверка состояния строки поэтому зависит от профиля, а код ответа —
    нет: именно его и обещает тест.
    """
    _, h = manager
    supplier = make_supplier(name="Старое имя")
    supplier_id = supplier.id

    r = raw_client.patch(f"/suppliers/{supplier_id}", headers=h, json={"name": None})

    assert r.status_code == 500, r.text
    db.expire_all()
    stored = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).one().name
    if _suppliers_name_is_not_null():
        assert stored == "Старое имя", "обнуление не должно было записаться"
    else:
        assert stored is None, "на пустой колонке значение всё-таки обнулилось"


def test_duplicate_supplier_names_and_unp_are_allowed(
        client, manager, db
):
    """КОНТРАКТ СХЕМЫ: уникальности на name/unp в справочнике нет.

    Прод-DDL (tests/fixtures/prod_schema.sql) объявляет suppliers без
    UNIQUE, поэтому дубли создаются молча и оба видны в списке. Если
    будущий рефакторинг добавит индекс, этот тест упадёт — и это ровно
    тот момент, когда нужно решать, совместимо ли это с данными боевой БД.
    """
    _, h = manager
    body = {"name": "ООО «Ромашка»", "unp": "770708123456", "phone": "00-00"}

    first = client.post("/suppliers", headers=h, json=body)
    second = client.post("/suppliers", headers=h, json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] != second.json()["id"]
    assert len(client.get("/suppliers", headers=h).json()) == 2

    with database.engine.connect() as conn:
        indexes = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'suppliers'"
        )).fetchall()
    assert not any("UNIQUE" in (row[0] or "").upper() for row in indexes), \
        "UNIQUE на suppliers появился — контракт дубликатов изменился"


# ---------------------------------------------------------------------------
# routers/suppliers_router — границы tenant на каскадах
# ---------------------------------------------------------------------------

def test_rename_supplier_does_not_touch_other_tenant_checklist(
        client, manager, two_tenant_users, make_card, make_supplier, db
):
    """Чек-лист на сделке чужого tenant переименование не трогает.

    _tenant_card_ids отсекает сделки вне tenant вызывающего, но для этого
    нужно, чтобы поставщик и сделка жили в РАЗНЫХ tenant'ах: связь
    card_checklists идёт через сделку, а не через поставщика, поэтому
    tenant_id у пункта отсутствует и защита держится на карточке.
    """
    _, h = manager
    supplier = make_supplier(name="Старое имя")
    foreign_card = make_card(
        title="Сделка чужого tenant",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add(models.CardChecklist(
        card_id=foreign_card.id, company_name="Старое имя",
        supplier_id=supplier.id,
    ))
    db.commit()

    r = client.patch(
        f"/suppliers/{supplier.id}", headers=h, json={"name": "Новое имя"}
    )

    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(models.CardChecklist).one().company_name == "Старое имя", \
        "снимок на сделке чужого tenant не должен переписываться"


def test_delete_supplier_unlinks_checklist_of_other_tenant_by_fk_action(
        client, admin, two_tenant_users, make_card, make_supplier, db
):
    """ФАКТИЧЕСКОЕ ПОВЕДЕНИЕ: при удалении обнуляются ВСЕ ссылки, включая чужие.

    Роутер фильтрует UPDATE по _tenant_card_ids, но поверх этого срабатывает
    `ON DELETE SET NULL` на card_checklists.supplier_id (models.py:192), а он
    действует на уровне БД и tenant не смотрит вовсе. То есть tenant-фильтр
    в delete_supplier для чек-листов избыточен, а обещание «чужой tenant не
    задеть» тут не выполняется: пункт закупки чужой компании теряет привязку
    к справочнику. Тест фиксирует факт — исправление (снять SET NULL и
    разбирать ссылки вручную по tenant) обязано упасть здесь.
    """
    _, h = admin
    supplier = make_supplier(name="Поставщик")
    supplier_id = supplier.id
    own_card = make_card(title="Своя сделка")
    foreign_card = make_card(
        title="Сделка чужого tenant",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add_all([
        models.CardChecklist(
            card_id=own_card.id, company_name="Поставщик", supplier_id=supplier_id,
        ),
        models.CardChecklist(
            card_id=foreign_card.id, company_name="Поставщик",
            supplier_id=supplier_id,
        ),
    ])
    db.commit()

    r = client.delete(f"/suppliers/{supplier_id}", headers=h)

    assert r.status_code == 200, r.text
    db.expire_all()
    items = {row.card_id: row for row in db.query(models.CardChecklist).all()}
    assert items[own_card.id].supplier_id is None
    assert items[foreign_card.id].supplier_id is None, \
        "ссылку снимает FK-действие БД: tenant-фильтр роутера его не ограничивает"


def test_supplier_purchases_skips_other_tenant_checklist_items(
        client, manager, two_tenant_users, make_card, make_supplier, db
):
    """Закупки показывают только пункты из своего tenant."""
    _, h = manager
    supplier = make_supplier(name="Поставщик")
    own_card = make_card(title="Своя сделка")
    foreign_card = make_card(
        title="Сделка чужого tenant",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add_all([
        models.CardChecklist(
            card_id=own_card.id, company_name="Поставщик",
            supplier_id=supplier.id, amount=10.0,
        ),
        models.CardChecklist(
            card_id=foreign_card.id, company_name="Поставщик",
            supplier_id=supplier.id, amount=700.0,
        ),
    ])
    db.commit()

    body = client.get(f"/suppliers/{supplier.id}/purchases", headers=h).json()

    assert body["count"] == 1
    assert body["total_amount"] == 10.0
    assert [row["card_id"] for row in body["items"]] == [own_card.id]


# ---------------------------------------------------------------------------
# secure_files — откат атомарной записи
# ---------------------------------------------------------------------------

def _fail_with(error_class, message="сбой"):
    def _raiser(*args, **kwargs):
        raise error_class(message)

    return _raiser


def test_atomic_write_private_removes_temp_file_when_replace_fails(
        tmp_path, monkeypatch
):
    """Сорванный os.replace не оставляет временного файла с секретом.

    Временник создаётся в каталоге цели и виден всем, кто умеет читать
    каталог, поэтому убирать его — часть контракта, а не гигиена.
    """
    target = tmp_path / "secret.json"
    monkeypatch.setattr(secure_files.os, "replace", _fail_with(OSError))

    with pytest.raises(OSError):
        secure_files.atomic_write_private(target, "секрет")

    assert list(tmp_path.glob(".secret.json.*.tmp")) == [], "временный файл не убран"
    assert not target.exists(), "битая запись не должна была заменить цель"


def test_atomic_write_private_closes_descriptor_when_setup_fails(
        tmp_path, monkeypatch
):
    """Ошибка до открытия потока (fchmod) закрывает дескриптор и чистит файл."""
    target = tmp_path / "secret.json"
    monkeypatch.setattr(secure_files.os, "fchmod", _fail_with(OSError))

    with pytest.raises(OSError):
        secure_files.atomic_write_private(target, "секрет")

    assert list(tmp_path.glob(".secret.json.*.tmp")) == []


def test_atomic_write_private_tolerates_already_removed_temp_file(
        tmp_path, monkeypatch
):
    """Если временника уже нет — это не вторая ошибка, а успешная уборка.

    Ветка важна для гонки: между сбоем записи и очисткой временный файл
    могли убрать снаружи (уборщик, рестарт), и падать на этом нельзя —
    наружу должен уйти исходный сбой записи, а не FileNotFoundError.
    """
    target = tmp_path / "secret.json"
    monkeypatch.setattr(
        secure_files.os, "replace", _fail_with(OSError, "replace невозможен")
    )
    monkeypatch.setattr(secure_files.os, "unlink", _fail_with(FileNotFoundError))

    with pytest.raises(OSError) as raised:
        secure_files.atomic_write_private(target, "секрет")

    assert "replace невозможен" in str(raised.value)


def test_atomic_write_private_accepts_bytes_without_broadening_mode(tmp_path):
    """Бинарный секрет пишется так же приватно, как текстовый."""
    target = tmp_path / "key.bin"

    secure_files.atomic_write_private(target, b"\x00\x01\x02")

    assert target.read_bytes() == b"\x00\x01\x02"
    assert target.stat().st_mode & 0o777 == secure_files.PRIVATE_FILE_MODE


# ---------------------------------------------------------------------------
# secure_files — инспекция файлов
# ---------------------------------------------------------------------------

def test_inspect_private_file_reports_missing_file_as_safe(tmp_path):
    """Отсутствующий опциональный файл безопасен — preflight не должен ругаться."""
    missing = tmp_path / "нет-такого"

    metadata = secure_files.inspect_private_file(missing)

    assert metadata == {
        "path": str(missing),
        "exists": False,
        "type": "missing",
        "mode": None,
        "uid": None,
        "owner": None,
        "secure": True,
    }


def test_inspect_private_file_does_not_follow_symlink(tmp_path):
    """lstat, а не stat: ссылка не должна выглядеть безопасным файлом.

    Иначе подмена секрета ссылкой прошла бы проверку прав, хотя права
    принадлежат цели, а не тому, что реально лежит по этому пути.
    """
    real = tmp_path / "secret"
    real.write_text("секрет", encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(real)

    through_link = secure_files.inspect_private_file(link)

    assert through_link["type"] == "special"
    assert through_link["secure"] is False
    assert secure_files.inspect_private_file(link.resolve())["secure"] is True


def test_inspect_private_file_keeps_path_as_given(tmp_path):
    """Путь не канонизируется: в отчёте видно ровно то, что проверяли.

    `..` в аргументе допустим (итог всё равно определяет lstat), но
    нормализация строки изменила бы отчёт — и в него попал бы путь,
    которого никто не проверял.
    """
    secret = tmp_path / "secret"
    secret.write_text("секрет", encoding="utf-8")
    secret.chmod(0o600)
    asked = tmp_path / "uploads" / ".." / "secret"

    metadata = secure_files.inspect_private_file(asked)

    assert metadata["path"] == str(asked)
    assert metadata["secure"] is True


def test_inspect_private_file_falls_back_to_numeric_owner(tmp_path, monkeypatch):
    """uid без записи в /etc/passwd отдаётся числом, а не роняет проверку.

    Контейнеры и внешние тома монтируют файлы на uid, которых в системной
    базе пользователей нет; preflight обязан такой файл всё равно описать.
    """
    secret = tmp_path / "secret"
    secret.write_text("секрет", encoding="utf-8")
    secret.chmod(0o600)

    def _unknown_user(uid):
        raise KeyError(uid)

    monkeypatch.setattr(secure_files.pwd, "getpwuid", _unknown_user)

    metadata = secure_files.inspect_private_file(secret)

    assert metadata["owner"] == str(os.geteuid())
    assert metadata["uid"] == os.geteuid()


def test_audit_private_files_skips_repeated_paths(tmp_path):
    """Один и тот же путь проверяется один раз, сколько бы его ни передали.

    Сравнение строковое, без канонизации: это осознанно (в отчёте видно
    ровно то, что просили проверить), поэтому один и тот же файл, названный
    по-разному, попадёт в отчёт дважды.
    """
    secret = tmp_path / "secret"
    secret.write_text("секрет", encoding="utf-8")
    secret.chmod(0o600)

    findings = secure_files.audit_private_files([secret, str(secret), secret])

    assert len(findings) == 1
    assert findings[0]["level"] == "OK"


# ---------------------------------------------------------------------------
# health_checks — каталог миграций и baseline
# ---------------------------------------------------------------------------

def test_discover_expected_migrations_empty_for_missing_directory(tmp_path):
    """Каталога миграций нет — ожидаемых имён ноль, а не исключение."""
    assert health_checks.discover_expected_migrations(
        tmp_path / "нет-каталога", environ={}
    ) == []


def test_discover_expected_migrations_rejects_non_ascii_name(tmp_path):
    """Имя миграции обязано совпадать с MIGRATION_NAME_RE.

    Критерий строгий намеренно: файл с кириллицей в имени не попал бы в
    baseline, и миграция молча осталась бы неприменённой.
    """
    with pytest.raises(ValueError, match="invalid migration name"):
        health_checks.discover_expected_migrations(
            tmp_path, {"CRM_REQUIRED_MIGRATIONS": "0001_переименование.py"}
        )


def test_readiness_fails_on_empty_migration_baseline(tmp_path):
    """Пустой baseline = миграции не подтверждены, даже если схема в порядке."""
    report = health_checks.readiness_report(
        database.engine, upload_path=tmp_path, expected_migrations=[]
    )

    assert report["ready"] is False
    assert report["checks"]["database"] == {"ok": True}
    assert report["checks"]["schema"] == {"ok": True}
    assert report["checks"]["migrations"] == {
        "ok": False,
        "detail": "Обязательные миграции основной базы не подтверждены",
    }


def test_readiness_fails_on_invalid_migration_name_in_baseline(tmp_path):
    """Некорректный baseline роняет проверку конфигурации, а не приложение.

    Исключение ValueError из discover_expected_migrations гасится, и в
    отчёт уходит ровно то же, что и при пустом baseline, — без traceback
    и без имён файлов.
    """
    report = health_checks.readiness_report(
        database.engine,
        upload_path=tmp_path,
        expected_migrations=["0001_не миграция.py"],
    )

    assert report["ready"] is False
    assert report["checks"]["schema"] == {"ok": True}
    assert report["checks"]["migrations"]["ok"] is False
    assert "не миграция" not in str(report)


def test_check_migrations_requires_non_empty_baseline():
    """Проверка журнала без ожидаемых имён обязана отвечать False.

    Иначе приходит False в обход проверки конфигурации, и пустой baseline
    выглядел бы как «всё применено».
    """
    with database.engine.connect() as connection:
        assert health_checks._check_migrations(connection, []) is False


def test_check_migrations_false_without_journal():
    """Без таблицы schema_migrations проверка обязана вернуть False, а не упасть."""
    isolated = create_engine("sqlite:///:memory:")
    with isolated.begin() as connection:
        connection.execute(text("CREATE TABLE cards (id INTEGER PRIMARY KEY)"))

    with isolated.connect() as connection:
        assert health_checks._check_migrations(connection, ["0001_a.py"]) is False

    isolated.dispose()


# ---------------------------------------------------------------------------
# health_checks — каталог загрузок
# ---------------------------------------------------------------------------

def test_readiness_reports_missing_uploads_directory(tmp_path):
    """Каталога загрузок нет — это отказ, а не «нечего проверять»."""
    report = health_checks.readiness_report(
        database.engine, upload_path=tmp_path / "нет-каталога"
    )

    assert report["ready"] is False
    assert report["checks"]["uploads"] == {
        "ok": False,
        "detail": "Каталог загрузок недоступен для записи",
    }
    assert report["checks"]["database"] == {"ok": True}, \
        "отказ по uploads не должен ронять остальные проверки"


def test_readiness_reports_uploads_temp_file_error(tmp_path, monkeypatch):
    """Read-only файловая система: mkstemp даёт OSError — каталог негоден."""
    def _read_only(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(health_checks.tempfile, "mkstemp", _read_only)

    report = health_checks.readiness_report(database.engine, upload_path=tmp_path)

    assert report["checks"]["uploads"]["ok"] is False
    assert report["checks"]["database"] == {"ok": True}
    assert report["checks"]["schema"] == {"ok": True}
    assert str(tmp_path) not in str(report)


def test_readiness_degrades_when_schema_check_breaks(tmp_path, monkeypatch):
    """Обрыв ПОСЛЕ успешной проверки БД не должен ронять весь отчёт.

    Схема проверяется вторым соединением: если оно падает, неготовность
    фиксируется по schema и migrations, а database остаётся зелёным —
    иначе клиент не отличит «БД недоступна» от «проверка сломалась».
    """
    def _broken_schema(connection):
        raise RuntimeError("соединение оборвалось")

    monkeypatch.setattr(health_checks, "_check_schema", _broken_schema)

    report = health_checks.readiness_report(database.engine, upload_path=tmp_path)

    assert report["ready"] is False
    assert report["checks"]["database"] == {"ok": True}
    assert report["checks"]["schema"] == {
        "ok": False,
        "detail": "Проверка обязательной схемы не пройдена",
    }
    assert report["checks"]["migrations"] == {
        "ok": False,
        "detail": "Обязательные миграции основной базы не подтверждены",
    }
    assert "оборвалось" not in str(report), "сырое исключение наружу не отдаётся"


# ---------------------------------------------------------------------------
# email_cleaner — тело письма
# ---------------------------------------------------------------------------

def test_clean_email_body_returns_empty_for_blank_input():
    """Пустое письмо даёт пустую строку, а не «Текст письма пуст»."""
    assert clean_email_body("") == ""
    assert clean_email_body(None) == ""


def test_clean_email_body_keeps_short_url_and_shortens_tracking_one():
    """Ссылки до 90 символов остаются как есть, трекинговые схлопываются."""
    short = "https://example.com/order/12"
    long_url = "https://tracker.example.com/click?campaign=autumn&id=" + "x" * 120

    assert clean_email_body(f"Ссылка {short}") == f"Ссылка {short}"

    cleaned = clean_email_body(f"Смотри {long_url}")
    assert cleaned == "Смотри [ссылка: tracker.example.com]"
    assert long_url not in cleaned


def test_clean_email_body_cuts_quote_history_and_marks_it():
    """История переписки отрезается и помечается явной подписью."""
    body = (
        "Это достаточно длинное вступление к письму, оно заведомо длиннее\n"
        "сорока символов, поэтому начало переписки находится не с первой\n"
        "строки и подлежит отрезанию.\n"
        "\n"
        "> ответ из прошлого письма\n"
        "> вторая строка ответа\n"
    )

    cleaned = clean_email_body(body)

    assert "вступление" in cleaned
    assert "ответ из прошлого" not in cleaned
    assert cleaned.endswith("— (история переписки скрыта)")


def test_clean_email_body_keeps_letter_that_is_quote_from_first_line():
    """Письмо, целиком состоящее из цитаты, не режется — но помечается.

    Иначе ответ на «цитата целиком» молча превратился бы в пустую карточку.
    """
    body = "> только цитата\n> вторая строка цитаты"

    cleaned = clean_email_body(body)

    assert "только цитата" in cleaned
    assert "вторая строка цитаты" in cleaned
    assert cleaned.endswith("— (история переписки скрыта)")


def test_clean_email_body_cuts_signature_far_from_top():
    """Подпись отрезается, только если она не в самом начале письма."""
    body = (
        "Текст письма достаточно длинный, поэтому подпись находится "
        "заведомо ниже тридцати символов от начала.\n\nС уважением,\nИван"
    )

    cleaned = clean_email_body(body)

    assert "Текст письма" in cleaned
    assert "С уважением" not in cleaned
    assert "Иван" not in cleaned


def test_clean_email_body_truncates_over_hard_limit():
    """Жёсткий предел 4000 символов помечается явным многоточием."""
    cleaned = clean_email_body("а" * 5000)

    assert len(cleaned) < 5000
    assert cleaned.endswith("[...текст письма обрезан]")


def test_clean_email_body_collapses_more_than_two_blank_lines():
    """Серии пустых строк схлопываются до двух, хвостовые убираются."""
    cleaned = clean_email_body("первая\n\n\n\n\nвторая\n\n\n")

    assert cleaned == "первая\n\n\nвторая"


# ---------------------------------------------------------------------------
# email_cleaner — ссылки в HTML
# ---------------------------------------------------------------------------

def test_html_to_text_keeps_link_that_is_its_own_text():
    """Ссылка, у которой текст совпадает с адресом, схлопывается в адрес."""
    assert html_to_text(
        '<a href="https://example.com/a">https://example.com/a</a>'
    ) == "https://example.com/a"


def test_html_to_text_swaps_link_text_and_url():
    """ЖИВОЙ ДЕФЕКТ: текст ссылки и её адрес меняются местами.

    В регулярке группа 1 — это href, группа 2 — текст внутри <a>, а
    link_repl читает их наоборот, поэтому «<a href=URL>Заказ</a>»
    превращается в «URL (Заказ)». Для писем с сайта это читается как
    испорченный текст письма. Тест фиксирует фактический вывод: починка
    порядка групп уронит его здесь, и это будет сделано осознанно.
    """
    assert html_to_text(
        '<a href="https://example.com/a">Заказ</a>'
    ) == "https://example.com/a (Заказ)"


def test_html_to_text_link_without_own_text_appends_empty_placeholder():
    """Хвост «()» у ссылки без своего текста — следствие того же дефекта."""
    assert html_to_text('<a href="https://example.com/a">   </a>') == \
        "https://example.com/a ()"


# ---------------------------------------------------------------------------
# versioning — граница транзакции
# ---------------------------------------------------------------------------

class _CommitFailsSession:
    """Прокси сессии, у которой ломается только commit().

    Нужен, чтобы дойти до ветки отката после неудачного коммита: настоящая
    сессия на живой SQLite коммит отклоняет крайне неохотно.
    """

    def __init__(self, session):
        self._session = session
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    def commit(self):
        raise RuntimeError("диск отвалился")

    def rollback(self):
        self.rollbacks += 1
        return self._session.rollback()


def test_save_version_rolls_back_and_returns_none_when_commit_fails(db):
    """Неудачный коммит откатывает всё и возвращает None.

    Это единственное место, где откат транзакции ВЫЗЫВАЮЩЕГО обязателен:
    после ошибки коммита сессия непригодна. Откат затрагивает и изменения
    вызывающей стороны — так и описано в docstring versioning.py.
    """
    db.add(models.Client(name="Правка вызывающего", tenant_id=DEFAULT_TEST_TENANT_ID))
    db.flush()
    proxy = _CommitFailsSession(db)

    assert save_version(
        proxy, "clients", 555, {"name": "X"}, tenant_id=DEFAULT_TEST_TENANT_ID
    ) is None
    assert proxy.rollbacks == 1, "после неудачного коммита нужен откат"

    with database.engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM record_versions WHERE record_id = 555"
        )).scalar_one() == 0, "незакоммиченная версия не должна была уехать в БД"

    # сессия вызывающего осталась пригодной для следующих операций
    assert db.query(models.Client).filter(
        models.Client.name == "Правка вызывающего").count() == 0, \
        "откат коммита затрагивает и незавершённые изменения вызывающего"
    db.add(models.Client(name="После отката", tenant_id=DEFAULT_TEST_TENANT_ID))
    db.commit()
    assert db.query(models.Client).filter(
        models.Client.name == "После отката").count() == 1


def test_save_version_without_commit_still_commits_on_clean_session(db):
    """ФАКТИЧЕСКОЕ ПОВЕДЕНИЕ: SAVEPOINT на чистой сессии фиксирует версию.

    SQLite коммитит транзакцию при RELEASE самого внешнего savepoint, а у
    сессии без незавершённых изменений pysqlite не открывает BEGIN — то
    есть savepoint оказывается внешним. Из-за этого save_version(commit=False)
    у вызывающего, которому нечего было фиксировать, версию всё равно
    коммитит, вопреки границе, описанной в docstring модуля. На текущих
    пяти вызовах это безвредно (они всегда идут серединой транзакции
    вызывающего, и тогда savepoint вложенный), но полагаться на границу
    при вызове с чистой сессией нельзя.
    """
    assert save_version(
        db, "clients", 556, {"name": "X"}, tenant_id=DEFAULT_TEST_TENANT_ID,
        commit=False,
    ) == 1

    with database.engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM record_versions WHERE record_id = 556"
        )).scalar_one() == 1, "версия зафиксировалась, хотя commit=False"


def test_get_versions_survives_broken_data_snapshot(db):
    """Битый JSON снимка не роняет чтение истории — отдаётся пустой словарь.

    Снимок пишется версионно и переживает правки схемы, поэтому в боевой
    базе он может оказаться нечитаемым; тогда карточка обязана открываться.
    """
    db.add(models.RecordVersion(
        table_name="cards", record_id=7, version=1,
        data_snapshot="{это не json", change_type="update",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    ))
    db.commit()

    history = get_versions(db, "cards", 7)

    assert len(history) == 1
    assert history[0]["data"] == {}
    assert history[0]["version"] == 1
    assert history[0]["change_type"] == "update"


# ---------------------------------------------------------------------------
# services/sqlite_writer — конкуренция SQLite
# ---------------------------------------------------------------------------

class _StubSession:
    """Сессия, у которой умеют работать только rollback и query."""

    def __init__(self, error=None):
        self.rollbacks = 0
        self._error = error

    def rollback(self):
        self.rollbacks += 1

    def query(self, model):
        return _StubQuery(self._error)


class _StubQuery:
    """Запрос, который умеет только filter/update и всегда пуст."""

    def __init__(self, error):
        self._error = error

    def filter(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        raise self._error

    def first(self):
        return None


def _locked_error():
    return OperationalError(
        "UPDATE cards SET id=id", {}, RuntimeError("database is locked")
    )


def test_write_transaction_turns_contention_into_503():
    """Ожидаемая конкуренция SQLite — управляемый 503 с Retry-After.

    Клиент должен уметь повторить операцию сам; для этого ответ обязан
    сказать «повторите», а не упасть в 500.
    """
    session = _StubSession()

    with pytest.raises(HTTPException) as raised:
        with sqlite_write_transaction(session):
            raise _locked_error()

    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "1"}
    assert session.rollbacks == 1, "незавершённая операция обязана быть откатана"


def test_write_transaction_reraises_other_operational_errors():
    """Конкуренция — единственное, что превращается в 503; остальное не прячем.

    Иначе «нет такой таблицы» или «нет такого столбца» доехали бы до
    клиента как «база занята, повторите» и маскировали реальную ошибку.
    """
    session = _StubSession()
    broken = OperationalError(
        "UPDATE cards SET id=id", {}, RuntimeError("no such column: cards.id")
    )

    with pytest.raises(OperationalError) as raised:
        with sqlite_write_transaction(session):
            raise broken

    assert "no such column" in str(raised.value)
    assert session.rollbacks == 1


def test_reserve_model_rows_reraises_non_contention_operational_error():
    """Та же асимметрия в резервировании строк: не-конкуренция пробрасывается.

    404 по ненайденной строке здесь неуместен — запрос был валидным, и
    клиенту нужна исходная ошибка, а не «сделка не найдена».
    """
    broken = OperationalError(
        "UPDATE cards SET id=id", {}, RuntimeError("no such table: cards")
    )
    session = _StubSession(error=broken)

    with pytest.raises(OperationalError) as raised:
        reserve_model_rows(
            session, SimpleNamespace(id="id"), [1],
            not_found_detail="Сделка не найдена",
        )

    assert "no such table" in str(raised.value)
    # два отката: сброс сессии перед резервированием (reset_session=True)
    # и откат незавершённой операции на самой ошибке
    assert session.rollbacks == 2


# ---------------------------------------------------------------------------
# tenant_policy — конфигурация и предикаты
# ---------------------------------------------------------------------------

def test_configured_legacy_tenant_id_reads_positive_int(monkeypatch):
    monkeypatch.setenv(LEGACY_TENANT_ENV, " 7 ")

    assert configured_legacy_tenant_id() == 7


def test_configured_legacy_tenant_id_defaults_to_none(monkeypatch):
    """Постоянного production-id в коде нет: без переменной — None.

    Молчаливая подстановка «tenant по умолчанию» означала бы запись данных
    не в ту компанию, поэтому дефолт обязан быть отсутствием значения.
    """
    monkeypatch.delenv(LEGACY_TENANT_ENV, raising=False)
    assert configured_legacy_tenant_id() is None

    monkeypatch.setenv(LEGACY_TENANT_ENV, "   ")
    assert configured_legacy_tenant_id() is None
    assert DEFAULT_LEGACY_TENANT_ID is None


@pytest.mark.parametrize("raw", ["не число", "0", "-3", "1.5"])
def test_configured_legacy_tenant_id_rejects_bad_values(monkeypatch, raw):
    """Диагностическая переменная проверяется, а не приводится силой.

    Любое нецелое и неположительное значение — ошибка конфигурации, а не
    «используем как есть»: молчаливый 0 или -1 уехал бы в tenant-запросы
    и записал бы данные не туда.
    """
    monkeypatch.setenv(LEGACY_TENANT_ENV, raw)

    with pytest.raises(TenantPolicyError, match=LEGACY_TENANT_ENV):
        configured_legacy_tenant_id()


def test_tenant_predicate_rejects_model_without_tenant_id():
    """Строка без tenant_id (card_checklists) — ошибка конфигурации, а не пустой результат.

    Молча вернуть false() значило бы навсегда спрятать чек-листы из любой
    выдачи и не сообщить об этом разработчику.
    """
    user = SimpleNamespace(role="manager", tenant_id=DEFAULT_TEST_TENANT_ID)

    assert not hasattr(models.CardChecklist, "tenant_id")
    with pytest.raises(TenantPolicyError, match="CardChecklist"):
        tenant_predicate(models.CardChecklist, user)


def test_get_tenant_child_404_for_unknown_child(db, manager):
    """Отсутствующий child даёт 404 с его собственным detail, а не родительским."""
    user, _ = manager

    with pytest.raises(HTTPException) as raised:
        get_tenant_child(
            db, models.CardChecklist, 999999, models.Card, "card_id", user,
            detail="Пункт чек-листа не найден", parent_detail="Сделка не найдена",
        )

    assert raised.value.status_code == 404
    assert raised.value.detail == "Пункт чек-листа не найден"


def test_get_tenant_child_hides_other_tenant_parent(db, two_tenant_users):
    """Ребёнок с глобальным id не грузится, если его родитель — чужой."""
    user = two_tenant_users["second_user"]
    card = models.Card(
        title="Сделка первого tenant", status="Новый запрос",
        is_deleted=False, tenant_id=two_tenant_users["first_tenant_id"],
    )
    db.add(card)
    db.commit()
    item = models.CardChecklist(card_id=card.id, company_name="Закупка", amount=1.0)
    db.add(item)
    db.commit()

    with pytest.raises(HTTPException) as raised:
        get_tenant_child(
            db, models.CardChecklist, item.id, models.Card, "card_id", user,
            detail="Пункт не найден", parent_detail="Сделка не найдена",
        )

    assert raised.value.status_code == 404
    assert raised.value.detail == "Сделка не найдена"


# ---------------------------------------------------------------------------
# db_utils — предохранитель списков
# ---------------------------------------------------------------------------

def test_cap_list_truncates_and_marks_headers():
    """Усечение помечается двумя заголовками; клиент читает их одинаково
    и у списка оплат, и здесь."""
    items = list(range(10))
    response = Response()

    result = cap_list(items, response, limit=4)

    assert result == [0, 1, 2, 3]
    assert response.headers["x-total-count"] == "10"
    assert response.headers["x-truncated"] == "true"


def test_cap_list_without_response_still_truncates():
    """Без объекта Response заголовки ставить некуда — но обрезка обязана быть."""
    assert cap_list(list(range(10)), None, limit=4) == [0, 1, 2, 3]


def test_cap_list_keeps_short_lists_untouched():
    assert cap_list([1, 2, 3], Response(), limit=4) == [1, 2, 3]


# ---------------------------------------------------------------------------
# constants — окружение без tzdata
# ---------------------------------------------------------------------------

def test_business_today_falls_back_to_local_date_without_tzdata(monkeypatch):
    """Slim-образ без tzdata обязан работать, а не падать на импорте.

    Модуль импортируется на старте приложения, поэтому ZoneInfoNotFoundError
    без обработки уронил бы процесс целиком ещё до первого запроса.
    """
    def _no_tzdata(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    try:
        monkeypatch.setattr(zoneinfo, "ZoneInfo", _no_tzdata)
        importlib.reload(constants)

        assert constants.BUSINESS_TZ is None
        assert constants.business_today() == datetime.now().date()
    finally:
        monkeypatch.undo()
        importlib.reload(constants)

    assert constants.BUSINESS_TZ is not None
    assert constants.business_today() >= datetime.now().date()


# ---------------------------------------------------------------------------
# services/nakladnye — битые данные и дубль документа
# ---------------------------------------------------------------------------

def test_parse_products_survives_broken_json():
    """Битая строка товаров не должна ронять сериализацию накладной."""
    assert nakladnye_service.parse_products(
        models.Nakladnaya(products_json="{это не json")
    ) == []
    assert nakladnye_service.parse_products(models.Nakladnaya(products_json=None)) == []
    assert nakladnye_service.parse_products(
        models.Nakladnaya(products_json='[{"name": "Гвозди"}]')
    ) == [{"name": "Гвозди"}]


def test_find_by_doc_key_ignores_document_without_digits(db):
    """Номер без цифр не образует ключа — искать нечего.

    Такой документ сознательно выпадает из-под уникального индекса
    (накладные бота «Не распознано» должны приниматься повторно).
    """
    assert nakladnye_service.find_by_doc_key(
        db, "ТТН", "Не распознано", DEFAULT_TEST_TENANT_ID
    ) is None
    assert nakladnye_service.find_by_doc_key(
        db, "ТТН", None, DEFAULT_TEST_TENANT_ID
    ) is None


class _NoDuplicateFoundSession:
    """Коммит падает с IntegrityError, но дубль в базе не находится."""

    def __init__(self):
        self.rollbacks = 0

    def commit(self):
        raise IntegrityError(
            "INSERT INTO nakladnye", {}, RuntimeError("UNIQUE constraint failed")
        )

    def rollback(self):
        self.rollbacks += 1

    def query(self, model):
        return _StubQuery(None)


def test_commit_guard_reraises_integrity_error_without_duplicate():
    """Нарушение не про ключ документа пробрасывается дальше.

    IntegrityError на битом FK — это не «дубль накладной»: объявить его
    дублем значило бы соврать пользователю и увести разбор в сторону.
    """
    session = _NoDuplicateFoundSession()

    with pytest.raises(IntegrityError):
        nakladnye_service.commit_with_doc_key_guard(
            session, "ТТН", "4881030", DEFAULT_TEST_TENANT_ID
        )

    assert session.rollbacks == 1


def test_nak_dict_survives_broken_photo_paths():
    """Битый JSON фотографий и пустые необязательные поля не ломают ответ.

    Роутер отдаёт этот dict напрямую, минуя pydantic-схему, поэтому
    каждое поле нормализуется здесь вручную.
    """
    nakladnaya = models.Nakladnaya(
        photo_paths="{это не json", supplier_name=None, amount=None,
        vat_amount=None, amount_no_vat=None, status=None, is_arrived=None,
        created_by_bot=None,
    )

    body = nakladnye_service.nak_dict(nakladnaya)

    assert body["photo_paths"] == []
    assert body["supplier_name"] == ""
    assert body["amount"] is None
    assert body["vat_amount"] is None
    assert body["amount_no_vat"] is None
    assert body["status"] == "new"
    assert body["is_arrived"] is False
    assert body["created_by_bot"] is False
    assert body["supplier"] is None


def test_nak_dict_parses_photo_paths():
    """Нормальный случай: фотографии читаются списком, поставщик подтягивается."""
    nakladnaya = models.Nakladnaya(
        photo_paths='["uploads/1.jpg"]', supplier_name="ООО «Ромашка»",
        amount=100.0, vat_amount=16.67, status="arrived",
    )

    body = nakladnye_service.nak_dict(nakladnaya)

    assert body["photo_paths"] == ["uploads/1.jpg"]
    assert body["amount"] == 100.0
    assert body["status"] == "arrived"
