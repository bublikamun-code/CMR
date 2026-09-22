"""Тесты миграции 0012 (ротация record_versions и activity_log).

Гоняются на синтетической sqlite-базе в памяти — ни стенд, ни прод не
затрагиваются (тот же приём, что в tests/test_migrations.py). Пустая база и
база вообще без этих таблиц проверяются отдельно: conftest накатывает все
миграции на каждый прогон тестов, поэтому падение здесь уронило бы весь набор.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from migrations import import_migration

MIGRATION = "0012_rotate_history_tables.py"
# Пороги читаем из самого модуля миграции: тест обязан оставаться честным,
# если владелец решит изменить число хранимых версий или горизонт.
_module = import_migration(MIGRATION)
KEEP_VERSIONS = _module.KEEP_VERSIONS_PER_RECORD
KEEP_LOG = _module.KEEP_LOG_PER_CARD
RETENTION_DAYS = _module.LOG_RETENTION_DAYS
FRESH_DAYS = _module.FRESH_DAYS

_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE record_versions (
    id INTEGER PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    data_snapshot TEXT NOT NULL,
    changed_by INTEGER REFERENCES users(id),
    change_type TEXT NOT NULL,
    changed_at DATETIME,
    tenant_id INTEGER);
CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    card_id INTEGER REFERENCES cards(id),
    action TEXT NOT NULL,
    details TEXT,
    tenant_id INTEGER,
    created_at DATETIME);
"""


def _db(with_tables=True):
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    if with_tables:
        conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _ago(days, sep=" ", suffix="", with_micro=True):
    """Строка даты в том виде, в котором её реально хранит SQLite."""
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    text = moment.strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    if with_micro:
        text += f".{moment.microsecond:06d}"
    return text + suffix


# Отличает «параметр не передали» от явного None: фабрики ниже умеют вставлять
# NULL-дату, и подмена None на «сейчас» превратила бы тест защиты NULL в пустышку.
_UNSET = object()


def _add_versions(cur, record_id, count, days=10, table_name="cards", start=1,
                  changed_at=_UNSET):
    for i in range(count):
        cur.execute(
            "INSERT INTO record_versions (table_name, record_id, version,"
            " data_snapshot, change_type, changed_at) VALUES (?,?,?,?,?,?)",
            (table_name, record_id, start + i, "{}", "update",
             _ago(days) if changed_at is _UNSET else changed_at),
        )


def _add_log(cur, card_id, count, days=10, action="Изменение", created_at=_UNSET):
    ids = []
    for i in range(count):
        cur.execute(
            "INSERT INTO activity_log (card_id, action, details, created_at)"
            " VALUES (?,?,?,?)",
            (card_id, action, f"событие {i}",
             _ago(days) if created_at is _UNSET else created_at),
        )
        ids.append(cur.lastrowid)
    return ids


def _run(conn):
    cur = conn.cursor()
    _module.up(cur)
    conn.commit()
    return cur


def _versions_left(cur, record_id, table_name="cards"):
    return cur.execute(
        "SELECT version FROM record_versions WHERE table_name=? AND record_id=?"
        " ORDER BY version", (table_name, record_id)).fetchall()


def _log_left(cur):
    return cur.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]


# ---------------------------------------------------------------------------
# record_versions
# ---------------------------------------------------------------------------

def test_0012_keeps_newest_versions_per_record():
    conn = _db()
    cur = conn.cursor()
    _add_versions(cur, 1, 30, days=10)
    conn.commit()

    _run(conn)
    left = [v[0] for v in _versions_left(cur, 1)]
    assert len(left) == KEEP_VERSIONS
    assert left == list(range(11, 31)), "оставлены самые ПОСЛЕДНИЕ версии, а не первые"


def test_0012_caps_each_record_independently():
    conn = _db()
    cur = conn.cursor()
    _add_versions(cur, 1, 25, days=10)
    _add_versions(cur, 2, 5, days=10)
    _add_versions(cur, 3, 21, days=10, table_name="clients")
    conn.commit()

    _run(conn)
    assert len(_versions_left(cur, 1)) == KEEP_VERSIONS
    assert len(_versions_left(cur, 2)) == 5, "запись под лимитом не трогается"
    assert len(_versions_left(cur, 3, "clients")) == KEEP_VERSIONS
    # partition — по (table_name, record_id), а не только по record_id
    assert len(_versions_left(cur, 3, "cards")) == 0


