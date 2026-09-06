"""0001: колонка cards.position — порядок карточек в колонке канбана.

Порт разовой миграции migrate_add_card_position.py (раньше жила только на
сервере и в git не попадала — причина появления раннера, Н5). Идемпотентна:
на базах, где колонка уже есть, ничего не делает.
"""


def up(cur):
    cols = {row[1] for row in cur.execute("PRAGMA table_info(cards)")}
    if "position" not in cols:
        cur.execute("ALTER TABLE cards ADD COLUMN position INTEGER")
