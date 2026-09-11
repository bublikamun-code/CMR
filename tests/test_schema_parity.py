"""Приоритет E — метатесты паритета: модели ↔ Pydantic-схемы ↔ боевой DDL.

Это защита от ЦЕЛОГО КЛАССА дефектов, а не от одного. Коммит 3d109d6 чинил
`NakladnayaUpdate.products`: поле есть в модели, но отсутствует в схеме,
из-за чего клиент физически не может его изменить — и это молча теряет данные.
Такой баг не видно ни в логах, ни в UI: запрос проходит со статусом 200.

Метатесты ниже делают три вещи:

1. Сравнивают колонки моделей с полями Create/Update-схем и сверяют расхождение
   с документированным списком. Список разделён на SERVER_MANAGED (так задумано)
   и KNOWN_DEFECTS (дефект). Тест зелёный, пока множество совпадает, — то есть
   новый незамеченный пробел уронит прогон, а починка известного заставит
   обновить список осознанно.

2. Сравнивают колонки моделей с ФАКТИЧЕСКИМ DDL боевой БД. Это ловит дрейф
   схемы: например nakladnye.amount_no_vat есть на проде, но отсутствует в models.py.

3. Находят мёртвые схемы и незарегистрированные роутеры. Исторический пример:
   email_parser_router.cron_router был определён, но не подключён в main.py,
   из-за чего запланированная синхронизация почты молча не работала.
"""
import ast
import pathlib
from collections import defaultdict

import pytest
from sqlalchemy import inspect, text

import database
import models
import schemas
from conftest import SCHEMA_PROFILE

pytestmark = pytest.mark.schema_parity

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Колонки, которыми управляет сервер или которые приходят из пути запроса.
AUTO = {"id", "created_at", "updated_at", "tenant_id"}

PARITY = {
    "Card": ["CardCreate", "CardUpdate"],
    "Task": ["TaskCreate", "TaskUpdate"],
    "Transaction": ["TransactionUpdate"],
    "Nakladnaya": ["NakladnayaCreate", "NakladnayaUpdate"],
    "Client": ["ClientCreate", "ClientUpdate"],
    "Supplier": ["SupplierCreate", "SupplierUpdate"],
    "User": ["UserCreate", "UserUpdate"],
    "CardChecklist": ["ChecklistCreate", "ChecklistUpdate"],
    "TaskChecklistItem": ["TaskChecklistItemCreate", "TaskChecklistItemUpdate"],
}

# Пробелы, которые ТАК ЗАДУМАНЫ. Рядом — почему.
SERVER_MANAGED = {
    ("Card", "CardCreate"): {
        "is_deleted",            # флаг корзины, ставится сервером
        "position",              # порядок на доске — отдельный reorder-эндпоинт
        "writeoff_group_id",     # назначается только через группу списаний
    },
    ("Card", "CardUpdate"): {
        "is_deleted", "position", "writeoff_group_id",
        "status",                # есть отдельный PATCH /kanban/cards/{id}/status
    },
    ("Task", "TaskCreate"): {
        "creator_id",            # берётся из current_user
        "completed_at",          # ставится сервером при переходе в done
        "card_title_snapshot",   # денормализованный снимок, считается сервером
        "client_name_snapshot",
    },
    ("Task", "TaskUpdate"): {
        "creator_id", "completed_at", "card_title_snapshot", "client_name_snapshot",
    },
    ("Transaction", "TransactionUpdate"): {
        "card_id",               # привязка меняется переносом, не правкой записи
        "writeoff_group_id",     # управляется группой списаний
    },
    ("Nakladnaya", "NakladnayaCreate"): {
        "created_by_bot",        # проставляет сам эндпоинт бота
        "excel_path",            # генерируется сервером
        "photo_paths",           # отдельный эндпоинт загрузки фото
        "products_json",         # клиент шлёт `products`, сервер сериализует в JSON
    },
    ("Nakladnaya", "NakladnayaUpdate"): {
        "created_by_bot", "excel_path", "photo_paths", "products_json",
    },
    ("User", "UserCreate"): {
        "hashed_password",       # создаётся из `password` через get_password_hash
    },
    ("User", "UserUpdate"): {
        "hashed_password",       # только через отдельную смену пароля
        "username",              # логин не переименовывается
    },
    ("CardChecklist", "ChecklistCreate"): {
        "card_id",               # comes from path
        "invoice_file_name",     # заполняется загрузкой файла
        "invoice_file_path",
    },
    ("CardChecklist", "ChecklistUpdate"): {
        "card_id", "invoice_file_name", "invoice_file_path",
    },
    ("TaskChecklistItem", "TaskChecklistItemCreate"): {
        "task_id",               # comes from path
        "position",              # порядок назначается сервером
        "is_done",               # пункт создаётся невыполненным
    },
    ("TaskChecklistItem", "TaskChecklistItemUpdate"): {
        "task_id", "position",
    },
}