def test_0012_records_under_cap_untouched():
    conn = _db()
    cur = conn.cursor()
    _add_versions(cur, 7, KEEP_VERSIONS, days=400)
    conn.commit()

    _run(conn)
    assert len(_versions_left(cur, 7)) == KEEP_VERSIONS


def test_0012_fresh_versions_are_never_deleted():
    """Абсолютный пол: свежее FRESH_DAYS не удаляется, даже если версий 30."""
    conn = _db()
    cur = conn.cursor()
    _add_versions(cur, 1, 30, days=0)  # «прямо сейчас»
    conn.commit()

    _run(conn)
    assert len(_versions_left(cur, 1)) == 30


def test_0012_versions_without_timestamp_are_kept():
    """NULL/пустую дату проверить нельзя — выбираем сохранить, а не удалить.

    Версии без даты намеренно сделаны САМЫМИ СТАРЫМИ (номера 1..6), чтобы они
    заведомо выпали за лимит последних KEEP_VERSIONS. Иначе тест проходил бы и
    без защиты — просто потому, что строки вообще не попали под удаление.
    """
    conn = _db()
    cur = conn.cursor()
    for i, stamp in enumerate([None, "", None, "", None, ""], start=1):
        _add_versions(cur, 1, 1, start=i, changed_at=stamp)
    _add_versions(cur, 1, 24, start=7, days=10)
    conn.commit()

    _run(conn)
    left = {v[0] for v in _versions_left(cur, 1)}
    # всего 30 строк, под лимит попадают версии 1..10; из них 1..6 без даты
    assert len(left) == KEEP_VERSIONS + 6
    assert set(range(1, 7)).issubset(left), "версии без даты удалены"
    assert not (set(range(7, 11)) & left), "обычные старые версии должны быть удалены"


def test_0012_mixed_timestamp_formats_compared_correctly():
    """В базе встречаются 'T'-разделитель, хвост '+00:00' и строки без микросекунд.

    Все пять вариантов сделаны одинаково старыми и заведомо выпадают за лимит:
    если какой-то формат распознается как «свежий», строка выживет и тест это
    поймает. Сравнение идёт по префиксу в 19 символов (см. _stamp в миграции).
    """
    conn = _db()
    cur = conn.cursor()
    old_days = FRESH_DAYS + 1
    formats = [
        _ago(old_days, sep="T"),
        _ago(old_days, suffix="+00:00"),
        _ago(old_days, with_micro=False),
        _ago(old_days, sep="T", with_micro=False),
        _ago(old_days, suffix="+00:00", with_micro=False),
    ]
    for i, stamp in enumerate(formats, start=1):
        _add_versions(cur, 1, 1, start=i, changed_at=stamp)
    _add_versions(cur, 1, 25, start=6, days=old_days)
    conn.commit()

    _run(conn)
    left = [v[0] for v in _versions_left(cur, 1)]
    assert left == list(range(11, 31)), \
        f"старые строки в смешанных форматах не удалены: {left}"


# ---------------------------------------------------------------------------
# activity_log
# ---------------------------------------------------------------------------

def test_0012_deletes_log_older_than_retention_horizon():
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    _add_log(cur, 1, 5, days=RETENTION_DAYS + 10)
    _add_log(cur, 1, 3, days=30)
    conn.commit()

    _run(conn)
    assert _log_left(cur) == 3, "старые события ушли, свежие (30 дней) остались"


def test_0012_keeps_log_within_horizon_even_across_many_cards():
    conn = _db()
    cur = conn.cursor()
    for card in (1, 2, 3):
        cur.execute("INSERT INTO cards (id, title) VALUES (?, 'Сделка')", (card,))
        _add_log(cur, card, 4, days=20)
    conn.commit()

    _run(conn)
    assert _log_left(cur) == 12, "внутри горизонта и под лимитом на карточку — всё цело"


def test_0012_caps_log_per_card_keeping_newest():
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    # все строки старше FRESH_DAYS, но моложе горизонта: работает только лимит
    old_ids = _add_log(cur, 1, KEEP_LOG + 50, days=10)
    conn.commit()

    _run(conn)
    left = cur.execute("SELECT id FROM activity_log ORDER BY id").fetchall()
    assert len(left) == KEEP_LOG
    assert [r[0] for r in left] == old_ids[-KEEP_LOG:], "оставлены последние по id/дате"


