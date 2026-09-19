"""0009: статус закупки пунктов чек-листа — ordered/received.

Фидбек 18.09: в v2 галочки «Заказано»/«Получено» не сохранялись —
модель хранила только is_paid/is_secondary_check (семантика старой
системы), а процесс закупки в v2 живёт в словах «заказано/получено».
Добавляем явные колонки; идемпотентна: добавляет только отсутствующие.
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(card_checklists)")}
    if "ordered" not in cols:
        cur.execute("ALTER TABLE card_checklists ADD COLUMN ordered BOOLEAN DEFAULT 0")
    if "received" not in cols:
        cur.execute("ALTER TABLE card_checklists ADD COLUMN received BOOLEAN DEFAULT 0")
