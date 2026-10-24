"""0021: индексы горячих фильтров — А-3 (аудит 2026-10-05).

Четыре колонки, по которым ходят фоновый скан просрочек и самые частые
списки, были без индексов:

  cards.due_date            — фильтр sync_overdue (просроченные сделки);
  cards.payment_due_date    — фильтр sync_overdue (card_payment-уведомления);
  activity_log.card_id      — лента карточки: самая растущая таблица проекта,
                              каждый Open карточки фильтрует по card_id;
  notifications.read_at     — Retention отбирает устаревшие уведомления.

Без индексов каждый из этих путей — полный скан таблицы, деградирующий
линейно с ростом базы (activity_log растёт быстрее всех). tasks.due_date
для сравнения индексирована всегда — эти четыре просто пропустили.

Идемпотентна: CREATE INDEX IF NOT EXISTS, повторный прогон ничего не меняет.
Зеркальные index=True добавлены в models.py, поэтому базы, собираемые
create_all (профиль CRM_TEST_SCHEMA=models), получают те же индексы.
"""


def up(cur):
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cards_due_date ON cards(due_date)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cards_payment_due_date ON cards(payment_due_date)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_log_card_id ON activity_log(card_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_read_at ON notifications(read_at)")
