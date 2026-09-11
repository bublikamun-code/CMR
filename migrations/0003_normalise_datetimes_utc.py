"""0003: единый формат дат в SQLite-строках — naive UTC → aware UTC (Н7).

Модели пишут tz-aware UTC (default = datetime.now(timezone.utc)), а часть
кода (tasks/notifications) писала naive utcnow(). SQLite хранит DateTime
строкой: "2026-09-06 12:00:00" и "2026-09-06 12:00:00+00:00" сравниваются
как строки, и смесь форматов ломает сортировку/фильтры (просрочки задач,
read_at/created_at уведомлений). Миграция добавляет суффикс "+00:00"
строкам без таймзоны в затронутых колонках. Идемпотентна: строки уже с
таймзоной не трогаются.
"""

# (таблица, колонка): колонки, куда писал/пишет код с разными форматами.
_TARGETS = [
    ("tasks", "due_date"),
    ("tasks", "completed_at"),
    ("tasks", "created_at"),
    ("tasks", "updated_at"),
    ("notifications", "read_at"),
    ("notifications", "created_at"),
]


def up(cur):
    for table, column in _TARGETS:
        # col IS NOT '' — пустая строка не должна стать '+00:00'
        cur.execute(
            f"UPDATE {table} SET {column} = {column} || '+00:00' "
            f"WHERE {column} IS NOT NULL AND {column} != '' "
            f"AND {column} NOT LIKE '%+%' AND {column} NOT LIKE '%Z'"
        )
