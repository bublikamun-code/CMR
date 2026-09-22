"""Миграция 0013 — заполнение deal_statuses из фактических cards.status.

Гоняется на синтетической sqlite-базе (DDL — как в tests/fixtures/prod_schema.sql),
боевые данные и таблицы тестовой сессии не затрагиваются.
"""
import sqlite3

from migrations import import_migration


_CARDS_DDL = """
CREATE TABLE cards (
    id INTEGER NOT NULL,
    title VARCHAR,
    status VARCHAR,
    PRIMARY KEY (id)
);
"""

_DEAL_STATUSES_DDL = """
CREATE TABLE deal_statuses (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    position INTEGER,
    color VARCHAR(20),
    is_active BOOLEAN,
    tenant_id INTEGER,
    PRIMARY KEY (id),
    UNIQUE (name)
);
"""

# Как на копии прод-БД: канонические статусы вперемешку и с дублями, плюс
# мусор (NULL, пустая строка, только пробелы), статус с пробелами по краям и
# два неканонических.
CARD_STATUSES = [
    "Закрыто",
    "Новый запрос",
    "В работе",
    "Новый запрос",
    "Ждет оплаты",
    "  В работе  ",
    "Сборка",
    "Архив 2024",
    "На списание",
    "Дачная",
    "Архив 2024",
    "Новый запрос",
    "",
    "   ",
    None,
]

CANON_ROWS = [
    ("Новый запрос", 0, "faint"),
    ("В работе", 1, "accent"),
    ("Ждет оплаты", 2, "warn"),
    ("Сборка", 3, "ok"),
    ("На списание", 4, "danger"),
    ("Закрыто", 5, "faint"),
]
CANON_NAMES = {row[0] for row in CANON_ROWS}


def _db(preexisting=()):
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CARDS_DDL + _DEAL_STATUSES_DDL)
    conn.executemany("INSERT INTO cards (title, status) VALUES ('Сделка', ?)",
                     [(status,) for status in CARD_STATUSES])
    conn.executemany(
        "INSERT INTO deal_statuses (name, position, color, is_active) VALUES (?, ?, ?, 1)",
        list(preexisting))
    conn.commit()
    return conn


def _run(cur, times=1):
    mod = import_migration("0013_fill_deal_statuses.py")
    for _ in range(times):
        mod.up(cur)


def _rows(cur):
    return cur.execute(
        "SELECT name, position, color FROM deal_statuses ORDER BY position, id"
    ).fetchall()


def test_0013_fills_canonical_statuses_in_board_order():
    cur = _db().cursor()
    _run(cur)

    rows = _rows(cur)
    assert [r for r in rows if r[0] in CANON_NAMES] == CANON_ROWS

    extra = [r for r in rows if r[0] not in CANON_NAMES]
    assert [r[0] for r in extra] == ["Архив 2024", "Дачная"], "неканонические — по алфавиту"
    assert [r[1] for r in extra] == [50, 51], "после канонических колонок"
    assert all(r[2] is None for r in extra), "у неканонического статуса цвет остаётся NULL"


def test_0013_skips_empty_and_dedups_trimmed_names():
    cur = _db().cursor()
    _run(cur)

    assert cur.execute("SELECT COUNT(*) FROM deal_statuses").fetchone()[0] == len(CANON_ROWS) + 2
    assert cur.execute(
        "SELECT COUNT(*) FROM deal_statuses WHERE name IS NULL OR trim(name) = ''"
    ).fetchone()[0] == 0, "NULL, пустые и пробельные статусы в справочник не попадают"
    assert cur.execute(
        "SELECT COUNT(*) FROM deal_statuses WHERE name <> trim(name)"
    ).fetchone()[0] == 0, "имена пишутся без обрамляющих пробелов"
    assert cur.execute(
        "SELECT COUNT(*) FROM deal_statuses WHERE name = 'В работе'"
    ).fetchone()[0] == 1, "'В работе' и '  В работе  ' — одна строка"
    assert cur.execute(
        "SELECT name FROM deal_statuses GROUP BY name HAVING COUNT(*) > 1"
    ).fetchall() == [], "имена уникальны"
    assert cur.execute(
        "SELECT COUNT(*) FROM deal_statuses WHERE is_active <> 1 OR tenant_id IS NOT NULL"
    ).fetchone()[0] == 0, "строки активны и без привязки к тенанту"


def test_0013_keeps_rows_created_by_hand():
    cur = _db(preexisting=[("Архив 2024", 7, "#ff8800"), ("Новый запрос", 42, "#123456")]).cursor()
    _run(cur)

    by_name = {row[0]: row[1:] for row in _rows(cur)}
    assert by_name["Архив 2024"] == (7, "#ff8800")
    assert by_name["Новый запрос"] == (42, "#123456"), "ручные позицию и цвет не перезаписываем"
    assert cur.execute(
        "SELECT COUNT(*) FROM deal_statuses WHERE name = 'Новый запрос'"
    ).fetchone()[0] == 1, "дубля уже созданного статуса миграция не делает"
    assert [r for r in _rows(cur) if r[0] in CANON_NAMES and r[0] != "Новый запрос"] == CANON_ROWS[1:]
    assert cur.execute("SELECT COUNT(*) FROM deal_statuses").fetchone()[0] == len(CANON_ROWS) + 2


def test_0013_is_idempotent():
    cur = _db().cursor()
    _run(cur)
    first = cur.execute(
        "SELECT id, name, position, color, is_active FROM deal_statuses ORDER BY id"
    ).fetchall()

    _run(cur, times=2)
    assert cur.execute(
        "SELECT id, name, position, color, is_active FROM deal_statuses ORDER BY id"
    ).fetchall() == first


def test_0013_on_empty_cards_adds_nothing():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CARDS_DDL + _DEAL_STATUSES_DDL)
    conn.commit()
    cur = conn.cursor()

    _run(cur, times=2)
    assert _rows(cur) == []
