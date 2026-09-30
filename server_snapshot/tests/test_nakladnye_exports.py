"""Выгрузки накладных, фильтры списка и автоподстановка поставщика.

Первая из двух частей по `routers/nakladnye_router.py` (вторая — про загрузку
фото и bot-ручки). Какие ветки закрывает этот файл:

1. ``GET /nakladnye/{nak_id}/excel`` — успешный путь до сих пор не выполнялся
   НИ РАЗУ: был только 404 на чужой накладной (test_nakladnye_tenant.py:263).
   Здесь — 200, разобранный xlsx (шапка документа, таблица товаров), пустые
   суммы и намеренный порядок «сначала проверка доступа, потом импорт
   openpyxl» (строка 534).
2. ``GET /nakladnye/export/products-excel`` — не проверены ни фильтр
   ``store``, ни ``date_from``/``date_to``, ни отбрасывание строк вне
   диапазона дат (строка 645-648). Изоляция по tenant уже закрыта в
   test_nakladnye_tenant.py:556 и здесь не дублируется.
3. ``GET /nakladnye`` — фильтры ``store``/``doc_type``/``status``/
   ``supplier_id`` и диапазон дат не проверялись ни разу. Важно: даты
   фильтруются в Python по строковому ``doc_date`` (строка 305-314), до БД
   не доходят, поэтому границы диапазона включительны и сравнение — строковое.
4. Автоподстановка ``supplier_name`` по ``supplier_id`` — не покрыта ни в
   create (строка 330-337), ни в patch (строка 379-385).
5. ``products`` при создании (строка 342) и ``PATCH`` с ``products: []``:
   первый сериализуется, второй очищает ``products_json`` (строка 390), и
   после этого список отдаёт пустой ``products``, а не ошибку.

Часть тестов фиксирует ЗАМЕЧЕННЫЕ ДЕФЕКТЫ (прод-код не чинился, тесты
документируют фактическое поведение и падают, когда дефект починят):
  * имя файла в Content-Disposition собирается из doc_type/doc_series/doc_number
    без учёта кодировки → кириллица роняет ответ (см. тест с doc_type=«ТТН»);
  * несуществующий supplier_id в create → 500 (FK), в patch → 409 «уже принята»;
  * в ответе `supplier` отдаётся поставщик чужого tenant'а, если его id
    заслан в накладную.
"""
import io
import itertools
import json
import sys

import pytest
from openpyxl import load_workbook

import models
import schemas

# Счётчик номеров документов: индекс uq_nakladnye_doc_key уникален внутри
# tenant'а, поэтому в одном тесте номера должны различаться.
_DOC_SEQ = itertools.count(1)


def _nak(db, **kw):
    """Накладная с минимальным набором полей.

    doc_type/doc_series по умолчанию НЕ заданы: имя файла выгрузки строится
    из них, и кириллица в нём ломает заголовок ответа (см. докстринг файла).
    Тестам, которым нужна серия, она передаётся явно латиницей.
    """
    defaults = {
        "doc_type": None,
        "doc_series": None,
        "doc_number": f"9000{next(_DOC_SEQ)}",
        "supplier_name": "ООО «Ромашка»",
        "doc_date": "2026-09-20",
        "amount": 1200.5,
        "vat_amount": 200.5,
        "store": "Матусевича 72",
    }
    defaults.update(kw)
    nak = models.Nakladnaya(**defaults)
    db.add(nak)
    db.commit()
    db.refresh(nak)
    return nak


def _supplier(db, name, tenant_id=None):
    """Запись справочника поставщиков.

    tenant_id=None оставляет подстановку маскирующему слушателю conftest
    (101); ЧУЖОЙ tenant всегда передаётся явно.
    """
    sup = models.Supplier(name=name, tenant_id=tenant_id)
    db.add(sup)
    db.commit()
    db.refresh(sup)
    return sup


def _products(*items):
    return json.dumps(list(items), ensure_ascii=False)


def _xlsx_rows(content: bytes):
    """Строки книги xlsx как кортежи значений (None вместо пустых ячеек)."""
    ws = load_workbook(io.BytesIO(content)).active
    return [tuple(row) for row in ws.iter_rows(values_only=True)]


def _xlsx_text(content: bytes) -> str:
    """Весь текст xlsx как строка — чтобы проверять ОТСУТСТВИЕ значения."""
    book = load_workbook(io.BytesIO(content))
    return "\n".join(
        str(cell.value)
        for sheet in book.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    )


