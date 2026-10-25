"""0024: webhook_deliveries.payload — тело доставки переживает рестарт (волна D).

Зачем. Очередь вебхуков живёт в памяти (BoundedDeliveryExecutor), а строка
аудита в webhook_deliveries писалась БЕЗ тела: после рестарта PM2 запись
'queued'/'delivering' навсегда оставалась без содержимого — событие терялось
молча, хотя выглядело как «в очереди». Теперь тело хранится в колонке, и старт
процесса (main.py, _recover_webhook_queue_on_boot) переподчиняет незавершённые
доставки через recover_pending_deliveries().

Строки, созданные ДО миграции, тела не имеют: восстановить их нельзя, старт
закрывает их failed/restart_lost_payload — честный dead-letter вместо вечного
'queued'.

Идемпотентна: колонка добавляется только при отсутствии (conftest гоняет
create_all по моделям раньше миграций — там колонка уже будет).
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(webhook_deliveries)")}
    if "payload" not in cols:
        cur.execute("ALTER TABLE webhook_deliveries ADD COLUMN payload TEXT")
        print("  webhook_deliveries: добавлена колонка payload")
