"""Изоляция кастомных объектов по tenant (аудит 26.09).

Раньше ручки сверяли `model.tenant_id == current_user.tenant_id` сырым, что
давало два дефекта:

* superadmin (`tenant_id is None`) получал `tenant_id IS NULL` в WHERE, то есть
  не видел ничего и при создании писал строку без tenant — а BEFORE INSERT
  триггер миграции 0018 отбивал такую вставку 500-м;
* пользователь с пустым tenant видел строки с `tenant_id IS NULL` вместо
  отказа.

Теперь выборки идут через `tenant_predicate`, а вставки — через
`writable_tenant_id`, который для пустого tenant даёт 403, а не 500.
"""
import pytest
from fastapi import HTTPException

import models
import routers.custom_objects_router as custom_objects_router
from tenant_policy import tenant_predicate


@pytest.fixture
def tenants(two_tenant_users):
    return two_tenant_users


def _make_type(client, h, name, label="Объект"):
    r = client.post("/custom/objects", headers=h, json={"name": name, "label": label})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_type_is_created_in_callers_tenant(client, tenants, db):
    obj_id = _make_type(client, tenants["first_headers"], "tenant_a_thing")

    obj = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.id == obj_id
    ).one()
    assert obj.tenant_id == tenants["first_tenant_id"]


def test_each_tenant_sees_only_its_own_types(client, tenants):
    _make_type(client, tenants["first_headers"], "only_a")
    _make_type(client, tenants["second_headers"], "only_b")

    for key, expected in (("first_headers", "only_a"), ("second_headers", "only_b")):
        names = [o["name"] for o in
                 client.get("/custom/objects", headers=tenants[key]).json()]
        assert expected in names
        other = "only_b" if expected == "only_a" else "only_a"
        assert other not in names


def test_foreign_type_is_404_for_patch_and_delete(client, tenants):
    obj_id = _make_type(client, tenants["first_headers"], "guarded")

    patch = client.patch(f"/custom/objects/{obj_id}", headers=tenants["second_headers"],
                         json={"label": "Взломано"})
    assert patch.status_code == 404, patch.text

    delete = client.delete(f"/custom/objects/{obj_id}", headers=tenants["second_headers"])
    assert delete.status_code == 404, delete.text


def test_foreign_field_is_404(client, tenants):
    obj_id = _make_type(client, tenants["first_headers"], "obj_with_field")
    created = client.post(f"/custom/objects/{obj_id}/fields",
                          headers=tenants["first_headers"],
                          json={"name": "field_a", "label": "Поле", "field_type": "text"})
    assert created.status_code == 200, created.text
    field_id = created.json()["id"]

    assert client.delete(f"/custom/fields/{field_id}",
                         headers=tenants["second_headers"]).status_code == 404
    assert client.patch(f"/custom/fields/{field_id}", headers=tenants["second_headers"],
                        json={"label": "Взломано"}).status_code == 404


def test_foreign_record_is_404(client, tenants):
    obj_id = _make_type(client, tenants["first_headers"], "obj_with_record")
    rec = client.post(f"/custom/objects/{obj_id}/records",
                      headers=tenants["first_headers"], json={"data": {}})
    assert rec.status_code == 200, rec.text
    rec_id = rec.json()["id"]

    assert client.delete(f"/custom/records/{rec_id}",
                         headers=tenants["second_headers"]).status_code == 404


def test_records_list_is_tenant_scoped(client, tenants):
    """Записи привязаны к tenant, а не только к id типа объекта."""
    a_obj = _make_type(client, tenants["first_headers"], "records_a")
    b_obj = _make_type(client, tenants["second_headers"], "records_b")
    for key, obj_id in (("first_headers", a_obj), ("second_headers", b_obj)):
        r = client.post(f"/custom/objects/{obj_id}/records",
                        headers=tenants[key], json={"data": {}})
        assert r.status_code == 200, r.text

    a_records = client.get(f"/custom/objects/{a_obj}/records",
                           headers=tenants["first_headers"]).json()
    b_records = client.get(f"/custom/objects/{b_obj}/records",
                           headers=tenants["second_headers"]).json()
    assert len(a_records) == 1
    assert len(b_records) == 1
    assert a_records[0]["id"] != b_records[0]["id"]


def test_cannot_write_record_into_foreign_object(client, tenants):
    """Чужой тип объекта недоступен для записи — 404, а не запись в чужой tenant."""
    a_obj = _make_type(client, tenants["first_headers"], "write_guard")

    r = client.post(f"/custom/objects/{a_obj}/records",
                    headers=tenants["second_headers"], json={"data": {}})
    assert r.status_code == 404, r.text


def test_superadmin_sees_types_of_all_tenants(client, tenants, make_user):
    _make_type(client, tenants["first_headers"], "sa_visible_a")
    _make_type(client, tenants["second_headers"], "sa_visible_b")

    _, h = make_user("superadmin", username="sa_reader")
    names = {o["name"] for o in client.get("/custom/objects", headers=h).json()}
    assert {"sa_visible_a", "sa_visible_b"} <= names


def test_create_with_empty_tenant_is_refused_not_500(client, db):
    """Пустой tenant — понятный отказ, а не 500 от триггера 0018.

    Пользователя с пустым tenant в БД не бывает: триггер миграции 0018 стоит и
    на `users`, поэтому ветка проверяется прямым вызовом роутера с заглушкой.
    """
    class _NoTenantUser:
        id = 1
        role = "superadmin"
        tenant_id = None

    with pytest.raises(HTTPException) as exc:
        custom_objects_router.create_object(
            custom_objects_router.ObjectTypeCreate(name="ghost", label="Призрак"),
            db, _NoTenantUser(), _=None,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant пользователя не настроен"


def test_tenant_predicate_hides_null_rows_from_empty_tenant_user():
    """Пользователь с пустым tenant не должен получать строки с tenant_id IS NULL."""
    class _NoTenantUser:
        role = "manager"
        tenant_id = None

    predicate = tenant_predicate(models.CustomObjectType, _NoTenantUser())
    assert str(predicate.compile(compile_kwargs={"literal_binds": True})) == "false"