# Пробелы, которые являются ДЕФЕКТОМ: клиент должен иметь возможность менять поле.
KNOWN_DEFECTS = {
    ("Card", "CardUpdate"): {"owner_id"},
    ("Task", "TaskCreate"): {"priority"},
    ("Task", "TaskUpdate"): {"priority"},
    ("Transaction", "TransactionUpdate"): {"date", "company_name", "is_secondary_check"},
}


def _model_columns(model_name):
    return {c.name for c in getattr(models, model_name).__table__.columns} - AUTO


def _schema_fields(schema_name):
    sc = getattr(schemas, schema_name, None)
    return set(sc.model_fields.keys()) if sc is not None else set()


def _compute_gaps():
    gaps = defaultdict(set)
    for model_name, schema_names in PARITY.items():
        cols = _model_columns(model_name)
        for schema_name in schema_names:
            missing = cols - _schema_fields(schema_name)
            if missing:
                gaps[(model_name, schema_name)] = missing
    return dict(gaps)


# ---------------------------------------------------------------------------
# 1. Паритет моделей и схем
# ---------------------------------------------------------------------------

def test_schema_gaps_match_the_documented_list():
    """Главный метатест: множество пробелов должно точно совпадать с документированным.

    Зелёный сейчас. Упадёт в двух случаях, и оба требуют осознанного решения:
      - появился НОВЫЙ незамеченный пробел (кто-то добавил колонку и забыл схему);
      - известный дефект починили — тогда его надо убрать из KNOWN_DEFECTS.
    """
    expected = defaultdict(set)
    for source in (SERVER_MANAGED, KNOWN_DEFECTS):
        for key, fields in source.items():
            expected[key] |= set(fields)
    expected = dict(expected)

    actual = _compute_gaps()
    new_gaps = {k: sorted(v) for k, v in actual.items() if k not in expected}
    gone = {k: sorted(v) for k, v in expected.items() if k not in actual}
    changed = {k: [sorted(expected[k]), sorted(actual[k])]
               for k in actual if k in expected and actual[k] != expected[k]}
    assert actual == expected, (
        "Расхождение моделей и схем изменилось — нужно осознанное решение.\n"
        f"  появилось нового: {new_gaps}\n"
        f"  исчезло: {gone}\n"
        f"  изменилось: {changed}"
    )


@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ (класс бага 3d109d6): поля есть в моделях, но "
                          "отсутствуют в Create/Update-схемах, поэтому клиент не может их "
                          "изменить, а запрос молча проходит со статусом 200. "
                          "Card.owner_id — нельзя переназначить владельца сделки. "
                          "Task.priority — приоритет нельзя ни задать, ни поменять. "
                          "Transaction.date — дату оплаты нельзя исправить, хотя реестр "
                          "сортируется по date. Transaction.company_name и "
                          "is_secondary_check — то же.")
def test_no_client_editable_field_is_missing_from_schemas():
    assert not KNOWN_DEFECTS, \
        f"незакрытые пробелы схем: {sorted(KNOWN_DEFECTS.items())}"