def test_0012_per_card_cap_does_not_apply_to_null_card():
    """События без сделки не к чему отнести — лимит «на карточку» их не режет."""
    conn = _db()
    cur = conn.cursor()
    _add_log(cur, None, KEEP_LOG + 50, days=10)
    conn.commit()

    _run(conn)
    assert _log_left(cur) == KEEP_LOG + 50


def test_0012_comments_are_never_deleted():
    """'Комментарий' — пользовательский текст, редактируется через PATCH /activity/{id}."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    _add_log(cur, 1, 3, days=RETENTION_DAYS + 100, action="Комментарий")
    _add_log(cur, 1, 2, days=RETENTION_DAYS + 100, action="Изменение")
    conn.commit()

    _run(conn)
    actions = [r[0] for r in cur.execute("SELECT action FROM activity_log").fetchall()]
    assert actions.count("Комментарий") == 3
    assert actions.count("Изменение") == 0


def test_0012_fresh_log_is_never_deleted():
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    _add_log(cur, 1, KEEP_LOG + 50, days=0)
    conn.commit()

    _run(conn)
    assert _log_left(cur) == KEEP_LOG + 50


def test_0012_log_without_timestamp_is_kept():
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    _add_log(cur, 1, 3, created_at=None)
    _add_log(cur, 1, 3, created_at="")
    conn.commit()

    _run(conn)
    assert _log_left(cur) == 6


# ---------------------------------------------------------------------------
# Идемпотентность, пустая и неполная база, целостность
# ---------------------------------------------------------------------------

def test_0012_is_idempotent():
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    _add_versions(cur, 1, 30, days=10)
    _add_log(cur, 1, 3, days=RETENTION_DAYS + 10)
    conn.commit()

    _run(conn)
    first = (cur.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0], _log_left(cur))
    _run(conn)
    _run(conn)
    second = (cur.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0], _log_left(cur))
    assert first == second == (KEEP_VERSIONS, 0)


def test_0012_on_empty_tables_is_noop():
    conn = _db()
    cur = _run(conn)  # не должно падать
    assert cur.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0] == 0
    assert cur.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0] == 0


def test_0012_without_tables_does_not_crash():
    """Свежая база до create_all: таблиц может не быть вовсе — миграция no-op."""
    conn = _db(with_tables=False)
    _run(conn)  # без исключения


def test_0012_with_only_one_table_does_not_crash():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE record_versions (
            id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, record_id INTEGER NOT NULL,
            version INTEGER NOT NULL, data_snapshot TEXT NOT NULL,
            change_type TEXT NOT NULL, changed_at DATETIME);
    """)
    cur = conn.cursor()
    _add_versions(cur, 1, 25, days=10)
    conn.commit()
    _run(conn)
    assert len(_versions_left(cur, 1)) == KEEP_VERSIONS


def test_0012_leaves_foreign_keys_clean():
    """Удаление строк журналов не может осиротить чужие записи."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (id, username) VALUES (1, 'boss')")
    cur.execute("INSERT INTO cards (id, title) VALUES (1, 'Сделка')")
    cur.execute(
        "INSERT INTO record_versions (table_name, record_id, version, data_snapshot,"
        " changed_by, change_type, changed_at) VALUES ('cards',1,1,'{}',1,'update',?)",
        (_ago(300),))
    _add_versions(cur, 1, 25, days=30)
    cur.execute(
        "INSERT INTO activity_log (card_id, user_id, action, details, created_at)"
        " VALUES (1,1,'Изменение','текст',?)", (_ago(300),))
    conn.commit()

    _run(conn)
    assert cur.execute("PRAGMA foreign_key_check").fetchall() == []
    assert cur.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # пользователь и сделка на месте
    assert cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert cur.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1


def test_0012_removes_bulk_of_audited_versions_on_real_shape():
    """Сценарий с копии прод-БД: одна сделка с 83 версиями, все старше пола."""
    conn = _db()
    cur = conn.cursor()
    _add_versions(cur, 1030, 83, days=5)
    _add_versions(cur, 553, 40, days=5)
    _add_versions(cur, 900, 3, days=5)
    conn.commit()

    _run(conn)
    total = cur.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0]
    assert total == KEEP_VERSIONS + KEEP_VERSIONS + 3
    assert len(_versions_left(cur, 1030)) == KEEP_VERSIONS
    assert len(_versions_left(cur, 900)) == 3
