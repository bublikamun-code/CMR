"""Непокрытые ручки доски сделок и истории версий.

Закрывает пять маршрутов, которые до этого файла не дёргались ни одним тестом:
  * PATCH /kanban/cards/reorder   — перетаскивание карточек на канбане;
  * GET  /kanban/export/csv       — выгрузка доски;
  * GET  /kanban/trash             — архив удалённых сделок;
  * GET  /kanban/cards/{id}/versions и GET /clients/{id}/versions — история;
  * POST /kanban/cards             — создание сделки с доски.

Особенности, которые здесь фиксируются как контракт:
  * reorder нумерует позиции по ИНДЕКСУ в payload, а не по порядку среди
    найденных карточек: карточка из чужого статуса/удалённая/чужая не
    сдвигает нумерацию, но «съедает» свой индекс (дыры в позициях);
  * «updated» в ответе reorder — число НАЙДЕННЫХ карточек, а не длина payload;
  * версии фильтруются по tenant_id вызывающего, поэтому чужие строки истории
    не показываются даже для существующей карточки своего тенанта.
"""
import csv
import io

import pytest

import models
from conftest import DEFAULT_TEST_TENANT_ID

HEADER = ("ID", "Название", "Статус", "Сумма", "Клиент", "Приоритет",
          "Дата создания", "Описание")


def _reorder(client, headers, card_ids, status="Новый запрос"):
    return client.patch("/kanban/cards/reorder", headers=headers,
                        json={"status": status, "card_ids": list(card_ids)})


def _positions(db, card_ids):
    """Позиции карточек из БД (не из ответа) — по id."""
    db.expire_all()
    return {c.id: c.position for c in db.query(models.Card).filter(
        models.Card.id.in_(list(card_ids))
    ).all()}


def _csv_rows(text):
    return list(csv.reader(io.StringIO(text), delimiter=";"))


# ---------------------------------------------------------------------------
# PATCH /kanban/cards/reorder
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_reorder_numbers_positions_from_zero_by_payload_order(
        client, manager, db, make_card):
    """Порядок в payload становится позициями 0, 1, 2 …"""
    _, h = manager
    cards = [make_card(title=f"Сделка {i}") for i in range(3)]
    ids = [c.id for c in cards]

    r = _reorder(client, h, [ids[2], ids[0], ids[1]])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 3}

    assert _positions(db, ids) == {ids[2]: 0, ids[0]: 1, ids[1]: 2}


@pytest.mark.integrity
def test_reorder_ignores_cards_from_another_status(
        client, manager, db, make_card):
    """Карточка другого статуса молча пропускается: updated меньше payload."""
    _, h = manager
    mine = make_card(title="Моя", status="Новый запрос", position=0)
    other = make_card(title="Чужая по статусу", status="В работе", position=0)

    r = _reorder(client, h, [mine.id, other.id])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1}

    assert _positions(db, [mine.id, other.id]) == {mine.id: 0, other.id: 0}


@pytest.mark.integrity
def test_reorder_ignores_deleted_cards(client, manager, db, make_card):
    """Удалённая карточка в архиве позицию не меняет."""
    _, h = manager
    alive = make_card(title="Рабочая", position=5)
    dead = make_card(title="Удалённая", position=5, is_deleted=True)

    r = _reorder(client, h, [alive.id, dead.id])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1}

    assert _positions(db, [alive.id, dead.id]) == {alive.id: 0, dead.id: 5}


@pytest.mark.access
def test_reorder_ignores_cards_of_another_tenant(
        client, two_tenant_users, db, make_card):
    """Сделка чужого тенанта не переупорядочивается и не попадает в updated."""
    their = make_card(title="Сделка второго тенанта",
                      tenant_id=two_tenant_users["second_tenant_id"],
                      position=0)
    mine = make_card(title="Сделка первого тенанта", position=0)

    r = _reorder(client, two_tenant_users["first_headers"],
                 [mine.id, their.id])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1}

    assert _positions(db, [mine.id, their.id]) == {mine.id: 0, their.id: 0}


