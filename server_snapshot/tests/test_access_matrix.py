"""Приоритет F — границы доступа: матрица «роль × эндпоинт».

Фиксирует и то, что защищено, и то, что НЕ защищено. Второе не менее важно:
несогласованность модели доступа — сама по себе дефект, и её надо видеть
целиком, а не находить по одному случаю.

Карта проверок по коду (2026-09-11, обновлено 2026-09-12 — create-tenant удалён,
2026-09-23 — пункт 17 плана v2 / V11, деструктивные бизнес-операции):
  require_admin()                    auth create/update/delete user, custom_objects,
                                     webhooks, workflows, dictionaries
  require_superadmin()               (нет эндпоинтов — create-tenant удалён в Фазе 4)
  require_role(admin, superadmin)    clients DELETE, clients payments DELETE,
                                     kanban cards/{id}/permanent,
                                     payments transactions DELETE,
                                     payments cards/{id}/writeoff,
                                     suppliers DELETE, tags/{id} DELETE,
                                     nakladnye/{id} DELETE
  require_role(manager, warehouse,   весь роутер email-parser (роль documents
              admin, superadmin)     к почте не допускается)
  role not in (admin, superadmin)    payments repair-writeoffs, tasks delete,
                                     activity (свой комментарий либо админ)
  БЕЗ проверки роли                  payments (создание/правка записей, выписка,
                                     дублирование в документы), nakladnye
                                     create/update/photos, writeoffs, writeoff_groups,
                                     kanban create/status/reorder/restore,
                                     PATCH /cards/{id}, теги на сделке,
                                     уведомления (фильтр по user_id)
  Полная матрица V11 (кто получает 403 на каждом закрытом роуте) —
  tests/test_role_gates_v11.py.
"""
import pytest

import models

pytestmark = pytest.mark.access


def _reload(db):
    db.expire_all()


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

PROTECTED_GETS = [
    "/kanban/cards", "/kanban/trash", "/payments/transactions", "/payments/documents",
    "/clients", "/suppliers", "/nakladnye", "/tasks", "/notifications",
    "/activity", "/tags", "/writeoffs/pending", "/writeoffs/groups/",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_unauthenticated_rejected(client, path):
    """Без токена — 401/403, и ни в коем случае не 500 и не 200."""
    code = client.get(path).status_code
    assert code in (401, 403), f"{path} вернул {code}"


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_authenticated_can_read(client, manager, path):
    """Обратная сторона: обычный менеджер читает все справочные разделы.

    Tenant-изоляция отключена намеренно (решение 2026-09-03), поэтому все
    аутентифицированные пользователи видят одни и те же данные.
    """
    _, h = manager
    assert client.get(path, headers=h).status_code == 200, path


# ---------------------------------------------------------------------------
# Защищено: только admin / superadmin
# ---------------------------------------------------------------------------

def test_manager_cannot_delete_card_permanently(client, manager, db, make_card):
    _, h = manager
    card = make_card()
    card_id = card.id
    assert client.delete(f"/kanban/cards/{card_id}/permanent", headers=h).status_code == 403
    _reload(db)
    assert db.query(models.Card).filter(models.Card.id == card_id).first() is not None


def test_admin_can_delete_card_permanently(client, admin, db, make_card):
    _, h = admin
    card = make_card()
    assert client.delete(f"/kanban/cards/{card.id}/permanent", headers=h).status_code == 200


def test_manager_cannot_delete_client(client, manager, db):
    _, h = manager
    c = models.Client(name="Клиент")
    db.add(c)
    db.commit()
    assert client.delete(f"/clients/{c.id}", headers=h).status_code == 403


def test_admin_can_delete_client(client, admin, db):
    _, h = admin
    c = models.Client(name="Клиент")
    db.add(c)
    db.commit()
    assert client.delete(f"/clients/{c.id}", headers=h).status_code == 200


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/auth/users", {"username": "x", "password": "Passw0rd!23"}),
    ("patch", "/auth/users/1", {"role": "manager"}),
])
def test_manager_cannot_manage_users(client, manager, method, path, payload):
    _, h = manager
    assert getattr(client, method)(path, headers=h, json=payload).status_code == 403


def test_manager_cannot_delete_users(client, manager, make_user):
    # TestClient.delete() не принимает json, поэтому отдельным тестом
    _, h = manager
    victim, _ = make_user("manager", username="to_delete")
    assert client.delete(f"/auth/users/{victim.id}", headers=h).status_code == 403


def test_manager_cannot_repair_writeoffs(client, manager):
    _, h = manager
    assert client.post("/payments/repair-writeoffs?dry_run=true", headers=h).status_code == 403


def test_admin_can_repair_writeoffs_dry_run(client, admin):
    """dry_run ничего не меняет — безопасная проверка для админа."""
    _, h = admin
    r = client.post("/payments/repair-writeoffs?dry_run=true", headers=h)
    assert r.status_code == 200, r.text


