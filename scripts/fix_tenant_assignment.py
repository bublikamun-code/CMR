#!/usr/bin/env python3
"""
Исправление привязки пользователей к тенанту.

Сценарий: пользователи созданы через create_users.py без tenant_id,
а данные находятся в tenant БД (например, tenants/crm_1.db).
В результате у одного пользователя (ManagerY) есть данные, у остальных — пустые таблицы.

Скрипт:
  1. Делает резервную копию основной БД.
  2. Находит целевой tenant_id (по умолчанию — tenant_id пользователя ManagerY).
  3. Обновляет tenant_id у всех не-суперадминов на целевой.
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MAIN_DB = APP_DIR / "crm_app.db"
BACKUP_DIR = APP_DIR / "backups"


def backup_db():
    BACKUP_DIR.mkdir(exist_ok=True)
    # UTC в имени бэкапа: имена должны быть монотонными независимо от TZ сервера.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"crm_app_pre_tenant_fix_{timestamp}.db"
    shutil.copy2(str(MAIN_DB), str(backup_path))
    print(f"[backup] Создана резервная копия: {backup_path}")
    return backup_path


def main():
    parser = argparse.ArgumentParser(description="Исправить привязку пользователей к тенанту")
    parser.add_argument("--tenant-id", type=int, default=None,
                        help="Целевой tenant_id (если не указан, используется tenant_id пользователя ManagerY)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, что будет изменено, не вносить изменения")
    parser.add_argument("--user", type=str, default="ManagerY",
                        help="Имя пользователя-эталона для определения tenant_id")
    args = parser.parse_args()

    if not MAIN_DB.exists():
        print(f"Ошибка: основная БД не найдена: {MAIN_DB}")
        sys.exit(1)

    conn = sqlite3.connect(str(MAIN_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Определяем целевой tenant_id
    target_tenant_id = args.tenant_id
    if target_tenant_id is None:
        cur.execute("SELECT tenant_id FROM users WHERE username = ?", (args.user,))
        row = cur.fetchone()
        if not row:
            print(f"Ошибка: пользователь-эталон '{args.user}' не найден.")
            conn.close()
            sys.exit(1)
        target_tenant_id = row["tenant_id"]
        if target_tenant_id is None:
            print(f"Ошибка: у пользователя-эталона '{args.user}' tenant_id = NULL.")
            print("Укажите целевой tenant_id вручную: --tenant-id <id>")
            conn.close()
            sys.exit(1)
        print(f"[info] Целевой tenant_id определён по пользователю '{args.user}': {target_tenant_id}")
    else:
        print(f"[info] Используется указанный tenant_id: {target_tenant_id}")

    # Проверяем, существует ли tenant
    cur.execute("SELECT id, name FROM tenants WHERE id = ?", (target_tenant_id,))
    tenant = cur.fetchone()
    if not tenant:
        print(f"Предупреждение: тенант с id={target_tenant_id} не найден в основной БД.")
        print("Продолжение может привести к некорректным tenant_id.")
        if not args.dry_run:
            confirm = input("Продолжить? (yes/no): ")
            if confirm.lower() != "yes":
                print("Отменено.")
                conn.close()
                sys.exit(0)
    else:
        print(f"[info] Тенант: id={tenant['id']}, name={tenant['name']}")

    # Показываем текущее состояние
    cur.execute("SELECT id, username, role, tenant_id FROM users ORDER BY id")
    users = cur.fetchall()
    print("\nТекущие пользователи:")
    for u in users:
        print(f"  id={u['id']:<3} username={u['username']:<15} role={u['role']:<12} tenant_id={u['tenant_id']}")

    # Находим пользователей для обновления
    cur.execute(
        "SELECT id, username, role, tenant_id FROM users WHERE role != 'superadmin' AND (tenant_id IS NULL OR tenant_id != ?)",
        (target_tenant_id,)
    )
    to_update = cur.fetchall()

    if not to_update:
        print("\nНет пользователей, требующих исправления tenant_id.")
        conn.close()
        sys.exit(0)

    print(f"\nБудет обновлено пользователей: {len(to_update)}")
    for u in to_update:
        print(f"  {u['username']}: tenant_id {u['tenant_id']} -> {target_tenant_id}")

    if args.dry_run:
        print("\n[dry-run] Изменения не внесены.")
        conn.close()
        sys.exit(0)

    # Подтверждение
    confirm = input("\nВнести изменения? (yes/no): ")
    if confirm.lower() != "yes":
        print("Отменено.")
        conn.close()
        sys.exit(0)

    # Бэкап и обновление
    backup_db()
    cur.execute(
        "UPDATE users SET tenant_id = ? WHERE role != 'superadmin' AND (tenant_id IS NULL OR tenant_id != ?)",
        (target_tenant_id, target_tenant_id)
    )
    conn.commit()
    print(f"[ok] Обновлено записей: {cur.rowcount}")

    # Проверяем результат
    cur.execute("SELECT id, username, role, tenant_id FROM users ORDER BY id")
    print("\nПользователи после исправления:")
    for u in cur.fetchall():
        print(f"  id={u['id']:<3} username={u['username']:<15} role={u['role']:<12} tenant_id={u['tenant_id']}")

    conn.close()
    print("\n[ok] Готово. Перезапуск приложения не требуется — tenant_id читается при следующем входе.")


if __name__ == "__main__":
    main()
