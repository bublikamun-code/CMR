"""Тесты идемпотентности миграции серверного отзыва токенов."""
import sqlite3

from migrations import import_migration


def test_0016_is_idempotent_and_enforces_unique_token_key():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    migration = import_migration("0016_revoked_auth_tokens.py")

    migration.up(cur)
    migration.up(cur)
    conn.commit()

    indexes = cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'revoked_auth_tokens'"
    ).fetchall()
    index_sql = {name: sql.lower() for name, sql in indexes}
    assert any("unique" in sql and "token_key" in sql
               for sql in index_sql.values())
    assert any("expires_at" in sql for sql in index_sql.values())

    cur.execute(
        "INSERT INTO revoked_auth_tokens "
        "(token_key, expires_at, revoked_at) VALUES (?, ?, ?)",
        ("jti:one", None, "2026-09-24 00:00:00"),
    )
    conn.commit()
    try:
        cur.execute(
            "INSERT INTO revoked_auth_tokens "
            "(token_key, expires_at, revoked_at) VALUES (?, ?, ?)",
            ("jti:one", None, "2026-09-24 00:00:01"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("повторный token_key должен нарушать уникальный индекс")

    assert cur.execute(
        "SELECT COUNT(*) FROM revoked_auth_tokens WHERE token_key = ?",
        ("jti:one",),
    ).fetchone()[0] == 1
    conn.close()
