"""Пункт 9 и пункт 15 плана v2 — V12: серверные гейты на административных мутациях.

Пункт 9 требует, чтобы мутации раздела «Управление» под ролью manager давали 403:
клиентскую половину (скрыть карточки и кнопки) делают по роли из /auth/me, эта
половина закрывает то же на сервере — правка из консоли не должна проходить.
Пункт 15 добавляет деактивацию пользователя: её имеет право переключать только
админ, а отключённый не входит и не держит активную сессию.

МАТРИЦА АДМИН-МУТАЦИЙ (2026-09-23). Критерий пункта 9 перечисляет конкретно:
«+ Новый пользователь/магазин/статус», «Изменить», «Синхронизировать».

| Что                                  | Роут                              | было               | стало                     |
| ------------------------------------ | --------------------------------- | ------------------ | ------------------------- |
| Магазины и статусы сделок            | POST/PATCH /dictionaries/stores   | admin (Этап 2.3)   | admin (регрессия)         |
|                                      | POST/PATCH /dictionaries/statuses |                    |                           |
| Пользователи                         | POST/PATCH/DELETE /auth/users     | admin              | admin (регрессия)         |
| Отключение/включение пользователя    | PATCH /auth/users/{id} is_active  | — (поля не было)   | admin, superadmin (V12)   |
| Настройки почтового ящика (запись)   | POST /email-parser/settings       | admin (V11)        | admin (регрессия, v11)    |
| Запуск синхронизации почты           | POST /email-parser/sync           | любая роль кроме  | admin, superadmin (V12)   |
|                                      |                                   | documents          |                           |
| Кастомные объекты (типы и поля)      | POST/DELETE /custom/objects,      | admin              | admin (регрессия)         |
|                                      | /custom/fields                    |                    |                           |
| Воркфлоу (конфигурация)              | /workflows (весь роутер)          | admin              | admin (регрессия)         |

ОПЕРАЦИОННЫЕ ручки гейтом не покрыты намеренно — второй раздел файла защищает и
от обратного перегиба: чтение справочников и поставщиков, история писем сделки
(GET /email-parser/related — вкладка «Письма отправителя», пункт 8 плана),
связка письма-дубля (POST /email-parser/link), клиент (POST/PATCH — менеджер
ведёт клиентов каждый день), тег (создание и навешивание на сделку, решение
V11), накладные, группы списания, выписка и галочки реестра (пункт 17).

СПОРНОЕ, оставлено открытым по решению главного агента (23.09):
  - POST /suppliers и PATCH /suppliers/{id}. Справочник поставщиков — не
    админская витрина, а операционная сущность менеджера: он заводит
    поставщиков под накладные и правит их контакты. Это кодируют четыре
    существующих теста целостности данных (test_data_integrity.py:439-556),
    которые прогоняют переименование именно под manager, потому что проверяют
    синхронизацию снимков supplier_name в накладных и чек-листах. Гейт,
    поставленный разведкой на эти ручки, ложный: дыра — это админское
    действие, доступное не-админу, а не операционное, доступное операционисту.
    DELETE /suppliers остаётся админским (V11) — удаление правит историю.
  - PATCH /payments/transactions/{id} и DELETE /writeoff_groups/{id}:
    аннулирование и групповая отмена уже админские (V11), а правка реквизитов
    ещё не закрытой записи и роспуск открытой группы — работа менеджера.
"""
import pytest

import auth
import models
from routers import email_parser_router

pytestmark = pytest.mark.access

NON_ADMIN_ROLES = ["manager", "warehouse", "documents"]
ADMIN_ROLES = ["admin", "superadmin"]


@pytest.fixture
def targets(db):
    """Записи админских справочников и пользователь-мишень для PATCH/DELETE."""
    supplier = models.Supplier(name="Поставщик «Люстра»")
    store = models.StoreLocation(name="Матусевича")
    status = models.DealStatus(name="Новый запрос", position=0)
    victim = models.User(
        username="mysha",
        hashed_password=auth.get_password_hash("Passw0rd!23"),
        role="manager",
    )
    db.add_all([supplier, store, status, victim])
    db.commit()
    db.refresh(supplier)
    db.refresh(store)
    db.refresh(status)
    db.refresh(victim)
    return {"supplier": supplier, "store": store, "status": status, "victim": victim}