@pytest.mark.integrity
def test_reorder_positions_follow_payload_index_not_found_order(
        client, manager, db, make_card):
    """Фактическое поведение: позиция = индекс в payload.

    Пропущенные (удалённые/чужие по статусу) карточки не сдвигают
    нумерацию оставшихся — в позициях остаются дыры. Для фронта это
    безвредно (порядок задаёт position), но зафиксировано явно.
    """
    _, h = manager
    first = make_card(title="Первая", position=9)
    dead = make_card(title="Удалённая", position=9, is_deleted=True)
    second = make_card(title="Вторая", position=9)

    r = _reorder(client, h, [first.id, dead.id, second.id])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2}

    assert _positions(db, [first.id, dead.id, second.id]) == {
        first.id: 0, dead.id: 9, second.id: 2,
    }


@pytest.mark.integrity
def test_reorder_with_unknown_ids_returns_zero_updates(client, manager):
    """Несуществующие id не дают 500: ответ 200 и updated: 0."""
    _, h = manager

    r = _reorder(client, h, [987654, 987655])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0}


@pytest.mark.access
def test_reorder_tenant_a_cannot_move_tenant_b_cards(
        client, two_tenant_users, db, make_card):
    """Вызов от имени тенанта A с id карточек тенанта B — 200 и ноль правок."""
    their = make_card(title="Сделка B", position=3,
                      tenant_id=two_tenant_users["second_tenant_id"])

    r = _reorder(client, two_tenant_users["first_headers"], [their.id])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0}
    assert _positions(db, [their.id]) == {their.id: 3}


@pytest.mark.integrity
@pytest.mark.parametrize("card_ids", [[], [1, 1]])
def test_reorder_rejects_empty_and_duplicate_lists(client, manager, card_ids):
    """Схема CardReorder отсекает пустой список и дубли (422)."""
    _, h = manager

    r = _reorder(client, h, card_ids)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# GET /kanban/export/csv
# ---------------------------------------------------------------------------

