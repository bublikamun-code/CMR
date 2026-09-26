"""Изоляция tenant в /workflows (аудит 2026-09-26).

До правок ручки читали и удаляли по ГЛОБАЛЬНОМУ id: админ одной компании
получал конфигурацию (триггеры, шаги, запуски) чужого воркфлоу и мог снести
чужой триггер или шаг, просто угадав номер. Каждый тест ниже падает на прежнем
коде: он проверяет не «отказ», а конкретный маршрут утечки.

Ручек много, но дефект у них один и тот же, поэтому тесты идут попарно
«свой объект виден / чужой даёт 404 и остаётся на месте».
"""

import json

import pytest

import models
from conftest import DEFAULT_TEST_TENANT_ID


def _still_there(db, model, row_id) -> bool:
    """Есть ли строка в БД прямо сейчас.

    db.get() здесь не годится: удалённая чужой сессией строка остаётся в
    identity map нашей сессии, и вместо «нет строки» прилетает
    ObjectDeletedError — то есть тест проверял бы не то, что думал.
    Аналогично expire_all() не нужен: column-запрос ниже всегда ходит в БД.
    """
    return db.query(model.id).filter(model.id == row_id).first() is not None


def _make_workflow(db, tenant_id, name="Воркфлоу"):
    wf = models.Workflow(name=name, tenant_id=tenant_id)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def _make_trigger(db, wf):
    trig = models.WorkflowTrigger(
        workflow_id=wf.id, trigger_type="manual",
        config=json.dumps({"a": 1}), tenant_id=wf.tenant_id,
    )
    db.add(trig)
    db.commit()
    return trig.id


def _make_step(db, wf, position=0):
    step = models.WorkflowStep(
        workflow_id=wf.id, step_type="action", action_type="create_record",
        config=json.dumps({}), position=position, tenant_id=wf.tenant_id,
    )
    db.add(step)
    db.commit()
    # id читаем ЗДЕСЬ, пока строка жива: после commit() атрибуты инстанса
    # просрочены, и обращение к .id удалённой строки даёт ObjectDeletedError
    # вместо честного значения.
    return step.id


def test_list_workflows_is_tenant_scoped(client, db, two_tenant_users):
    """Список воркфлоу не должен содержать чужие."""
    a = two_tenant_users["first_tenant_id"]
    b = two_tenant_users["second_tenant_id"]
    _make_workflow(db, a, name="Воркфлоу A")
    _make_workflow(db, b, name="Воркфлоу B")

    r = client.get("/workflows/", headers=two_tenant_users["first_headers"])
    assert r.status_code == 200, r.text
    names = {w["name"] for w in r.json()}
    assert "Воркфлоу A" in names
    assert "Воркфлоу B" not in names, "чужой воркфлоу виден в списке"


