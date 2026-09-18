"""
Миграция v2.2 — добавление новых таблиц и индексов.
Запуск: python migrate_v2_2.py
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crm_app.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"БД не найдена: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # UTC: скрипт запускают и на хостинге, и локально, отметка в логе должна
    # читаться однозначно.
    print(f"=== Миграция v2.2 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ===")
    print(f"БД: {DB_PATH}")
    print()

    # Проверяем, какие таблицы уже есть
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cur.fetchall()}
    print(f"Существующие таблицы: {len(existing)}")

    # 1. Таблица версий записей
    if "record_versions" not in existing:
        print("[1/8] Создаю таблицу record_versions...")
        cur.execute("""
            CREATE TABLE record_versions (
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
        cur.execute("CREATE INDEX idx_rv_table_record ON record_versions(table_name, record_id)")
        cur.execute("CREATE INDEX idx_rv_changed_at ON record_versions(changed_at)")
        print("  OK")
    else:
        print("[1/8] record_versions уже существует, пропускаю")

    # 2. Кастомные объекты
    if "custom_object_types" not in existing:
        print("[2/8] Создаю таблицу custom_object_types...")
        cur.execute("""
            CREATE TABLE custom_object_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) UNIQUE NOT NULL,
                label VARCHAR(200) NOT NULL,
                icon VARCHAR(50),
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  OK")
    else:
        print("[2/8] custom_object_types уже существует, пропускаю")

    if "custom_field_defs" not in existing:
        print("      Создаю таблицу custom_field_defs...")
        cur.execute("""
            CREATE TABLE custom_field_defs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_type_id INTEGER REFERENCES custom_object_types(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                label VARCHAR(200) NOT NULL,
                field_type VARCHAR(50) NOT NULL,
                is_required BOOLEAN DEFAULT 0,
                options TEXT,
                relation_target VARCHAR(100),
                position INTEGER DEFAULT 0,
                tenant_id INTEGER
            )
        """)
        print("  OK")

    if "custom_records" not in existing:
        print("      Создаю таблицу custom_records...")
        cur.execute("""
            CREATE TABLE custom_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_type_id INTEGER REFERENCES custom_object_types(id),
                created_by INTEGER REFERENCES users(id),
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  OK")

    if "custom_field_values" not in existing:
        print("      Создаю таблицу custom_field_values...")
        cur.execute("""
            CREATE TABLE custom_field_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER REFERENCES custom_records(id) ON DELETE CASCADE,
                field_def_id INTEGER REFERENCES custom_field_defs(id),
                value_text TEXT,
                value_number NUMERIC,
                value_boolean BOOLEAN,
                value_date TIMESTAMP,
                value_json TEXT,
                value_relation INTEGER REFERENCES custom_records(id),
                UNIQUE(record_id, field_def_id)
            )
        """)
        cur.execute("CREATE INDEX idx_cfv_record ON custom_field_values(record_id)")
        cur.execute("CREATE INDEX idx_cfv_field ON custom_field_values(field_def_id)")
        print("  OK")
    else:
        print("[2/8] custom_field_defs/records/values уже существуют, пропускаю")

    # 3. Справочник магазинов
    if "store_locations" not in existing:
        print("[3/8] Создаю таблицу store_locations...")
        cur.execute("""
            CREATE TABLE store_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) UNIQUE NOT NULL,
                address TEXT,
                phone VARCHAR(50),
                is_active BOOLEAN DEFAULT 1,
                tenant_id INTEGER
            )
        """)
        # Заполняем дефолтными значениями
        cur.execute("INSERT OR IGNORE INTO store_locations (name) VALUES ('Матусевича')")
        cur.execute("INSERT OR IGNORE INTO store_locations (name) VALUES ('Богдановича')")
        cur.execute("INSERT OR IGNORE INTO store_locations (name) VALUES ('БН')")
        print("  OK (добавлены: Матусевича, Богдановича, БН)")
    else:
        print("[3/8] store_locations уже существует, пропускаю")

    # 4. Справочник статусов сделок
    if "deal_statuses" not in existing:
        print("[4/8] Создаю таблицу deal_statuses...")
        cur.execute("""
            CREATE TABLE deal_statuses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) UNIQUE NOT NULL,
                position INTEGER DEFAULT 0,
                color VARCHAR(20),
                is_active BOOLEAN DEFAULT 1,
                tenant_id INTEGER
            )
        """)
        cur.execute("INSERT OR IGNORE INTO deal_statuses (name, position) VALUES ('Новый запрос', 0)")
        cur.execute("INSERT OR IGNORE INTO deal_statuses (name, position) VALUES ('В работе', 1)")
        cur.execute("INSERT OR IGNORE INTO deal_statuses (name, position) VALUES ('Ждет оплаты', 2)")
        cur.execute("INSERT OR IGNORE INTO deal_statuses (name, position) VALUES ('Сборка', 3)")
        print("  OK (добавлены 4 статуса)")
    else:
        print("[4/8] deal_statuses уже существует, пропускаю")

    # 5. Воркфлоу
    if "workflows" not in existing:
        print("[5/8] Создаю таблицы workflows...")
        cur.execute("""
            CREATE TABLE workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT 0,
                created_by INTEGER REFERENCES users(id),
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE workflow_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
                trigger_type VARCHAR(50) NOT NULL,
                config TEXT NOT NULL,
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE workflow_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
                parent_step_id INTEGER REFERENCES workflow_steps(id),
                step_type VARCHAR(50) NOT NULL,
                action_type VARCHAR(50),
                config TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                tenant_id INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE workflow_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER REFERENCES workflows(id),
                trigger_id INTEGER REFERENCES workflow_triggers(id),
                status VARCHAR(20) DEFAULT 'running',
                input_data TEXT,
                output_data TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                tenant_id INTEGER
            )
        """)
        print("  OK")
    else:
        print("[5/8] workflows уже существует, пропускаю")

    # 6. Webhooks
    if "webhooks" not in existing:
        print("[6/8] Создаю таблицу webhooks...")
        cur.execute("""
            CREATE TABLE webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url VARCHAR(500) NOT NULL,
                secret VARCHAR(200),
                events TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  OK")
    else:
        print("[6/8] webhooks уже существует, пропускаю")

    # 7. Сохранённые представления
    if "saved_views" not in existing:
        print("[7/8] Создаю таблицу saved_views...")
        cur.execute("""
            CREATE TABLE saved_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL,
                view_type VARCHAR(20) DEFAULT 'table',
                target_page VARCHAR(50) NOT NULL,
                filters TEXT,
                sort_by VARCHAR(100),
                sort_direction VARCHAR(10) DEFAULT 'asc',
                group_by VARCHAR(100),
                is_default BOOLEAN DEFAULT 0,
                created_by INTEGER REFERENCES users(id),
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  OK")
    else:
        print("[7/8] saved_views уже существует, пропускаю")

    # 8. Доп. индексы
    print("[8/8] Добавляю индексы...")
    indexes = [
        ("idx_cards_status", "cards", "status"),
        ("idx_cards_owner", "cards", "owner_id"),
        ("idx_cards_client", "cards", "client_id"),
        ("idx_cards_created", "cards", "created_at"),
        ("idx_cards_store", "cards", "store_location"),
        ("idx_tx_card", "transactions", "card_id"),
        ("idx_tx_date", "transactions", "date"),
        ("idx_tx_store", "transactions", "store_location"),
        ("idx_al_card", "activity_log", "card_id"),
        ("idx_al_user", "activity_log", "user_id"),
        ("idx_al_created", "activity_log", "created_at"),
    ]
    added = 0
    for idx_name, table, col in indexes:
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})")
            added += 1
        except sqlite3.OperationalError:
            pass  # индекс уже существует
    print(f"  Добавлено/проверено: {added} индексов")

    conn.commit()
    conn.close()

    print()
    print("=== Миграция v2.2 завершена! ===")
    print("Новые таблицы: record_versions, custom_object_types, custom_field_defs,")
    print("  custom_records, custom_field_values, store_locations, deal_statuses,")
    print("  workflows, workflow_triggers, workflow_steps, workflow_runs,")
    print("  webhooks, saved_views")

if __name__ == "__main__":
    migrate()
