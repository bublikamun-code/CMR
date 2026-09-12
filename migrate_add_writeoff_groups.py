#!/usr/bin/env python3
"""
Миграция v2.3 — групповое списание.

Создаёт таблицу writeoff_groups и добавляет колонку writeoff_group_id
в cards и transactions. Применяется к основной БД и всем tenant-БД.
"""
import os
import sqlite3

from database import DATA_DIR


def migrate_db(path: str):
    print(f"Migrating {path} ...")
    conn = sqlite3.connect(path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS writeoff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            client_id INTEGER,
            store_location VARCHAR,
            total_amount NUMERIC(12, 2),
            invoice_number VARCHAR,
            invoice_date VARCHAR,
            written_off BOOLEAN DEFAULT 0,
            tenant_id INTEGER,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_writeoff_groups_id ON writeoff_groups (id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_writeoff_groups_client_id ON writeoff_groups (client_id)")

    for col, defn in [("writeoff_group_id", "INTEGER")]:
        for table in ("cards", "transactions"):
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                print(f"  + added {col} to {table}")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    print(f"  ~ {col} already exists in {table}")
                else:
                    raise

    conn.commit()
    conn.close()
    print("  OK")


def main():
    main_db = os.path.join(DATA_DIR, "crm_app.db")
    if os.path.exists(main_db):
        migrate_db(main_db)
    else:
        print(f"Main DB not found at {main_db}")

    tenants_dir = os.path.join(DATA_DIR, "tenants")
    if os.path.isdir(tenants_dir):
        for fname in sorted(os.listdir(tenants_dir)):
            if fname.endswith(".db"):
                migrate_db(os.path.join(tenants_dir, fname))


if __name__ == "__main__":
    main()
