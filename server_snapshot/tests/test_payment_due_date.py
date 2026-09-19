"""Дата отсрочки платежа при PATCH /cards/{id}/payment.

V2 (аудит прода 19.09): «Не оплачен» на фронте шлёт payment_due_date: null,
но сервер игнорировал None, и дата отсрочки оставалась висеть на сделке.
Явный null теперь стирает дату; если поля нет в запросе — дата не трогается.
"""
import datetime

import pytest

import models

pytestmark = pytest.mark.money


def _last_log(db, card_id):
    return (db.query(models.ActivityLog)
              .filter(models.ActivityLog.card_id == card_id)
              .order_by(models.ActivityLog.id.desc())
              .first())


def test_explicit_null_clears_due_date(client, manager, db, make_card):
    """Явный payment_due_date: null при «Не оплачен» стирает дату в БД."""
    _, h = manager
    card = make_card(title="С отсрочкой", total_amount=500.0, paid_amount=0.0,
                     payment_status="Не оплачен",
                     payment_due_date=datetime.date(2026, 10, 1))
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"payment_status": "Не оплачен",
                           "payment_due_date": None})
    assert r.status_code == 200, r.text

    db.expire_all()
    stored = db.get(models.Card, card.id).payment_due_date
    assert stored is None, "явный null должен стереть дату отсрочки"


def test_missing_field_keeps_due_date(client, manager, db, make_card):
    """PATCH без поля payment_due_date дату не трогает."""
    _, h = manager
    card = make_card(title="С отсрочкой", total_amount=500.0, paid_amount=0.0,
                     payment_status="Не оплачен",
                     payment_due_date=datetime.date(2026, 10, 1))
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"payment_status": "Оплачен"})
    assert r.status_code == 200, r.text

    db.expire_all()
    stored = db.get(models.Card, card.id).payment_due_date
    assert stored == datetime.date(2026, 10, 1), "поле не пришло — дата на месте"


def test_clearing_due_date_is_logged(client, manager, db, make_card):
    """Стирание даты попадает в журнал: «Отсрочка до: 01.10.2026 → —»."""
    _, h = manager
    card = make_card(title="С отсрочкой", total_amount=500.0, paid_amount=0.0,
                     payment_status="Не оплачен",
                     payment_due_date=datetime.date(2026, 10, 1))
    r = client.patch(f"/cards/{card.id}/payment", headers=h,
                     json={"payment_status": "Не оплачен",
                           "payment_due_date": None})
    assert r.status_code == 200, r.text

    db.expire_all()
    log = _last_log(db, card.id)
    assert log is not None and log.action == "Изменение"
    assert "Отсрочка до:" in (log.details or "")
    assert "—" in (log.details or ""), "стёртая дата в журнале — прочерк"
