"""Единая политика денежного ввода: finite, копейки, Numeric(12,2), знак."""
from pydantic import ValidationError
import pytest

import models
from routers.payments_router import InvoiceCreateRequest, IssueInvoiceRequest
from routers.writeoff_groups_router import IssueGroupInvoiceRequest
import schemas


pytestmark = pytest.mark.money


INVALID_MONEY = [float("nan"), float("inf"), float("-inf")]


def _raw_json_request(client, method, path, headers, payload):
    return client.request(
        method,
        path,
        headers={**headers, "content-type": "application/json"},
        content=payload,
    )


@pytest.mark.parametrize("value", INVALID_MONEY)
def test_shared_money_validator_rejects_non_finite(value):
    with pytest.raises(ValueError, match="конечным"):
        schemas.validate_money(value)


@pytest.mark.parametrize("value", [-0.01, 1.001, 9999999999.991])
def test_shared_money_validator_rejects_invalid_sign_precision_or_range(value):
    with pytest.raises(ValueError):
        schemas.validate_money(value)


def test_money_boundaries_and_float_storage_contract():
    validated = schemas.validate_money(9999999999.99)
    assert validated == 9999999999.99
    assert isinstance(validated, float)
    assert schemas.validate_money(0.0) == 0.0


@pytest.mark.parametrize("value", [0, 1, 1.25, 9999999999])
def test_numeric_json_money_keeps_float_contract(value):
    card = schemas.CardCreate(title="Сделка", total_amount=value)
    assert card.total_amount == float(value)
    assert isinstance(card.total_amount, float)


@pytest.mark.parametrize(
    "value",
    [True, False, "1.25", object(), 10 ** 10000],
    ids=["true", "false", "string", "object", "huge-int"],
)
def test_direct_money_validator_normalizes_bad_types_to_value_error(value):
    with pytest.raises(ValueError, match="числом|превышать"):
        schemas.validate_money(value)


@pytest.mark.parametrize("value", [True, False])
def test_card_create_rejects_boolean_money(value):
    with pytest.raises(ValidationError):
        schemas.CardCreate(title="Сделка", total_amount=value)


