"""Аудит 10.09: регресс на исправления.

B1 эскалация роли в create_user, B3 перепроведение накладной сверх остатка,
B4 честный writeoff-status (остаток не считается накладной), B2 пересчёт
группы при выводе карточки, B6 валидация card_id в задачах, B7 честная 404
воркфлоу-триггеров, B12 карточка из корзины не воскрешается, B13 кривая дата
custom-записи = 400.
"""
import pytest

from conftest import USERS
from database import SessionLocal
import models


def _create_card(client, title, amount, **extra):
    payload = {"title": title, "status": "Новый запрос", "total_amount": amount,
               "store_location": "Тестовый магазин"}
    payload.update(extra)
    response = client.post("/kanban/cards", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _to_assembly(client, card_id, store="Тестовый магазин"):
    r = client.post(f"/payments/trigger_from_card/{card_id}", json={"store_location": store})
    assert r.status_code == 200, r.text
    # trigger_from_card создаёт запись реестра, статус переводит кнопка UI
    r = client.patch(f"/kanban/cards/{card_id}/status", json={"status": "Сборка"})
    assert r.status_code == 200, r.text


# --- B1: админ не создаёт superadmin ----------------------------------

def test_admin_cannot_create_superadmin(admin_client):
    r = admin_client.post("/auth/users", json={
        "username": "evil_super", "password": "evil-pass-123", "role": "superadmin"})
    assert r.status_code == 403, "эскалация роли должна быть закрыта"


def test_admin_cannot_create_admin(admin_client):
    r = admin_client.post("/auth/users", json={
        "username": "evil_admin", "password": "evil-pass-123", "role": "admin"})
    assert r.status_code == 403


def test_superadmin_still_creates_admin(superadmin_client):
    r = superadmin_client.post("/auth/users", json={
        "username": "fresh_admin", "password": "fresh-pass-123", "role": "admin"})
    assert r.status_code == 200, r.text
    # прибираем
    db = SessionLocal()
    try:
        u = db.query(models.User).filter(models.User.username == "fresh_admin").first()
        if u:
            db.delete(u)
            db.commit()
    finally:
        db.close()


# --- B3: накладная больше остатка отклоняется --------------------------

def test_add_invoice_rejects_overissue(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест перепроведения", 500.0)
    _to_assembly(superadmin_client, card_id)
    r = superadmin_client.post(f"/payments/cards/{card_id}/invoices", json={
        "invoice_number": "ПЕРЕ-1", "amount": 9999.0})
    assert r.status_code == 400, "накладную сверх остатка нужно отклонять"
    # в пределах остатка — работает
    r2 = superadmin_client.post(f"/payments/cards/{card_id}/invoices", json={
        "invoice_number": "ПЕРЕ-2", "amount": 200.0})
    assert r2.status_code == 200, r2.text


# --- B4: остаток не считается накладной --------------------------------

def test_writeoff_status_ignores_remainder_row(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест статуса списания", 1000.0)
    _to_assembly(superadmin_client, card_id)
    r = superadmin_client.get(f"/payments/cards/{card_id}/writeoff-status")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["total_invoices"] == 0, "запись-остаток не накладная"
    assert st["invoices_amount"] == 0.0
    assert st["fully_covered"] is False, "без накладных покрытие неполное"
    assert st["card_amount"] == 1000.0


# --- B2: вывод карточки пересчитывает группу и распускает пустую --------

def test_group_total_recomputed_and_empty_group_disbanded(superadmin_client):
    c1 = _create_card(superadmin_client, "Группа-тест 1", 300.0, client_id=None)
    c2 = _create_card(superadmin_client, "Группа-тест 2", 700.0, client_id=None)
    _to_assembly(superadmin_client, c1)
    _to_assembly(superadmin_client, c2)
    r = superadmin_client.post("/writeoffs/groups/", json={"card_ids": [c1, c2], "name": "Аудит-группа"})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]
    assert r.json()["total_amount"] == 1000.0

    # выводим вторую карточку — сумма группы становится 300
    r = superadmin_client.delete(f"/writeoffs/groups/{group_id}/cards/{c2}")
    assert r.status_code == 200, r.text
    g = superadmin_client.get(f"/writeoffs/groups/{group_id}").json()
    assert g["total_amount"] == 300.0, "сумма группы должна считаться без выведенной карточки"

    # выводим последнюю — группа распускается
    r = superadmin_client.delete(f"/writeoffs/groups/{group_id}/cards/{c1}")
    assert r.status_code == 200, r.text
    gone = superadmin_client.get(f"/writeoffs/groups/{group_id}")
    assert gone.status_code == 404, "пустая группа должна распуститься"


# --- B6: задачи с несуществующей сделкой = 404, а не 409 ---------------

def test_task_with_unknown_card_rejected_cleanly(superadmin_client):
    r = superadmin_client.post("/tasks", json={"title": "Тест B6", "card_id": 99999999})
    assert r.status_code == 404
    assert "Сделка" in r.json()["detail"]


# --- B7: триггер несуществующего воркфлоу = 404 ------------------------

def test_workflow_trigger_unknown_workflow_404(superadmin_client):
    r = superadmin_client.post("/workflows/999999/triggers",
                               json={"trigger_type": "manual", "config": {}})
    assert r.status_code == 404, "должна быть честная 404, а не 409"


# --- B12: карточка в корзине не воскрешается ---------------------------

def test_remove_from_writeoff_keeps_trashed_card_hidden(superadmin_client):
    card_id = _create_card(superadmin_client, "Тест корзины B12", 250.0)
    _to_assembly(superadmin_client, card_id)
    # в корзину
    r = superadmin_client.delete(f"/kanban/cards/{card_id}")
    assert r.status_code == 200, r.text
    # убрать из списания — карточка должна остаться в корзине
    r = superadmin_client.delete(f"/payments/cards/{card_id}/writeoff")
    assert r.status_code == 200, r.text
    card = superadmin_client.get(f"/kanban/cards/{card_id}")
    if card.status_code == 200:
        assert card.json()["is_deleted"] is True, "корзина — значит корзина"
    else:
        assert card.status_code == 404, "удалённая карточка не видна в общем списке"


# --- B13: кривая дата в custom-записи = 400 ----------------------------

def test_custom_record_bad_date_returns_400(superadmin_client):
    r = superadmin_client.post("/custom/objects", json={"name": "audit_obj_b13", "label": "Аудит B13"})
    assert r.status_code == 200, r.text
    obj_id = r.json()["id"]
    r = superadmin_client.post(f"/custom/objects/{obj_id}/fields", json={
        "name": "when", "label": "Когда", "field_type": "date"})
    assert r.status_code == 200, r.text
    r = superadmin_client.post(f"/custom/objects/{obj_id}/records", json={
        "data": {"when": "не дата вовсе"}})
    assert r.status_code == 400, "кривая дата должна давать 400"
    # прибираем
    superadmin_client.delete(f"/custom/objects/{obj_id}")