def test_manager_cannot_create_custom_object(client, manager):
    _, h = manager
    r = client.post("/custom/objects", headers=h, json={"name": "x", "label": "X"})
    assert r.status_code == 403


def test_admin_can_create_custom_object(client, admin):
    _, h = admin
    r = client.post("/custom/objects", headers=h, json={"name": "obj", "label": "Объект"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Эскалация привилегий (исправлено на проде, регрессия обязательна)
# ---------------------------------------------------------------------------

def test_admin_cannot_create_superadmin(client, admin):
    """РЕГРЕССИЯ на фикс эскалации привилегий (auth_router.py:99).

    В репозитории до Фазы 0 блокировалась только роль "admin", поэтому
    админ мог создать себе superadmin.
    """
    _, h = admin
    r = client.post("/auth/users", headers=h, json={
        "username": "escalated", "password": "Passw0rd!23", "role": "superadmin",
    })
    assert r.status_code == 403, f"эскалация прошла: {r.status_code} {r.text}"


def test_admin_cannot_create_another_admin(client, admin):
    _, h = admin
    r = client.post("/auth/users", headers=h, json={
        "username": "second_admin", "password": "Passw0rd!23", "role": "admin",
    })
    assert r.status_code == 403


def test_superadmin_can_create_admin(client, superadmin):
    _, h = superadmin
    r = client.post("/auth/users", headers=h, json={
        "username": "new_admin", "password": "Passw0rd!23", "role": "admin",
    })
    assert r.status_code == 200, r.text


def test_admin_cannot_modify_superadmin(client, admin, make_user):
    _, h = admin
    sup, _ = make_user("superadmin", username="the_boss")
    r = client.patch(f"/auth/users/{sup.id}", headers=h, json={"role": "manager"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Задачи: доступ по автору/исполнителю
# ---------------------------------------------------------------------------

def test_manager_cannot_delete_others_task(client, manager, make_user, db):
    owner, _ = make_user("manager", username="owner")
    _, h = manager
    task = models.Task(title="Чужая", creator_id=owner.id, assignee_id=owner.id)
    db.add(task)
    db.commit()
    # чужая задача не видна вовсе — 404, а не 403
    assert client.delete(f"/tasks/{task.id}", headers=h).status_code == 404


def test_admin_can_delete_any_task(client, admin, make_user, db):
    owner, _ = make_user("manager", username="owner2")
    _, h = admin
    task = models.Task(title="Чужая", creator_id=owner.id, assignee_id=owner.id)
    db.add(task)
    db.commit()
    assert client.delete(f"/tasks/{task.id}", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# НЕ защищено — документируем несогласованность модели доступа
# ---------------------------------------------------------------------------
# Пункт 17 плана v2 (V11) закрыл три несоответствия, которые этот раздел
# фиксировал раньше: DELETE /suppliers/{id}, DELETE /nakladnye/{id} и
# DELETE /payments/transactions/{id} стали админскими. Их матрица
# (manager/warehouse/documents → 403, admin/superadmin → успех) живёт в
# tests/test_role_gates_v11.py. Здесь остаётся то, что сознательно НЕ закрыто.

def test_manager_can_edit_any_card_money(client, manager, make_user, db, make_card):
    """Менеджер правит суммы чужой сделки: проверки владельца нет.

    Остаётся открытым сознательно: владелец не принимал решения о том, что
    менеджер работает только со своими сделками (карточки правят и склад,
    и документооборот). Если такое решение появится — это отдельный пункт,
    а не побочный эффект ролевых гейтов V11.
    """
    _, h = manager
    owner, _ = make_user("manager", username="card_owner")
    card = make_card(title="Чужая сделка", total_amount=100.0, owner_id=owner.id)

    r = client.patch(f"/cards/{card.id}/payment", headers=h, json={"paid_amount": 99.0})
    assert r.status_code == 200, r.text
    _reload(db)
    fresh = db.query(models.Card).filter(models.Card.id == card.id).first()
    assert round(float(fresh.paid_amount), 2) == 99.0


# ---------------------------------------------------------------------------
# Служебные токены: cron и бот
# ---------------------------------------------------------------------------

def test_cron_endpoints_reject_missing_token(client):
    assert client.post("/notifications/sync-overdue").status_code == 403
    assert client.post("/notifications/sync-overdue",
                       headers={"X-Cron-Token": "wrong"}).status_code == 403


def test_cron_endpoints_reject_user_jwt(client, manager):
    """Пользовательский JWT не заменяет cron-токен: у этих ручек нет user-контекста."""
    _, h = manager
    assert client.post("/notifications/sync-overdue", headers=h).status_code == 403


def test_bot_token_does_not_grant_user_access(client, bot_headers):
    """Токен бота не должен работать как пользовательский."""
    assert client.get("/kanban/cards", headers=bot_headers).status_code == 401


def test_user_jwt_does_not_grant_bot_access(client, manager):
    _, h = manager
    assert client.get("/nakladnye/bot/check-duplicate?doc_number=1",
                      headers=h).status_code == 403
