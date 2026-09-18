"""0008: приходы денег от клиента — client_payments (баланс клиента).

Фидбек 18.09: оплата ведётся НА КЛИЕНТА — бывают переплаты и допборы
(«Рацио Домус оплатили 57 тысяч, новый счёт на 789 берут в счёт тех
средств; оплаты фактически новой не было»). Вводится приход денег:

    баланс клиента = Σ client_payments.amount − Σ cards.total_amount
    (по неудалённым карточкам клиента; плюс — аванс, минус — долг).

Оплата карточки новыми деньгами создаёт приход (card_id заполняется,
выравнивание делает update_card_payment); закрытие карточки ИЗ БАЛАНСА
(PATCH /cards/{id}/payment с from_balance=1) увеличивает только
paid_amount карточки — баланс уменьшается автоматически.

Перенос истории: по каждой неудалённой карточке с paid_amount > 0
создаётся начальный приход на её paid_amount, чтобы баланс сходился с
первого дня. Идемпотентна: таблица создаётся только если её нет.
"""


def up(cur):
    exists = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='client_payments'"
    ).fetchone()
    if exists:
        return
    cur.execute(
        """
        CREATE TABLE client_payments (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
            amount NUMERIC(12, 2) NOT NULL,
            note VARCHAR(500),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at DATETIME,
            tenant_id INTEGER REFERENCES tenants(id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_payments_client_id "
        "ON client_payments (client_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_payments_card_id "
        "ON client_payments (card_id)"
    )
    # Перенос истории: начальные приходы по текущим paid карточек.
    cur.execute(
        """
        INSERT INTO client_payments (client_id, card_id, amount, note,
                                     created_at, tenant_id)
        SELECT c.client_id, c.id, c.paid_amount,
               'Перенос истории оплат (до 18.09.2026)',
               COALESCE(c.updated_at, CURRENT_TIMESTAMP), c.tenant_id
        FROM cards c
        WHERE COALESCE(c.is_deleted, 0) = 0
          AND c.client_id IS NOT NULL
          AND COALESCE(c.paid_amount, 0) > 0
        """
    )
