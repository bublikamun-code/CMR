"""0007: колонки writeoff_group_id в cards и transactions (аудит 18.09).

В моделях cards.writeoff_group_id и transactions.writeoff_group_id уже
давно есть (FK на writeoff_groups.id ON DELETE SET NULL, index=True), но
на проде колонки добавлялись вручную, а дев-снапшот crm_app.db
(server_snapshot/, в git не входит) отстал — свежий локальный запуск
падал 500 на /kanban/cards.

Заодно страховочно создаётся сама таблица writeoff_groups (CREATE TABLE
IF NOT EXISTS — зеркально модели WriteoffGroup) и индексы.

Идемпотентна: колонки добавляются только отсутствующие (PRAGMA
table_info), таблица и индексы — через IF NOT EXISTS.
"""


def up(cur):
    # Таблица групп списаний — как в модели WriteoffGroup (models.py).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS writeoff_groups (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR NOT NULL,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            store_location VARCHAR,
            total_amount NUMERIC(12, 2),
            invoice_number VARCHAR,
            invoice_date VARCHAR,
            written_off BOOLEAN,
            tenant_id INTEGER REFERENCES tenants(id),
            created_at DATETIME,
            updated_at DATETIME
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_writeoff_groups_client_id "
        "ON writeoff_groups (client_id)"
    )

    # Колонка-ссылка на группу списаний: cards и transactions.
    for table in ("cards", "transactions"):
        cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
        if "writeoff_group_id" not in cols:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN writeoff_group_id INTEGER "
                "REFERENCES writeoff_groups(id) ON DELETE SET NULL"
            )
        # Имя индекса — как генерирует SQLAlchemy для index=True.
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_writeoff_group_id "
            f"ON {table} (writeoff_group_id)"
        )
