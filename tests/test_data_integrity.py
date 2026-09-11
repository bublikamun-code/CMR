"""Приоритет D — связи между таблицами, каскады, целостность данных.

Проверяем КОНЕЧНОЕ СОСТОЯНИЕ, а не механизм: на проде часть FK отсутствует в DDL
(например cards.writeoff_group_id — голый INTEGER без REFERENCES), и роутеры
компенсируют это ручным отвязыванием детей. Тесту всё равно, как достигнуто
отсутствие сирот, — важно, что оно достигнуто.

Важная деталь: все id захватываются в локальные переменные ДО удаления.
После expire_all() обращение к атрибуту удалённого ORM-объекта поднимает
ObjectDeletedError, поэтому полагаться на card.id после DELETE нельзя.
"""
import pytest

import models

pytestmark = pytest.mark.integrity


def _reload(db):
    db.expire_all()


def _exists(db, model, pk):
    return db.query(model).filter(model.id == pk).first() is not None


def _count(db, model, **flt):
    q = db.query(model)
    for k, v in flt.items():
        q = q.filter(getattr(model, k) == v)
    return q.count()


# ---------------------------------------------------------------------------
# Удаление сделки
# ---------------------------------------------------------------------------

def test_delete_card_permanently_cascades_and_unlinks(client, admin, db, make_card):
    """Удаление сделки: дети-владельцы удаляются, ссылки из общих таблиц зануляются."""
    _, h = admin
    card = make_card(title="Сделка на удаление", total_amount=1000.0)
    card_id = card.id

    db.add(models.CardAttachment(card_id=card_id, file_name="a.pdf", file_path="/tmp/a.pdf"))
    db.add(models.CardChecklist(card_id=card_id, company_name="Закупка", amount=100.0))
    tag = models.Tag(name="тест-тег")
    db.add(tag)
    db.commit()
    tag_id = tag.id
    db.add(models.CardTag(card_id=card_id, tag_id=tag_id))
    db.add(models.Transaction(card_id=card_id, company_name="Сделка на удаление", amount=500.0))
    db.add(models.ActivityLog(card_id=card_id, action="Создана"))
    db.add(models.Task(title="Задача по сделке", card_id=card_id))
    db.commit()

    r = client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    assert not _exists(db, models.Card, card_id)
    # CASCADE
    assert _count(db, models.CardAttachment, card_id=card_id) == 0
    assert _count(db, models.CardChecklist, card_id=card_id) == 0
    assert _count(db, models.CardTag, card_id=card_id) == 0
    # SET NULL — записи живут, но больше не указывают на удалённую сделку
    assert _count(db, models.Transaction, card_id=card_id) == 0
    assert _count(db, models.Transaction) == 1, "транзакция должна остаться, но без card_id"
    assert _count(db, models.ActivityLog, card_id=card_id) == 0
    assert _count(db, models.ActivityLog) == 1
    assert _count(db, models.Task, card_id=card_id) == 0
    assert _count(db, models.Task) == 1, "задача должна остаться, но без card_id"


def test_delete_card_permanently_requires_admin(client, manager, db, make_card):
    _, h = manager
    card = make_card()
    card_id = card.id
    r = client.delete(f"/kanban/cards/{card_id}/permanent", headers=h)
    assert r.status_code == 403
    _reload(db)
    assert _exists(db, models.Card, card_id)


def test_no_fk_violations_after_card_deletion(client, admin, db, make_card,
                                              foreign_key_violations):
    _, h = admin
    card = make_card(total_amount=100.0)
    card_id = card.id
    db.add(models.Transaction(card_id=card_id, amount=100.0))
    db.add(models.CardAttachment(card_id=card_id, file_name="x", file_path="/tmp/x"))
    db.commit()

    assert client.delete(f"/kanban/cards/{card_id}/permanent", headers=h).status_code == 200
    assert foreign_key_violations() == []


# ---------------------------------------------------------------------------
# Удаление клиента
# ---------------------------------------------------------------------------

