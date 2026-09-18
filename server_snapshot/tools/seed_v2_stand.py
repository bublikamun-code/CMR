#!/usr/bin/env python3
"""Сид демо-данных для локального стенда site-v2 (Этап 0 плана замены).

Создаёт поставщиков, клиентов, сделки (по всем статусам) и задачи в БД,
на которую указывает CRM_DATA_DIR. Запускать ПОСЛЕ migrate/старта сервера
и create_users.py:

    CRM_DATA_DIR=/tmp/v2-stand CRM_DEFAULT_PASSWORD=... \
        .venv/bin/python tools/seed_v2_stand.py

Идемпотентность: если клиент/поставщик с таким именем уже есть —
используется существующий; сделки создаются только в пустой базе.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

import auth
import models
import models_tenant  # noqa: F401  # таблица tenants нужна мапперу
from database import SessionLocal

STAND_USERS = [
    {"username": "Admin", "password": os.environ.get("CRM_DEFAULT_PASSWORD", "stand-pass-1"), "role": "admin"},
    {"username": "ManagerY", "password": os.environ.get("CRM_DEFAULT_PASSWORD", "stand-pass-1"), "role": "manager"},
    {"username": "ManagerA", "password": os.environ.get("CRM_DEFAULT_PASSWORD", "stand-pass-1"), "role": "manager"},
]

SUPPLIERS = [
    {"name": "Люстра Опт", "unp": "191001122", "contact_person": "Хомич Андрей", "phone": "+375 29 700-11-22", "email": "sale@lustra-opt.by", "address": "г. Минск, ул. Селицкого, д. 17"},
    {"name": "СветКомплект", "unp": "192003344", "contact_person": "Романова Ирина", "phone": "+375 29 700-33-44", "email": "info@svetkomplekt.by", "address": "г. Минск, ул. Аранская, д. 44"},
]
CLIENTS = [
    {"name": "ЗАО «СветлогорскДом»", "unp": "100555666", "contact_person": "Лукашевич Ирина", "phone": "+375 29 111-22-33", "address": "г. Минск, пр-т Дзержинского, д. 119, пом. 3"},
    {"name": "ООО «РемонтСити»", "unp": "191777888", "contact_person": "Гончарук Дмитрий", "phone": "+375 29 444-55-66", "address": "г. Минск, ул. Кошевого, д. 8"},
    {"name": "ИП Ковалёв С.М.", "unp": "291333444", "contact_person": "Ковалёв Сергей", "phone": "+375 29 222-33-44", "address": "г. Минск, ул. Одинцова, д. 42"},
]
# (title, status, total, paid, store, через сколько дней срок)
CARDS = [
    ("Освещение офиса РемонтСити", "Новый запрос", 4400.00, 0, "БН", 7),
    ("LED-ленты для склада Веснаторг", "В работе", 8900.00, 1500.00, "Матусевича", 5),
    ("Подсветка кухни РемонтСити", "Ждет оплаты", 3100.00, 500.00, "Богдановича", -2),
    ("Люстры залов СветлогорскДом", "Сборка", 6200.00, 0, "Матусевича", 3),
    ("Трек-система шоурума Ковалёв", "На списание", 5150.00, 1000.00, "Богдановича", 6),
    ("БРА-серия для кафе Ковалёв", "Закрыто", 2750.00, 2750.00, "БН", -5),
]
TASKS = [
    ("Позвонить СветлогорскДом по отсрочке", 1, "done"),
    ("Отгрузка LED-лент РемонтСити", 0, "open"),
]


def days_from_now(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).date()


def main():
    db = SessionLocal()
    try:
        for data in STAND_USERS:
            if not db.query(models.User).filter_by(username=data["username"]).first():
                db.add(models.User(
                    username=data["username"],
                    hashed_password=auth.get_password_hash(data["password"]),
                    role=data["role"],
                ))
        db.commit()

        cards_exist = db.query(models.Card).count() > 0

        suppliers = {}
        for data in SUPPLIERS:
            obj = db.query(models.Supplier).filter_by(name=data["name"]).first()
            if not obj:
                obj = models.Supplier(**data)
                db.add(obj)
                db.flush()
            suppliers[data["name"]] = obj

        clients = {}
        for data in CLIENTS:
            obj = db.query(models.Client).filter_by(name=data["name"]).first()
            if not obj:
                obj = models.Client(**data)
                db.add(obj)
                db.flush()
            clients[data["name"]] = obj

        manager = db.query(models.User).filter_by(role="manager").first()
        owner_id = manager.id if manager else None

        for index, (title, status, total, paid, store, due_in) in enumerate([] if cards_exist else CARDS):
            card = models.Card(
                title=title,
                status=status,
                total_amount=total,
                paid_amount=paid,
                payment_status="Не оплачен" if paid < total else "Оплачен",
                store_location=store,
                due_date=days_from_now(due_in),
                owner_id=owner_id,
                client_id=clients[CLIENTS[index % len(CLIENTS)]["name"]].id,
                position=index,
            )
            db.add(card)
            db.flush()
            supplier = suppliers[SUPPLIERS[index % len(SUPPLIERS)]["name"]]
            db.add(models.CardChecklist(
                card_id=card.id,
                company_name=supplier.name,
                amount=round(total / 2, 2),
                is_paid=False,
                is_secondary_check=False,
                supplier_id=supplier.id,
            ))
            if index == 1:
                db.add(models.Task(
                    title="Отгрузка LED-лент РемонтСити",
                    status="open",
                    due_date=days_from_now(1),
                    priority=1,
                    assignee_id=owner_id,
                    card_id=card.id,
                    client_id=card.client_id,
                ))
        db.add(models.Task(
            title="Позвонить СветлогорскДом по отсрочке",
            status="done",
            due_date=days_from_now(-1),
            completed_at=datetime.now(timezone.utc),
            priority=0,
            assignee_id=owner_id,
            client_id=clients[CLIENTS[0]["name"]].id,
        ))

        # Реестр оплат: по сделке запись-остаток (is_document=False) и, где была
        # оплата/выписка, документ с номером ТН. Остаток = сумма − выписанное.
        if db.execute(text("SELECT COUNT(*) FROM nakladnye")).scalar() == 0:
            supplier = suppliers[SUPPLIERS[0]["name"]]
            db.execute(text(
                "INSERT INTO nakladnye (supplier_id, doc_number, doc_date, amount, vat_amount,"
                " amount_no_vat, store, is_verified, is_arrived, is_paid, status, created_by_bot)"
                " VALUES (:sid, 'ВХ-501', '16.09.2026', 2400, 400, 2000, 'Матусевича', 1, 0, 1, 'paid', 1)"
            ), {"sid": supplier.id})

        if db.query(models.Transaction).count() == 0:
            for card in db.query(models.Card).all():
                total = float(card.total_amount or 0)
                paid = float(card.paid_amount or 0)
                db.add(models.Transaction(
                    company_name=card.title,
                    amount=round(total - paid, 2),
                    store_location=card.store_location or "",
                    card_id=card.id,
                    is_document=False,
                ))
                if paid > 0:
                    db.add(models.Transaction(
                        company_name=card.title,
                        amount=paid,
                        store_location=card.store_location or "",
                        card_id=card.id,
                        is_document=True,
                        is_invoice_issued=True,
                        invoice_number="ТН-09%02d" % card.id,
                        invoice_date="17.09.2026",
                        date=datetime.now(timezone.utc),
                    ))

        db.commit()
        print(f"Сид готов: {len(CARDS)} сделок, {len(CLIENTS)} клиентов, {len(TASKS)} задач, {len(SUPPLIERS)} поставщиков.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
