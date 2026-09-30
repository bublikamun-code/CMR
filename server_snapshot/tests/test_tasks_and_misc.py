"""Тесты на четыре непокрытые ручки: исполнители задач, удаление пункта
чек-листа, публичная версия и cron-синк всей почты.

Что здесь проверяется и почему:

1. ``GET /tasks/assignees`` — список «кому можно поручить задачу».
   Ручка отдаёт пользователей своего tenant (tenant_policy), отсортированных
   по username, и обязана быть закрыта токеном.
2. ``DELETE /tasks/checklist/{item_id}`` — удаление пункта чек-листа.
   Доступ к пункту наследуется от задачи (``_get_item_or_404`` ->
   ``_get_visible_task_or_404``), поэтому менеджер не может удалить пункт в
   задаче другого менеджера, а чужой tenant не отличить от отсутствующего id
   (404 в обоих случаях).
3. ``GET /api/version`` — единственная ручка, которая по контракту живёт без
   авторизации: её читает внешний мониторинг. Тест не привязан к конкретной
   строке версии, сверяет только форму ответа и совпадение с модулем
   ``version``.
4. ``POST /email-parser/sync-all`` — cron-ручка под ``require_cron_token``.
   Критично, что отсутствие файла настроек даёт явную запись об ошибке, а не
   «успех с нулём»: иначе watchdog посчитал бы пропавший ящик рабочим.

Сеть не используется: IMAP/SMTP в тестах не открываются, ``_sync_tenant_emails``
подменяется через monkeypatch. В файле НЕТ ни одного реального обращения к
почте.
"""
import pytest

import models
import models_tenant
import schemas
import version
from routers import email_parser_router

SYNC_ALL_URL = "/email-parser/sync-all"


# --- 1. GET /tasks/assignees ----------------------------------------------

@pytest.mark.access
def test_assignees_returns_own_tenant_users_sorted_by_username(client, make_user, db):
    """Список исполнителей: только свой tenant, сортировка по username."""
    make_user("manager", username="zulu_manager")
    make_user("manager", username="alpha_manager")
    user, headers = make_user("admin", username="mid_admin")

    r = client.get("/tasks/assignees", headers=headers)
    assert r.status_code == 200, r.text

    body = r.json()
    usernames = [u["username"] for u in body]
    assert usernames == sorted(usernames), f"выдача не отсортирована: {usernames}"
    assert set(usernames) == {"zulu_manager", "alpha_manager", "mid_admin"}, body

    # Форма ответа — ровно схема TaskAssignee (id + username), лишних полей нет.
    expected_fields = set(schemas.TaskAssignee.model_fields)
    for item in body:
        assert set(item) == expected_fields, item
        assert isinstance(item["id"], int), item
        assert isinstance(item["username"], str), item

    known = {
        u.id: u.username
        for u in db.query(models.User).filter(
            models.User.tenant_id == user.tenant_id
        ).all()
    }
    assert {u["id"]: u["username"] for u in body} == known, \
        "выдача не совпала с пользователями своего tenant"


@pytest.mark.access
def test_assignees_hides_users_of_another_tenant(client, two_tenant_users):
    """Пользователь чужого tenant в список исполнителей не попадает."""
    r = client.get("/tasks/assignees", headers=two_tenant_users["first_headers"])
    assert r.status_code == 200, r.text

    usernames = {u["username"] for u in r.json()}
    assert two_tenant_users["first_user"].username in usernames
    assert two_tenant_users["second_user"].username not in usernames, \
        "список исполнителей протекает за пределы tenant"


@pytest.mark.access
def test_assignees_requires_token(client):
    """Без токена список исполнителей недоступен."""
    r = client.get("/tasks/assignees")
    assert r.status_code == 401, r.text


# --- 2. DELETE /tasks/checklist/{item_id} ----------------------------------

