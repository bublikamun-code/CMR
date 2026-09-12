#!/usr/bin/env python3
"""Add the missing cards payment columns (paid_amount, payment_status, payment_due_date).

models.Card gained these columns without a matching migration: they were added
to the production databases by hand, so any database created before that point
(the local dev tenant DBs, a restore from an old backup) raises
"no such column: cards.paid_amount" on every card list request.

Idempotent: safe to run repeatedly and on already-migrated databases.
Applies to the main database and to every per-tenant database under tenants/.
"""

import os
import sqlite3
import sys

DATA_DIR = os.environ.get("CRM_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

COLUMNS = [
    ("paid_amount", "NUMERIC(12, 2) DEFAULT 0"),
    ("payment_status", "STRING DEFAULT 'Не оплачен'"),
    ("payment_due_date", "DATE"),
]


def migrate(db_path: str) -> str:
    if not os.path.exists(db_path):
        return f"skip (no such file): {db_path}"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
        if not cur.fetchone():
            return f"skip (no cards table): {db_path}"

        cols = {row[1] for row in cur.execute("PRAGMA table_info(cards)")}
        missing = [(name, ddl) for name, ddl in COLUMNS if name not in cols]
        if not missing:
            return f"ok (already present): {db_path}"

        for name, ddl in missing:
            cur.execute(f"ALTER TABLE cards ADD COLUMN {name} {ddl}")
        conn.commit()
        return f"MIGRATED ({', '.join(n for n, _ in missing)}): {db_path}"
    finally:
        conn.close()


def main() -> int:
    targets = [os.path.join(DATA_DIR, "crm_app.db")]
    tenants_dir = os.path.join(DATA_DIR, "tenants")
    if os.path.isdir(tenants_dir):
        targets += [
            os.path.join(tenants_dir, f)
            for f in sorted(os.listdir(tenants_dir))
            if f.endswith(".db")
        ]

    for path in targets:
        print(migrate(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
