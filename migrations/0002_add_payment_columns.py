"""0002: платёжные колонки карточки — paid_amount, payment_status,
payment_due_date.

Порт разовой миграции migrate_add_payment_columns.py (на проде колонки
добавлялись вручную; в dev-томе их отсутствие роняло канбан 500).
Идемпотентна: добавляет только отсутствующие колонки.
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(cards)")}
    if "paid_amount" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN paid_amount NUMERIC(12,2) DEFAULT 0")
    if "payment_status" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN payment_status VARCHAR DEFAULT 'Не оплачен'")
    if "payment_due_date" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN payment_due_date VARCHAR")