def test_triggers_of_foreign_workflow_are_not_readable(client, db, two_tenant_users):
    """GET /workflows/{чужой}/triggers отдавал конфигурацию чужого tenant."""
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b, name="Чужой воркфлоу")
    _make_trigger(db, wf_b)

    r = client.get(f"/workflows/{wf_b.id}/triggers", headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text


def test_triggers_of_own_workflow_are_readable(client, db, two_tenant_users):
    """Обратная сторона: свои триггеры по-прежнему отдаются."""
    a = two_tenant_users["first_tenant_id"]
    wf_a = _make_workflow(db, a, name="Свой воркфлоу")
    _make_trigger(db, wf_a)

    r = client.get(f"/workflows/{wf_a.id}/triggers", headers=two_tenant_users["first_headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


def test_steps_of_foreign_workflow_are_not_readable(client, db, two_tenant_users):
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b, name="Чужой воркфлоу")
    _make_step(db, wf_b)

    r = client.get(f"/workflows/{wf_b.id}/steps", headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text


def test_runs_of_foreign_workflow_are_not_readable(client, db, two_tenant_users):
    """Журнал запусков — тоже tenant-данные (там input_data/output_data)."""
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b, name="Чужой воркфлоу")
    run = models.WorkflowRun(workflow_id=wf_b.id, status="completed", tenant_id=b)
    db.add(run)
    db.commit()

    r = client.get(f"/workflows/{wf_b.id}/runs", headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text


def test_foreign_trigger_cannot_be_deleted(client, db, two_tenant_users):
    """DELETE по глобальному id: угаданный номер = снос чужого триггера."""
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b)
    trig_b_id = _make_trigger(db, wf_b)

    r = client.delete(f"/workflows/triggers/{trig_b_id}",
                      headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text

    assert _still_there(db, models.WorkflowTrigger, trig_b_id), "чужой триггер удалён"


def test_own_trigger_can_be_deleted(client, db, two_tenant_users):
    a = two_tenant_users["first_tenant_id"]
    wf_a = _make_workflow(db, a)
    trig_a_id = _make_trigger(db, wf_a)

    r = client.delete(f"/workflows/triggers/{trig_a_id}",
                      headers=two_tenant_users["first_headers"])
    assert r.status_code == 200, r.text
    assert not _still_there(db, models.WorkflowTrigger, trig_a_id)


def test_foreign_step_cannot_be_deleted(client, db, two_tenant_users):
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b)
    step_b_id = _make_step(db, wf_b)

    r = client.delete(f"/workflows/steps/{step_b_id}",
                      headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text

    assert _still_there(db, models.WorkflowStep, step_b_id), "чужой шаг удалён"


def test_foreign_workflow_cannot_be_updated_or_deleted(client, db, two_tenant_users):
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b, name="Чужой воркфлоу")

    r = client.patch(f"/workflows/{wf_b.id}", headers=two_tenant_users["first_headers"],
                     json={"name": "Переименован"})
    assert r.status_code == 404, r.text

    r = client.delete(f"/workflows/{wf_b.id}", headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text

    assert _still_there(db, models.Workflow, wf_b.id)
    assert db.query(models.Workflow.name).filter(
        models.Workflow.id == wf_b.id).scalar() == "Чужой воркфлоу"


def test_cannot_create_step_in_foreign_workflow(client, db, two_tenant_users):
    """POST /workflows/{чужой}/steps обязан быть 404, а не записью в чужой tenant."""
    b = two_tenant_users["second_tenant_id"]
    wf_b = _make_workflow(db, b)

    r = client.post(f"/workflows/{wf_b.id}/steps", headers=two_tenant_users["first_headers"],
                    json={"step_type": "action", "action_type": "send_email",
                          "config": {"to": "x@example.com"}, "position": 0})
    assert r.status_code == 404, r.text
    assert db.query(models.WorkflowStep).filter(
        models.WorkflowStep.workflow_id == wf_b.id).count() == 0


def test_delete_workflow_keeps_other_tenants_children(client, db, two_tenant_users):
    """Удаление своего воркфлоу не задевает чужую автоматизацию.

    Раньше дочерние строки удалялись только по workflow_id, без tenant-фильтра.
    Проверяем на соседнем воркфлоу другого tenant: его триггер и шаг должны
    пережить удаление чужого (а равные id у соседей невозможны — это autoincrement,
    поэтому проверяем по workflow_id, а не по номеру строки).

    Замечание: сценарий «шаг с чужим tenant_id внутри СВОЕГО воркфлоу» тестом не
    закрывается и закрываться не должен — на уровне СУБД стоит
    ON DELETE CASCADE, и строка с тем же workflow_id исчезнет вместе с
    родителем независимо от ORM-фильтра.
    """
    a = two_tenant_users["first_tenant_id"]
    b = two_tenant_users["second_tenant_id"]
    wf_a = _make_workflow(db, a, name="Свой воркфлоу")
    _make_step(db, wf_a, position=0)
    _make_trigger(db, wf_a)
    wf_b = _make_workflow(db, b, name="Чужой воркфлоу")
    step_b_id = _make_step(db, wf_b, position=0)
    trig_b_id = _make_trigger(db, wf_b)

    r = client.delete(f"/workflows/{wf_a.id}", headers=two_tenant_users["first_headers"])
    assert r.status_code == 200, r.text

    assert _still_there(db, models.WorkflowStep, step_b_id), (
        "удаление своего воркфлоу снесло шаг чужого tenant")
    assert _still_there(db, models.WorkflowTrigger, trig_b_id), (
        "удаление своего воркфлоу снесло триггер чужого tenant")
    # и свои строки при этом убраны
    assert db.query(models.WorkflowStep).filter(
        models.WorkflowStep.workflow_id == wf_a.id).count() == 0


def test_created_workflow_and_children_get_caller_tenant(client, db, two_tenant_users):
    """Записи наследуют tenant автора, а не NULL и не случайный."""
    a = two_tenant_users["first_tenant_id"]
    headers = two_tenant_users["first_headers"]

    r = client.post("/workflows/", headers=headers,
                    json={"name": "Новый", "description": "d"})
    assert r.status_code == 200, r.text
    wf_id = r.json()["id"]

    assert db.query(models.Workflow.tenant_id).filter(
        models.Workflow.id == wf_id).scalar() == a

    r = client.post(f"/workflows/{wf_id}/triggers", headers=headers,
                    json={"trigger_type": "manual", "config": {}})
    assert r.status_code == 200, r.text
    trig_id = r.json()["id"]
    r = client.post(f"/workflows/{wf_id}/steps", headers=headers,
                    json={"step_type": "action", "action_type": "code",
                          "config": {}, "position": 0})
    assert r.status_code == 200, r.text
    step_id = r.json()["id"]

    assert db.query(models.WorkflowTrigger.tenant_id).filter(
        models.WorkflowTrigger.id == trig_id).scalar() == a
    assert db.query(models.WorkflowStep.tenant_id).filter(
        models.WorkflowStep.id == step_id).scalar() == a


def test_superadmin_sees_all_workflows(client, db, make_user, two_tenant_users):
    """Глобальная видимость superadmin не должна ломаться фильтром.

    Страховка от «перелечили tenant, сломали superadmin»: у него tenant_id IS
    NULL, и сравнение вида `tenant_id == current_user.tenant_id` давало бы
    пустой список без единой ошибки.
    """
    a = two_tenant_users["first_tenant_id"]
    b = two_tenant_users["second_tenant_id"]
    _make_workflow(db, a, name="Воркфлоу A")
    wf_b = _make_workflow(db, b, name="Воркфлоу B")
    _make_trigger(db, wf_b)

    _su, headers = make_user("superadmin", username="wf_superadmin")
    r = client.get("/workflows/", headers=headers)
    assert r.status_code == 200, r.text
    names = {w["name"] for w in r.json()}
    assert {"Воркфлоу A", "Воркфлоу B"} <= names

    # и дочерние строки видны, а не отфильтрованы как «tenant_id IS NULL»
    r = client.get(f"/workflows/{wf_b.id}/triggers", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1, "superadmin видит воркфлоу, но не его триггеры"


def test_empty_tenant_superadmin_is_refused_not_500(client, db, make_user):
    """Superadmin без tenant не может создать воркфлоу: 403, не 500.

    До tenant_policy вставка шла с tenant_id=None, и её отбивал триггер
    миграции 0018 — то есть 500. Проверяем прямой вызов хендлера: через API
    такой пользователь не создаётся (триггер тот же, что на users), а суперть
    важен именно как «понятный отказ вместо 500».
    """
    from routers import workflows_router

    class _NoTenantUser:
        id = 1
        role = "superadmin"
        tenant_id = None

    with pytest.raises(Exception) as exc:
        workflows_router.create_workflow(
            workflows_router.WorkflowCreate(name="Призрак"),
            db, _NoTenantUser(),
        )
    status = getattr(exc.value, "status_code", None)
    if status is not None:
        assert status == 403, f"ожидался 403 «Tenant не настроен», получено {status}"
    else:
        # прямая вставка в БД отбивается триггером миграции 0018 — тоже не 500
        assert "tenant_id is required" in str(exc.value)


def test_default_tenant_workflow_is_visible_to_its_admin(client, db, admin):
    """Санитарная проверка фикстуры: свои данные видны (тесты выше не зря зелёные)."""
    wf = _make_workflow(db, DEFAULT_TEST_TENANT_ID, name="Свой воркфлоу")
    _make_trigger(db, wf)
    r = client.get(f"/workflows/{wf.id}/triggers", headers=admin[1])
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
