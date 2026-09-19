"""0011: справочник магазинов из фактических значений карточек.

store_locations на проде был пуст (0 строк), хотя карточки несли строки
«Матусевича» (258) и «Богдановича» (95): v2 достраивал магазины из имён,
и такие «неявные» магазины нельзя было править из админки (id == имя).
Наполняем справочник уникальными непустыми значениями из
cards.store_location, чтобы магазины стали полноценными записями с id.

Сама привязка карточек к новым id не меняется намеренно: сервер хранит
store_location строкой и синхронизирует её по имени, поэтому поведение
не меняется, а справочник перестаёт быть пустым. Идемпотентна.
"""


def up(cur):
    cur.execute(
        "INSERT INTO store_locations (name, is_active) "
        "SELECT DISTINCT trim(store_location), 1 FROM cards "
        "WHERE store_location IS NOT NULL AND trim(store_location) <> '' "
        "AND trim(store_location) NOT IN (SELECT name FROM store_locations)"
    )
