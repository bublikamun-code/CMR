"""0023: касса клиента становится журналом + явные привязки (волна C, 24.10).

Зачем. До этой миграции оплата сделки и деньги клиента существовали как два
скаляра (``cards.paid_amount`` и строки ``client_payments``), связанных только
честным словом: привязка прихода к карточке — информационная метка ``card_id``,
«сколько денег внесено по карточке» и «откуда взялось покрытие счёта» списки
не показывали никак, списание из баланса не оставляло следа в деньгах, а
возврата не существовало вовсе (Д-6 аудита 2026-10-05).

Что делает.

1. ``client_payments`` получает поля журнала:
   ``kind``  — deposit | refund (сумма всегда положительная, знак даёт kind);
   ``paid_at`` — дата платежа (не дата строки): касса дня и задние числа;
   ``method`` — наличные | карта | рс, справочно.
   Существующие строки — взносы (kind='deposit'), paid_at = created_at.

2. Новая таблица ``client_payment_allocations`` — явная привязка средств к
   карточке. Инвариант: ``cards.paid_amount == Σ активных привязок карточки``.

3. Бэкфилл: по каждой карточке с paid_amount > 0 создаётся одна миграционная
   привязка (source='migration') на весь paid_amount. Историю «какие приходы
   закрывали счёт» для старых данных восстановить нельзя — 6 прод-аномалий
   (paid_amount ≠ Σ приходов карточки, снимок 19.09) именно поэтому видны:
   сверка делается отчётом, данные не подгоняются. Карточки без клиента
   тоже получают привязку (client_id NULL) — инвариант карточки единый,
   клиентский инвариант (Σ привязок ≤ касса) проверяется только при
   наличии клиента.

Идемпотентна: колонки и таблица создаются IF NOT EXISTS / проверкой,
бэкфилл вставляет привязку только если у карточки ещё нет ни одной.
"""


def up(cur):
    # Идемпотентность обязательна (conftest: create_all по моделям идёт РАНЬШЕ
    # миграций и уже создаёт отсутствующие в прод-дампе таблицы с новыми
    # колонками — например, client_payments в дамп не входит вовсе). На боевом
    # же сервере client_payments существует с 0008 без этих колонок — там ALTER
    # реально применится.
    cols = {row[1] for row in cur.execute("PRAGMA table_info(client_payments)")}
    if "kind" not in cols:
        cur.execute(
            "ALTER TABLE client_payments ADD COLUMN kind TEXT NOT NULL DEFAULT 'deposit'")
        print("  client_payments: добавлена колонка kind")
    if "method" not in cols:
        cur.execute("ALTER TABLE client_payments ADD COLUMN method TEXT")
        print("  client_payments: добавлена колонка method")
    if "paid_at" not in cols:
        cur.execute("ALTER TABLE client_payments ADD COLUMN paid_at DATETIME")
        print("  client_payments: добавлена колонка paid_at")
    cur.execute("UPDATE client_payments SET paid_at = created_at WHERE paid_at IS NULL")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_payment_allocations (
            id INTEGER PRIMARY KEY,
            client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE SET NULL,
            payment_id INTEGER REFERENCES client_payments(id) ON DELETE SET NULL,
            amount NUMERIC(12, 2) NOT NULL,
            source VARCHAR(12) NOT NULL DEFAULT 'payment',
            status VARCHAR(10) NOT NULL DEFAULT 'active',
            note VARCHAR(500),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at DATETIME,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id)
        )
    """)
    for table, column in (
        ("client_payment_allocations", "client_id"),
        ("client_payment_allocations", "card_id"),
        ("client_payment_allocations", "status"),
        ("client_payment_allocations", "tenant_id"),
        ("client_payments", "kind"),
    ):
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")

    cur.execute("""
        INSERT INTO client_payment_allocations
            (client_id, card_id, amount, source, status, created_at, tenant_id)
        SELECT c.client_id, c.id, c.paid_amount, 'migration', 'active',
               COALESCE(c.updated_at, c.created_at, CURRENT_TIMESTAMP), c.tenant_id
        FROM cards c
        WHERE IFNULL(c.paid_amount, 0) > 0.005
          AND NOT EXISTS (SELECT 1 FROM client_payment_allocations a WHERE a.card_id = c.id)
    """)
