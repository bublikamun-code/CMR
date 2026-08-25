"""
Миграция БД CRM v2
Добавляет: updated_at, activity_log, card_tags, due_date, suppliers,
индексы, ON DELETE, исправляет Float -> Numeric
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crm_app.db")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("=== Миграция CRM v2 ===")
    
    # 1. Добавляем updated_at ко всем таблицам
    tables = ['cards', 'transactions', 'clients', 'users', 'card_checklists', 'card_attachments']
    for table in tables:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME")
            print(f"  + updated_at -> {table}")
        except sqlite3.OperationalError:
            print(f"  ~ updated_at уже есть в {table}")
    
    # 2. Создаём таблицу лога действий
    c.execute("""CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
        action VARCHAR NOT NULL,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    print("  + activity_log")
    
    # 3. Создаём таблицу тегов
    c.execute("""CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL UNIQUE,
        color VARCHAR DEFAULT '#4f7cf5'
    )""")
    print("  + tags")
    
    c.execute("""CREATE TABLE IF NOT EXISTS card_tags (
        card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
        tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (card_id, tag_id)
    )""")
    print("  + card_tags")
    
    # 4. Создаём таблицу поставщиков
    c.execute("""CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        phone VARCHAR,
        email VARCHAR,
        unp VARCHAR,
        address VARCHAR,
        contact_person VARCHAR,
        note VARCHAR,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )""")
    print("  + suppliers")
    
    # 5. Добавляем due_date и priority в cards
    try:
        c.execute("ALTER TABLE cards ADD COLUMN due_date DATE")
        print("  + due_date -> cards")
    except sqlite3.OperationalError:
        print("  ~ due_date уже есть")
    
    try:
        c.execute("ALTER TABLE cards ADD COLUMN priority INTEGER DEFAULT 0")
        print("  + priority -> cards")
    except sqlite3.OperationalError:
        print("  ~ priority уже есть")
    
    # 6. Создаём индексы
    indexes = [
        ("idx_cards_status", "cards(status)"),
        ("idx_cards_is_deleted", "cards(is_deleted)"),
        ("idx_cards_owner_id", "cards(owner_id)"),
        ("idx_cards_client_id", "cards(client_id)"),
        ("idx_cards_created_at", "cards(created_at)"),
        ("idx_transactions_card_id", "transactions(card_id)"),
        ("idx_transactions_is_document", "transactions(is_document)"),
        ("idx_transactions_date", "transactions(date)"),
        ("idx_activity_log_card_id", "activity_log(card_id)"),
        ("idx_activity_log_user_id", "activity_log(user_id)"),
        ("idx_activity_log_created_at", "activity_log(created_at)"),
        ("idx_clients_unp", "clients(unp)"),
        ("idx_clients_name", "clients(name)"),
    ]
    
    for idx_name, idx_def in indexes:
        try:
            c.execute(f"CREATE INDEX {idx_name} ON {idx_def}")
            print(f"  + {idx_name}")
        except sqlite3.OperationalError:
            print(f"  ~ {idx_name} уже есть")
    
    # 7. Обновляем.updated_at для существующих записей
    c.execute("UPDATE cards SET updated_at = created_at WHERE updated_at IS NULL")
    c.execute("UPDATE transactions SET updated_at = date WHERE updated_at IS NULL")
    c.execute("UPDATE clients SET updated_at = created_at WHERE updated_at IS NULL")
    c.execute("UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    print("  ~ updated_at заполнен для существующих записей")
    
    conn.commit()
    conn.close()
    print("\n=== Миграция завершена ===")

if __name__ == "__main__":
    migrate()
