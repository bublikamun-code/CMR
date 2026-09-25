"""Cross-tenant IDOR matrix для закрытых tenant-owned маршрутов."""
import io

import pytest

import auth
import models


@pytest.mark.parametrize("method,path,body", [
    ("get", "/clients/{client_id}", None),
    ("patch", "/clients/{client_id}", {"name": "Не должен обновиться"}),
    ("get", "/clients/{client_id}/cards", None),
    ("get", "/clients/{client_id}/balance", None),
    ("post", "/clients/{client_id}/payments", {"amount": 1}),
    ("get", "/kanban/cards/{card_id}", None),
    ("patch", "/kanban/cards/{card_id}/status", {"status": "В работе"}),
    ("post", "/kanban/cards/{card_id}/checklists", {
        "company_name": "Чужой поставщик", "amount": 1,
    }),
    ("get", "/suppliers/{supplier_id}", None),
    ("patch", "/suppliers/{supplier_id}", {"name": "Не должен обновиться"}),
    ("get", "/suppliers/{supplier_id}/purchases", None),
    ("patch", "/dictionaries/stores/{store_id}", {"name": "Не должен обновиться"}),
    ("patch", "/tasks/{task_id}", {"title": "Не должен обновиться"}),
    ("post", "/tags/cards/{card_id}/tags/{tag_id}", None),
    ("get", "/attachments/{attachment_id}/download", None),
    ("get", "/checklists/{checklist_id}/invoice/download", None),
])
def test_object_routes_hide_other_tenant_with_404(
    client, db, two_tenant_users, method, path, body
):
    tenant = two_tenant_users["second_tenant_id"]
    other = models.Client(name="Другой tenant", tenant_id=tenant)
    supplier = models.Supplier(name="Другой поставщик", tenant_id=tenant)
    store = models.StoreLocation(name="Другой магазин", tenant_id=tenant)
    tag = models.Tag(name="Другой тег", tenant_id=tenant)
    db.add_all([other, supplier, store, tag])
    db.flush()
    card = models.Card(
        title="Другая сделка", status="Новый запрос", is_deleted=False,
        tenant_id=tenant,
    )
    db.add(card)
    db.flush()
    task = models.Task(
        title="Другая задача", creator_id=two_tenant_users["second_user"].id,
        tenant_id=tenant,
    )
    transaction = models.Transaction(
        company_name="Другой tenant", amount=1, store_location="Другой",
        card_id=card.id, tenant_id=tenant,
    )
    db.add_all([task, transaction])
    db.flush()
    checklist = models.CardChecklist(
        company_name="Другая закупка", amount=1, card_id=card.id,
    )
    attachment = models.CardAttachment(
        file_name="other.pdf", file_path="/tmp/does-not-matter.pdf",
        card_id=card.id,
    )
    db.add_all([checklist, attachment])
    db.commit()

    replacements = {
        "client_id": other.id,
        "supplier_id": supplier.id,
        "store_id": store.id,
        "tag_id": tag.id,
        "card_id": card.id,
        "task_id": task.id,
        "attachment_id": attachment.id,
        "checklist_id": checklist.id,
    }
    response = getattr(client, method)(
        path.format(**replacements),
        headers=two_tenant_users["first_headers"],
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == 404, (method, path, response.text)


def test_upload_paths_check_parent_before_file_processing(
    client, db, two_tenant_users
):
    tenant = two_tenant_users["second_tenant_id"]
    card = models.Card(
        title="Чужое вложение", status="Новый запрос", tenant_id=tenant
    )
    db.add(card)
    db.commit()

    response = client.post(
        f"/cards/{card.id}/attachments",
        headers=two_tenant_users["first_headers"],
        files={"file": ("probe.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
    )
    assert response.status_code == 404


def test_lists_and_aggregates_are_tenant_scoped(client, db, two_tenant_users):
    first = two_tenant_users["first_tenant_id"]
    second = two_tenant_users["second_tenant_id"]
    first_client = models.Client(name="Первый tenant", tenant_id=first)
    second_client = models.Client(name="Второй tenant", tenant_id=second)
    first_card = models.Card(
        title="Первая сделка", status="Новый запрос", client_id=first_client.id,
        tenant_id=first,
    )
    db.add_all([first_client, second_client, first_card])
    db.commit()

    clients = client.get(
        "/clients", headers=two_tenant_users["first_headers"]
    ).json()
    cards = client.get(
        "/kanban/cards", headers=two_tenant_users["first_headers"]
    ).json()
    assert [item["name"] for item in clients] == ["Первый tenant"]
    assert [item["title"] for item in cards] == ["Первая сделка"]
    assert second_client.id not in {item["id"] for item in clients}
    assert second_client.id not in {item["id"] for item in cards}


def test_superadmin_keeps_explicit_global_read_scope(
    client, db, two_tenant_users, superadmin
):
    superadmin_user, _ = superadmin
    other_client = models.Client(
        name="Глобальный объект",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add(other_client)
    db.commit()
    token = auth.create_access_token({
        "sub": superadmin_user.username,
        "tenant_id": superadmin_user.tenant_id,
        "pv": auth.password_version(superadmin_user.hashed_password),
    })
    response = client.get(
        f"/clients/{other_client.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_jwt_tenant_claim_must_match_current_user(db, make_token, client):
    user, headers = make_token(
        claims=lambda current: {
            "sub": current.username,
            "tenant_id": 202,
            "pv": auth.password_version(current.hashed_password),
        }
    )
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_known_object_permission_keeps_403_policy(
    client, db, make_user
):
    author, _ = make_user("manager", username="comment_author")
    editor, editor_headers = make_user("manager", username="comment_editor")
    entry = models.ActivityLog(
        user_id=author.id,
        action="Комментарий",
        details="Чужой комментарий",
        tenant_id=editor.tenant_id,
    )
    db.add(entry)
    db.commit()
    response = client.patch(
        f"/activity/{entry.id}",
        headers=editor_headers,
        json={"details": "Правка"},
    )
    assert response.status_code == 403
