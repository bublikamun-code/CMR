"""
Идемпотентная миграция: снимки названий в tasks.

Задачи живут в основной БД, а сделка/клиент, к которым они привязаны, —
в tenant-базе, недоступной сериализатору задач. Храним названия снимком
(card_title_snapshot / client_name_snapshot) по образцу
CardChecklist.company_name.

Запуск: python migrate_task_snapshots.py   (из каталога приложения)
Применяет колонки к основной и всем tenant-базам, ничего не ломая при
повторном запуске.
"""
import os
import sqlite3

DATA_DIR = os.environ.get("CRM_DATA_DIR", ".")


def main():
    targets = [os.path.join(DATA_DIR, "crm_app.db")]
    tenants_dir = os.path.join(DATA_DIR, "tenants")
    if os.path.isdir(tenants_dir):
        targets.extend(
            os.path.join(tenants_dir, name)
            for name in sorted(os.listdir(tenants_dir))
            if name.endswith(".db")
        )

    for path in targets:
        if not os.path.exists(path):
            continue
        con = sqlite3.connect(path)
        cur = con.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        changed = []
        if "tasks" in tables:
            cols = {r[1] for r in cur.execute("PRAGMA table_info(tasks)").fetchall()}
            if "card_title_snapshot" not in cols:
                cur.execute("ALTER TABLE tasks ADD COLUMN card_title_snapshot VARCHAR(255)")
                changed.append("card_title_snapshot")
            if "client_name_snapshot" not in cols:
                cur.execute("ALTER TABLE tasks ADD COLUMN client_name_snapshot VARCHAR(255)")
                changed.append("client_name_snapshot")
        con.commit()
        con.close()
        print(f"{path}: {'+ ' + ', '.join(changed) if changed else 'без изменений'}")


if __name__ == "__main__":
    main()
