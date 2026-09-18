"""0006: условие оплаты карточки — payment_terms.

Появилось в новом интерфейсе (Этап 2.4 плана замены фронта): «Не выбраны»
(NULL), «Отсрочка» (deferred), «Оплата 100%» (full), «Частичная оплата +
отсрочка платежа» (partial_deferred). Условие ≠ факт оплаты: paid_amount и
payment_status остаются независимыми. Идемпотентна: добавляет только
отсутствующую колонку.
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(cards)")}
    if "payment_terms" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN payment_terms VARCHAR(30)")
