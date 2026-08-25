#!/usr/bin/env python3
"""Add the missing cards.position column.

models.Card gained `position` (kanban drag-and-drop ordering) without a matching
migration, so any database created before that commit raises
"no such column: cards.position" on every card list request.

Idempotent: safe to run repeatedly and on already-migrated databases.
Applies to the main database and to every per-tenant database under tenants/.
"""

import os
import sqlite3
import sys

DATA_DIR = os.environ.get("CRM_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))


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
        if "position" in cols:
            return f"ok (already present): {db_path}"

        cur.execute("ALTER TABLE cards ADD COLUMN position INTEGER DEFAULT 0")
        # Seed a stable initial order so existing boards do not collapse to 0.
        cur.execute("UPDATE cards SET position = id WHERE position IS NULL OR position = 0")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_cards_position ON cards (position)")
        conn.commit()
        return f"MIGRATED: {db_path}"
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
