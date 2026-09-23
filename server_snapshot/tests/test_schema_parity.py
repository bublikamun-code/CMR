"""Приоритет E — метатесты паритета: модели ↔ Pydantic-схемы ↔ боевой DDL.

Это защита от ЦЕЛОГО КЛАССА дефектов, а не от одного. Коммит 3d109d6 чинил
`NakladnayaUpdate.products`: поле есть в модели, но отсутствует в схеме,
из-за чего клиент физически не может его изменить — и это молча теряет данные.
Такой баг не видно ни в логах, ни в UI: запрос проходит со статусом 200.

Метатесты ниже делают четыре вещи:

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

4. Проверяют вторую половину того же контракта: поле, заявленное в *Update-схеме,
   ОБЯЗАНО доходить до записи в соответствующем PATCH/PUT-хендлере. Наличие
   поля в схеме ничего не гарантирует — update_card молча не читал sender_email
   (пункт 5 плана v2, 23.09), хотя поле было и в CardBase, и в CardUpdate, и
   колонка на месте. Раздел 5 разбирает роутеры через ast, раздел 6 подтверждает
   каждое поле CardUpdate живым запросом, раздел 7 добавляет к этому null-
   семантику (очистка против «не прислали») админских PATCH-роутов вебхуков и
   сценариев и проверку существования ответственного в задачах.
"""
import ast
import json
import pathlib
import socket
from collections import defaultdict
from datetime import date, datetime, timezone

import pytest
from conftest import SCHEMA_PROFILE
from sqlalchemy import inspect, text

import database
import models
import schemas

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
        # производные ключи документа для уникального индекса (миграция 0005,
        # дефект 7): считаются слушателем models._nakladnaya_sync_doc_key из
        # doc_series/doc_number. Клиент их не присылает — иначе он мог бы
        # обойти дедупликацию, прислав ключ, не совпадающий с сырыми полями.
        "doc_series_norm", "doc_number_norm",
    },
    ("Nakladnaya", "NakladnayaUpdate"): {
        "created_by_bot", "excel_path", "photo_paths", "products_json",
        "doc_series_norm", "doc_number_norm",
    },
    ("User", "UserCreate"): {
        "hashed_password",       # создаётся из `password` через get_password_hash
        # Новый пользователь всегда активен; отключают его отдельным PATCH
        # /auth/users/{id} (пункт 15 плана v2, миграция 0014).
        "is_active",
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
#
# 2026-09-12 (Фаза 2, дефект 13) — список закрыт полностью. В схемы вернулись:
#   Card.owner_id                              → CardUpdate
#   Task.priority                              → TaskCreate, TaskUpdate
#   Transaction.date / company_name /
#   Transaction.is_secondary_check             → TransactionUpdate
# Каждый пробел означал, что PATCH отвечает 200 и молча ничего не меняет.
# Список оставлен пустым, а не удалён: test_schema_gaps_match_the_documented_list
# сверяет множество SERVER_MANAGED | KNOWN_DEFECTS с фактическим, и новый
# незамеченный пробел обязан уронить прогон.
KNOWN_DEFECTS = {}


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


def test_no_client_editable_field_is_missing_from_schemas():
    """Починено в Фазе 2 (2026-09-12, дефект 13).

    Все пять пробелов закрыты: Card.owner_id, Task.priority (Create и Update),
    Transaction.date / company_name / is_secondary_check. До починки каждый из
    них означал запрос, который проходит со статусом 200 и ничего не меняет —
    самый тихий класс дефектов: его не видно ни в логах, ни в UI.

    Проверка оставлена как гейт: KNOWN_DEFECTS пуст, и любой новый пробел
    (добавили колонку в модель и забыли схему) уронит и этот тест, и
    test_schema_gaps_match_the_documented_list.
    """
    assert not KNOWN_DEFECTS, \
        f"незакрытые пробелы схем: {sorted(KNOWN_DEFECTS.items())}"


def test_priority_is_editable_and_visible_in_response():
    """Приоритет задачи ОТДАЁТСЯ клиенту и принимается обратно.

    До Фазы 2 (дефект 13) этот тест утверждал обратное — что priority есть
    в TaskResponse, но отсутствует в TaskCreate/TaskUpdate, то есть UI может
    приоритет показать, но не может сохранить. Теперь обе стороны на месте.
    """
    assert "priority" in schemas.TaskResponse.model_fields
    assert "priority" in schemas.TaskCreate.model_fields
    assert "priority" in schemas.TaskUpdate.model_fields
    # дефолт создания совпадает с дефолтом колонки в models.py (0)
    assert schemas.TaskCreate.model_fields["priority"].default == 0


# ---------------------------------------------------------------------------
# 2. Паритет моделей и фактического DDL боевой БД
# ---------------------------------------------------------------------------

# Задокументированный дрейф «боевой DDL ↔ models.py».
#
# 2026-09-12 (Фаза 2, дефект 14) — дрейф закрыт: nakladnye.amount_no_vat
# добавлена в models.py и в схемы. Список оставлен пустым, а не удалён:
# test_model_columns_match_prod_ddl сверяет с ним фактическое расхождение,
# и новый дрейф (колонку добавили миграцией на сервере и забыли отразить
# в моделях) обязан уронить прогон. Именно так amount_no_vat и появилась.
KNOWN_DDL_DRIFT = {}


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
    files = [*list((ROOT / "routers").glob("*.py")), ROOT / "main.py"]
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
        problems.extend(
            f"{module}: импортирован как {local}, но не зарегистрирован"
            for local in names
            if not any(m == local for m, _ in registered)
        )

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


# ---------------------------------------------------------------------------
# 4. Закрытые пробелы работают насквозь (дефект 13, починен 2026-09-12)
# ---------------------------------------------------------------------------
#
# Метатесты выше доказывают только наличие поля в схеме. Этого мало: роутеры
# пишут поля по-разному — где-то общим setattr по model_dump(exclude_unset=True),
# а где-то явными ветками `if 'field' in model_fields_set`. Поле в схеме без
# ветки в роутере дало бы ровно тот же тихий дефект: 200 и никаких изменений.
# Поэтому каждое возвращённое поле проверено запросом к API и чтением из базы.


def _reload(db):
    db.expire_all()


def test_card_owner_id_is_reassignable(client, manager, db, make_card, make_user):
    new_owner, _ = make_user("manager", username="new_owner")
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    assert card.owner_id is None

    r = client.patch(f"/cards/{card.id}", headers=h, json={"owner_id": new_owner.id})
    assert r.status_code == 200, r.text
    _reload(db)
    assert db.get(models.Card, card.id).owner_id == new_owner.id

    # чужой id — 400, а не IntegrityError/500: PRAGMA foreign_keys=ON
    r2 = client.patch(f"/cards/{card.id}", headers=h, json={"owner_id": 999999})
    assert r2.status_code == 400, r2.text

    # null — штатное значение: у FK ondelete="SET NULL", auth_router.remove_user
    # обнуляет owner_id карточек удаляемого пользователя
    r3 = client.patch(f"/cards/{card.id}", headers=h, json={"owner_id": None})
    assert r3.status_code == 200, r3.text
    _reload(db)
    assert db.get(models.Card, card.id).owner_id is None


def test_card_owner_change_is_logged(client, manager, db, make_card, make_user):
    """Переназначение владельца видно в ленте — как смена клиента или магазина."""
    new_owner, _ = make_user("manager", username="owner2")
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)

    assert client.patch(f"/cards/{card.id}", headers=h,
                        json={"owner_id": new_owner.id}).status_code == 200
    _reload(db)
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id).all()
    assert any("Ответственный" in (l.details or "") for l in logs), \
        f"смена владельца не попала в ленту: {[l.details for l in logs]}"


