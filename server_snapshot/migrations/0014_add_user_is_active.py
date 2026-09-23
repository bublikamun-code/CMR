"""0014: деактивация пользователей — users.is_active.

Пункт 15 плана v2 требует не только удаление пользователя (DELETE
/auth/users/{id} было и раньше), но и отключение: сотрудника, ушедшего в
отпуск или уволенного, нельзя вычёркивать вместе с его карточками, лентой
активности и задачами, но и пускать в CRM больше не надо.

Идемпотентна по образцу 0006: добавляет только отсутствующую колонку.
INTEGER NOT NULL DEFAULT 1 — все существующие пользователи остаются
активными: SQLite проставляет DEFAULT и уже записанным строкам при ADD
COLUMN. Веткой ELSE правится редкий случай, когда колонку уже добавили
вручную без NOT NULL: в ней лежат NULL, а трактовав NULL как «выключен»
мы бы отключили всю команду при следующем деплое.
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(users)")}
    if "is_active" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    else:
        cur.execute("UPDATE users SET is_active = 1 WHERE is_active IS NULL")
