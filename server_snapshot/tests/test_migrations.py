"""Тесты миграций: гоняются на синтетической sqlite-базе, боевые данные
не затрагиваются (CRM_DATA_DIR в conftest уже указывает во временную папку)."""
import sqlite3

from migrations import import_migration


def _synthetic_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, total_amount NUMERIC)")
    conn.execute("""CREATE TABLE transactions (
        id INTEGER PRIMARY KEY, card_id INTEGER, amount NUMERIC,
        invoice_number TEXT, is_document INTEGER DEFAULT 0,
        is_warehouse_writeoff INTEGER DEFAULT 0)""")
    return conn


def test_0004_dedups_remainders_and_creates_unique_index():
    conn = _synthetic_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, total_amount) VALUES (1, 1000)")
    # Остаток + два дубля-заглушки (гонка trigger_from_card); накладная и
    # документ не должны пострадать ни при каком раскладе.
    cur.executemany("INSERT INTO transactions (id, card_id, amount, invoice_number, is_document, is_warehouse_writeoff) VALUES (?,?,?,?,?,?)", [
        (10, 1, 1000, None, 0, 0),   # остаток (первый)
        (11, 1, 1000, None, 0, 0),   # дубль-заглушка
        (12, 1, 700, None, 0, 0),    # дубль-заглушка (устаревшая сумма)
        (13, 1, 300, "ТН-1", 0, 0),  # накладная — неприкосновенна
        (14, 1, 300, "ТН-1", 1, 0),  # документ-копия — неприкосновенна
    ])
    conn.commit()

    import_migration("0004_unique_remainder_per_card.py").up(cur)
    conn.commit()

    rows = cur.execute(
        "SELECT id, amount FROM transactions WHERE card_id = 1 ORDER BY id"
    ).fetchall()
    remainders = [r for r in rows
                  if r[0] not in (13, 14)]
    assert len(remainders) == 1, f"должен остаться один остаток: {rows}"
    # Авторский остаток = 1000 − 300 (накладная), а не сумма заглушек
    assert abs(float(remainders[0][1]) - 700.0) < 0.01
    assert cur.execute(
        "SELECT COUNT(*) FROM transactions WHERE id IN (13, 14)"
    ).fetchone()[0] == 2, "накладная и документ не удаляются"

    # unique-индекс создан и действительно запрещает второй остаток
    indexes = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transactions'"
    ).fetchall()
    assert any("uq_remainder_per_card" == i[0] for i in indexes)
    try:
        cur.execute(
            "INSERT INTO transactions (id, card_id, amount, invoice_number, is_document, is_warehouse_writeoff) "
            "VALUES (99, 1, 700, NULL, 0, 0)"
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "второй остаток по сделке должен запрещаться индексом"


def test_0004_is_idempotent():
    conn = _synthetic_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, total_amount) VALUES (1, 500)")
    mod = import_migration("0004_unique_remainder_per_card.py")
    mod.up(cur); conn.commit()
    mod.up(cur); conn.commit()  # повтор — пусто, без ошибок
    assert cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
