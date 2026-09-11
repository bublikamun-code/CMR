"""
Миграция: создание таблицы nakladnye для накладных (ТН/ТТН/УПД).
Запуск: python migrate_add_nakladnye.py
"""
import os
import sqlite3
import glob

DATA_DIR = os.environ.get("CRM_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))

DB_PATHS = [os.path.join(DATA_DIR, "crm_app.db")]
DB_PATHS += glob.glob(os.path.join(DATA_DIR, "tenants", "crm_*.db"))

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS nakladnye (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    supplier_name TEXT,
    doc_type TEXT(20),
    doc_series TEXT(50),
    doc_number TEXT(50),
    doc_date TEXT(20),
    amount REAL,
    amount_no_vat REAL,
    unload_address TEXT(255),
    store TEXT(100),
    is_verified INTEGER DEFAULT 0,
    is_paid INTEGER DEFAULT 0,
    status TEXT(20) DEFAULT 'new',
    photo_paths TEXT,
    created_by_bot INTEGER DEFAULT 0,
    tenant_id INTEGER REFERENCES tenants(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_nakladnye_supplier_id ON nakladnye(supplier_id);",
    "CREATE INDEX IF NOT EXISTS ix_nakladnye_supplier_name ON nakladnye(supplier_name);",
    "CREATE INDEX IF NOT EXISTS ix_nakladnye_doc_number ON nakladnye(doc_number);",
    "CREATE INDEX IF NOT EXISTS ix_nakladnye_store ON nakladnye(store);",
    "CREATE INDEX IF NOT EXISTS ix_nakladnye_status ON nakladnye(status);",
]

for db_path in DB_PATHS:
    if not os.path.exists(db_path):
        print(f"[skip] {db_path} — не существует")
        continue
    print(f"[migrate] {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(CREATE_TABLE)
    for idx in INDEXES:
        cur.execute(idx)
    conn.commit()
    conn.close()
    print(f"  ✓ nakladnye создана")

print("Готово.")
