"""0004: одна запись-остаток на сделку — дедупликация + unique-индекс (Н9).

Инвариант системы: по сделке существует максимум одна «пустая» запись-остаток
(is_document=0, не списание, без номера накладной). Гонка check-then-insert в
trigger_from_card могла плодить дубли.

Порядок миграции (безопасность данных — приоритет):
  1. Находятся карточки с >1 остатком.
  2. Для каждой вычисляется АВТОРСКИЙ остаток: сумма сделки минус выписанное
     (та же формула, что в PATCH /cards и issue_invoice).
  3. Из дубликатов выживается ОДНА запись: приоритет — та, чья сумма уже
     совпадает с авторским остатком; иначе запись с максимальным id, которой
     сумма ПЕРЕСЧИТЫВАЕТСЯ до авторской. Удаляются только строки-заглушки
     (накладные, документы и списания не трогаются ни при каком раскладе).
  4. Создаётся частичный unique-индекс — теперь гонка физически невозможна,
     а trigger_from_card ловит IntegrityError и возвращает существующую запись.

Идемпотентна: без дублей шаги 1-3 пусты, индекс IF NOT EXISTS.
"""


def up(cur):
    cur.execute(
        "SELECT card_id, COUNT(*) AS c FROM transactions "
        "WHERE card_id IS NOT NULL AND is_document = 0 "
        "AND (invoice_number IS NULL OR invoice_number = '') "
        "AND is_warehouse_writeoff = 0 "
        "GROUP BY card_id HAVING c > 1"
    )
    dup_cards = [row[0] for row in cur.fetchall()]

    for card_id in dup_cards:
        card = cur.execute(
            "SELECT total_amount FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        card_total = float(card[0]) if card and card[0] is not None else 0.0

        issued = cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE card_id = ? AND is_document = 0 "
            "AND (is_warehouse_writeoff = 1 OR (invoice_number IS NOT NULL AND invoice_number != ''))",
            (card_id,),
        ).fetchone()[0]
        expected = round(card_total - float(issued), 2)

        remainders = cur.execute(
            "SELECT id, amount FROM transactions "
            "WHERE card_id = ? AND is_document = 0 "
            "AND (invoice_number IS NULL OR invoice_number = '') "
            "AND is_warehouse_writeoff = 0 ORDER BY id",
            (card_id,),
        ).fetchall()

        keep = next(
            (r[0] for r in remainders
             if r[1] is not None and abs(float(r[1]) - expected) <= 0.01),
            remainders[-1][0],  # иначе — самая свежая запись
        )
        cur.execute(
            "UPDATE transactions SET amount = ? WHERE id = ?", (expected, keep)
        )
        drop_ids = [r[0] for r in remainders if r[0] != keep]
        for tid in drop_ids:
            cur.execute("DELETE FROM transactions WHERE id = ?", (tid,))
        print(f"  карточка {card_id}: остатков было {len(remainders)}, "
              f"оставлена id={keep} (сумма {expected}), удалено {len(drop_ids)}")

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_remainder_per_card "
        "ON transactions(card_id) "
        "WHERE card_id IS NOT NULL AND is_document = 0 "
        "AND (invoice_number IS NULL OR invoice_number = '') "
        "AND is_warehouse_writeoff = 0"
    )