def test_delete_client_unlinks_cards_groups_and_tasks(client, admin, db):
    _, h = admin
    client_obj = models.Client(name="ООО Клиент")
    db.add(client_obj)
    db.commit()
    client_id = client_obj.id

    db.add(models.Card(title="Сделка клиента", client_id=client_id, total_amount=100.0))
    db.add(models.WriteoffGroup(name="Группа клиента", client_id=client_id))
    db.add(models.Task(title="Задача клиента", client_id=client_id))
    db.commit()

    r = client.delete(f"/clients/{client_id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    assert not _exists(db, models.Client, client_id)
    assert _count(db, models.Card, client_id=client_id) == 0
    assert _count(db, models.Card) == 1, "сделка должна остаться без клиента"
    assert _count(db, models.WriteoffGroup, client_id=client_id) == 0
    assert _count(db, models.WriteoffGroup) == 1
    assert _count(db, models.Task, client_id=client_id) == 0
    assert _count(db, models.Task) == 1


def test_delete_client_requires_admin(client, manager, db):
    _, h = manager
    c = models.Client(name="Клиент")
    db.add(c)
    db.commit()
    assert client.delete(f"/clients/{c.id}", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# Удаление поставщика
# ---------------------------------------------------------------------------

def test_delete_supplier_unlinks_checklists_and_nakladnye(client, manager, db):
    """И чек-листы, и накладные должны потерять ссылку, но не запись."""
    _, h = manager
    sup = models.Supplier(name="Поставщик")
    db.add(sup)
    db.commit()
    sup_id = sup.id

    card = models.Card(title="Сделка", total_amount=100.0)
    db.add(card)
    db.commit()
    db.add(models.CardChecklist(card_id=card.id, company_name="Закупка",
                                amount=50.0, supplier_id=sup_id))
    db.add(models.Nakladnaya(supplier_id=sup_id, supplier_name="Поставщик",
                             doc_type="ТТН", doc_series="АБ", doc_number="123",
                             amount=200.0, vat_amount=33.33))
    db.commit()

    r = client.delete(f"/suppliers/{sup_id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    assert not _exists(db, models.Supplier, sup_id)
    assert _count(db, models.CardChecklist, supplier_id=sup_id) == 0
    assert _count(db, models.CardChecklist) == 1, "чек-лист должен остаться"
    assert _count(db, models.Nakladnaya, supplier_id=sup_id) == 0
    assert _count(db, models.Nakladnaya) == 1, "накладная должна остаться"


def test_delete_supplier_allowed_for_manager_is_inconsistent_with_clients(
        client, manager, db):
    """Фиксируем несоответствие модели доступа: DELETE /clients требует admin,
    а DELETE /suppliers — нет. Тест документирует текущее поведение."""
    _, h = manager
    sup = models.Supplier(name="Поставщик 2")
    db.add(sup)
    db.commit()
    r = client.delete(f"/suppliers/{sup.id}", headers=h)
    assert r.status_code == 200, "сейчас manager может удалить поставщика"


# ---------------------------------------------------------------------------
# Удаление пользователя
# ---------------------------------------------------------------------------

def test_delete_user_unlinks_artifacts_and_removes_notifications(client, admin, db, make_user):
    _, h = admin
    victim, _ = make_user("manager", username="victim")
    victim_id = victim.id

    db.add(models.Card(title="Сделка жертвы", owner_id=victim_id, total_amount=100.0))
    db.add(models.ActivityLog(user_id=victim_id, action="Действие"))
    db.add(models.Task(title="Задача", assignee_id=victim_id, creator_id=victim_id))
    db.add(models.Notification(user_id=victim_id, type="info", title="Уведомление"))
    db.commit()

    r = client.delete(f"/auth/users/{victim_id}", headers=h)
    assert r.status_code == 200, r.text

    _reload(db)
    assert not _exists(db, models.User, victim_id)
    assert _count(db, models.Card, owner_id=victim_id) == 0
    assert _count(db, models.Card) == 1
    assert _count(db, models.ActivityLog, user_id=victim_id) == 0
    assert _count(db, models.ActivityLog) == 1
    assert _count(db, models.Task, assignee_id=victim_id) == 0
    assert _count(db, models.Notification, user_id=victim_id) == 0
    assert _count(db, models.Notification) == 0


def test_no_fk_violations_after_bulk_deletions(client, admin, db, make_user, make_card,
                                               foreign_key_violations):
    """Серия удалений разных сущностей не должна оставлять нарушений FK."""
    _, h = admin
    u, _ = make_user("manager", username="bulk_victim")
    sup = models.Supplier(name="S")
    cli = models.Client(name="C")
    db.add_all([sup, cli])
    db.commit()
    u_id, sup_id, cli_id = u.id, sup.id, cli.id

    card = make_card(title="C", owner_id=u_id, client_id=cli_id, total_amount=100.0)
    card_id = card.id
    db.add(models.Transaction(card_id=card_id, amount=100.0))
    db.add(models.CardChecklist(card_id=card_id, company_name="x", supplier_id=sup_id))
    db.add(models.Nakladnaya(supplier_id=sup_id, supplier_name="S", doc_number="1"))
    db.commit()

    assert client.delete(f"/suppliers/{sup_id}", headers=h).status_code == 200
    assert client.delete(f"/clients/{cli_id}", headers=h).status_code == 200
    assert client.delete(f"/auth/users/{u_id}", headers=h).status_code == 200
    assert client.delete(f"/kanban/cards/{card_id}/permanent", headers=h).status_code == 200

    assert foreign_key_violations() == []


# ---------------------------------------------------------------------------
# Теги и задачи
# ---------------------------------------------------------------------------

def test_delete_tag_removes_card_links(client, manager, db, make_card):
    _, h = manager
    tag = models.Tag(name="важно")
    card = make_card()
    db.add(tag)
    db.commit()
    tag_id, card_id = tag.id, card.id
    db.add(models.CardTag(card_id=card_id, tag_id=tag_id))
    db.commit()

    assert client.delete(f"/tags/{tag_id}", headers=h).status_code == 200
    _reload(db)
    assert _count(db, models.CardTag, tag_id=tag_id) == 0
    assert _exists(db, models.Card, card_id)


def test_delete_task_cascades_checklist_items(client, manager, db):
    """Задача должна быть видна пользователю (assignee/creator), иначе 404.

    Починено в Фазе 2 (2026-09-11): backref checklist_items получил
    passive_deletes=True, поэтому подзадачи удаляет база по ON DELETE CASCADE,
    а ORM больше не пытается обнулить NOT NULL-колонку task_id.
    """
    user, h = manager
    task = models.Task(title="Задача с подзадачами", creator_id=user.id,
                       assignee_id=user.id)
    db.add(task)
    db.commit()
    task_id = task.id
    db.add(models.TaskChecklistItem(task_id=task_id, title="шаг 1"))
    db.add(models.TaskChecklistItem(task_id=task_id, title="шаг 2"))
    db.commit()

    r = client.delete(f"/tasks/{task_id}", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    assert _count(db, models.TaskChecklistItem, task_id=task_id) == 0


def test_delete_task_without_checklist_works(client, manager, db):
    """Изолируем дефект выше: задача БЕЗ подзадач удаляется нормально."""
    user, h = manager
    task = models.Task(title="Простая задача", creator_id=user.id, assignee_id=user.id)
    db.add(task)
    db.commit()
    task_id = task.id

    r = client.delete(f"/tasks/{task_id}", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    assert not _exists(db, models.Task, task_id)


def test_task_not_visible_to_stranger(client, manager, make_user, db):
    """Чужая задача без assignee/creator — 404, не 403 и не 200."""
    owner, _ = make_user("manager", username="task_owner")
    _, h = manager
    task = models.Task(title="Чужая задача", creator_id=owner.id, assignee_id=owner.id)
    db.add(task)
    db.commit()
    assert client.delete(f"/tasks/{task.id}", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# Кастомные объекты и воркфлоу: ссылки без ondelete
# ---------------------------------------------------------------------------

def test_delete_custom_record_referenced_by_value_relation(client, admin, db):
    """Починено в Фазе 2 (2026-09-11): ссылки value_relation отвязываются,
    а не удаляются вместе с чужими записями."""
    _, h = admin
    otype = models.CustomObjectType(name="obj", label="Объект")
    db.add(otype)
    db.commit()
    fdef = models.CustomFieldDef(object_type_id=otype.id, name="ref",
                                 label="Ссылка", field_type="relation")
    db.add(fdef)
    db.commit()

    target = models.CustomRecord(object_type_id=otype.id)
    source = models.CustomRecord(object_type_id=otype.id)
    db.add_all([target, source])
    db.commit()
    target_id = target.id
    db.add(models.CustomFieldValue(record_id=source.id, field_def_id=fdef.id,
                                   value_relation=target_id))
    db.commit()

    r = client.delete(f"/custom/records/{target_id}", headers=h)
    assert r.status_code != 500, f"500 при удалении записи со ссылкой: {r.text}"

    _reload(db)
    assert _count(db, models.CustomFieldValue, value_relation=target_id) == 0, \
        "осталась висячая ссылка value_relation"


def test_delete_custom_record_without_references(client, admin, db):
    """Базовый случай работает: свои значения полей удаляются вместе с записью."""
    _, h = admin
    otype = models.CustomObjectType(name="obj2", label="Объект 2")
    db.add(otype)
    db.commit()
    fdef = models.CustomFieldDef(object_type_id=otype.id, name="t",
                                 label="Текст", field_type="text")
    db.add(fdef)
    db.commit()
    rec = models.CustomRecord(object_type_id=otype.id)
    db.add(rec)
    db.commit()
    rec_id = rec.id
    db.add(models.CustomFieldValue(record_id=rec_id, field_def_id=fdef.id,
                                   value_text="значение"))
    db.commit()

    r = client.delete(f"/custom/records/{rec_id}", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    assert _count(db, models.CustomFieldValue, record_id=rec_id) == 0


def test_delete_custom_object_type_cascades_defs(client, admin, db):
    _, h = admin
    otype = models.CustomObjectType(name="obj3", label="Объект 3")
    db.add(otype)
    db.commit()
    otype_id = otype.id
    db.add(models.CustomFieldDef(object_type_id=otype_id, name="f",
                                 label="F", field_type="text"))
    db.commit()

    r = client.delete(f"/custom/objects/{otype_id}", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    assert _count(db, models.CustomFieldDef, object_type_id=otype_id) == 0


def test_delete_workflow_step_with_children(client, admin, db):
    """Починено в Фазе 2 (2026-09-11): дочерние шаги поднимаются на верхний
    уровень вместо удаления — их настройки дороже потери вложенности."""
    _, h = admin
    wf = models.Workflow(name="wf")
    db.add(wf)
    db.commit()
    parent = models.WorkflowStep(workflow_id=wf.id, parent_step_id=None,
                                 step_type="action", config="{}")
    db.add(parent)
    db.commit()
    parent_id = parent.id
    db.add(models.WorkflowStep(workflow_id=wf.id, parent_step_id=parent_id,
                               step_type="action", config="{}"))
    db.commit()

    r = client.delete(f"/workflows/steps/{parent_id}", headers=h)
    assert r.status_code != 500, f"500 при удалении шага с детьми: {r.text}"

    _reload(db)
    assert _count(db, models.WorkflowStep, parent_step_id=parent_id) == 0, \
        "остались шаги со ссылкой на удалённого родителя"


def test_delete_workflow_cascades_parts(client, admin, db):
    _, h = admin
    wf = models.Workflow(name="wf2")
    db.add(wf)
    db.commit()
    wf_id = wf.id
    db.add(models.WorkflowTrigger(workflow_id=wf_id, trigger_type="manual", config="{}"))
    db.add(models.WorkflowStep(workflow_id=wf_id, step_type="action", config="{}"))
    db.commit()

    r = client.delete(f"/workflows/{wf_id}", headers=h)
    assert r.status_code == 200, r.text
    _reload(db)
    assert _count(db, models.WorkflowTrigger, workflow_id=wf_id) == 0
    assert _count(db, models.WorkflowStep, workflow_id=wf_id) == 0


# ---------------------------------------------------------------------------
# Денормализованные снимки и агрегаты — живые дефекты
# ---------------------------------------------------------------------------

@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: suppliers_router.update_supplier меняет только "
                          "Supplier. Снимки nakladnye.supplier_name и "
                          "card_checklists.company_name остаются со старым именем.")
def test_rename_supplier_updates_snapshots(client, manager, db):
    _, h = manager
    sup = models.Supplier(name="Старое имя")
    db.add(sup)
    db.commit()
    sup_id = sup.id
    card = models.Card(title="Сделка", total_amount=100.0)
    db.add(card)
    db.commit()
    db.add(models.CardChecklist(card_id=card.id, company_name="Старое имя", supplier_id=sup_id))
    db.add(models.Nakladnaya(supplier_id=sup_id, supplier_name="Старое имя", doc_number="1"))
    db.commit()

    r = client.patch(f"/suppliers/{sup_id}", headers=h, json={"name": "Новое имя"})
    assert r.status_code == 200, r.text

    _reload(db)
    nakl = db.query(models.Nakladnaya).filter(models.Nakladnaya.supplier_id.is_(None)).first() \
        or db.query(models.Nakladnaya).first()
    assert nakl.supplier_name == "Новое имя"


@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="ЖИВОЙ ДЕФЕКТ: writeoff_groups.total_amount пересчитывается "
                          "только в эндпоинтах группы. Смена cards.total_amount через "
                          "PATCH /cards/{id} сумму группы не обновляет.")
def test_card_total_change_recomputes_group_total(client, manager, db, make_card):
    _, h = manager
    group = models.WriteoffGroup(name="Группа", total_amount=0.0)
    db.add(group)
    db.commit()
    group_id = group.id
    c1 = make_card(title="A", total_amount=100.0, status="Сборка", writeoff_group_id=group_id)
    make_card(title="B", total_amount=200.0, status="Сборка", writeoff_group_id=group_id)

    r = client.patch(f"/cards/{c1.id}", headers=h, json={"total_amount": 500.0})
    assert r.status_code == 200, r.text

    _reload(db)
    fresh = db.query(models.WriteoffGroup).filter(models.WriteoffGroup.id == group_id).first()
    assert round(float(fresh.total_amount), 2) == 700.0