def test_task_priority_is_settable_on_create_and_update(client, manager, db):
    _, h = manager
    r = client.post("/tasks", headers=h, json={"title": "Задача", "priority": 3})
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == 3
    task_id = r.json()["id"]

    r2 = client.patch(f"/tasks/{task_id}", headers=h, json={"priority": 1})
    assert r2.status_code == 200, r2.text
    assert r2.json()["priority"] == 1

    _reload(db)
    assert db.get(models.Task, task_id).priority == 1


def test_task_priority_defaults_to_zero(client, manager, db):
    """Без priority в запросе задача создаётся с нулём — дефолт колонки.

    TaskResponse объявляет priority: int (не Optional), поэтому None в колонке
    уронил бы сериализацию ответа.
    """
    _, h = manager
    r = client.post("/tasks", headers=h, json={"title": "Задача"})
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == 0
    _reload(db)
    assert db.get(models.Task, r.json()["id"]).priority == 0


def test_transaction_date_is_editable(client, manager, db, make_card, make_transaction):
    """Дата оплаты правится; день меняется, время суток и формат — нет.

    js/payments.js правит дату инлайн-редактором и шлёт {"date": "YYYY-MM-DD"}.
    Время исходной записи сохраняется нарочно: реестр сортируется по date,
    и скачок времени внутри дня переставлял бы строки.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    tx = make_transaction(card, amount=100.0)
    before = tx.date

    r = client.patch(f"/payments/transactions/{tx.id}", headers=h,
                     json={"date": "2026-01-05"})
    assert r.status_code == 200, r.text

    _reload(db)
    fresh = db.get(models.Transaction, tx.id)
    assert fresh.date.date().isoformat() == "2026-01-05"
    assert (fresh.date.hour, fresh.date.minute) == (before.hour, before.minute)
    # смешение naive/aware в одной колонке ломает сортировку строковым
    # сравнением — ровно это чинила миграция 0003
    assert (fresh.date.tzinfo is None) == (before.tzinfo is None)


def test_transaction_date_rejects_garbage_with_400(client, manager, db,
                                                    make_card, make_transaction):
    """Поле date стало достижимым, поэтому разбор защищён: 400, а не 500."""
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    tx = make_transaction(card, amount=100.0)

    r = client.patch(f"/payments/transactions/{tx.id}", headers=h,
                     json={"date": "пятого января"})
    assert r.status_code == 400, r.text


def test_transaction_company_name_and_secondary_check_are_editable(
        client, manager, db, make_card, make_transaction):
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    tx = make_transaction(card, amount=100.0)

    r = client.patch(f"/payments/transactions/{tx.id}", headers=h,
                     json={"company_name": "Другая подпись",
                           "is_secondary_check": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # is_secondary_check добавлен и в ответ: записываемое, но невидимое
    # значение фронт не смог бы ни показать, ни корректно переключить
    assert body["company_name"] == "Другая подпись"
    assert body["is_secondary_check"] is True

    _reload(db)
    fresh = db.get(models.Transaction, tx.id)
    assert fresh.company_name == "Другая подпись"
    assert bool(fresh.is_secondary_check) is True


def test_transaction_company_name_is_still_overwritten_by_card_title(
        client, manager, db, make_card, make_transaction):
    """ОГРАНИЧЕНИЕ ручной подписи — зафиксировано, а не починено.

    card_details_router.update_card при смене названия сделки перезаписывает
    company_name ВО ВСЕХ её транзакциях: денормализация так задумана
    (PLAN_MODERNIZATION.md, Фаза 2 п.1 — синхронизация company_name сохраняется,
    в отличие от сумм). Поэтому ручная правка подписи держится только до
    следующего переименования сделки. Менять это — продуктовое решение.
    """
    _, h = manager
    card = make_card(title="Сделка", total_amount=100.0)
    tx = make_transaction(card, amount=100.0)

    assert client.patch(f"/payments/transactions/{tx.id}", headers=h,
                        json={"company_name": "Ручная подпись"}).status_code == 200
    assert client.patch(f"/cards/{card.id}", headers=h,
                        json={"title": "Сделка (уточнено)"}).status_code == 200

    _reload(db)
    assert db.get(models.Transaction, tx.id).company_name == "Сделка (уточнено)"


# ---------------------------------------------------------------------------
# 5. Каждое поле *Update-схемы обязано доходить до хендлера (2026-09-23)
# ---------------------------------------------------------------------------
#
# Раздел 1 доказывает только наличие поля в СХЕМЕ. sender_email (пункт 5 плана
# v2) показал, что этого мало: поле было и в CardBase, и в CardUpdate, колонка
# в models.py есть, в базе 741 карточка с адресом — но update_card его не читал
# вообще. PATCH отвечал 200 и молча ничего не менял, а после перезагрузки поля
# фронт откатывал значение. Тот же класс тихого расхождения контракта и
# реализации, что чинили в 3d109d6 и Фазе 2, только с другой стороны.
#
# Метод — ast по всем @router.patch/@router.put: для каждого поля схемы ищем
# доказательство, что хендлер его применяет. Три штатных способа записи в этом
# коде (соседи по update_card):
#   1) явная ветка `if '<поле>' in <параметр>.model_fields_set:`;
#   2) чтение атрибута `<параметр>.<поле>` (title/total_amount в update_card,
#      поля в tasks_router — там `is not None` вместо model_fields_set);
#   3) blanket `for k, v in <параметр>.model_dump(exclude_unset=True).items():
#      setattr(obj, k, v)` — клиенты, поставщики, справочники, накладные,
#      транзакции, чек-листы.
# Просто «роутер упомянул поле» — слишком слабое утверждение, поэтому два вида
# упоминаний доказательством НЕ считаются:
#   - getattr(<параметр>, '<поле>') — так поле читают только как переключатель
#     поведения (clear_client), в запись оно не идёт;
#   - чтение внутри `if <проверка>: raise` — поле сверяют, чтобы отвергнуть
#     запрос (PasswordChange.old_password), а не чтобы сохранить.
# Список «прочитано, но не записано» — в UPDATE_NOT_PERSISTED, с причиной на
# каждое поле: рост списка молча означает новый пробел в контракте.

# Поля, которые хендлер заведомо НЕ переносит в запись, с причиной.
UPDATE_NOT_PERSISTED = {
    ("CardUpdate", "clear_client"):
        "не колонка, а управляющий флаг PATCH /cards/{id}: client_id=null "
        "обнуляет клиента только вместе с clear_client=true (защита A7)",
    ("PasswordChange", "old_password"):
        "не пишется в запись: сверяется с текущим хэшем в verify_password и "
        "только отвергает запрос при несовпадении (PUT /auth/me/password)",
}

# Поля blanket-хендлеров, которых нет в колонках модели: setattr по такому имени
# создал бы обычный python-атрибут — данные потерялись бы молча.
BLANKET_NOT_COLUMNS = {
    ("NakladnayaUpdate", "products"):
        "извлекается из dump через pop() и сериализуется в products_json",
}


def _is_patch_route(dec):
    return (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in ("patch", "put"))


def _module_basemodel_fields(tree):
    """Поля pydantic-моделей, объявленных внутри роутера (webhooks, workflows)."""
    out = {}
    for n in tree.body:
        if not isinstance(n, ast.ClassDef):
            continue
        bases = {(b.id if isinstance(b, ast.Name) else getattr(b, "attr", ""))
                 for b in n.bases}
        if "BaseModel" not in bases:
            continue
        out[n.name] = [st.target.id for st in n.body
                       if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)]
    return out


def _body_param(fn, local_models):
    """(имя параметра, имя схемы, список полей) для pydantic-тела хендлера."""
    for a in fn.args.args:
        if a.annotation is None:
            continue
        ann = ast.unparse(a.annotation)
        if ann.startswith("schemas."):
            name = ann.split(".", 1)[1]
            sc = getattr(schemas, name, None)
            if sc is not None and hasattr(sc, "model_fields"):
                return a.arg, name, list(sc.model_fields)
        if ann in local_models:
            return a.arg, ann, local_models[ann]
    return None, None, None


def _handler_model(fn):
    """models.X из первого db.query(models.X) — модель, в которую пишет хендлер."""
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "query" and node.args):
            arg = ast.unparse(node.args[0])
            if arg.startswith("models."):
                return arg.split(".", 1)[1]
    return None


def _guard_ranges(fn):
    """Строки «поле читают только чтобы отвергнуть запрос»: `if <проверка>: raise`.

    Чтение поля внутри такой ветки применением не считается — иначе проверка
    формата, которая поле никуда не записывает, выглядела бы как его поддержка.
    Тело If при этом должен составлять именно Raise (без присваиваний и
    вложенных веток): иначе под запрет попадёт и нормальная ветка применения.
    """
    ranges = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise):
            ranges.append((node.lineno, node.end_lineno))
        elif isinstance(node, ast.If) and any(isinstance(s, ast.Raise) for s in node.body):
            ranges.append((node.lineno, node.end_lineno))
    return ranges


def _in_guard(line, ranges):
    return any(start <= line <= end for start, end in ranges)


def _applied_evidence(fn, param):
    """(множество применённых полей, строка blanket-цикла или None)."""
    applied = set()
    dump_vars = set()
    guards = _guard_ranges(fn)

    def mark(line, field):
        if not _in_guard(line, guards):
            applied.add(field)

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            # <var> = <param>.model_dump(exclude_unset=True) — в т.ч. внутри IfExp
            for call in ast.walk(node.value):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("model_dump", "dict")
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == param
                        and any(k.arg in ("exclude_unset", "exclude_none")
                                for k in call.keywords)):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            dump_vars.add(t.id)
        # 1) явная ветка по model_fields_set
        if (isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops)
                and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                and "model_fields_set" in ast.unparse(node.comparators[0])
                and param in ast.unparse(node.comparators[0])):
            mark(node.lineno, node.left.value)
        # 2) чтение атрибута тела
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == param):
            mark(node.lineno, node.attr)
        # 3) разбор dump-словаря по имени поля
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id in dump_vars
                and node.args and isinstance(node.args[0], ast.Constant)):
            mark(node.lineno, node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in dump_vars and isinstance(node.slice, ast.Constant)):
            mark(node.lineno, node.slice.value)
    blanket = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Call):
            continue
        if not (isinstance(node.iter.func, ast.Attribute) and node.iter.func.attr == "items"):
            continue
        src = ast.unparse(node.iter)
        if param not in src and not any(v in src for v in dump_vars):
            continue
        if any(isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
               and s.func.id == "setattr" for s in ast.walk(node)):
            blanket = f"{param} -> setattr, строка {node.iter.lineno}"
    return applied, blanket


def _patch_handlers():
    handlers = []
    for path in sorted((ROOT / "routers").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        local_models = _module_basemodel_fields(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_patch_route(d) for d in node.decorator_list):
                continue
            param, schema_name, fields = _body_param(node, local_models)
            if not param:
                continue
            applied, blanket = _applied_evidence(node, param)
            handlers.append({
                "file": path.name, "fn": node.name, "param": param,
                "schema": schema_name, "fields": fields,
                "applied": applied, "blanket": blanket, "model": _handler_model(node),
            })
    return handlers


PATCH_HANDLERS = _patch_handlers()


def _update_fields_universe():
    pairs = set()
    for h in PATCH_HANDLERS:
        for f in h["fields"]:
            pairs.add((h["schema"], f))
    return sorted(pairs)


def _is_applied(handler, field):
    return bool(handler["blanket"]) or field in handler["applied"]


@pytest.mark.parametrize("schema_name,field", _update_fields_universe())
def test_update_schema_field_is_read_by_its_router(schema_name, field):
    """Поле есть в схеме PATCH-эндпоинта → его должен читать хотя бы один хендлер.

    Иначе PATCH отвечает 200 и молча ничего не меняет — ровно дефект
    sender_email в update_card.
    """
    handlers = [h for h in PATCH_HANDLERS if h["schema"] == schema_name]
    if any(_is_applied(h, field) for h in handlers):
        return
    assert (schema_name, field) in UPDATE_NOT_PERSISTED, (
        f"{schema_name}.{field}: поле заявлено в контракте, но ни один "
        f"PATCH/PUT-хендлер его не применяет "
        f"(схему принимают: {[h['file'] + '::' + h['fn'] for h in handlers]}). "
        f"Либо почините ветку применения (образец — 'sender_email' в "
        f"card_details_router.update_card), либо внесите поле в "
        f"UPDATE_NOT_PERSISTED с причиной."
    )


def test_update_not_persisted_list_stays_accurate():
    """Список исключений не должен ни расти молча, ни переживать починку."""
    universe = set(_update_fields_universe())
    stale = sorted(k for k in UPDATE_NOT_PERSISTED if k not in universe)
    assert not stale, f"исключения больше нет в контракте — уберите его: {stale}"
    fixed = sorted(k for k in UPDATE_NOT_PERSISTED
                   if any(_is_applied(h, k[1]) for h in PATCH_HANDLERS
                          if h["schema"] == k[0]))
    assert not fixed, f"поле начали применять, а исключение осталось: {fixed}"


def test_every_update_schema_with_patch_route_is_covered():
    """Ни одна схема тела PATCH/PUT не должна выпасть из проверки.

    Защита от дыры в самом гейте: если хендлер примет тело не как параметр с
    аннотацией `schemas.X` или локальной BaseModel, поле уйдёт из вселенной
    молча, и test_update_schema_field_is_read_by_its_router перестанет что-либо
    утверждать. Единственное разрешённое исключение — роут без тела вообще.
    """
    routes = []
    for path in sorted((ROOT / "routers").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(_is_patch_route(d) for d in node.decorator_list)):
                routes.append((path.name, node.name))
    covered = {(h["file"], h["fn"]) for h in PATCH_HANDLERS}
    # restore_card тела не принимает: PATCH /kanban/cards/{id}/restore без JSON
    without_body = {("kanban_router.py", "restore_card")}
    unaccounted = sorted(set(routes) - covered - without_body)
    assert not unaccounted, (
        f"PATCH/PUT-хендлеры без разбора полей схемы (проверка их не видит): "
        f"{unaccounted}")


def test_blanket_setattr_fields_exist_on_the_model():
    """Blanket `setattr(obj, k, v)` по полям схемы: имя обязано быть колонкой.

    SQLAlchemy не возражает против произвольного python-атрибута — правка
    несуществующей колонки выглядела бы успехом и пропала бы при commit.
    """
    problems = []
    for h in PATCH_HANDLERS:
        if not h["blanket"]:
            continue
        model = getattr(models, h["model"], None) if h["model"] else None
        if model is None:
            problems.append(f"{h['file']}::{h['fn']}: не определена модель хендлера")
            continue
        cols = {c.name for c in model.__table__.columns}
        for f in h["fields"]:
            if f not in cols and (h["schema"], f) not in BLANKET_NOT_COLUMNS:
                problems.append(f"{h['schema']}.{f} -> {model.__name__}: не колонка")
    assert not problems, f"blanket-хендлеры пишут поля, которых нет в модели: {problems}"


def test_blanket_exceptions_stay_accurate():
    universe = {(h["schema"], f) for h in PATCH_HANDLERS if h["blanket"] for f in h["fields"]}
    stale = sorted(k for k in BLANKET_NOT_COLUMNS if k not in universe)
    assert not stale, f"исключение blanket больше неактуально: {stale}"


# ---------------------------------------------------------------------------
# 6. Поля Update-схем насквозь: тело PATCH → колонка → ответ (2026-09-23)
# ---------------------------------------------------------------------------
#
# Статический разбор раздела 5 отвечает «хендлер поле читает». Это необходимое,
# но не достаточное условие: прочесть поле можно и только ради сообщения об
# ошибке. Поэтому для CardUpdate каждое поле дополнительно проверено запросом:
# PATCH с одним полем → значение легло в колонку → так же вернулось в GET. Здесь
# же закреплена семантика очистки (null) для TaskUpdate: статический гейт её не
# ловил в принципе — роутер поля читал, но null отбрасывал.
# ---------------------------------------------------------------------------


def _card_update_cases(db, owner_id):
    """поле CardUpdate -> (что прислать в PATCH, что должно лежать в колонке).

    ФК-значения (клиент, тег) создаются по месту: присылать id несуществующей
    записи нельзя — FK включён, и update_card честно отверг бы чужого клиента.
    """
    client = models.Client(name="Клиент из теста")
    tag = models.Tag(name="тег из теста")
    db.add_all([client, tag])
    db.commit()
    db.refresh(client)
    db.refresh(tag)
    return {
        "title": ("Сделка после правки", "Сделка после правки"),
        "description": ("проверка описания", "проверка описания"),
        "total_amount": (1500.0, 1500.0),
        "paid_amount": (100.0, 100.0),
        "payment_status": ("Частично", "Частично"),
        "payment_due_date": ("2026-05-05", date(2026, 5, 5)),
        "payment_terms": ("deferred", "deferred"),
        "store_location": ("Склад Север", "Склад Север"),
        "client_id": (client.id, client.id),
        "due_date": ("2026-06-01", date(2026, 6, 1)),
        "priority": (3, 3),
        "sender_email": ("klient@example.com", "klient@example.com"),
        "tag_ids": ([tag.id], [tag.id]),
        "owner_id": (owner_id, owner_id),
    }


CARD_UPDATE_NOT_PERSISTED = sorted(k[1] for k in UPDATE_NOT_PERSISTED if k[0] == "CardUpdate")


@pytest.mark.parametrize("field", sorted(set(schemas.CardUpdate.model_fields)
                                         - set(CARD_UPDATE_NOT_PERSISTED)))
def test_every_card_update_field_reaches_the_column(client, manager, db, make_card, field):
    """Каждое поле CardUpdate доезжает до колонки — страховка от класса багов.

    Убери ветку применения в update_card (или переименуй её) — и этот тест
    упадёт: присланное значение останется прежним. Так было со sender_email
    до фикса 23.09: 200 OK, колонка нетронута.
    """
    user, h = manager
    card = make_card(title="Сделка", total_amount=2000.0)
    payload_value, expected = _card_update_cases(db, user.id)[field]

    r = client.patch(f"/cards/{card.id}", headers=h, json={field: payload_value})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Card, card.id)
    actual = ([t.id for t in fresh.tags] if field == "tag_ids"
              else getattr(fresh, field))
    assert actual == expected, (
        f"PATCH {{\"{field}\": {payload_value!r}}} → 200, но в колонке {actual!r}, "
        f"ожидалось {expected!r}: поле заявлено в CardUpdate и молча игнорируется")

    # то же значение обязано вернуться в GET — иначе фронт не сможет его показать
    g = client.get(f"/kanban/cards/{card.id}", headers=h)
    assert g.status_code == 200, g.text
    # в CardResponse теги лежат в `tags` (в CardBase их нет), остальные поля
    # называются так же, как в схеме
    body = g.json()["tags" if field == "tag_ids" else field]
    if field == "tag_ids":
        assert [t["id"] for t in body] == expected
    elif hasattr(expected, "isoformat"):     # date сериализуется в ISO-строку
        assert body == expected.isoformat()
    else:
        assert body == expected


def test_card_update_columns_cover_the_whole_schema():
    """Таблица кейсов не должна отставать от схемы: новое поле обязано быть проверено."""
    fields = set(schemas.CardUpdate.model_fields)
    checked = {"title", "description", "total_amount", "paid_amount", "payment_status",
               "payment_due_date", "payment_terms", "store_location", "client_id",
               "due_date", "priority", "sender_email", "tag_ids", "owner_id"}
    missing = sorted(fields - checked - set(CARD_UPDATE_NOT_PERSISTED))
    extra = sorted(checked - fields)
    assert not missing and not extra, (
        f"карточка полей CardUpdate разошлась со схемой: нет кейсов {missing}, "
        f"лишние {extra}")


# --- конкретное поведение sender_email (пункт 5 плана v2, 23.09) -----------

def test_card_sender_email_is_saved_and_visible(client, manager, db, make_card):
    """Адрес из «Почты отправителя» переживает перезагрузку карточки."""
    _, h = manager
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"sender_email": "Zakazchik@Example.COM"})
    assert r.status_code == 200, r.text
    assert r.json()["sender_email"] == "Zakazchik@Example.COM"

    db.expire_all()
    assert db.get(models.Card, card.id).sender_email == "Zakazchik@Example.COM"
    assert client.get(f"/kanban/cards/{card.id}", headers=h).json()["sender_email"] \
        == "Zakazchik@Example.COM"


def test_card_sender_email_is_editable_by_manager(client, manager, db, make_card):
    """Поле правится под manager: это операционная ручка карточки, не админская."""
    _, h = manager
    card = make_card(title="Сделка", sender_email="staryj@example.com")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"sender_email": "novyj@example.com"})
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.get(models.Card, card.id).sender_email == "novyj@example.com"

    # и без авторизации ручка закрыта как и раньше
    assert client.patch(f"/cards/{card.id}",
                        json={"sender_email": "chuzhoj@example.com"}).status_code == 401


@pytest.mark.parametrize("value", ["", "   ", None])
def test_card_sender_email_blank_clears_the_address(client, manager, db, make_card, value):
    """«Адреса нет» — это NULL, а не пустая строка: колонка nullable.

    Фронт при очистке шлёт null; пустая строка и пробелы приходят из ввода,
    который стёрли, но пробелы в field.type='email' оставить можно.
    """
    _, h = manager
    card = make_card(title="Сделка", sender_email="klient@example.com")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"sender_email": value})
    assert r.status_code == 200, r.text
    assert r.json()["sender_email"] is None

    db.expire_all()
    fresh = db.get(models.Card, card.id)
    assert fresh.sender_email is None
    # distinct от '' — иначе «не указан» и «пустая строка» неразличимы
    assert fresh.sender_email != ""


def test_card_sender_email_is_trimmed(client, manager, db, make_card):
    _, h = manager
    card = make_card(title="Сделка")

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"sender_email": "  klient@example.com  "})
    assert r.status_code == 200, r.text
    assert r.json()["sender_email"] == "klient@example.com"


def test_card_sender_email_absent_field_is_not_touched(client, manager, db, make_card):
    """Без ключа в теле значение не меняется: решает model_fields_set.

    Полный ответ на этот пункт — паттерн «прислали только sender_email»; если
    сервер начал бы обнулять поля по умолчанию, соседние сохранение в карточке
    затирало бы адрес.
    """
    _, h = manager
    card = make_card(title="Сделка", sender_email="klient@example.com")

    r = client.patch(f"/cards/{card.id}", headers=h, json={"priority": 2})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Card, card.id)
    assert fresh.sender_email == "klient@example.com"
    assert fresh.priority == 2


def test_card_sender_email_change_is_logged(client, manager, db, make_card):
    """Смена адреса видна в ленте: по нему «История» подбирает письма сделки."""
    _, h = manager
    card = make_card(title="Сделка", sender_email="staryj@example.com")

    assert client.patch(f"/cards/{card.id}", headers=h,
                        json={"sender_email": "novyj@example.com"}).status_code == 200
    _reload(db)
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card.id).all()
    assert any("Почта отправителя" in (l.details or "") for l in logs), \
        f"смена адреса не попала в ленту: {[l.details for l in logs]}"


# --- очистка полей задачи через null (пункт 6.1, 23.09) --------------------
#
# До правки update_task гейтил каждое поле `if data.<поле> is not None`, то
# есть null в теле читался как «поле не прислали». Legacy-форма шлёт null
# именно когда пользователь стёр значение (site/js/tasks.js:498-520) и получала
# 200 без изменений — снять срок, ответственного или привязку к сделке было
# невозможно. Теперь гейт `'поле' in data.model_fields_set`, как в
# card_details_router.update_card; тесты ниже падают при возврате к is not None.


def _full_task(db, creator, assignee, card, client, due_date=None):
    """Задача, заполненная по всем восьми полям TaskUpdate, со снимками названий.

    Автор — creator, а не assignee: PATCH виден и тому, и другому
    (_get_visible_task_or_404), а тесты снимают ответственного.
    """
    task = models.Task(
        title="Задача", status="todo", description="Было описание",
        due_date=due_date or datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        priority=3, assignee_id=assignee.id, creator_id=creator.id,
        card_id=card.id, client_id=client.id,
        card_title_snapshot=card.title, client_name_snapshot=client.name,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_task_update_null_clears_the_fields(client, manager, db, make_card, make_user):
    """PATCH /tasks/{id} с null очищает колонку — страховка от возврата гейта.

    Было (до 23.09, пункт 6.1): update_task гейтил каждое поле
    `if data.<поле> is not None`, то есть null в теле читался как «поле не
    прислали». Legacy-форма шлёт null именно когда пользователь стёр значение
    (site/js/tasks.js:498-520) и получала 200 без изменений — снять срок,
    ответственного или привязку к сделке было невозможно. Теперь гейт —
    `'поле' in data.model_fields_set`, как в card_details_router.update_card.

    priority — единственное поле из списка, где «пусто» не NULL, а 0:
    TaskResponse объявляет priority: int (тот же приём в create_task).
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)

    r = client.patch(f"/tasks/{task.id}", headers=h, json={
        "description": None, "due_date": None, "priority": None,
        "assignee_id": None})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.description is None, "description: null не очистил колонку"
    assert fresh.due_date is None, "due_date: null не очистил колонку"
    assert fresh.priority == 0, "priority: null обязан сброситься в 0, а не в NULL"
    assert fresh.assignee_id is None, "assignee_id: null не снял ответственного"
    # title и status не очищаются — они не nullable, см. отдельный тест
    assert fresh.title == "Задача"
    assert fresh.status == "todo"


