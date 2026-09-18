"""Условие оплаты карточки (payment_terms) — Этап 2.4 плана замены фронта.

Миграция 0006 добавляет колонку; PATCH /cards/{id} сохраняет условие и
отдаёт его в ответе. Условие не меняет paid_amount/payment_status.
"""


def test_payment_terms_roundtrip(client, admin, make_card):
    _, headers = admin
    card = make_card()
    r = client.patch(f"/cards/{card.id}", json={"payment_terms": "deferred"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["payment_terms"] == "deferred"
    assert r.json()["paid_amount"] == 0, "условие оплаты не трогает факт оплаты"

    r = client.patch(f"/cards/{card.id}", json={"payment_terms": None}, headers=headers)
    assert r.status_code == 200
    assert r.json()["payment_terms"] is None
