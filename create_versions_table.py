"""Создание таблицы record_versions"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crm_app.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name VARCHAR(50) NOT NULL,
    record_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    data_snapshot TEXT NOT NULL,
    changed_by INTEGER,
    change_type VARCHAR(20) NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tenant_id INTEGER
)
""")

cur.execute("CREATE INDEX IF NOT EXISTS idx_rv_table_record ON record_versions(table_name, record_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_rv_changed_at ON record_versions(changed_at)")

conn.commit()
conn.close()

print("Таблица record_versions создана!")