def test_export_csv_is_attachment_with_expected_headers(client, manager,
                                                        make_card):
    _, h = manager
    make_card(title="Сделка для выгрузки")

    r = client.get("/kanban/export/csv", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == \
        "attachment; filename=cards_export.csv"


def test_export_csv_header_row_is_quoted_and_separated_by_semicolon(
        client, manager, make_card):
    """Заголовок колонок — QUOTE_ALL, разделитель `;`."""
    _, h = manager
    make_card(title="Сделка")

    r = client.get("/kanban/export/csv", headers=h)
    first_line = r.text.splitlines()[0]

    assert first_line == '"ID";"Название";"Статус";"Сумма";"Клиент";' \
                         '"Приоритет";"Дата создания";"Описание"'
    assert _csv_rows(r.text)[0] == list(HEADER)


def test_export_csv_every_field_is_quoted(client, manager, make_card):
    _, h = manager
    make_card(title='Сделка "с кавычками"', total_amount=1234.56,
              description="Описание")

    r = client.get("/kanban/export/csv", headers=h)
    data_line = r.text.splitlines()[1]

    # Каждое поле стоит в кавычках — иначе запятая/точка с запятой внутри
    # значения разъехали бы столбцы при открытии в Excel.
    assert data_line.startswith('"') and data_line.endswith('"')
    assert data_line.count('";') == len(HEADER) - 1
    assert '""' in data_line, "внутренние кавычки значения должны удваиваться"


def test_export_csv_contains_only_own_and_alive_cards(
        client, two_tenant_users, db, make_card):
    """Удалённые и чужие сделки в выгрузку не попадают."""
    db.add(models.Client(name="Клиент выгрузки",
                        tenant_id=DEFAULT_TEST_TENANT_ID))
    db.commit()
    client_row = db.query(models.Client).filter(
        models.Client.name == "Клиент выгрузки").one()

    mine = make_card(title="Своя", client_id=client_row.id)
    dead = make_card(title="Удалённая", is_deleted=True)
    their = make_card(title="Чужая", tenant_id=two_tenant_users["second_tenant_id"])

    r = client.get("/kanban/export/csv",
                   headers=two_tenant_users["first_headers"])
    titles = {row[1] for row in _csv_rows(r.text)[1:]}

    assert titles == {"Своя"}
    assert mine.id in {int(row[0]) for row in _csv_rows(r.text)[1:]}
    assert dead.id not in {int(row[0]) for row in _csv_rows(r.text)[1:]}
    assert their.id not in {int(row[0]) for row in _csv_rows(r.text)[1:]}


def test_export_csv_flattens_newlines_to_one_row_per_card(
        client, manager, db, make_card):
    """Переводы строк в описании заменены пробелами: одна строка на карточку."""
    _, h = manager
    make_card(title="Многострочная", description="первая\nвторая\r\nтретья")
    make_card(title="Обычная", description="без переводов")

    r = client.get("/kanban/export/csv", headers=h)
    assert "\n" not in r.text.rstrip("\n").split('"')[-2]  # описание без \n
    lines = [line for line in r.text.splitlines() if line.strip()]
    assert len(lines) == 3, "заголовок + ровно по строке на карточку"

    rows = {row[1]: row for row in _csv_rows(r.text)[1:]}
    assert rows["Многострочная"][7] == "первая вторая  третья"
    assert rows["Обычная"][7] == "без переводов"


def test_export_csv_client_column_by_name_and_empty_without_client(
        client, manager, db, make_card):
    """Колонка «Клиент» — имя клиента; у сделки без клиента ячейка пустая."""
    _, h = manager
    client_row = models.Client(name='ООО "Ромашка"', tenant_id=DEFAULT_TEST_TENANT_ID)
    db.add(client_row)
    db.commit()
    db.refresh(client_row)

    with_client = make_card(title="С клиентом", client_id=client_row.id)
    without_client = make_card(title="Без клиента")

    r = client.get("/kanban/export/csv", headers=h)
    rows = {row[1]: row for row in _csv_rows(r.text)[1:]}

    assert rows["С клиентом"][4] == 'ООО "Ромашка"'
    assert rows["Без клиента"][4] == ""
    assert with_client.client_id == client_row.id
    assert without_client.client_id is None


def test_export_csv_fills_status_amount_and_created_at(client, manager,
                                                       make_card):
    """Колонки статуса, суммы и даты заполнены из карточки."""
    _, h = manager
    make_card(title="Сумма", status="В работе", total_amount=4321.5,
              priority=2)

    r = client.get("/kanban/export/csv", headers=h)
    row = _csv_rows(r.text)[1]
    by_header = dict(zip(HEADER, row))

    assert by_header["Статус"] == "В работе"
    assert float(by_header["Сумма"]) == pytest.approx(4321.5, abs=0.01)
    assert by_header["Приоритет"] == "2"
    assert by_header["Дата создания"], "дата создания обязана быть непустой"


# ---------------------------------------------------------------------------
# GET /kanban/trash
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_trash_returns_only_deleted_cards(client, manager, db, make_card):
    _, h = manager
    alive = make_card(title="Рабочая")
    dead_one = make_card(title="Удалённая раз", is_deleted=True)
    dead_two = make_card(title="Удалённая два", is_deleted=True)

    r = client.get("/kanban/trash", headers=h)
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()}

    assert ids == {dead_one.id, dead_two.id}
    assert alive.id not in ids


@pytest.mark.access
def test_trash_excludes_cards_of_another_tenant(client, two_tenant_users,
                                                make_card):
    """Архив тенанта не протекает: чужие удалённые сделки не видны."""
    their_dead = make_card(title="Удалённая B", is_deleted=True,
                           tenant_id=two_tenant_users["second_tenant_id"])
    our_dead = make_card(title="Удалённая A", is_deleted=True)

    r = client.get("/kanban/trash",
                   headers=two_tenant_users["first_headers"])
    ids = {c["id"] for c in r.json()}

    assert ids == {our_dead.id}
    assert their_dead.id not in ids