# (id, метод, путь, тело). {store}/{status}/{victim} подставляется из фикстуры
# targets — путь с id нужен, чтобы PATCH/DELETE шли на реальную строку.
ADMIN_CASES = [
    ("store_create", "post", "/dictionaries/stores", {"name": "Новый магазин"}),
    ("store_update", "patch", "/dictionaries/stores/{store}", {"address": "д. 5"}),
    ("status_create", "post", "/dictionaries/statuses", {"name": "Отгружен", "position": 7}),
    ("status_update", "patch", "/dictionaries/statuses/{status}", {"color": "ok"}),
    ("user_create", "post", "/auth/users", {"username": "novichok", "password": "Passw0rd!23", "role": "manager"}),
    ("user_role", "patch", "/auth/users/{victim}", {"role": "warehouse"}),
    ("user_deactivate", "patch", "/auth/users/{victim}", {"is_active": False}),
    ("user_activate", "patch", "/auth/users/{victim}", {"is_active": True}),
    ("user_delete", "delete", "/auth/users/{victim}", None),
    ("custom_object", "post", "/custom/objects", {"name": "opros", "label": "Опросы"}),
    ("workflow_create", "post", "/workflows/", {"name": "Напоминание по оплате"}),
]
# POST /email-parser/settings в матрицу не включён: она есть в
# test_role_gates_v11.py::test_email_settings_write_is_admin_only, а запрос
# записал бы email_settings.json во временную директорию тестов.


def _request(client, headers, case, targets):
    _, method, path, body = case
    path = path.format(**{k: v.id for k, v in targets.items()})
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("case", ADMIN_CASES, ids=[c[0] for c in ADMIN_CASES])
@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_admin_mutation_denied_for_non_admin(client, make_user, targets, role, case):
    """manager / warehouse / documents → 403 на любой админской мутации.

    403 даёт зависимость роута (require_admin / require_role), поэтому тело
    ответа не проверяем — до обработки запроса дело не доходит.
    """
    _, h = make_user(role)
    r = _request(client, h, case, targets)
    assert r.status_code == 403, f"{role} прошёл на {case[1].upper()} {case[2]}: {r.status_code} {r.text}"


@pytest.mark.parametrize("case", ADMIN_CASES, ids=[c[0] for c in ADMIN_CASES])
@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_admin_mutation_allowed_for_admin_roles(client, make_user, targets, role, case):
    """Обратная половина матрицы: админ и суперадмин проходят (2xx)."""
    _, h = make_user(role)
    r = _request(client, h, case, targets)
    assert 200 <= r.status_code < 300, f"{role} не прошёл на {case[1].upper()} {case[2]}: {r.status_code} {r.text}"


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_mail_sync_denied_for_non_admin(client, make_user, role):
    """Запуск синхронизации почты — админский (V12, кнопка на «Управлении»).

    403 отдаёт зависимость роута, до IMAP дело не доходит — поэтому запрос
    безопасен даже с email_settings.json, оставшимся от теста настроек v11.
    """
    _, h = make_user(role)
    r = client.post("/email-parser/sync", headers=h)
    assert r.status_code == 403, f"{role} запустил синк: {r.status_code} {r.text}"


def test_mail_sync_passes_gate_for_admin(client, admin, monkeypatch):
    """Админ проходит гейт роли: пустой ящик даёт честный 400, а не 403.

    Настройки подменяются, иначе запрос полез бы на реальный IMAP — тесты не
    должны ходить в сеть.
    """
    monkeypatch.setattr(email_parser_router, "load_settings",
                        lambda tenant_id=None: {"email": "", "password": ""})
    _, h = admin
    r = client.post("/email-parser/sync", headers=h)
    assert r.status_code == 400, r.text
    assert "не заполнены" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Пункт 15: деактивация действует, включение возвращает доступ
# ---------------------------------------------------------------------------

def test_admin_can_disable_and_enable_user(client, admin, targets, db):
    _, h = admin
    victim = targets["victim"]

    r = client.patch(f"/auth/users/{victim.id}", headers=h, json={"is_active": False})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(models.User).filter(models.User.id == victim.id).first().is_active is False

    # Списки админа показывают отключённого, а не прячут его: решение «удалить
    # или только выключить» принимает человек, ему нужна строка в админке.
    listed = {u["username"]: u["is_active"] for u in client.get("/auth/users", headers=h).json()}
    assert listed["mysha"] is False

    assert client.patch(f"/auth/users/{victim.id}", headers=h, json={"is_active": True}).status_code == 200
    db.expire_all()
    assert db.query(models.User).filter(models.User.id == victim.id).first().is_active is True


def test_manager_cannot_disable_anyone(client, manager, targets, db):
    _, h = manager
    victim = targets["victim"]
    assert client.patch(f"/auth/users/{victim.id}", headers=h,
                        json={"is_active": False}).status_code == 403
    assert client.patch(f"/auth/users/{victim.id}", headers=h,
                        json={"is_active": True}).status_code == 403
    db.expire_all()
    assert db.query(models.User).filter(models.User.id == victim.id).first().is_active is True


def test_admin_cannot_disable_self(client, admin, db):
    """Зеркало DELETE: себя отключить нельзя — некому будет включить обратно."""
    me, h = admin
    r = client.patch(f"/auth/users/{me.id}", headers=h, json={"is_active": False})
    assert r.status_code == 400, r.text
    db.expire_all()
    assert db.query(models.User).filter(models.User.id == me.id).first().is_active is True