@pytest.fixture
def raw_client():
    """TestClient, который не поднимает исключение хендлера наружу.

    Нужен ровно для фиксации необработанных 500: обычный `client` из conftest
    re-raise'ит их, и тест падает на исключении, а не на ответе.
    """
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def no_openpyxl(monkeypatch):
    """Импорт openpyxl падает с ImportError — как в venv без пакета."""
    monkeypatch.setitem(sys.modules, "openpyxl", None)


# ---------------------------------------------------------------------------
# 1. GET /nakladnye/{nak_id}/excel — успешный путь
# ---------------------------------------------------------------------------

def test_excel_export_contains_document_header_and_products(client, manager, db):
    """Шапка документа и товары попадают в книгу; сумма = qty × цена без НДС."""
    _user, headers = manager
    nak = _nak(db, doc_series="TTN", doc_number="1234567",
               products_json=_products(
                   {"name": "Лампа LED", "qty": 3, "unit": "шт", "price_no_vat": 10.0},
                   # товар без количества и цены: ветка `or 0` в роутере
                   {"name": "Крепёж"},
               ))

    response = client.get(f"/nakladnye/{nak.id}/excel", headers=headers)
    assert response.status_code == 200, response.text

    rows = _xlsx_rows(response.content)
    assert rows[0][:2] == ("Поставщик", "ООО «Ромашка»")
    assert rows[1][:2] == ("Тип", None), "doc_type не задан — ячейка пустая"
    assert rows[2][:2] == ("Серия", "TTN")
    assert rows[3][:2] == ("Номер", "1234567")
    assert rows[4][:2] == ("Дата", "2026-09-20")
    assert rows[5][:2] == ("Сумма с НДС", 1200.5)
    assert rows[6][:2] == ("НДС", 200.5)
    assert rows[7][:2] == ("Магазин", "Матусевича 72")
    # пустая строка-отступ, затем заголовок таблицы товаров
    assert rows[8] == (None,) * 5
    assert rows[9] == ("Товар", "Кол-во", "Ед.", "Цена без НДС", "Сумма без НДС")
    assert rows[10] == ("Лампа LED", 3, "шт", 10.0, 30.0)
    assert rows[11] == ("Крепёж", 0, None, 0, 0)
    assert len(rows) == 12, f"в книге лишние строки: {rows[12:]}"