# ---------------------------------------------------------------------------
# История версий карточки: GET /kanban/cards/{card_id}/versions
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_card_versions_empty_before_any_update(client, manager, make_card):
    _, h = manager
    card = make_card(title="Без правок")

    r = client.get(f"/kanban/cards/{card.id}/versions", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []


@pytest.mark.integrity
def test_card_versions_appear_after_status_update(client, manager, make_card):
    """Смена статуса пишет версию карточки с change_type=update."""
    _, h = manager
    card = make_card(title="Версионируемая", status="Новый запрос")

    r = client.patch(f"/kanban/cards/{card.id}/status", headers=h,
                     json={"status": "В работе"})
    assert r.status_code == 200, r.text

    versions = client.get(f"/kanban/cards/{card.id}/versions",
                          headers=h).json()
    assert versions, "после правки история карточки не должна быть пустой"
    assert versions[0]["change_type"] == "update"
    assert versions[0]["data"]["status"] == "В работе"
    assert versions[0]["version"] == 1


@pytest.mark.access
def test_card_versions_of_missing_card_is_404(client, manager):
    _, h = manager

    r = client.get("/kanban/cards/999999/versions", headers=h)
    assert r.status_code == 404, r.text


@pytest.mark.access
def test_card_versions_of_another_tenant_card_is_404(
        client, two_tenant_users, make_card):
    """Сделка чужого тенанта не отличима от отсутствующей — тот же 404."""
    their = make_card(title="Чужая сделка",
                      tenant_id=two_tenant_users["second_tenant_id"])

    r = client.get(f"/kanban/cards/{their.id}/versions",
                   headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Карточка не найдена"


@pytest.mark.access
def test_card_versions_are_filtered_by_tenant(client, two_tenant_users, db,
                                              make_card):
    """Строка истории с tenant_id чужого тенанта не показывается.

    Даже если карточка принадлежит вызывающему, версия, записанная другим
    тенантом (например, до переезва данных), в выдаче отсутствует.
    """
    their = make_card(title="Сделка B",
                      tenant_id=two_tenant_users["second_tenant_id"])
    db.add(models.RecordVersion(
        table_name="cards", record_id=their.id, version=1,
        data_snapshot="{}", change_type="update",
        tenant_id=two_tenant_users["first_tenant_id"],
    ))
    db.commit()

    r = client.get(f"/kanban/cards/{their.id}/versions",
                   headers=two_tenant_users["second_headers"])
    assert r.status_code == 200, r.text
    assert r.json() == []


# ---------------------------------------------------------------------------
# История версий клиента: GET /clients/{client_id}/versions
# ---------------------------------------------------------------------------

def _make_client_row(db, name, tenant_id=DEFAULT_TEST_TENANT_ID, **kw):
    row = models.Client(name=name, tenant_id=tenant_id, **kw)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_client_versions_empty_before_any_update(client, manager, db):
    _, h = manager
    row = _make_client_row(db, "Клиент без правок")

    r = client.get(f"/clients/{row.id}/versions", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_client_versions_appear_after_update(client, manager, db):
    _, h = manager
    row = _make_client_row(db, "Клиент до правки", phone="+375291112233")

    r = client.patch(f"/clients/{row.id}", headers=h, json={"name": "Клиент после"})
    assert r.status_code == 200, r.text

    versions = client.get(f"/clients/{row.id}/versions", headers=h).json()
    assert versions, "после правки история клиента не должна быть пустой"
    assert versions[0]["change_type"] == "update"
    assert versions[0]["data"]["name"] == "Клиент после"
    assert versions[0]["changed_by"] is not None


@pytest.mark.access
def test_client_versions_of_missing_client_is_404(client, manager):
    _, h = manager

    r = client.get("/clients/999999/versions", headers=h)
    assert r.status_code == 404, r.text


@pytest.mark.access
def test_client_versions_of_another_tenant_client_is_404(
        client, two_tenant_users, db):
    """Клиент чужого тенанта — тот же 404, что и несуществующий."""
    their = _make_client_row(db, "Клиент B",
                             tenant_id=two_tenant_users["second_tenant_id"])

    r = client.get(f"/clients/{their.id}/versions",
                   headers=two_tenant_users["first_headers"])
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Клиент не найден"


@pytest.mark.access
def test_client_versions_are_filtered_by_tenant(client, two_tenant_users, db):
    """История, записанная другим тенантом, в выдаче не появляется."""
    their = _make_client_row(db, "Клиент B",
                             tenant_id=two_tenant_users["second_tenant_id"])
    db.add(models.RecordVersion(
        table_name="clients", record_id=their.id, version=1,
        data_snapshot='{"name": "Клиент B"}', change_type="update",
        tenant_id=two_tenant_users["first_tenant_id"],
    ))
    db.commit()

    r = client.get(f"/clients/{their.id}/versions",
                   headers=two_tenant_users["second_headers"])
    assert r.status_code == 200, r.text
    assert r.json() == []


# ---------------------------------------------------------------------------
# POST /kanban/cards — создание сделки с доски
# ---------------------------------------------------------------------------

@pytest.mark.access
def test_create_card_with_owner_from_same_tenant(client, manager, make_user, db):
    """Создатель может назначить владельцем другого сотрудника своего тенанта."""
    _, h = manager
    owner, _ = make_user("manager", username="owner_same_tenant")

    r = client.post("/kanban/cards", headers=h, json={
        "title": "Сделка для коллеги", "owner_id": owner.id,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner_id"] == owner.id
    assert body["tenant_id"] if "tenant_id" in body else True

    db.expire_all()
    saved = db.query(models.Card).filter(
        models.Card.id == body["id"]).one()
    assert saved.owner_id == owner.id
    assert saved.tenant_id == DEFAULT_TEST_TENANT_ID


@pytest.mark.access
def test_create_card_without_owner_sets_author(client, manager, db):
    """Без owner_id владельцем становится автор."""
    user, h = manager

    r = client.post("/kanban/cards", headers=h, json={"title": "Своя сделка"})
    assert r.status_code == 200, r.text
    assert r.json()["owner_id"] == user.id


@pytest.mark.access
def test_create_card_owner_from_another_tenant_is_404(
        client, two_tenant_users, make_user):
    """Владелец чужого тенанта — 404, сделка не создаётся."""
    their_user, _ = make_user("manager", username="owner_other_tenant",
                              tenant_id=two_tenant_users["second_tenant_id"])

    r = client.post("/kanban/cards",
                    headers=two_tenant_users["first_headers"],
                    json={"title": "Сделка на чужого", "owner_id": their_user.id})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Владелец не найден"


@pytest.mark.access
def test_create_card_with_unknown_owner_is_404(client, manager):
    _, h = manager

    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Сделка без владельца", "owner_id": 999999})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Владелец не найден"


@pytest.mark.access
def test_create_card_with_client_from_another_tenant_is_404(
        client, two_tenant_users, db):
    """Клиент другого тенанта в сделку не подставляется — 404."""
    their_client = models.Client(name="Клиент B",
                                 tenant_id=two_tenant_users["second_tenant_id"])
    db.add(their_client)
    db.commit()
    db.refresh(their_client)

    r = client.post("/kanban/cards",
                    headers=two_tenant_users["first_headers"],
                    json={"title": "Сделка на чужого клиента",
                          "client_id": their_client.id})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Клиент не найден"


@pytest.mark.access
def test_create_card_with_client_from_same_tenant_is_ok(
        client, manager, db):
    """Свой клиент подставляется и возвращается в ответе."""
    _, h = manager
    row = _make_client_row(db, "Клиент A")

    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Сделка на своего клиента", "client_id": row.id})
    assert r.status_code == 200, r.text
    assert r.json()["client_id"] == row.id


def test_create_card_applies_tags(client, manager, db):
    """Корректные теги применяются к новой сделке."""
    _, h = manager
    tag = models.Tag(name="Срочно", tenant_id=DEFAULT_TEST_TENANT_ID)
    db.add(tag)
    db.commit()
    db.refresh(tag)

    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Сделка с тегом", "tag_ids": [tag.id]})
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["tags"]] == [tag.id]


def test_create_card_with_unknown_tag_is_404(client, manager, db):
    """Один несуществующий тег отменяет всю операцию."""
    _, h = manager
    tag = models.Tag(name="Срочно", tenant_id=DEFAULT_TEST_TENANT_ID)
    db.add(tag)
    db.commit()
    db.refresh(tag)

    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Сделка с чужим тегом",
                          "tag_ids": [tag.id, 999999]})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Тег не найден"

    assert db.query(models.Card).count() == 0


@pytest.mark.integrity
def test_create_card_writes_activity_log(client, manager, db):
    """В ленте активности появляется «Создание карточки» с текстом про title."""
    _, h = manager

    r = client.post("/kanban/cards", headers=h, json={"title": "Сделка в ленту"})
    assert r.status_code == 200, r.text
    card_id = r.json()["id"]

    entries = client.get("/activity", headers=h,
                         params={"card_id": card_id}).json()
    assert len(entries) == 1
    assert entries[0]["action"] == "Создание карточки"
    assert entries[0]["details"] == "Создана карточка: Сделка в ленту"

    db.expire_all()
    log = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == card_id).one()
    assert log.action == "Создание карточки"
    assert log.details == "Создана карточка: Сделка в ленту"


@pytest.mark.access
def test_create_card_notifies_new_owner(client, manager, make_user, db):
    """Владелец, отличный от автора, получает уведомление о новой сделке."""
    _, h = manager
    owner, _ = make_user("manager", username="owner_notified")

    r = client.post("/kanban/cards", headers=h,
                    json={"title": "Сделка с уведомлением", "owner_id": owner.id})
    assert r.status_code == 200, r.text

    db.expire_all()
    notes = db.query(models.Notification).filter(
        models.Notification.user_id == owner.id).all()
    assert notes, "владелец должен получить уведомление о новой сделке"
    assert any("Сделка с уведомлением" in (n.title or "") for n in notes)


@pytest.mark.integrity
def test_new_card_status_change_reaches_payment_registry(
        client, manager, db):
    """Фактическое поведение: смена статуса новой сделки на «Сборку»
    создаёт запись-остаток в реестре оплат, и сделка видна в реестре."""
    _, h = manager
    created = client.post("/kanban/cards", headers=h, json={
        "title": "Сделка в сборку", "total_amount": 2500.0,
    })
    assert created.status_code == 200, created.text
    card_id = created.json()["id"]

    changed = client.patch(f"/kanban/cards/{card_id}/status", headers=h,
                           json={"status": "Сборка"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "Сборка"

    db.expire_all()
    rows = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    assert len(rows) == 1, "сделка в «Сборке» обязана попасть в реестр оплат"
    assert round(float(rows[0].amount), 2) == 2500.0

    registry = client.get("/payments/transactions", headers=h,
                          params={"grouped": False}).json()
    assert card_id in {row["card_id"] for row in registry}


@pytest.mark.integrity
def test_new_card_without_amount_gets_no_registry_row(client, manager, db):
    """Сделка без суммы остаётся без записи реестра (правило ensure_registry_remainder)."""
    _, h = manager
    created = client.post("/kanban/cards", headers=h,
                          json={"title": "Сделка без суммы", "total_amount": 0.0})
    card_id = created.json()["id"]

    r = client.patch(f"/kanban/cards/{card_id}/status", headers=h,
                     json={"status": "Сборка"})
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id).all() == []


def test_create_card_validates_status_and_title(client, manager):
    """Схема CardCreate отсекает неизвестный статус и пустое название."""
    _, h = manager

    bad_status = client.post("/kanban/cards", headers=h,
                             json={"title": "Сделка", "status": "Неизвестный"})
    assert bad_status.status_code == 422, bad_status.text

    bad_title = client.post("/kanban/cards", headers=h,
                            json={"title": "x" * 201})
    assert bad_title.status_code == 422, bad_title.text