def test_task_update_absent_keys_leave_the_values(client, manager, db, make_card, make_user):
    """Отсутствие ключа в теле — не «очистить»: model_fields_set решает.

    Это вторая половина того же контракта: если перевести гейты на «поле
    пришло» слишком ретиво, любой частичный PATCH (legacy меняет только
    статус: site/js/tasks.js:289) затёр бы остальные семь полей.
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_absent")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)
    before = {f: getattr(task, f) for f in
              ("title", "description", "status", "due_date", "priority",
               "assignee_id", "card_id", "client_id")}

    r = client.patch(f"/tasks/{task.id}", headers=h, json={"status": "in_work"})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.status == "in_work"
    for field, value in before.items():
        if field == "status":
            continue
        assert getattr(fresh, field) == value, \
            f"{field} затёрт PATCH-ем, который его не присылал"


def test_task_update_applies_every_field(client, manager, db, make_card, make_user):
    """Значение каждого из восьми полей TaskUpdate доезжает до колонки."""
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_apply")
    other, _ = make_user("manager", username="task_other_creator")
    card = make_card(title="Сделка задачи")
    card2 = make_card(title="Другая сделка")
    cl = models.Client(name="Клиент задачи")
    cl2 = models.Client(name="Другой клиент")
    db.add_all([cl, cl2])
    db.commit()
    task = _full_task(db, user, executor, card, cl)

    r = client.patch(f"/tasks/{task.id}", headers=h, json={
        "title": "Переименовали",
        "description": "Новое описание",
        "status": "done",
        "due_date": "2026-12-31T09:30:00",
        "priority": 1,
        "assignee_id": other.id,
        "card_id": card2.id,
        "client_id": cl2.id,
    })
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.title == "Переименовали"
    assert fresh.description == "Новое описание"
    assert fresh.status == "done"
    assert fresh.due_date == datetime(2026, 12, 31, 9, 30)
    assert fresh.priority == 1
    assert fresh.assignee_id == other.id
    assert fresh.card_id == card2.id
    assert fresh.client_id == cl2.id
    # снимки названий обязаны пойти за новыми привязками
    assert fresh.card_title_snapshot == "Другая сделка"
    assert fresh.client_name_snapshot == "Другой клиент"


def test_task_update_legacy_full_payload_clears_only_what_user_emptied(
        client, manager, db, make_card, make_user):
    """Форма legacy шлёт все шесть полей сразу — затирания не должно быть.

    Ровно этот запрос строит site/js/tasks.js:498-520: title/description/
    assignee_id/due_date всегда присутствуют в теле, а card_id/client_id
    доезжают исходными, если текст связи не трогали (ветка 510-514). Здесь
    пользователь стёр только описание: остальные значения обязаны пережить
    правку, иначе перевод гейтов на model_fields_set превратился бы в
    массовое молчаливое очистилище.
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_legacy")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)
    due_iso = task.due_date.isoformat()

    r = client.patch(f"/tasks/{task.id}", headers=h, json={
        "title": "Переименовали",
        "description": None,                  # поле стёрли в форме
        "assignee_id": executor.id,           # echo текущего значения
        "due_date": due_iso,                  # echo текущего значения
        "card_id": card.id,                   # текст связи не меняли
        "client_id": cl.id,
    })
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.title == "Переименовали"
    assert fresh.description is None
    assert fresh.assignee_id == executor.id, "ответственный стёрся echo-значением"
    assert fresh.card_id == card.id, "привязка к сделке стёрлась echo-значением"
    assert fresh.client_id == cl.id, "привязка к клиенту стёрлась echo-значением"
    assert fresh.card_title_snapshot == "Сделка задачи"
    assert fresh.client_name_snapshot == "Клиент задачи"
    assert fresh.due_date is not None, "срок стёрся echo-значением"