def test_excel_export_sets_filename_and_content_type(client, manager, db):
    """Имя файла собирается из типа/серии/номера, ответ — xlsx-вложение."""
    _user, headers = manager
    nak = _nak(db, doc_series="TTN", doc_number="1234567")

    response = client.get(f"/nakladnye/{nak.id}/excel", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == \
        'attachment; filename="nak_doc_TTN1234567.xlsx"'
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_excel_export_filename_falls_back_to_id(client, manager, db):
    """Без типа, серии и номера в имя файла подставляется id записи."""
    _user, headers = manager
    nak = _nak(db, doc_number=None)

    response = client.get(f"/nakladnye/{nak.id}/excel", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == \
        f'attachment; filename="nak_doc_{nak.id}.xlsx"'


def test_excel_export_survives_missing_amounts(client, manager, db):
    """amount/vat_amount = None не роняют выгрузку: ячейки остаются пустыми.

    Там же видно quirk: проверка `if nak.amount` отличает NULL от нуля не
    умеет, поэтому реальная сумма 0.0 в файл попадает как пустая ячейка.
    """
    _user, headers = manager
    no_amounts = _nak(db, doc_series="TTN", doc_number="2000001",
                      amount=None, vat_amount=None)
    zero_amounts = _nak(db, doc_series="TTN", doc_number="2000002",
                        amount=0.0, vat_amount=0.0)

    first = client.get(f"/nakladnye/{no_amounts.id}/excel", headers=headers)
    assert first.status_code == 200, first.text
    rows = _xlsx_rows(first.content)
    assert rows[5][:2] == ("Сумма с НДС", None)
    assert rows[6][:2] == ("НДС", None)

    second = client.get(f"/nakladnye/{zero_amounts.id}/excel", headers=headers)
    assert second.status_code == 200, second.text
    zero_rows = _xlsx_rows(second.content)
    assert zero_rows[5][:2] == ("Сумма с НДС", None)
    assert zero_rows[6][:2] == ("НДС", None)


def test_excel_export_without_products_has_empty_table(client, manager, db):
    """Накладная без товаров: шапка есть, строк товаров нет."""
    _user, headers = manager
    nak = _nak(db, doc_series="TTN", products_json=None)

    response = client.get(f"/nakladnye/{nak.id}/excel", headers=headers)
    assert response.status_code == 200, response.text
    rows = _xlsx_rows(response.content)
    assert rows[9][0] == "Товар", "заголовок таблицы товаров должен остаться"
    assert len(rows) == 10, f"ожидалась только шапка, получено строк: {len(rows)}"


def test_excel_export_without_openpyxl_returns_500(client, manager, db, no_openpyxl):
    """Своя накладная + отсутствующий openpyxl → 500 с внятным текстом."""
    _user, headers = manager
    nak = _nak(db)

    response = client.get(f"/nakladnye/{nak.id}/excel", headers=headers)
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "openpyxl не установлен"


def test_excel_export_of_foreign_nakladnaya_is_404_even_without_openpyxl(
        client, two_tenant_users, db, no_openpyxl):
    """ПОРЯДОК ПРОВЕРОК (строка 534): доступ проверяется ДО импорта openpyxl.

    Чужая накладная обязана отвечать 404 независимо от того, установлен ли
    пакет: иначе отсутствие необязательной зависимости превращало бы
    «чужой документ» в 500 с текстом про openpyxl.
    """
    nak = _nak(db, doc_series="TTN", tenant_id=two_tenant_users["second_tenant_id"])

    response = client.get(f"/nakladnye/{nak.id}/excel",
                          headers=two_tenant_users["first_headers"])
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Накладная не найдена"


def test_excel_export_filename_with_cyrillic_series_breaks_response(
        raw_client, manager, db):
    """ДЕФЕКТ: кириллица в серии/типе роняет выгрузку в 500.

    Имя файла подставляется в Content-Disposition сырым текстом, а HTTP-заголовки
    кодируются latin-1: «ТТН» в серии (штатный тип документа!) даёт
    UnicodeEncodeError → 500 вместо файла. Проверка зафиксирована как есть,
    потому что без неё дефект вернётся незаметно: тесты соседей касаются
    только 404-ветки, а имя файла там не собирается.

    Ожидаемое поведение после починки — 200 и RFC 5987
    (`filename*=UTF-8''...`), поэтому тест помечен как xfail-строгий только
    на ожидаемый код: как только ручка начнёт отдавать файл, тест упадёт
    и заставит обновить утверждение.
    """
    _user, headers = manager
    broken = _nak(db, doc_type="ТТН", doc_series="ТТН", doc_number="4881030")
    working = _nak(db, doc_type=None, doc_series="TTN", doc_number="4881031")

    failed = raw_client.get(f"/nakladnye/{broken.id}/excel", headers=headers)
    assert failed.status_code == 500, (
        f"ожидался зафиксированный дефект (500 на кириллице в имени файла), "
        f"получено {failed.status_code}: {failed.text[:200]}"
    )

    # Тот же документ с латинской серией выгружается — триггер именно в имени.
    ok = raw_client.get(f"/nakladnye/{working.id}/excel", headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-disposition"] == 'attachment; filename="nak_doc_TTN4881031.xlsx"'


# ---------------------------------------------------------------------------
# 2. GET /nakladnye/export/products-excel — фильтры и границы диапазона
# ---------------------------------------------------------------------------

def test_products_export_writes_one_row_per_product(client, manager, db):
    """Каждая позиция товара — отдельная строка с данными документа."""
    _user, headers = manager
    _nak(db, doc_type="ТТН", doc_series="ТТН", doc_number="4881030",
         doc_date="2026-09-01", store="Александровская",
         products_json=_products(
             {"name": "Лампа LED", "qty": 2, "unit": "шт", "price_no_vat": 5.0},
             {"name": "Диммер", "qty": 1, "unit": "шт", "price_no_vat": 100.0},
         ))

    response = client.get("/nakladnye/export/products-excel", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == \
        "attachment; filename=nakladnye_products.xlsx"

    rows = _xlsx_rows(response.content)
    assert rows[0] == ("Дата", "Поставщик", "Тип", "Серия", "№ накладной",
                       "Магазин", "Товар", "Кол-во", "Ед.", "Цена без НДС",
                       "Сумма без НДС")
    assert rows[1] == ("2026-09-01", "ООО «Ромашка»", "ТТН", "ТТН", "4881030",
                       "Александровская", "Лампа LED", 2, "шт", 5.0, 10.0)
    assert rows[2][6:11] == ("Диммер", 1, "шт", 100.0, 100.0)
    assert len(rows) == 3


def test_products_export_filters_by_store(client, manager, db):
    """store отсекает чужие магазины; неизвестное значение даёт пустую книгу."""
    _user, headers = manager
    _nak(db, store="Александровская", doc_date="2026-09-01",
         products_json=_products({"name": "ТОВАР-А", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, store="Матусевича 72", doc_date="2026-09-01",
         products_json=_products({"name": "ТОВАР-Б", "qty": 1, "price_no_vat": 1.0}))

    response = client.get("/nakladnye/export/products-excel", headers=headers,
                          params={"store": "Александровская"})
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert "ТОВАР-А" in text
    assert "ТОВАР-Б" not in text

    unknown = client.get("/nakladnye/export/products-excel", headers=headers,
                         params={"store": "Магазина-не-существует"})
    assert unknown.status_code == 200, unknown.text
    assert len(_xlsx_rows(unknown.content)) == 1, "в выгрузку попали чужие строки"


def test_products_export_date_range_is_inclusive(client, manager, db):
    """Обе границы диапазона включаются: сравнение строковое, «<»/«>»."""
    _user, headers = manager
    _nak(db, doc_date="2026-01-01", products_json=_products({"name": "НИЖНЯЯ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-02-15", products_json=_products({"name": "ВНУТРИ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-03-01", products_json=_products({"name": "ВЕРХНЯЯ", "qty": 1, "price_no_vat": 1.0}))

    response = client.get("/nakladnye/export/products-excel", headers=headers,
                          params={"date_from": "2026-01-01", "date_to": "2026-02-15"})
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert "НИЖНЯЯ" in text, "документ ровно на нижней границе потерян"
    assert "ВНУТРИ" in text
    assert "ВЕРХНЯЯ" not in text, "документ выше date_to попал в выгрузку"


@pytest.mark.parametrize("params,expected,unexpected", [
    ({"date_from": "2026-02-15"}, "ВНУТРИ", "НИЖНЯЯ"),
    ({"date_to": "2026-02-15"}, "ВНУТРИ", "ВЕРХНЯЯ"),
    ({"date_from": "2026-02-15", "date_to": "2026-03-01"}, "ВЕРХНЯЯ", "НИЖНЯЯ"),
])
def test_products_export_single_sided_bounds(client, manager, db, params,
                                             expected, unexpected):
    """Одна граница тоже включительна: date_from=дата отдаёт саму дату."""
    _user, headers = manager
    _nak(db, doc_date="2026-01-01", products_json=_products({"name": "НИЖНЯЯ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-02-15", products_json=_products({"name": "ВНУТРИ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-03-01", products_json=_products({"name": "ВЕРХНЯЯ", "qty": 1, "price_no_vat": 1.0}))

    response = client.get("/nakladnye/export/products-excel", headers=headers,
                          params=params)
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert expected in text
    assert unexpected not in text


def test_products_export_skips_rows_without_date(client, manager, db):
    """Накладная без даты выпадает из выгрузки по любому date_from.

    Фильтр дат здесь строковый (строка 644: `d = nak.doc_date or ""`), поэтому
    пустая дата меньше любой непустой границы и документ молча исчезает из
    выборки. Это зафиксировано как фактическое поведение: заполненную дату
    должен присылать бот, а не сервер.
    """
    _user, headers = manager
    _nak(db, doc_date=None, products_json=_products({"name": "БЕЗ-ДАТЫ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-02-15", products_json=_products({"name": "С-ДАТОЙ", "qty": 1, "price_no_vat": 1.0}))

    without_filter = client.get("/nakladnye/export/products-excel", headers=headers)
    assert "БЕЗ-ДАТЫ" in _xlsx_text(without_filter.content)

    response = client.get("/nakladnye/export/products-excel", headers=headers,
                          params={"date_from": "2026-01-01"})
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert "С-ДАТОЙ" in text
    assert "БЕЗ-ДАТЫ" not in text, "накладная без даты не должна попадать в диапазон"


def test_products_export_ignores_nakladnyya_without_usable_products(client, manager, db):
    """Пустой список товаров и битый products_json не дают ни одной строки.

    Отбор идёт по `products_json IS NOT NULL`, поэтому битое значение всё
    равно тянется в выборку — спасает разбор в parse_products, который на
    мусоре возвращает пустой список.
    """
    _user, headers = manager
    _nak(db, doc_date="2026-02-15", products_json=_products({"name": "ЕСТЬ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, doc_date="2026-02-15", products_json=_products())
    _nak(db, doc_date="2026-02-15", products_json="{это не json")
    _nak(db, doc_date="2026-02-15", products_json=None)

    response = client.get("/nakladnye/export/products-excel", headers=headers)
    assert response.status_code == 200, response.text
    rows = _xlsx_rows(response.content)
    assert [row[6] for row in rows[1:]] == ["ЕСТЬ"], rows


def test_products_export_combines_store_and_dates(client, manager, db):
    """Фильтры складываются: магазин и диапазон дат применяются вместе."""
    _user, headers = manager
    _nak(db, store="Александровская", doc_date="2026-01-05",
         products_json=_products({"name": "А-ЯНВ", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, store="Александровская", doc_date="2026-03-05",
         products_json=_products({"name": "А-МАР", "qty": 1, "price_no_vat": 1.0}))
    _nak(db, store="Матусевича 72", doc_date="2026-02-05",
         products_json=_products({"name": "Б-ФЕВ", "qty": 1, "price_no_vat": 1.0}))

    response = client.get("/nakladnye/export/products-excel", headers=headers,
                          params={"store": "Александровская",
                                  "date_from": "2026-01-01", "date_to": "2026-02-28"})
    assert response.status_code == 200, response.text
    text = _xlsx_text(response.content)
    assert "А-ЯНВ" in text
    assert "А-МАР" not in text, "документ вне диапазона дат попал в выгрузку"
    assert "Б-ФЕВ" not in text, "чужой магазин попал в выгрузку"


def test_products_export_empty_selection_keeps_header(client, manager, db):
    """Пустая выборка — валидная книга с одной строкой заголовков."""
    _user, headers = manager
    response = client.get("/nakladnye/export/products-excel", headers=headers)
    assert response.status_code == 200, response.text
    rows = _xlsx_rows(response.content)
    assert len(rows) == 1
    assert rows[0][0] == "Дата"


def test_products_export_without_openpyxl_returns_500(client, manager, db, no_openpyxl):
    """Импорт здесь до выборки, поэтому 500 получает даже пустая база."""
    _user, headers = manager
    response = client.get("/nakladnye/export/products-excel", headers=headers)
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "openpyxl не установлен"


# ---------------------------------------------------------------------------
# 3. Фильтры GET /nakladnye
# ---------------------------------------------------------------------------

def _numbers(client, headers, **params):
    """Номера документов из выдачи списка в порядке ответа.

    Номера в тестах этой секции — только цифры: ключ документа нормализуется
    до цифр (models.nakladnaya_doc_key), поэтому «D-1» и «DATED-1» стали бы
    одним ключом и упали бы на уникальном индексе.
    """
    response = client.get("/nakladnye", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return [row["doc_number"] for row in response.json()]


def test_list_filters_by_store(client, manager, db):
    _user, headers = manager
    _nak(db, doc_number="7001", store="Александровская")
    _nak(db, doc_number="7002", store="Матусевича 72")

    assert _numbers(client, headers, store="Александровская") == ["7001"]
    assert _numbers(client, headers, store="Нет-такого-магазина") == []


def test_list_filters_by_doc_type(client, manager, db):
    _user, headers = manager
    _nak(db, doc_number="7011", doc_type="ТТН")
    _nak(db, doc_number="7012", doc_type="УПД")
    _nak(db, doc_number="7013", doc_type=None)

    assert _numbers(client, headers, doc_type="ТТН") == ["7011"]
    assert _numbers(client, headers, doc_type="УПД") == ["7012"]
    # Пустая строка фильтра означает «не фильтровать», а не «doc_type IS NULL».
    assert sorted(_numbers(client, headers, doc_type="")) == \
        ["7011", "7012", "7013"]


def test_list_filters_by_status(client, manager, db):
    _user, headers = manager
    _nak(db, doc_number="7021", status="new")
    _nak(db, doc_number="7022", status="verified")
    _nak(db, doc_number="7023", status="paid")

    assert _numbers(client, headers, status="verified") == ["7022"]
    assert _numbers(client, headers, status="new") == ["7021"]
    assert _numbers(client, headers, status="paid") == ["7023"]
    assert _numbers(client, headers, status="arrived") == []


def test_list_status_filter_finds_nakladnaya_created_without_status(client, manager, db):
    """Явный status=None не оставляет запись без статуса.

    У модели Column(default="new"), и SQLAlchemy подставляет его даже при
    явно переданном None, поэтому фильтр status=new такую накладную находит,
    а nak_dict-ветка `n.status or "new"` остаётся страховкой для записей,
    записанных мимо ORM.
    """
    _user, headers = manager
    _nak(db, doc_number="7024", status=None)
    _nak(db, doc_number="7025", status="verified")

    assert sorted(_numbers(client, headers, status="new")) == ["7024"]
    listed = client.get("/nakladnye", headers=headers).json()
    statuses = {row["doc_number"]: row["status"] for row in listed}
    assert statuses == {"7024": "new", "7025": "verified"}


def test_list_filters_by_supplier_id(client, manager, db):
    """supplier_id фильтрует по id справочника, а не по имени."""
    _user, headers = manager
    first = _supplier(db, "ООО «Ромашка»")
    second = _supplier(db, "ООО «Гвоздь»")
    _nak(db, doc_number="7031", supplier_id=first.id, supplier_name=first.name)
    _nak(db, doc_number="7032", supplier_id=second.id, supplier_name=second.name)
    _nak(db, doc_number="7033", supplier_id=None, supplier_name="Не распознано")

    assert _numbers(client, headers, supplier_id=first.id) == ["7031"]
    assert _numbers(client, headers, supplier_id=second.id) == ["7032"]
    assert _numbers(client, headers, supplier_id=first.id + second.id + 1000) == []


def test_list_date_range_is_inclusive(client, manager, db):
    """Границы включительны: сравнение строковое, ровно на границе остаётся."""
    _user, headers = manager
    _nak(db, doc_number="7041", doc_date="2026-01-01")
    _nak(db, doc_number="7042", doc_date="2026-02-15")
    _nak(db, doc_number="7043", doc_date="2026-03-01")

    assert sorted(_numbers(client, headers, date_from="2026-01-01",
                           date_to="2026-02-15")) == ["7041", "7042"]
    assert sorted(_numbers(client, headers, date_from="2026-02-15")) == \
        ["7042", "7043"]
    assert sorted(_numbers(client, headers, date_to="2026-02-15")) == \
        ["7041", "7042"]
    assert _numbers(client, headers, date_from="2026-01-01",
                    date_to="2026-01-01") == ["7041"]


def test_list_date_filter_drops_nakladnaya_without_date(client, manager, db):
    """doc_date = None читается как "" и выпадает при любом date_from.

    Тот же строковый фильтр, что и в экспорте: пустая дата меньше непустой
    границы. Поведение зафиксировано, чтобы смена типа фильтра на дату
    была осознанным изменением, а не побочным эффектом.
    """
    _user, headers = manager
    _nak(db, doc_number="7051", doc_date=None)
    _nak(db, doc_number="7052", doc_date="2026-02-15")

    assert sorted(_numbers(client, headers)) == ["7051", "7052"]
    assert _numbers(client, headers, date_from="2026-01-01") == ["7052"]
    # date_to пустую дату не отсекает: "" <= "2026-12-31"
    assert sorted(_numbers(client, headers, date_to="2026-12-31")) == \
        ["7051", "7052"]


def test_list_combines_store_status_and_dates(client, manager, db):
    _user, headers = manager
    _nak(db, doc_number="7061", store="Александровская", status="verified",
         doc_date="2026-01-10")
    _nak(db, doc_number="7062", store="Александровская", status="paid",
         doc_date="2026-01-10")
    _nak(db, doc_number="7063", store="Александровская", status="verified",
         doc_date="2026-04-10")
    _nak(db, doc_number="7064", store="Матусевича 72", status="verified",
         doc_date="2026-01-10")

    assert _numbers(client, headers, store="Александровская", status="verified",
                    date_from="2026-01-01", date_to="2026-02-01") == ["7061"]


# ---------------------------------------------------------------------------
# 4. Автоподстановка supplier_name по supplier_id
# ---------------------------------------------------------------------------

def test_create_fills_supplier_name_from_supplier_id(client, manager, db):
    """POST без имени, но с supplier_id берёт имя из справочника."""
    _user, headers = manager
    sup = _supplier(db, "ООО «Ромашка»")

    response = client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "SUP-AUTO-1", "supplier_id": sup.id,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supplier_id"] == sup.id
    assert body["supplier_name"] == "ООО «Ромашка»"
    assert body["supplier"] == {"id": sup.id, "name": "ООО «Ромашка»"}


def test_create_replaces_explicitly_empty_supplier_name(client, manager, db):
    """Явная пустая строка не отключает подстановку (условие `not data.get(...)`).

    Клиент, который присылает supplier_name="" (форма не заполнена), получает
    имя из справочника — это сделано намеренно, и тест фиксирует именно
    такое поведение, чтобы его не «починили» вслед за другими дефектами.
    """
    _user, headers = manager
    sup = _supplier(db, "ООО «Гвоздь»")

    response = client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "SUP-AUTO-2",
        "supplier_id": sup.id, "supplier_name": "",
    })
    assert response.status_code == 200, response.text
    assert response.json()["supplier_name"] == "ООО «Гвоздь»"


def test_create_keeps_explicit_supplier_name(client, manager, db):
    """Непустое имя клиента имеет приоритет над справочником."""
    _user, headers = manager
    sup = _supplier(db, "ООО «Гвоздь»")

    response = client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "SUP-AUTO-3",
        "supplier_id": sup.id, "supplier_name": "ООО «Ромашка» (другое юрлицо)",
    })
    assert response.status_code == 200, response.text
    assert response.json()["supplier_name"] == "ООО «Ромашка» (другое юрлицо)"


def test_create_without_supplier_id_leaves_name_as_sent(client, manager, db):
    """Без supplier_id подстановки нет: пустое имя остаётся пустым."""
    _user, headers = manager
    response = client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "SUP-AUTO-4", "supplier_name": "",
    })
    assert response.status_code == 200, response.text
    assert response.json()["supplier_name"] == ""
    assert response.json()["supplier_id"] is None


def test_create_with_unknown_supplier_id_fails_with_500(raw_client, manager, db):
    """ДЕФЕКТ: несуществующий supplier_id в create даёт 500 (FK), а не 400/422.

    Схема принимает любое целое, справочник не проверяется, INSERT отбивает
    внешний ключ. Клиенту (форма выбора поставщика) это неотличимо от падения
    сервера. Зафиксировано как есть; после починки тест надо обновить.
    """
    _user, headers = manager
    response = raw_client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "SUP-AUTO-5", "supplier_id": 987654321,
    })
    assert response.status_code == 500, (
        f"ожидался зафиксированный дефект (500 на чужом supplier_id), "
        f"получено {response.status_code}: {response.text[:200]}"
    )
    db.expire_all()
    assert db.query(models.Nakladnaya).count() == 0, "запись всё же создалась"


def test_create_does_not_take_supplier_name_from_other_tenant(
        client, two_tenant_users, db):
    """Чужой поставщик не подставляется — lookup tenant-scoped.

    Заодно зафиксирован побочный дефект nak_dict: связь supplier отдаётся
    без tenant-фильтра, поэтому в ответе видно ИМЯ поставщика чужой компании.
    Имя строки в supplier_name при этом остаётся пустым.
    """
    foreign = _supplier(db, "ООО «Чужой»",
                        tenant_id=two_tenant_users["second_tenant_id"])

    response = client.post("/nakladnye", headers=two_tenant_users["first_headers"],
                           json={"doc_series": "TTN", "doc_number": "SUP-AUTO-6",
                                 "supplier_id": foreign.id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supplier_name"] == "", "имя чужого поставщика подставлено"
    assert body["supplier"] == {"id": foreign.id, "name": "ООО «Чужой»"}, (
        "ожидался зафиксированный дефект nak_dict: объект supplier "
        "отдаётся без tenant-фильтра"
    )


def test_patch_fills_supplier_name_from_new_supplier_id(client, manager, db):
    """PATCH со сменой поставщика по id обновляет и имя."""
    _user, headers = manager
    old = _supplier(db, "ООО «Старый»")
    new = _supplier(db, "ООО «Новый»")
    nak = _nak(db, supplier_id=old.id, supplier_name=old.name)

    response = client.patch(f"/nakladnye/{nak.id}", headers=headers,
                            json={"supplier_id": new.id})
    assert response.status_code == 200, response.text
    assert response.json()["supplier_name"] == "ООО «Новый»"
    assert response.json()["supplier_id"] == new.id


def test_patch_keeps_explicit_supplier_name(client, manager, db):
    """Явно присланное имя не перетирается — даже пустой строкой.

    Здесь условие строгое (`"supplier_name" not in update_data`), в отличие от
    create: достаточно самого факта передачи поля.
    """
    _user, headers = manager
    sup = _supplier(db, "ООО «Новый»")
    nak = _nak(db, supplier_name="ООО «Старый»")

    response = client.patch(f"/nakladnye/{nak.id}", headers=headers,
                            json={"supplier_id": sup.id, "supplier_name": "Как в договоре"})
    assert response.status_code == 200, response.text
    assert response.json()["supplier_name"] == "Как в договоре"

    emptied = client.patch(f"/nakladnye/{nak.id}", headers=headers,
                           json={"supplier_name": ""})
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["supplier_name"] == ""


def test_patch_with_null_supplier_id_keeps_name(client, manager, db):
    """supplier_id = null — это не «сменить поставщика», а отвязать накладную."""
    _user, headers = manager
    sup = _supplier(db, "ООО «Ромашка»")
    nak = _nak(db, supplier_id=sup.id, supplier_name=sup.name)

    response = client.patch(f"/nakladnye/{nak.id}", headers=headers,
                            json={"supplier_id": None})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supplier_id"] is None
    assert body["supplier_name"] == "ООО «Ромашка»", "имя затёрлось при отвязке"
    assert body["supplier"] is None


def test_patch_with_unknown_supplier_id_reports_duplicate_409(raw_client, manager, db):
    """ДЕФЕКТ: нарушение FK в PATCH отдаётся как 409 «уже принята».

    commit_with_doc_key_guard после rollback ищет документ по ключу и находит
    саму накладную, которую правили, — поэтому клиенту показывают ложный дубль
    с её же id. Зафиксировано как есть; после починки тест надо обновить.
    """
    _user, headers = manager
    nak = _nak(db, doc_series="TTN", doc_number="4881099", supplier_name="ООО «Ромашка»")

    response = raw_client.patch(f"/nakladnye/{nak.id}", headers=headers,
                                json={"supplier_id": 987654321})
    assert response.status_code == 409, (
        f"ожидался зафиксированный дефект (409 на чужом supplier_id), "
        f"получено {response.status_code}: {response.text[:200]}"
    )
    detail = response.json()["detail"]
    assert detail["existing_id"] == nak.id, "сообщение указывает на чужую запись"

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).one()
    assert stored.supplier_id is None, "невалидная ссылка всё же записалась"
    assert stored.supplier_name == "ООО «Ромашка»"


# ---------------------------------------------------------------------------
# 5. products при создании и PATCH с products: [] — очистка товаров
# ---------------------------------------------------------------------------

def test_create_stores_products_json(client, manager, db):
    """products при создании сериализуются, а не теряются (регресс 3d109d6)."""
    _user, headers = manager
    products = [{"name": "Лампа LED", "qty": 3, "unit": "шт", "price_no_vat": 10.0}]

    response = client.post("/nakladnye", headers=headers, json={
        "doc_series": "TTN", "doc_number": "7100", "products": products,
    })
    assert response.status_code == 200, response.text
    assert response.json()["products"] == products

    db.expire_all()
    stored = db.query(models.Nakladnaya).one()
    assert json.loads(stored.products_json) == products


def test_patch_with_empty_products_clears_products_json(client, manager, db):
    """products: [] обнуляет products_json, и список отдаёт пустой products."""
    _user, headers = manager
    nak = _nak(db, products_json=_products({"name": "Лампа LED", "qty": 3,
                                            "unit": "шт", "price_no_vat": 10.0}))
    nak_id = nak.id

    response = client.patch(f"/nakladnye/{nak_id}", headers=headers,
                            json={"products": []})
    assert response.status_code == 200, response.text
    assert response.json()["products"] == []

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak_id).one()
    assert stored.products_json is None, "JSON-строка не очищена"

    listed = client.get("/nakladnye", headers=headers).json()
    assert listed[0]["id"] == nak_id
    assert listed[0]["products"] == [], "после очистки список отдаёт ошибку"


def test_patch_without_products_field_keeps_products(client, manager, db):
    """Правка других полей не трогает товары: products=None ≠ products=[]."""
    _user, headers = manager
    products = [{"name": "Лампа LED", "qty": 3, "unit": "шт", "price_no_vat": 10.0}]
    nak = _nak(db, products_json=_products(*products))

    # products=None, переданный явно, — это уже «поле передано»; смысл имеют
    # только exclude_unset (поле не прислали вовсе).
    assert schemas.NakladnayaUpdate(status="verified").model_dump(
        exclude_unset=True) == {"status": "verified"}

    response = client.patch(f"/nakladnye/{nak.id}", headers=headers,
                            json={"status": "verified"})
    assert response.status_code == 200, response.text
    assert response.json()["products"] == products

    db.expire_all()
    stored = db.query(models.Nakladnaya).filter(
        models.Nakladnaya.id == nak.id).one()
    assert json.loads(stored.products_json) == products