def test_priority_is_visible_in_response_but_not_editable():
    """Приоритет задачи ОТДАЁТСЯ клиенту, но не принимается обратно —
    то есть UI может его показать, но не может сохранить."""
    assert "priority" in schemas.TaskResponse.model_fields
    assert "priority" not in schemas.TaskCreate.model_fields
    assert "priority" not in schemas.TaskUpdate.model_fields


# ---------------------------------------------------------------------------
# 2. Паритет моделей и фактического DDL боевой БД
# ---------------------------------------------------------------------------

# Задокументированный дрейф «боевой DDL ↔ models.py». Колонка amount_no_vat
# добавлена в nakladnye миграцией, но в модели не отражена: клиент не может её
# ни прочитать, ни заполнить. Чинится в Фазе 2 вместе с остальными пробелами схем.
KNOWN_DDL_DRIFT = {
    "nakladnye": {
        "в DDL, но нет в models.py": ["amount_no_vat"],
        "в models.py, но нет в DDL": [],
    },
}


@pytest.mark.skipif(SCHEMA_PROFILE != "prod",
                    reason="сравниваем с боевым DDL — осмысленно только на профиле prod")
def test_model_columns_match_prod_ddl():
    """models.py обязан описывать ту схему, которая реально лежит на проде.

    Известный дрейф задокументирован в KNOWN_DDL_DRIFT; любое НОВОЕ расхождение
    уронит тест. Это защита от повторения истории, когда колонку добавили
    миграцией на сервере и забыли отразить в моделях.
    """
    insp = inspect(database.engine)
    drift = {}
    for model_name in PARITY:
        table = getattr(models, model_name).__tablename__
        if not insp.has_table(table):
            drift[table] = {"проблема": "таблицы нет в боевой БД"}
            continue
        ddl_cols = {c["name"] for c in insp.get_columns(table)} - AUTO
        model_cols = _model_columns(model_name)
        only_ddl = ddl_cols - model_cols
        only_model = model_cols - ddl_cols
        entry = {
            "в DDL, но нет в models.py": sorted(only_ddl),
            "в models.py, но нет в DDL": sorted(only_model),
        }
        if (only_ddl or only_model) and entry != KNOWN_DDL_DRIFT.get(table):
            drift[table] = entry

    assert not drift, f"НЕЗАДОКУМЕНТИРОВАННЫЙ дрейф схемы: {drift}"


def test_money_column_types_are_documented():
    """Фиксируем реальность, из-за которой тесты обязаны идти на DDL прода:
    models.py объявляет Numeric(12,2), а боевая БД хранит FLOAT/REAL."""
    if SCHEMA_PROFILE != "prod":
        pytest.skip("проверка имеет смысл только на боевом DDL")
    with database.engine.connect() as conn:
        cards_ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='cards'")).scalar()
    assert "total_amount FLOAT" in cards_ddl
    # и при этом модель декларирует Numeric
    col = models.Card.__table__.columns["total_amount"]
    assert "NUMERIC" in repr(col.type).upper()


# ---------------------------------------------------------------------------
# 3. Мёртвые схемы и незарегистрированные роутеры
# ---------------------------------------------------------------------------

def _dead_schemas():
    tree = ast.parse((ROOT / "schemas.py").read_text(encoding="utf-8"))
    classes = {n.name: [b.id for b in n.bases if isinstance(b, ast.Name)]
               for n in tree.body if isinstance(n, ast.ClassDef)}
    used = set()
    files = list((ROOT / "routers").glob("*.py")) + [ROOT / "main.py"]
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "schemas"):
                used.add(node.attr)
    # базовые классы используемых схем считаются живыми
    alive = set(used)
    changed = True
    while changed:
        changed = False
        for name, bases in classes.items():
            if name in alive:
                for b in bases:
                    if b not in alive:
                        alive.add(b)
                        changed = True
    return sorted(c for c in classes if c not in alive)