def _task(db, user, tenant_id=None, title="Задача на удаление пункта"):
    """Задача создаётся напрямую в БД: проверяем ручку, а не create_task."""
    task = models.Task(
        title=title,
        status="todo",
        assignee_id=user.id,
        creator_id=user.id,
        tenant_id=user.tenant_id if tenant_id is None else tenant_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _item(db, task, title, position=0):
    item = models.TaskChecklistItem(
        task_id=task.id, title=title, position=position, tenant_id=task.tenant_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_delete_checklist_item_removes_only_that_item(client, manager, db):
    """Пункт удаляется, соседние пункты задачи остаются целы."""
    user, headers = manager
    task = _task(db, user)
    first = _item(db, task, "Первый пункт", position=0)
    middle = _item(db, task, "Средний пункт", position=1)
    last = _item(db, task, "Последний пункт", position=2)

    r = client.delete(f"/tasks/checklist/{middle.id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    db.expire_all()
    left = {i.id for i in db.query(models.TaskChecklistItem).all()}
    assert left == {first.id, last.id}, "удаление задело соседние пункты"
    assert db.query(models.Task).filter(models.Task.id == task.id).first() is not None, \
        "вместе с пунктом удалилась сама задача"


@pytest.mark.access
def test_delete_checklist_item_unknown_id_returns_404(client, manager):
    """Несуществующий id — 404 с понятным detail."""
    _, headers = manager
    r = client.delete("/tasks/checklist/999999", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Пункт не найден", r.text


@pytest.mark.access
def test_delete_checklist_item_of_task_of_another_manager_returns_404(client, make_user, db):
    """Менеджер не удаляет пункт в задаче, которую не видит."""
    owner, _ = make_user("manager", username="task_owner")
    intruder, headers = make_user("manager", username="task_intruder")
    task = _task(db, owner)
    item = _item(db, task, "Пункт чужой задачи", position=0)

    r = client.delete(f"/tasks/checklist/{item.id}", headers=headers)
    assert r.status_code == 404, r.text

    db.expire_all()
    assert db.query(models.TaskChecklistItem).filter(
        models.TaskChecklistItem.id == item.id
    ).first() is not None, "пункт чужой задачи всё-таки удалился"


@pytest.mark.access
def test_delete_checklist_item_of_another_tenant_returns_404(client, two_tenant_users, db):
    """Пункт чужого tenant неотличим от отсутствующего — тоже 404."""
    foreign_task = _task(db, two_tenant_users["second_user"],
                         tenant_id=two_tenant_users["second_tenant_id"],
                         title="Задача другого tenant")
    foreign_item = _item(db, foreign_task, "Пункт чужого tenant", position=0)

    r = client.delete(f"/tasks/checklist/{foreign_item.id}",
                      headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text

    db.expire_all()
    assert db.query(models.TaskChecklistItem).filter(
        models.TaskChecklistItem.id == foreign_item.id
    ).first() is not None, "пункт чужого tenant всё-таки удалился"


@pytest.mark.access
def test_delete_checklist_item_requires_token(client, manager, db):
    """Без токена удаление пункта не выполняется."""
    user, headers = manager
    task = _task(db, user)
    item = _item(db, task, "Пункт под защитой", position=0)

    r = client.delete(f"/tasks/checklist/{item.id}")
    assert r.status_code == 401, r.text

    db.expire_all()
    assert db.query(models.TaskChecklistItem).filter(
        models.TaskChecklistItem.id == item.id
    ).first() is not None, "пункт удалился без авторизации"


# --- 3. GET /api/version ---------------------------------------------------

def test_api_version_is_public_and_matches_version_module(client):
    """Версия читается без токена, тело соответствует модулю version.

    ФАКТИЧЕСКОЕ ПОВЕДЕНИЕ (в отличие от формулировки задачи): ``build`` в
    ответе — число, а не строка, потому что ``version.__build__ = 20260913``
    объявлен int и main.py отдаёт его как есть. Тест фиксирует реальный тип
    и сверяет значение с модулем; строковый тип build здесь НЕ проверяется.
    """
    r = client.get("/api/version")
    assert r.status_code == 200, r.text

    body = r.json()
    assert set(body) == {"version", "build"}, body
    assert isinstance(body["version"], str) and body["version"], body
    assert body["version"] == version.__version__

    expected_build = getattr(version, "__build__", None)
    if expected_build is not None:
        assert body["build"] == expected_build, body


# --- 4. POST /email-parser/sync-all ---------------------------------------
# Сеть не используется: во всех тестах ниже load_settings и _sync_tenant_emails
# подменены через monkeypatch, до IMAP/SMTP дело не доходит.

@pytest.mark.access
def test_sync_all_without_cron_token_is_rejected(client):
    """Без X-Cron-Token ручка закрыта.

    Фактический код ответа require_cron_token — 403 (auth.py:291), а не 401:
    это сознательный выбор проекта, тест его фиксирует.
    """
    r = client.post(SYNC_ALL_URL)
    assert r.status_code == 403, r.text

    # Неверный токен отвергается так же, как отсутствующий (заголовок ASCII,
    # иначе httpx не даст его отправить).
    r_wrong = client.post(SYNC_ALL_URL, headers={"X-Cron-Token": "wrong-cron-token"})
    assert r_wrong.status_code == 403, r_wrong.text


def test_sync_all_reports_shape_and_total_on_success(client, cron_headers, monkeypatch):
    """Успешная ветка: total_count складывается из результатов."""
    monkeypatch.setattr(
        email_parser_router, "load_settings",
        lambda tenant_id=None: {"email": "box@example.com", "imap_server": "imap.example.com"},
    )
    monkeypatch.setattr(
        email_parser_router, "_sync_tenant_emails",
        lambda tenant_id, settings, db: {"tenant_id": tenant_id, "success": True, "count": 3},
    )

    r = client.post(SYNC_ALL_URL, headers=cron_headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert set(body) == {
        "success", "total_count", "tenants_processed", "tenants_failed", "results",
    }, body
    assert body["success"] is True
    assert body["total_count"] == 3, body
    assert body["tenants_failed"] == 0, body
    assert isinstance(body["results"], list) and body["results"], body
    first = body["results"][0]
    assert set(first) >= {"tenant_id", "success", "count"}, first
    assert first["success"] is True
    assert first["tenant_id"] is None, "главный ящик синхронизируется без tenant"


def test_sync_all_fails_explicitly_when_settings_unreadable(client, cron_headers, monkeypatch):
    """Нечитаемый файл настроек — явная ошибка, а не «успех с нулём»."""
    def _boom(tenant_id=None):
        raise email_parser_router.EmailSettingsReadError("Файл настроек почты не читается")

    called = []

    def _must_not_run(tenant_id, settings, db):
        called.append(tenant_id)
        raise AssertionError("синк не должен запускаться при недоступных настройках")

    monkeypatch.setattr(email_parser_router, "load_settings", _boom)
    monkeypatch.setattr(email_parser_router, "_sync_tenant_emails", _must_not_run)

    r = client.post(SYNC_ALL_URL, headers=cron_headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["tenants_failed"] == 1, body
    assert body["total_count"] == 0, body
    result = body["results"][0]
    assert result["success"] is False, result
    assert "недоступен" in result["error"], result
    assert called == [], "IMAP трогать нельзя, настроек нет"


def test_sync_all_fails_explicitly_on_unexpected_error(client, cron_headers, monkeypatch):
    """Общий except: любая неожиданная ошибка тоже даёт запись об ошибке."""
    monkeypatch.setattr(
        email_parser_router, "load_settings", lambda tenant_id=None: {"email": "box@example.com"},
    )

    def _boom(tenant_id, settings, db):
        raise RuntimeError("сломалось соединение")

    monkeypatch.setattr(email_parser_router, "_sync_tenant_emails", _boom)

    r = client.post(SYNC_ALL_URL, headers=cron_headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["tenants_failed"] == 1, body
    assert body["total_count"] == 0, body
    result = body["results"][0]
    assert result["success"] is False, result
    assert result["error"] == "Ошибка синхронизации почты", result


def test_sync_all_does_not_iterate_tenants_table(client, cron_headers, db, monkeypatch):
    """Фактическое поведение: цикла по таблице tenants НЕТ.

    Ручка называется sync_all_tenants и в её комментарии сказано, что раньше
    цикл шёл по таблице tenants, но в текущей реализации синхронизируется
    только главный (безtenant) ящик. Тест фиксирует наблюдаемый факт: даже при
    нескольких tenant в БД вызовов синка ровно один и tenant_id в нём None.
    Если цикл вернётся, тест об этом сообщит.
    """
    if db.query(models_tenant.Tenant).filter(
        models_tenant.Tenant.id == 303
    ).first() is None:
        db.add(models_tenant.Tenant(id=303, name="Третий tenant для проверки",
                                    db_path="tests/crm_303.db"))
        db.commit()
    tenants_before = db.query(models_tenant.Tenant).count()
    assert tenants_before > 1, "нужен минимум один tenant плюс главный ящик"

    calls = []
    monkeypatch.setattr(
        email_parser_router, "load_settings", lambda tenant_id=None: {"email": "box@example.com"},
    )

    def _record(tenant_id, settings, db):
        calls.append(tenant_id)
        return {"tenant_id": tenant_id, "success": True, "count": 1}

    monkeypatch.setattr(email_parser_router, "_sync_tenant_emails", _record)

    r = client.post(SYNC_ALL_URL, headers=cron_headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert calls == [None], f"ожидался только главный ящик, синк был вызван для {calls}"
    assert body["tenants_processed"] == 1, body
    assert body["total_count"] == 1, body
