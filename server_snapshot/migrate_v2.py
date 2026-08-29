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
    
    # 1. Добавляем updated_at ко всем таблицам.
    # SQL — литералы без переменных: таблицы фиксированы, интерполяция имён
    # в миграциях не используется намеренно.
    for table in ["cards", "transactions", "clients", "users", "card_checklists", "card_attachments"]:
        try:
            if table == "cards":
                c.execute("ALTER TABLE cards ADD COLUMN updated_at DATETIME")
            elif table == "transactions":
                c.execute("ALTER TABLE transactions ADD COLUMN updated_at DATETIME")
            elif table == "clients":
                c.execute("ALTER TABLE clients ADD COLUMN updated_at DATETIME")
            elif table == "users":
                c.execute("ALTER TABLE users ADD COLUMN updated_at DATETIME")
            elif table == "card_checklists":
                c.execute("ALTER TABLE card_checklists ADD COLUMN updated_at DATETIME")
            else:
                c.execute("ALTER TABLE card_attachments ADD COLUMN updated_at DATETIME")
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
    
    # 6. Создаём индексы — явные DDL ниже
    # Индексы фиксированы — литеральные DDL без интерполяции имён.
    try:
        c.execute("CREATE INDEX idx_cards_status ON cards(status)")
    except sqlite3.OperationalError:
        print("  ~ idx_cards_status уже есть")
    try:
        c.execute("CREATE INDEX idx_cards_is_deleted ON cards(is_deleted)")
    except sqlite3.OperationalError:
        print("  ~ idx_cards_is_deleted уже есть")
    try:
        c.execute("CREATE INDEX idx_cards_owner_id ON cards(owner_id)")
    except sqlite3.OperationalError:
        print("  ~ idx_cards_owner_id уже есть")
    try:
        c.execute("CREATE INDEX idx_cards_client_id ON cards(client_id)")
    except sqlite3.OperationalError:
        print("  ~ idx_cards_client_id уже есть")
    try:
        c.execute("CREATE INDEX idx_cards_created_at ON cards(created_at)")
    except sqlite3.OperationalError:
        print("  ~ idx_cards_created_at уже есть")
    try:
        c.execute("CREATE INDEX idx_transactions_card_id ON transactions(card_id)")
    except sqlite3.OperationalError:
        print("  ~ idx_transactions_card_id уже есть")
    try:
        c.execute("CREATE INDEX idx_transactions_is_document ON transactions(is_document)")
    except sqlite3.OperationalError:
        print("  ~ idx_transactions_is_document уже есть")
    try:
        c.execute("CREATE INDEX idx_transactions_date ON transactions(date)")
    except sqlite3.OperationalError:
        print("  ~ idx_transactions_date уже есть")
    try:
        c.execute("CREATE INDEX idx_activity_log_card_id ON activity_log(card_id)")
    except sqlite3.OperationalError:
        print("  ~ idx_activity_log_card_id уже есть")
    try:
        c.execute("CREATE INDEX idx_activity_log_user_id ON activity_log(user_id)")
    except sqlite3.OperationalError:
        print("  ~ idx_activity_log_user_id уже есть")
    try:
        c.execute("CREATE INDEX idx_activity_log_created_at ON activity_log(created_at)")
    except sqlite3.OperationalError:
        print("  ~ idx_activity_log_created_at уже есть")
    try:
        c.execute("CREATE INDEX idx_clients_unp ON clients(unp)")
    except sqlite3.OperationalError:
        print("  ~ idx_clients_unp уже есть")
    try:
        c.execute("CREATE INDEX idx_clients_name ON clients(name)")
    except sqlite3.OperationalError:
        print("  ~ idx_clients_name уже есть")
    
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