@pytest.mark.parametrize("value", [True, False])
def test_card_create_endpoint_rejects_boolean_money_422(
        client, manager, value):
    _, headers = manager
    response = client.post(
        "/kanban/cards",
        headers=headers,
        json={"title": "Сделка", "total_amount": value},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("value", [True, False])
def test_transaction_patch_schema_and_endpoint_reject_boolean_money(
        client, manager, make_card, make_transaction, value):
    _, headers = manager
    with pytest.raises(ValidationError):
        schemas.TransactionUpdate(amount=value)

    card = make_card(total_amount=100.0)
    transaction = make_transaction(card, amount=25.0)
    response = client.patch(
        f"/payments/transactions/{transaction.id}",
        headers=headers,
        json={"amount": value},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("model,payload", [
    (schemas.CardCreate, {"title": "Сделка", "total_amount": float("nan")}),
    (schemas.TransactionCreate, {"company_name": "Плательщик", "amount": float("inf")}),
    (schemas.ClientPaymentCreate, {"amount": float("-inf")}),
    (schemas.ChecklistCreate, {"company_name": "Закупка", "amount": 1.001}),
    (schemas.NakladnayaCreate, {"amount": 100.001}),
])
def test_money_schemas_reject_bad_values(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_card_patch_rejects_non_finite(client, manager, make_card, literal):
    _, headers = manager
    card = make_card(total_amount=100.0)
    response = _raw_json_request(
        client, "PATCH", f"/cards/{card.id}", headers,
        f'{{"total_amount": {literal}}}',
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("value", [-1.0, 1.001, 9999999999.991])
def test_card_patch_rejects_invalid_sign_precision_or_range(
        client, manager, make_card, value
):
    _, headers = manager
    card = make_card(total_amount=100.0)
    response = client.patch(
        f"/cards/{card.id}", headers=headers, json={"total_amount": value}
    )
    assert response.status_code == 422, response.text


def test_paid_amount_is_validated_in_both_card_patch_paths(
        client, manager, db, make_card
):
    _, headers = manager
    card = make_card(total_amount=100.0)
    for path, payload in [
        (f"/cards/{card.id}", {"paid_amount": -1.0}),
        (f"/cards/{card.id}/payment", {"paid_amount": -1.0}),
    ]:
        response = client.patch(path, headers=headers, json=payload)
        assert response.status_code == 422, response.text
    db.expire_all()
    db.refresh(card)
    assert float(card.paid_amount) == 0.0


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_transaction_patch_rejects_non_finite_without_mutation(
        client, manager, db, make_card, make_transaction, literal
):
    _, headers = manager
    card = make_card(total_amount=100.0)
    transaction = make_transaction(card, amount=25.0)

    response = _raw_json_request(
        client, "PATCH", f"/payments/transactions/{transaction.id}", headers,
        f'{{"amount": {literal}}}',
    )
    assert response.status_code == 422, response.text
    db.expire_all()
    db.refresh(transaction)
    assert float(transaction.amount) == 25.0


@pytest.mark.parametrize("value", [-1.0, 1.001, 9999999999.991])
def test_transaction_patch_rejects_invalid_money_without_mutation(
        client, manager, db, make_card, make_transaction, value
):
    _, headers = manager
    card = make_card(total_amount=100.0)
    transaction = make_transaction(card, amount=25.0)

    response = client.patch(
        f"/payments/transactions/{transaction.id}",
        headers=headers,
        json={"amount": value},
    )
    assert response.status_code == 422, response.text
    db.expire_all()
    db.refresh(transaction)
    assert float(transaction.amount) == 25.0


def test_transaction_patch_over_available_does_not_commit_partial_state(
        client, manager, db, make_card, make_transaction
):
    _, headers = manager
    card = make_card(total_amount=100.0, status="На списание")
    transaction = make_transaction(
        card, amount=10.0, invoice_number="ТН-BAD"
    )
    make_transaction(card, amount=80.0, invoice_number="ТН-OTHER")

    response = client.patch(
        f"/payments/transactions/{transaction.id}",
        headers=headers,
        json={"amount": 26.0},
    )
    assert response.status_code == 400, response.text
    db.expire_all()
    db.refresh(transaction)
    assert float(transaction.amount) == 10.0
    assert transaction.invoice_number == "ТН-BAD"


@pytest.mark.parametrize("request_model", [
    InvoiceCreateRequest,
    IssueInvoiceRequest,
])
def test_invoice_request_models_reject_bad_money(request_model):
    payload = {"amount": float("nan")}
    if request_model is IssueInvoiceRequest:
        payload["invoice_number"] = "ТН-1"
    with pytest.raises(ValidationError):
        request_model(**payload)


def test_issue_group_invoice_request_rejects_bad_money():
    with pytest.raises(ValidationError):
        IssueGroupInvoiceRequest(invoice_number="Г-1", amount=-0.01)


def test_group_writeoff_endpoint_rejects_non_finite(
        client, manager, make_card
):
    _, headers = manager
    first = make_card(total_amount=10.0, status="Сборка")
    second = make_card(total_amount=20.0, status="Сборка")
    created = client.post(
        "/writeoffs/groups/", headers=headers,
        json={"card_ids": [first.id, second.id], "name": "Группа"},
    )
    assert created.status_code == 200, created.text
    response = _raw_json_request(
        client, "POST",
        f"/writeoffs/groups/{created.json()['id']}/issue-invoice",
        headers,
        '{"invoice_number": "Г-1", "amount": NaN}',
    )
    assert response.status_code == 422, response.text


def test_client_payment_endpoint_rejects_non_finite(client, manager, db):
    _, headers = manager
    client_row = models.Client(name="Клиент")
    db.add(client_row)
    db.commit()
    response = _raw_json_request(
        client, "POST", f"/clients/{client_row.id}/payments", headers,
        '{"amount": NaN}',
    )
    assert response.status_code == 422, response.text
    assert db.query(models.ClientPayment).count() == 0


@pytest.mark.parametrize("field", ["amount", "vat_amount", "amount_no_vat"])
def test_nakladnaya_endpoint_rejects_non_finite(
        client, manager, field
):
    _, headers = manager
    response = _raw_json_request(
        client, "POST", "/nakladnye", headers,
        f'{{"doc_number": "money-test", "{field}": Infinity}}',
    )
    assert response.status_code == 422, response.text


def test_nakladnaya_partial_patch_rejects_non_finite(
        client, manager, db
):
    _, headers = manager
    created = client.post(
        "/nakladnye", headers=headers,
        json={"doc_number": "partial", "amount": 100.0, "vat_amount": 16.0},
    )
    assert created.status_code == 200, created.text
    row_id = created.json()["id"]
    response = _raw_json_request(
        client, "PATCH", f"/nakladnye/{row_id}", headers,
        '{"vat_amount": NaN}',
    )
    assert response.status_code == 422, response.text
    db.expire_all()
    row = db.query(models.Nakladnaya).filter(models.Nakladnaya.id == row_id).one()
    assert float(row.vat_amount) == 16.0
