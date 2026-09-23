"""Правка типа кастомного объекта и его поля (пункт 15 плана v2, 23.09).

Роли: админ и суперадмин проходят, остальные — 403, без токена — 401. Матрицу
«кто допускаен» заодно закрывает test_role_gates_admin.ADMIN_CASES (PATCH-кейсы
добавлены туда), а здесь проверяется само поведение: 404 на чужой id, 400 на
пустые/недопустимые значения, null = очистить / отсутствие ключа = не трогать,
и главное ограничение — тип поля нельзя менять, когда у поля есть значения.

Почему тип поля ограничен именно так: значения записей лежат НЕ в JSON-колонке,
а в типизированных столбцах custom_field_values (value_text / value_number /
value_boolean / value_date / value_json / value_relation), столбец выбирается по
field_type в момент записи. list_records читает их «первый непустой». Смена типа
у поля со значениями расщепила бы одно поле на два представления.
"""
import models
import pytest


# --- помощники -------------------------------------------------------------

def _make_type(client, h, name="equipment", label="Оборудование", icon=None):
    body = {"name": name, "label": label}
    if icon is not None:
        body["icon"] = icon
    r = client.post("/custom/objects", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_field(client, h, obj_id, name="power", label="Мощность",
                field_type="text", **kw):
    body = {"name": name, "label": label, "field_type": field_type}
    body.update(kw)
    r = client.post(f"/custom/objects/{obj_id}/fields", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def obj_id(client, admin):
    _, h = admin
    return _make_type(client, h)


@pytest.fixture
def field_id(client, admin, obj_id):
    _, h = admin
    return _make_field(client, h, obj_id)


# --- тип объекта: PATCH /custom/objects/{id} -------------------------------

def test_object_patch_changes_name_label_icon(client, admin, obj_id, db):
    _, h = admin
    r = client.patch(f"/custom/objects/{obj_id}", headers=h, json={
        "name": "equipment_v2", "label": "Оборудование склада", "icon": "wrench"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": obj_id, "name": "equipment_v2",
                        "label": "Оборудование склада", "icon": "wrench"}

    db.expire_all()
    obj = db.get(models.CustomObjectType, obj_id)
    assert (obj.name, obj.label, obj.icon) == ("equipment_v2", "Оборудование склада", "wrench")
    listed = client.get("/custom/objects", headers=h).json()
    assert [o for o in listed if o["id"] == obj_id][0]["name"] == "equipment_v2"


def test_object_patch_absent_keys_leave_values(client, admin, obj_id, db):
    _, h = admin
    r = client.patch(f"/custom/objects/{obj_id}", headers=h, json={"label": "Новое название"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "equipment"
    assert r.json()["icon"] is None

    db.expire_all()
    obj = db.get(models.CustomObjectType, obj_id)
    assert obj.name == "equipment"
    assert obj.label == "Новое название"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_object_patch_icon_blank_clears(client, admin, obj_id, db, blank):
    """Иконка nullable: null и пустая строка — одно и то же «иконки нет»."""
    _, h = admin
    assert client.patch(f"/custom/objects/{obj_id}", headers=h,
                        json={"icon": "wrench"}).status_code == 200
    r = client.patch(f"/custom/objects/{obj_id}", headers=h, json={"icon": blank})
    assert r.status_code == 200, r.text
    assert r.json()["icon"] is None

    db.expire_all()
    assert db.get(models.CustomObjectType, obj_id).icon is None


def test_object_patch_404_for_unknown_id(client, admin):
    _, h = admin
    r = client.patch("/custom/objects/999999", headers=h, json={"label": "Нет такого"})
    assert r.status_code == 404, r.text
    assert "не найден" in r.json()["detail"].lower()


@pytest.mark.parametrize("body", [
    {"name": None}, {"name": ""}, {"name": "   "},
    {"name": "Оборудование"},        # кириллица — не ключ в data записей
    {"name": "2equipment"},          # не с буквы
    {"name": "equipment power"},     # с пробелом
    {"label": None}, {"label": ""},
])
def test_object_patch_rejects_bad_values(client, admin, obj_id, db, body):
    _, h = admin
    r = client.patch(f"/custom/objects/{obj_id}", headers=h, json=body)
    assert r.status_code == 400, f"{body}: {r.status_code} {r.text}"
    assert r.json()["detail"], "ответ без объяснения по-русски"

    db.expire_all()
    obj = db.get(models.CustomObjectType, obj_id)
    assert (obj.name, obj.label) == ("equipment", "Оборудование"), \
        "отклонённая правка оставила след в записи"


def test_object_patch_rejects_duplicate_name(client, admin, obj_id):
    _, h = admin
    other = _make_type(client, h, name="tools", label="Инструмент")
    r = client.patch(f"/custom/objects/{other}", headers=h, json={"name": "equipment"})
    assert r.status_code == 400, r.text
    assert "уже существует" in r.json()["detail"]
    # имя своего же объекта меняется молча (коллизия с самим собой — не коллизия)
    assert client.patch(f"/custom/objects/{obj_id}", headers=h,
                        json={"name": "equipment"}).status_code == 200


def test_object_rename_moves_relation_targets(client, admin, obj_id, db):
    """CustomFieldDef.relation_target хранит ИМЯ типа, поэтому переименование
    обязано переставить и ссылки — иначе поле-связка укажет в несуществующий тип."""
    _, h = admin
    field = models.CustomFieldDef(object_type_id=obj_id, name="link", label="Связь",
                                  field_type="relation", relation_target="equipment")
    db.add(field)
    db.commit()
    field_id = field.id

    assert client.patch(f"/custom/objects/{obj_id}", headers=h,
                        json={"name": "equipment_v2"}).status_code == 200

    db.expire_all()
    assert db.get(models.CustomFieldDef, field_id).relation_target == "equipment_v2"


def test_object_patch_empty_body_is_noop(client, admin, obj_id):
    """Тело {} — валидный no-op: ключей нет, значит ничего не трогается."""
    _, h = admin
    r = client.patch(f"/custom/objects/{obj_id}", headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "equipment"


def test_object_create_validates_like_patch(client, admin):
    """POST и PATCH проверяют имя одинаково: иначе в базе осталась бы строка,
    которую PATCH не смог бы ни сохранить, ни починить."""
    _, h = admin
    for body in ({"name": "two words", "label": "X"},
                 {"name": "", "label": "X"},
                 {"name": "ok", "label": "   "}):
        r = client.post("/custom/objects", headers=h, json=body)
        assert r.status_code == 400, f"{body}: {r.status_code} {r.text}"
    assert client.get("/custom/objects", headers=h).json() == []


# --- поле: PATCH /custom/fields/{id} --------------------------------------

def test_field_patch_changes_label_required_position(client, admin, obj_id, field_id, db):
    _, h = admin
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={
        "label": "Мощность, кВт", "is_required": True, "position": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "Мощность, кВт"
    assert body["is_required"] is True
    assert body["position"] == 4
    assert body["name"] == "power" and body["field_type"] == "text", \
        "поля, которые не присылали, пропали из ответа"

    db.expire_all()
    fresh = db.get(models.CustomFieldDef, field_id)
    assert (fresh.label, bool(fresh.is_required), fresh.position) == ("Мощность, кВт", True, 4)


def test_field_patch_position_null_resets_to_zero(client, admin, field_id, db):
    """null в position = сброс на дефолт колонки (0), а не NULL: по position
    сортируется список полей, NULL уехал бы в начало."""
    _, h = admin
    assert client.patch(f"/custom/fields/{field_id}", headers=h,
                        json={"position": 7}).json()["position"] == 7
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={"position": None})
    assert r.status_code == 200, r.text
    assert r.json()["position"] == 0

    db.expire_all()
    assert db.get(models.CustomFieldDef, field_id).position == 0


def test_field_patch_renames_within_one_object(client, admin, obj_id, field_id, db):
    _, h = admin
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={"name": "watt"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "watt"

    db.expire_all()
    assert db.get(models.CustomFieldDef, field_id).name == "watt"
    # и ключ в data записей следом за именем: list_records отдаёт data по name
    assert client.post(f"/custom/objects/{obj_id}/records", headers=h,
                       json={"data": {"watt": "2 kW"}}).status_code == 200
    assert client.get(f"/custom/objects/{obj_id}/records", headers=h).json()[0]["data"] == {"watt": "2 kW"}


def test_field_patch_duplicate_name_in_object_is_400(client, admin, obj_id, db):
    _, h = admin
    first = _make_field(client, h, obj_id, name="power", label="Мощность")
    second = _make_field(client, h, obj_id, name="voltage", label="Напряжение")
    r = client.patch(f"/custom/fields/{second}", headers=h, json={"name": "power"})
    assert r.status_code == 400, r.text
    assert "уже существует" in r.json()["detail"]

    db.expire_all()
    assert db.get(models.CustomFieldDef, second).name == "voltage"
    assert db.get(models.CustomFieldDef, first).name == "power"


def test_field_patch_name_reused_in_other_object_is_allowed(client, admin, obj_id):
    _, h = admin
    other_type = _make_type(client, h, name="rooms", label="Помещения")
    mine = _make_field(client, h, obj_id, name="power", label="Мощность")
    theirs = _make_field(client, h, other_type, name="power", label="Мощность")
    assert mine != theirs
    assert client.patch(f"/custom/fields/{theirs}", headers=h,
                        json={"label": "Мощность лампы"}).status_code == 200


def test_field_patch_404_for_unknown_id(client, admin):
    _, h = admin
    r = client.patch("/custom/fields/999999", headers=h, json={"label": "Нет такого"})
    assert r.status_code == 404, r.text
    assert "не найдено" in r.json()["detail"].lower()


@pytest.mark.parametrize("body", [
    {"name": None}, {"name": ""}, {"name": "мощность"}, {"name": "_power"},
    {"label": None}, {"label": "  "},
    {"field_type": None}, {"field_type": ""}, {"field_type": "money"},
    {"is_required": None},
])
def test_field_patch_rejects_bad_values(client, admin, field_id, db, body):
    _, h = admin
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json=body)
    assert r.status_code == 400, f"{body}: {r.status_code} {r.text}"

    db.expire_all()
    fresh = db.get(models.CustomFieldDef, field_id)
    assert (fresh.name, fresh.label, fresh.field_type) == ("power", "Мощность", "text")


# --- смена типа поля -------------------------------------------------------

def test_field_type_change_allowed_while_empty(client, admin, obj_id, field_id, db):
    _, h = admin
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={"field_type": "number"})
    assert r.status_code == 200, r.text
    assert r.json()["field_type"] == "number"

    db.expire_all()
    assert db.get(models.CustomFieldDef, field_id).field_type == "number"


def test_field_type_change_blocked_when_values_exist(client, admin, obj_id, field_id, db):
    """Главное ограничение: тип не меняется, если у поля уже есть значения."""
    _, h = admin
    assert client.post(f"/custom/objects/{obj_id}/records", headers=h,
                       json={"data": {"power": "1500 Вт"}}).status_code == 200

    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={"field_type": "number"})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "тип" in detail and "значен" in detail, detail

    db.expire_all()
    assert db.get(models.CustomFieldDef, field_id).field_type == "text", \
        "отклонённая правка всё-таки сменила тип"
    # значение на месте и по-прежнему читается
    assert client.get(f"/custom/objects/{obj_id}/records", headers=h).json()[0]["data"] == {"power": "1500 Вт"}


def test_field_type_change_allowed_when_values_are_empty(client, admin, obj_id, field_id, db):
    """Строка значения со всеми пустыми столбцами типу не мешает."""
    _, h = admin
    assert client.post(f"/custom/objects/{obj_id}/records", headers=h,
                       json={"data": {"power": None}}).status_code == 200
    rows = db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.field_def_id == field_id).all()
    assert rows and all(v.value_text is None for v in rows), "ожидалась пустая строка значения"

    assert client.patch(f"/custom/fields/{field_id}", headers=h,
                        json={"field_type": "number"}).status_code == 200


def test_field_type_to_select_requires_options(client, admin, obj_id, field_id, db):
    _, h = admin
    r = client.patch(f"/custom/fields/{field_id}", headers=h, json={"field_type": "select"})
    assert r.status_code == 400, r.text
    assert "вариант" in r.json()["detail"]

    ok = client.patch(f"/custom/fields/{field_id}", headers=h, json={
        "field_type": "select",
        "options": [{"label": "новое", "value": "новое"},
                    {"label": "списано", "value": "списано"}]})
    assert ok.status_code == 200, ok.text
    assert [o["value"] for o in ok.json()["options"]] == ["новое", "списано"]

    db.expire_all()
    fresh = db.get(models.CustomFieldDef, field_id)
    assert fresh.field_type == "select" and fresh.options
    # варианты нельзя вычистить у поля, которое остаётся списком
    assert client.patch(f"/custom/fields/{field_id}", headers=h,
                        json={"options": None}).status_code == 400


def test_field_options_cleared_when_type_is_not_select(client, admin, obj_id, field_id, db):
    _, h = admin
    select_id = _make_field(client, h, obj_id, name="state", label="Состояние",
                            field_type="select",
                            options=[{"label": "новое", "value": "новое"}])
    assert client.patch(f"/custom/fields/{select_id}", headers=h,
                        json={"field_type": "text", "options": None}).status_code == 200
    db.expire_all()
    assert db.get(models.CustomFieldDef, select_id).options is None
    assert client.patch(f"/custom/fields/{field_id}", headers=h,
                        json={"options": None}).status_code == 200


def test_field_create_validates_like_patch(client, admin, obj_id):
    """Тот же набор проверок на POST: тип из палитры, имя — ключ, select — с вариантами."""
    _, h = admin
    for body in ({"name": "bad name", "label": "X", "field_type": "text"},
                 {"name": "ok", "label": "X", "field_type": "money"},
                 {"name": "ok", "label": "X", "field_type": "select"},
                 {"name": "ok", "label": "  ", "field_type": "text"}):
        r = client.post(f"/custom/objects/{obj_id}/fields", headers=h, json=body)
        assert r.status_code == 400, f"{body}: {r.status_code} {r.text}"
    dup = {"name": "power", "label": "Дубликат", "field_type": "text"}
    assert client.post(f"/custom/objects/{obj_id}/fields", headers=h, json=dup).status_code == 200
    assert client.post(f"/custom/objects/{obj_id}/fields", headers=h, json=dup).status_code == 400


# --- права ----------------------------------------------------------------

@pytest.mark.parametrize("role", ["manager", "warehouse", "documents"])
def test_patch_custom_type_and_field_denied_for_non_admin(client, make_user, obj_id, field_id, role):
    _, h = make_user(role)
    for path, body in ((f"/custom/objects/{obj_id}", {"label": "Х"}),
                       (f"/custom/fields/{field_id}", {"label": "Х"})):
        r = client.patch(path, headers=h, json=body)
        assert r.status_code == 403, f"{role} прошёл на PATCH {path}: {r.status_code} {r.text}"


@pytest.mark.parametrize("path", ["/custom/objects/1", "/custom/fields/1"])
def test_patch_custom_type_and_field_requires_auth(client, path):
    assert client.patch(path, json={"label": "Х"}).status_code == 401


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_patch_custom_type_and_field_allowed_for_admin_roles(client, make_user, obj_id, role):
    _, h = make_user(role)
    field = _make_field(client, h, obj_id)
    assert client.patch(f"/custom/objects/{obj_id}", headers=h,
                        json={"label": "Новое название"}).status_code == 200
    assert client.patch(f"/custom/fields/{field}", headers=h,
                        json={"position": 2}).status_code == 200


def test_custom_type_and_field_read_is_not_admin_only(client, manager, obj_id, field_id):
    """Чтение остаётся операционным: форма записей строит поля по GET /fields."""
    _, h = manager
    assert client.get("/custom/objects", headers=h).status_code == 200
    listed = client.get(f"/custom/objects/{obj_id}/fields", headers=h)
    assert listed.status_code == 200
    assert [f["id"] for f in listed.json()] == [field_id]