def test_task_update_broken_link_is_rejected_without_touching_the_task(
        client, manager, db, make_card, make_user):
    """Несуществующая привязка — 404 от _validate_link, и запись не меняется.

    После перевода гейта на model_fields_set эта проверка стала достижимее:
    карточка могла быть удалена между загрузкой формы и сохранением.
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_fk")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)

    r = client.patch(f"/tasks/{task.id}", headers=h, json={"card_id": 999999})
    assert r.status_code == 404, r.text
    assert "не найдена" in r.json()["detail"], r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.card_id == card.id, "отклонённый PATCH уронил рабочую привязку"
    assert fresh.card_title_snapshot == "Сделка задачи"


def test_task_update_null_on_not_null_field_is_400(client, manager, db, make_card, make_user):
    """null в NOT NULL / доменное поле — 400 с русским текстом, а не 500.

    title — NOT NULL и в модели, и в бою (prod_schema.sql:337); status
    формально nullable, но вне TASK_STATUSES задача выпадает из всех фильтров
    списка, а NULL ломает TaskResponse (status: str).
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_nn")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)

    for field, hint in (("title", "название"), ("status", "статус")):
        r = client.patch(f"/tasks/{task.id}", headers=h, json={field: None})
        assert r.status_code == 400, f"{field}: {r.status_code} {r.text}"
        assert hint in r.json()["detail"].lower(), f"{field}: {r.text}"
        # пустая строка — тот же отказ
        r2 = client.patch(f"/tasks/{task.id}", headers=h, json={field: "   "})
        assert r2.status_code == 400, f"{field} пробелами: {r2.text}"

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.title == "Задача" and fresh.status == "todo", \
        "отклонённый PATCH оставил след в записи"


