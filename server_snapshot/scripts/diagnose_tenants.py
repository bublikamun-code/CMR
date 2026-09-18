#!/usr/bin/env python3
"""
Диагностика мультитенантности CRM.
Только читает данные, ничего не изменяет.

Помогает понять, почему у одних пользователей есть данные, а у других — пустые таблицы.
"""
import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MAIN_DB = APP_DIR / "crm_app.db"
TENANTS_DIR = APP_DIR / "tenants"


def human_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def count_rows(conn, table):
    try:
        cur = conn.cursor()
        # S608 подавлен: имя таблицы приходит из литералов этого скрипта
        # ("cards", "clients", "users"), внешних аргументов скрипт не принимает.
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        return cur.fetchone()[0]
    except sqlite3.OperationalError:
        return None


def main():
    print("=" * 60)
    print("Диагностика тенантов CRM")
    print("=" * 60)
    print(f"Рабочая директория: {APP_DIR}")
    print(f"Основная БД: {MAIN_DB} ({'exists' if MAIN_DB.exists() else 'NOT FOUND'})")
    print()

    if not MAIN_DB.exists():
        print("Основная БД не найдена. Диагностика невозможна.")
        return

    conn = sqlite3.connect(str(MAIN_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Пользователи
    print("--- Пользователи в основной БД ---")
    try:
        cur.execute("SELECT id, username, role, tenant_id FROM users ORDER BY id")
        users = cur.fetchall()
        if not users:
            print("  Пользователей нет.")
        for u in users:
            tid = u["tenant_id"]
            print(f"  id={u['id']:<3} username={u['username']:<15} role={u['role']:<12} tenant_id={tid if tid is not None else 'NULL'}")
    except sqlite3.OperationalError as e:
        print(f"  Ошибка чтения users: {e}")
    print()

    # 2. Тенанты
    print("--- Тенанты в основной БД ---")
    try:
        cur.execute("SELECT id, name, db_path, created_at FROM tenants ORDER BY id")
        tenants = cur.fetchall()
        if not tenants:
            print("  Тенантов нет.")
        for t in tenants:
            print(f"  id={t['id']:<3} name={t['name']:<20} db_path={t['db_path']:<30} created_at={t['created_at']}")
    except sqlite3.OperationalError as e:
        print(f"  Ошибка чтения tenants: {e}")
    print()

    # 3. Файлы tenant БД
    print(f"--- Файлы tenant БД в {TENANTS_DIR} ---")
    if TENANTS_DIR.exists():
        db_files = sorted(TENANTS_DIR.glob("crm_*.db"))
        if not db_files:
            print("  Файлов tenant БД нет.")
        for db_file in db_files:
            size = db_file.stat().st_size
            tconn = sqlite3.connect(str(db_file))
            cards = count_rows(tconn, "cards")
            clients = count_rows(tconn, "clients")
            users = count_rows(tconn, "users")
            tconn.close()
            print(f"  {db_file.name:<25} size={human_size(size):<10} cards={cards} clients={clients} users={users}")
    else:
        print("  Директория tenants не существует.")
    print()

    # 4. Содержимое основной БД
    print("--- Содержимое основной БД ---")
    for table in ["cards", "clients", "users", "transactions", "tags", "suppliers"]:
        count = count_rows(conn, table)
        print(f"  {table:<15} rows={count if count is not None else 'N/A'}")
    print()

    # 5. Рекомендация
    print("--- Рекомендация ---")
    print("Если у ManagerY tenant_id заполнен, а у остальных NULL,")
    print("и файл tenants/crm_<tenant_id>.db содержит данные — причина найдена:")
    print("остальные пользователи смотрят в пустую основную БД вместо tenant БД.")
    print("Исправление: scripts/fix_tenant_assignment.py")

    conn.close()


if __name__ == "__main__":
    main()
