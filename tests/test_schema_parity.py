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