def test_task_update_clearing_links_resets_name_snapshots(client, manager, db, make_card, make_user):
    """Снятие привязки обнуляет денормализованные снимки названий.

    Иначе в задаче остаётся card_title_snapshot от уже несуществующей связи —
    ровно тот класс мусора, что чинили для накладных (дефект 8, Фаза 2).
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_snap")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl)
    assert client.patch(f"/tasks/{task.id}", headers=h,
                        json={"card_id": card.id}).status_code == 200
    db.expire_all()
    assert db.get(models.Task, task.id).card_title_snapshot == "Сделка задачи"

    r = client.patch(f"/tasks/{task.id}", headers=h,
                     json={"card_id": None, "client_id": None})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Task, task.id)
    assert fresh.card_id is None and fresh.client_id is None
    assert fresh.card_title_snapshot is None, \
        f"снимок сделки пережил снятие привязки: {fresh.card_title_snapshot!r}"
    assert fresh.client_name_snapshot is None, \
        f"снимок клиента пережил снятие привязки: {fresh.client_name_snapshot!r}"
    # и в ответе снятая связь не должна показывать старое название
    body = r.json()
    assert body["card_title"] is None and body["client_name"] is None, body


def test_task_update_cleared_deadline_notifies_and_stops_being_overdue(
        client, manager, cron_headers, db, make_card, make_user):
    """Снятый срок: уведомление пишется, cron-просрочка больше его не видит.

    Ветка с details="срок снят" в update_task была написана под очистку, но до
    перевода гейта на model_fields_set была недостижима. notifications_router
    фильтрует просрочки как `due_date != None AND due_date < now`
    (notifications_router.py:144-147), поэтому NULL из расчёта выпадает —
    висящих уведомлений не появляется и старых не прибавляется.
    """
    user, h = manager
    executor, _ = make_user("manager", username="task_executor_overdue")
    card = make_card(title="Сделка задачи")
    cl = models.Client(name="Клиент задачи")
    db.add(cl)
    db.commit()
    task = _full_task(db, user, executor, card, cl,
                      due_date=datetime(2020, 1, 2, 3, 4))

    assert client.post("/notifications/sync-overdue", headers=cron_headers).status_code == 200
    overdue_before = db.query(models.Notification).filter(
        models.Notification.type == "task_overdue",
        models.Notification.entity_id == task.id).count()
    # по уведомлению автору и исполнителю: cron пишет каждому из пары
    assert overdue_before == 2, "задача с просроченным сроком не попала в cron-рассылку"

    r = client.patch(f"/tasks/{task.id}", headers=h, json={"due_date": None})
    assert r.status_code == 200, r.text
    cleared_note = db.query(models.Notification).filter(
        models.Notification.type == "task_due",
        models.Notification.entity_id == task.id).all()
    assert any(n.details == "срок снят" for n in cleared_note), \
        f"снятие срока не уведомилось: {[(n.user_id, n.details) for n in cleared_note]}"

    assert client.post("/notifications/sync-overdue", headers=cron_headers).status_code == 200
    overdue_after = db.query(models.Notification).filter(
        models.Notification.type == "task_overdue",
        models.Notification.entity_id == task.id).count()
    assert overdue_after == overdue_before, \
        "после очистки срока cron снова считает задачу просроченной"
    # и сам фильтр больше не находит задачу
    assert db.query(models.Task).filter(
        models.Task.status != "done",
        models.Task.due_date != None,          # noqa: E711
        models.Task.due_date < datetime.now(timezone.utc),
    ).count() == 0


def test_task_checklist_item_update_gates(client, manager):
    """PATCH /tasks/checklist/{id}: тот же гейт model_fields_set.

    is_done=null снимает отметку (колонка nullable, но ответ объявлен bool),
    пустой title — 400, потому что title там NOT NULL.
    """
    _, h = manager
    task = client.post("/tasks", headers=h, json={"title": "Задача"}).json()
    item = client.post(f"/tasks/{task['id']}/checklist", headers=h,
                       json={"title": "Пункт"}).json()

    assert client.patch(f"/tasks/checklist/{item['id']}", headers=h,
                        json={"is_done": True}).json()["is_done"] is True
    r = client.patch(f"/tasks/checklist/{item['id']}", headers=h,
                     json={"is_done": None})
    assert r.status_code == 200, r.text
    assert r.json()["is_done"] is False, "is_done=null должен снимать отметку"

    for bad in (None, "", "   "):
        r2 = client.patch(f"/tasks/checklist/{item['id']}", headers=h,
                          json={"title": bad})
        assert r2.status_code == 400, f"title={bad!r}: {r2.text}"
    assert client.patch(f"/tasks/checklist/{item['id']}", headers=h,
                        json={"title": "Новый пункт"}).json()["title"] == "Новый пункт"
    # отсутствие ключа ничего не трогает
    r3 = client.patch(f"/tasks/checklist/{item['id']}", headers=h, json={"is_done": True})
    assert r3.json()["title"] == "Новый пункт"


def test_card_sender_email_over_column_length_is_rejected(client, manager, make_card):
    """Длина ограничена колонкой String(255): SQLite за ней не следит.

    Формат при этом НЕ проверяется — исторические адреса из почтового парсера
    жёсткая валидация отвернула бы вместе с сохранением любого другого поля.
    """
    _, h = manager
    card = make_card(title="Сделка")
    limit = models.Card.__table__.columns["sender_email"].type.length

    r = client.patch(f"/cards/{card.id}", headers=h,
                     json={"sender_email": "a" * (limit + 1) + "@example.com"})
    assert r.status_code == 400, r.text

    edge = "b" * (limit - len("@e.co")) + "@e.co"
    ok = client.patch(f"/cards/{card.id}", headers=h, json={"sender_email": edge})
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["sender_email"]) == limit


# ---------------------------------------------------------------------------
# 7. Null-семантика админских PATCH и FK ответственного (пункт 15, 23.09)
# ---------------------------------------------------------------------------
#
# Раздел 5 ловит только «хендлер поле читает», а webhooks_router и
# workflows_router читали поля через `is not None` — то есть проходили гейт,
# будучи слепыми к очистке: null в теле означал «не прислали», и PATCH не мог
# ни снять подпись вебхука, ни убрать описание сценария. Здесь те же поля
# проверяются запросом, по образцу раздела 6. Заодно — ответственный в задачах:
# FK есть, а проверки существования не было ни на create, ни на patch.


@pytest.fixture
def public_dns(monkeypatch):
    """DNS без сети: _validate_webhook_url резолвит хост в рамке SSRF-проверки.

    Без подмены тесты вебхуков зависели бы от выхода в интернет (и от того,
    что example.com не окажется в чёрном списке).
    """
    def _lookup(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port or 80))]
    monkeypatch.setattr(socket, "getaddrinfo", _lookup)


def _webhook(client, h, **over):
    body = {"url": "https://example.com/hook", "secret": "podpis-123",
            "events": ["card.created"]}
    body.update(over)
    r = client.post("/webhooks/", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _workflow(client, h, **over):
    body = {"name": "Напоминание по оплате", "description": "за три дня до срока"}
    body.update(over)
    r = client.post("/workflows/", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --- вебхуки ---------------------------------------------------------------

def test_webhook_patch_absent_keys_leave_every_value(client, admin, db, public_dns):
    """Классический частичный PATCH клиента: без ключа secret подпись обязана остаться.

    Это ровно то, что присылает v2 (shell-v2-management.js:1140-1144 шлёт
    secret только заполненным): до перевода гейта на model_fields_set случай
    выглядел рабочим по другой причине, и проверка нужна, чтобы «очистка по
    null» не превратилась в «очистку по умолчанию».
    """
    _, h = admin
    wh_id = _webhook(client, h)

    r = client.patch(f"/webhooks/{wh_id}", headers=h, json={"events": ["card.updated"]})
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(models.Webhook, wh_id)
    assert fresh.url == "https://example.com/hook"
    assert fresh.secret == "podpis-123", "PATCH без secret стёр подпись"
    assert json.loads(fresh.events) == ["card.updated"]
    assert fresh.is_active is True


def test_webhook_patch_null_secret_clears_signature(client, admin, db, public_dns):
    """{"secret": null} — единственный жест «подписи нет»: колонка nullable."""
    _, h = admin
    wh_id = _webhook(client, h)

    r = client.patch(f"/webhooks/{wh_id}", headers=h, json={"secret": None})
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.get(models.Webhook, wh_id).secret is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_webhook_patch_empty_secret_is_rejected(client, admin, db, public_dns, blank):
    """Пустая строка не стирает подпись: её форма отдаёт, когда поле не трогали.

    До правки было наоборот: `if wh.secret is not None` пропускал "" и молча
    лишал получателя проверки X-Webhook-Signature — ошибка всплывала только на
    его стороне.
    """
    _, h = admin
    wh_id = _webhook(client, h)

    r = client.patch(f"/webhooks/{wh_id}", headers=h, json={"secret": blank})
    assert r.status_code == 400, r.text
    assert "null" in r.json()["detail"], r.text

    db.expire_all()
    assert db.get(models.Webhook, wh_id).secret == "podpis-123", \
        "отклонённый PATCH всё-таки тронул секрет"


@pytest.mark.parametrize("field", ["url", "events", "is_active"])
def test_webhook_patch_null_on_not_null_columns_is_400(client, admin, db, public_dns, field):
    """url/events NOT NULL, у флага нет третьего состояния — null отвергается, без следа."""
    _, h = admin
    wh_id = _webhook(client, h, events=["card.created", "card.updated"])

    r = client.patch(f"/webhooks/{wh_id}", headers=h, json={field: None})
    assert r.status_code == 400, f"{field}: {r.status_code} {r.text}"
    assert r.json()["detail"]

    db.expire_all()
    fresh = db.get(models.Webhook, wh_id)
    assert fresh.url == "https://example.com/hook"
    assert fresh.secret == "podpis-123"
    assert json.loads(fresh.events) == ["card.created", "card.updated"]
    assert fresh.is_active is True


@pytest.mark.parametrize("blank", ["", "   ", "не url"])
def test_webhook_patch_blank_or_broken_url_is_rejected(client, admin, db, public_dns, blank):
    _, h = admin
    wh_id = _webhook(client, h)
    r = client.patch(f"/webhooks/{wh_id}", headers=h, json={"url": blank})
    assert r.status_code == 400, r.text

    db.expire_all()
    assert db.get(models.Webhook, wh_id).url == "https://example.com/hook"


def test_webhook_patch_empty_events_is_allowed(client, admin, db, public_dns):
    """[] — «ни на что не подписан», это валидное состояние, а не ошибка."""
    _, h = admin
    wh_id = _webhook(client, h)
    assert client.patch(f"/webhooks/{wh_id}", headers=h,
                        json={"events": []}).status_code == 200
    db.expire_all()
    assert json.loads(db.get(models.Webhook, wh_id).events) == []


@pytest.mark.parametrize("value", [True, False])
def test_webhook_patch_is_active_bool_applies(client, admin, db, public_dns, value):
    _, h = admin
    wh_id = _webhook(client, h)
    assert client.patch(f"/webhooks/{wh_id}", headers=h,
                        json={"is_active": value}).status_code == 200
    db.expire_all()
    assert db.get(models.Webhook, wh_id).is_active is value


def test_webhook_patch_unknown_id_is_404(client, admin, public_dns):
    """404 важнее валидации URL: чужого id нет, и резолвить ради него хост незачем."""
    _, h = admin
    r = client.patch("/webhooks/999999", headers=h, json={"url": "http://127.0.0.1/hook"})
    assert r.status_code == 404, r.text


# --- сценарии автоматизаций ------------------------------------------------

def test_workflow_patch_absent_keys_leave_every_value(client, admin, db):
    _, h = admin
    wf_id = _workflow(client, h)

    assert client.patch(f"/workflows/{wf_id}", headers=h,
                        json={"name": "Памятка о сроке"}).status_code == 200
    db.expire_all()
    fresh = db.get(models.Workflow, wf_id)
    assert fresh.name == "Памятка о сроке"
    assert fresh.description == "за три дня до срока", "PATCH без description стёр описание"
    assert fresh.is_active is False


def test_workflow_patch_null_description_clears(client, admin, db):
    """description nullable: null (и пустая строка) очищают, а не no-op."""
    _, h = admin
    wf_id = _workflow(client, h)
    assert client.patch(f"/workflows/{wf_id}", headers=h,
                        json={"name": "Прежнее"}).status_code == 200

    r = client.patch(f"/workflows/{wf_id}", headers=h, json={"description": None})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(models.Workflow, wf_id).description is None

    assert client.patch(f"/workflows/{wf_id}", headers=h,
                        json={"description": "вернулось"}).status_code == 200
    assert client.patch(f"/workflows/{wf_id}", headers=h,
                        json={"description": "  "}).status_code == 200
    db.expire_all()
    assert db.get(models.Workflow, wf_id).description is None


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_workflow_patch_blank_name_is_rejected(client, admin, db, bad):
    """name — NOT NULL: null отвергается, запись остаётся прежней."""
    _, h = admin
    wf_id = _workflow(client, h)
    r = client.patch(f"/workflows/{wf_id}", headers=h, json={"name": bad})
    assert r.status_code == 400, f"{bad!r}: {r.status_code} {r.text}"
    assert "назван" in r.json()["detail"].lower(), r.text

    db.expire_all()
    assert db.get(models.Workflow, wf_id).name == "Напоминание по оплате"


def test_workflow_patch_null_is_active_is_rejected_without_trace(client, admin, db):
    _, h = admin
    wf_id = _workflow(client, h)
    r = client.patch(f"/workflows/{wf_id}", headers=h, json={"is_active": None})
    assert r.status_code == 400, r.text
    db.expire_all()
    w = db.get(models.Workflow, wf_id)
    assert w.name == "Напоминание по оплате"
    assert w.is_active is False
    assert w.description == "за три дня до срока"


def test_workflow_patch_is_active_bool_applies(client, admin, db):
    _, h = admin
    wf_id = _workflow(client, h)
    assert client.patch(f"/workflows/{wf_id}", headers=h,
                        json={"is_active": True}).status_code == 200
    db.expire_all()
    assert db.get(models.Workflow, wf_id).is_active is True


def test_workflow_patch_unknown_id_is_404(client, admin):
    _, h = admin
    assert client.patch("/workflows/999999", headers=h,
                        json={"name": "Нет такого"}).status_code == 404


# --- админские ручки закрыты для остальных --------------------------------

def test_webhook_and_workflow_patch_reject_manager_and_anonymous(client, admin, manager,
                                                                 public_dns):
    """403 для manager и 401 без токена на PATCH тех же роутов, где новая null-семантика.

    Гейт висит на всём роутере (dependencies=[Depends(require_admin())]), поэтому
    он проверяется и до разбора тела — важно, что он не разъехался с правкой.
    """
    _, ah = admin
    wh_id = _webhook(client, ah)
    wf_id = _workflow(client, ah)
    _, mh = manager

    for path, body in ((f"/webhooks/{wh_id}", {"events": ["card.created"]}),
                       (f"/workflows/{wf_id}", {"name": "Чужими руками"})):
        assert client.patch(path, headers=mh, json=body).status_code == 403, path
        assert client.patch(path, json=body).status_code == 401, path


# --- ответственный в задаче обязан существовать ----------------------------

def test_task_assignee_must_exist_on_create_and_update(client, manager, db, make_user):
    """Несуществующий assignee_id — 400, а не FK/500; null по-прежнему снимает.

    Тот же класс, что чинили для owner_id в update_card и для card_id/client_id
    в _validate_link: PRAGMA foreign_keys=ON (database.py:39) превращает чужой
    id в IntegrityError на flush. Семантику очистки (null = «ответственного
    нет») правка трогать не должна — закреплено отдельной веткой ниже.
    """
    _, h = manager
    executor, _ = make_user("manager", username="assignee_exists")

    bad = client.post("/tasks", headers=h, json={"title": "Задача", "assignee_id": 999999})
    assert bad.status_code == 400, bad.text
    assert "не найден" in bad.json()["detail"], bad.text
    assert db.query(models.Task).count() == 0, "отклонённый POST создал строку"

    created = client.post("/tasks", headers=h, json={"title": "Задача",
                                                     "assignee_id": executor.id})
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    assert created.json()["assignee_username"] == "assignee_exists"

    bad_patch = client.patch(f"/tasks/{task_id}", headers=h, json={"assignee_id": 999999})
    assert bad_patch.status_code == 400, bad_patch.text
    db.expire_all()
    assert db.get(models.Task, task_id).assignee_id == executor.id, \
        "отклонённый PATCH тронул ответственного"

    cleared = client.patch(f"/tasks/{task_id}", headers=h, json={"assignee_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assignee_id"] is None
    db.expire_all()
    assert db.get(models.Task, task_id).assignee_id is None, \
        "null перестал снимать ответственного — регресс пункта 6.1"


def test_task_assignee_validation_does_not_block_other_fields(client, manager, make_card, db):
    """Проверка ответственного не мешает соседним полям того же PATCH.

    400 обязан приходить ДО мутаций: иначе запрос «сменить срок и назначить
    несуществующего человека» оставил бы половину изменений.
    """
    _, h = manager
    card = make_card(title="Сделка")
    task_id = client.post("/tasks", headers=h, json={"title": "Задача",
                                                     "card_id": card.id}).json()["id"]

    r = client.patch(f"/tasks/{task_id}", headers=h,
                     json={"due_date": "2026-11-11T10:00:00", "assignee_id": 888888})
    assert r.status_code == 400, r.text

    db.expire_all()
    fresh = db.get(models.Task, task_id)
    assert fresh.due_date is None, "отклонённый PATCH записал срок"
    assert fresh.assignee_id is None