KNOWN_DEAD_SCHEMAS = {
    "AttachmentCreate",     # вложения создаются загрузкой файла, не JSON-ом
    "CardStatusUpdate",     # вместо неё используется CardUpdateStatus
    "NakladnayaResponse",   # nakladnye_router отдаёт dict через _nak_dict
    "NotificationResponse",
    "SupplierBrief",
    "TransactionCreate",    # транзакция создаётся через trigger/issue-invoice
    "WriteoffGroupCard",    # writeoff_groups_router объявил свои локальные схемы
    "WriteoffGroupCreate",
}


def test_dead_schemas_match_the_documented_list():
    """Мёртвая схема — это ловушка: её правят, ожидая эффекта, а эффект нулевой.

    Тест не требует удалить их немедленно, но требует, чтобы список не рос молча.
    """
    actual = set(_dead_schemas())
    assert actual == KNOWN_DEAD_SCHEMAS, (
        f"появились новые мёртвые схемы: {sorted(actual - KNOWN_DEAD_SCHEMAS)}; "
        f"ожили: {sorted(KNOWN_DEAD_SCHEMAS - actual)}"
    )


# Модули в routers/, которые намеренно не подключены. Задокументировано,
# чтобы новое «забыли зарегистрировать» не прошло молча.
#
# saved_views_router: фича «Сохранённые представления» мертва с обоих концов —
# роутер не импортирован в main.py, js/saved_views.js не подключён ни одной
# страницей, обращений к /views с фронта нет. При этом таблица saved_views
# в боевой БД существует и роутер полностью написан (5 эндпоинтов).
# Решение о судьбе фичи — за владельцем (Фаза 2/4).
KNOWN_UNREGISTERED = {"saved_views_router"}


def test_every_router_module_is_registered():
    """Каждый модуль routers/ обязан быть подключён в main.py.

    Исторический дефект: email_parser_router.cron_router был определён, но не
    зарегистрирован — запланированная синхронизация почты молча не работала.
    Тот же класс дефекта сейчас выявлен для saved_views_router (см. выше).
    """
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(main_src)

    registered = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router" and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Attribute):
                registered.add((arg.value.id, arg.attr))
            elif isinstance(arg, ast.Name):
                registered.add((arg.id, "router"))

    imported = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "routers":
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name

    problems = []
    for module_file in sorted((ROOT / "routers").glob("*_router.py")):
        module = module_file.stem
        if module in KNOWN_UNREGISTERED:
            continue
        names = [local for local, mod in imported.items() if mod == module]
        if not names:
            problems.append(f"{module}: не импортирован в main.py")
            continue
        for local in names:
            if not any(m == local for m, _ in registered):
                problems.append(f"{module}: импортирован как {local}, но не зарегистрирован")

    # отдельно: все публичные APIRouter в модуле должны быть подключены
    for module_file in sorted((ROOT / "routers").glob("*_router.py")):
        module = module_file.stem
        if module in KNOWN_UNREGISTERED:
            continue
        src = module_file.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "id", "") == "APIRouter"):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        local = next((l for l, m in imported.items() if m == module), None)
                        if local and (local, target.id) not in registered:
                            problems.append(
                                f"{module}.{target.id}: определён, но не подключён в main.py")

    assert not problems, "незарегистрированные роутеры:\n  " + "\n  ".join(sorted(set(problems)))


def test_known_unregistered_stays_accurate():
    """Если забытый роутер подключат, этот тест напомнит убрать его из списка."""
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    for module in KNOWN_UNREGISTERED:
        assert module not in main_src, \
            f"{module} больше не импортируется в main.py — уберите его из KNOWN_UNREGISTERED"


def test_cron_routers_are_registered():
    """cron_router'ы живут отдельно от основных и их легко забыть подключить."""
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    for module, attr in [("email_parser_router", "cron_router"),
                         ("notifications_router", "cron_router")]:
        assert f"{module}.{attr}" in main_src or f".{attr}" in main_src, \
            f"{module}.{attr} не подключён — cron-задачи молча не работают"