def test_disabled_user_session_dies_and_login_rejected(client, admin, make_user):
    """Отключение действует сразу: и для выданного токена, и для нового входа."""
    victim, vh = make_user("manager", username="otklyuchenny")
    _, ah = admin

    assert client.get("/auth/me", headers=vh).status_code == 200
    assert client.post("/auth/login", headers={"Content-Type": "application/x-www-form-urlencoded"},
                       data={"username": "otklyuchenny", "password": "Passw0rd!23"}).status_code == 200

    r = client.patch(f"/auth/users/{victim.id}", headers=ah, json={"is_active": False})
    assert r.status_code == 200, r.text

    # 401 на токен — тот же путь, что у просроченного: фронт сбрасывает куку и
    # уходит на экран входа без специальной ветки.
    assert client.get("/auth/me", headers=vh).status_code == 401
    denied = client.post("/auth/login",
                         data={"username": "otklyuchenny", "password": "Passw0rd!23"})
    assert denied.status_code == 403, denied.text
    assert "отключ" in denied.json()["detail"].lower()

    assert client.patch(f"/auth/users/{victim.id}", headers=ah, json={"is_active": True}).status_code == 200
    assert client.get("/auth/me", headers=vh).status_code == 200


def test_disabled_answer_does_not_leak_unknown_accounts(client, admin, make_user):
    """Симметрия входа: «отключён» слышит только тот, кто ввёл верный пароль.

    Неверный пароль и несуществующий логин и раньше давали один и тот же 401 —
    он остаётся тем же и для отключённого пользователя с неверным паролем, так
    что перечислить отключённые аккаунты подбором нельзя.
    """
    _, ah = admin
    victim, _ = make_user("manager", username="smychkova")
    assert client.patch(f"/auth/users/{victim.id}", headers=ah,
                        json={"is_active": False}).status_code == 200

    disabled = client.post("/auth/login",
                           data={"username": "smychkova", "password": "Passw0rd!23"})
    unknown = client.post("/auth/login",
                          data={"username": "niet_takogo", "password": "Passw0rd!23"})
    bad_password = client.post("/auth/login",
                               data={"username": "smychkova", "password": "NeTotParol!9"})

    assert disabled.status_code == 403, disabled.text
    assert unknown.status_code == 401 and bad_password.status_code == 401
    assert unknown.json()["detail"] == bad_password.json()["detail"], \
        "ответ для неверного пароля перестал быть одинаковым — появилась enumeration-дыра"


# ---------------------------------------------------------------------------
# Защита от обратного перегиба: операционная работа менеджера не задета
# ---------------------------------------------------------------------------

def test_manager_reads_dictionaries_and_mail_history(client, manager, targets, make_card):
    """GET справочников и «Письма отправителя» в карточке — по-прежнему 200.

    Чтение закрывать нельзя: из него доска, фильтры и закупка берут списки, а
    GET /email-parser/related кормит вкладку «История → Письма отправителя»
    (пункт 8 плана) — 403 там выглядел бы как «пусто», а не как запрет.
    """
    _, h = manager
    card = make_card(title="Письмо-заявка", sender_email="klient@example.com")
    paths = [
        "/suppliers",
        f"/suppliers/{targets['supplier'].id}",
        "/dictionaries/stores",
        "/dictionaries/statuses",
        "/email-parser/settings",
        f"/email-parser/related/{card.id}",
        "/auth/users",
    ]
    for path in paths:
        assert client.get(path, headers=h).status_code == 200, path


def test_manager_keeps_operational_writes(client, manager, targets, make_card):
    """Поставщик, клиент, тег и связка письма — рабочая операция, не админка.

    Поставщик здесь ключевой: разведка пункта 9 сочла POST/PATCH /suppliers
    дырой, а это ежедневная работа менеджера (завести поставщика под накладную,
    поправить контакт). Синхронизация почты — наоборот, админская (см. выше),
    поэтому её менеджер уже не вызывает.
    """
    _, h = manager

    created = client.post("/suppliers", headers=h, json={"name": "Поставщик менеджера"})
    assert created.status_code == 200, created.text
    sup_id = created.json()["id"]
    patched = client.patch(f"/suppliers/{sup_id}", headers=h, json={"phone": "722"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["phone"] == "722"

    assert client.post("/clients", headers=h,
                       json={"name": "Новый клиент"}).status_code == 200
    assert client.post("/tags", headers=h,
                       json={"name": "Срочный"}).status_code == 200

    src = make_card(title="Письмо-дубль", sender_email="dva@example.com")
    dst = make_card(title="Основная сделка", sender_email="dva@example.com")
    link = client.post(f"/email-parser/link/{src.id}", headers=h,
                       json={"target_card_id": dst.id})
    assert link.status_code != 403, link.text
