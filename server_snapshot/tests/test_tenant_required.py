"""Регрессии обязательного tenant_id у записей реестра."""
import sys

from sqlalchemy import event

import models

# Pytest обычно загружает conftest как ``conftest``; при импорте как пакета
# он доступен как ``tests.conftest``. Берём уже загруженный модуль, иначе
# импорт второго экземпляра зарегистрирует ещё один маскирующий слушатель.
cft = sys.modules.get("conftest")
if cft is None:
    from tests import conftest as cft


def _without_test_tenant_listener():
    """Снять маскирующий слушатель conftest перед реальным запросом."""
    event.remove(models.Base, "before_insert", cft._set_test_tenant)


def _restore_test_tenant_listener():
    event.listen(models.Base, "before_insert", cft._set_test_tenant, propagate=True)


def test_registry_transactions_inherit_card_tenant_without_test_listener(
    client, two_tenant_users, db, make_card
):
    """Остаток, накладная и документ получают tenant той же сделки."""
    headers = two_tenant_users["second_headers"]
    card = make_card(
        title="Сделка второго tenant",
        total_amount=1000.0,
        status="Новый запрос",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    card_id = card.id

    _without_test_tenant_listener()
    try:
        response = client.patch(
            f"/kanban/cards/{card_id}/status",
            headers=headers,
            json={"status": "Сборка"},
        )
        assert response.status_code == 200, response.text

        response = client.post(
            f"/payments/cards/{card_id}/issue-invoice",
            headers=headers,
            json={
                "invoice_number": "ТТН-tenant",
                "invoice_date": "2026-09-25",
                "amount": 400.0,
                "store_location": "Магазин",
            },
        )
        assert response.status_code == 200, response.text
    finally:
        _restore_test_tenant_listener()

    db.expire_all()
    transactions = db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id
    ).all()
    assert len(transactions) == 3
    assert {row.tenant_id for row in transactions} == {card.tenant_id}
    assert {row.tenant_id for row in transactions if row.is_document} == {
        card.tenant_id
    }
    assert any(
        not row.is_document and (row.invoice_number or "").strip() == "ТТН-tenant"
        for row in transactions
    )
    assert any(
        not row.is_document and not (row.invoice_number or "").strip()
        for row in transactions
    )
