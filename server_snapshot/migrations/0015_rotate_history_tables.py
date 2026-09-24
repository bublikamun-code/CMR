"""0015: ротация журналов — record_versions и activity_log (пункт 16 плана).

Зачем: очистки у этих таблиц не было вовсе, и они растут линейно. На копии
прод-БД от 2026-09-22 — 1477 версий и 1675 событий при возрасте базы всего
~70 дней (2026-07-15 … 2026-09-22). Единственная существующая чистка —
retention-блок в routers/notifications_router.py — для версий срабатывает
только на строках старше 180 дней, а таких в базе нет ни одной, то есть
фактически не срабатывает никогда; для activity_log ротации нет вообще.

Пороги выбраны не произвольно, а по потолку того, что API способно отдать,
и по уже объявленным в репо правилам — поэтому ротация невидима для UI:
  * KEEP_VERSIONS_PER_RECORD = 20 — та же константа, что в retention-блоке
    notifications_router («оставляем по 20 последних на запись»). Миграция не
    вводит новое правило, а применяет уже объявленное, снимая порог 180 дней,
    из-за которого оно никогда не срабатывало. На копии прод-БД убирает 94
    строки из 1477 (рекорд — 83 версии у одной сделки, и все они созданы за
    один день 2026-09-18 прогоном автотестов); versioning.get_versions()
    при этом читает limit=50 и просто вернёт меньше записей.
  * KEEP_LOG_PER_CARD = 200 — ровно максимум activity_router.list_activity
    (Query(50, le=200)); клиент просит limit=100 (v2-boot-template.js).
    Меньше брать нельзя: при потолке ниже API-максимума ротация молча
    отрезала бы то, что интерфейс способен запросить. Сегодня максимум на
    карточку — 26, то есть правило ничего не удаляет, а только ставит
    потолок на лавинный рост одной сделки.
  * LOG_RETENTION_DAYS = 180 — горизонт хранения журнала, тот же, что
    retention-блок notifications_router применяет к истории. Журнал сделки —
    пользовательские данные, которые видно в карточке, поэтому горизонт
    намеренно длиннее лимита на запись: на копии прод-БД (возраст базы
    ~70 дней) он не удаляет ни строки и начинает работать только по мере
    старения данных.
  * FRESH_DAYS = 1 — абсолютный пол: строки, созданные за последние сутки,
    не удаляются ни по одному из правил. Пол нужен не для «сохранения
    истории» (её хранят лимиты на объект), а против перекоса часов сервера
    и против удаления того, что прямо сейчас пишут или читают. Держать его
    длиннее бессмысленно: миграция выполняется один раз, и слишком широкий
    пол просто отменил бы очистку накопившегося.

Что НЕ удаляется ни при каком раскладе:
  * activity_log.action = 'Комментарий' — это пользовательский текст, который
    к тому же редактируется через PATCH /activity/{id}; молча потерять его
    хуже, чем перерасход места. На копии прод-БД таких 2 строки;
  * activity_log с created_at IS NULL или пустым — дату не проверить, поэтому
    выбираем сохранить;
  * record_versions с changed_at IS NULL или пустым — то же рассуждение.

Строки activity_log с card_id IS NULL (массовые правки оплат, события без
сделки — 62 шт.) не попадают под лимит «на карточку»: объекта, к которому их
можно отнести, нет. Под горизонт хранения они попадают на общих основаниях.

FK-безопасность: ни одна таблица схемы не ссылается на record_versions и
activity_log (ссылаются ОНИ — на users и cards), поэтому удаление строк не
может осиротить чужие записи и не требует никаких UPDATE.

Идемпотентна: после первого прогона строк под удаление не остаётся, повторный
прогон — no-op. На пустой базе и на базе без этих таблиц не падает.
"""
from datetime import datetime, timedelta, timezone

# Свежее этого возраста не удаляется никогда (см. докстринг).
FRESH_DAYS = 1
KEEP_VERSIONS_PER_RECORD = 20
KEEP_LOG_PER_CARD = 200
LOG_RETENTION_DAYS = 180

# SQLite ограничивает число параметров запроса — удаляем порциями.
_DELETE_CHUNK = 500


def _table_exists(cur, name):
    """База может быть свежей и ещё без таблиц — тогда ротация просто не нужна."""
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    )
    return cur.fetchone() is not None


def _cutoff(days):
    """Порог возраста строкой в формате, которым реально пишет SQLite.

    Колонки DateTime хранятся как naive-UTC строки 'YYYY-MM-DD HH:MM:SS.ffffff'
    (SQLAlchemy отбрасывает tzinfo при записи), поэтому сравниваем префиксом
    в 19 символов — см. _stamp(). Формируем порог в том же виде и в UTC,
    иначе сравнение строки в базе с порогом в локальном времени сервера
    сдвинуло бы границу на несколько часов.
    """
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _stamp(column):
    """SQL-выражение: дата колонки, приведённая к сравнимому префиксу.

    Первые 19 символов делают сравнение корректным сразу для трёх вариантов,
    которые встречаются в этой базе: с микросекундами и без, с разделителем
    'T' и с пробелом, с хвостом '+00:00' и без него (миграция 0003
    нормализовала только tasks/notifications, эти две таблицы не трогала).
    NULL и короткая строка дают NULL/'' и под условие удаления не попадают —
    то есть неизвестная дата трактуется как «сохранить».
    """
    return f"substr(replace({column}, 'T', ' '), 1, 19)"


def _delete_by_ids(cur, table, ids):
    for start in range(0, len(ids), _DELETE_CHUNK):
        chunk = ids[start:start + _DELETE_CHUNK]
        marks = ",".join("?" * len(chunk))
        cur.execute(f"DELETE FROM {table} WHERE id IN ({marks})", chunk)


def up(cur):
    fresh = _cutoff(FRESH_DAYS)
    horizon = _cutoff(LOG_RETENTION_DAYS)

    removed_versions = 0
    if _table_exists(cur, "record_versions"):
        # Держим KEEP_VERSIONS_PER_RECORD последних версий на (table_name,
        # record_id). Сортировка (version DESC, id DESC) — версия растёт
        # монотонно (versioning.save_version), id страхует равенство версий.
        cur.execute(
            "SELECT id FROM ("
            "  SELECT id, " + _stamp("changed_at") + " AS stamp,"
            "         ROW_NUMBER() OVER ("
            "             PARTITION BY table_name, record_id"
            "             ORDER BY version DESC, id DESC"
            "         ) AS rn"
            "  FROM record_versions"
            ") WHERE rn > ? AND stamp IS NOT NULL AND stamp <> '' AND stamp < ?",
            (KEEP_VERSIONS_PER_RECORD, fresh),
        )
        ids = [row[0] for row in cur.fetchall()]
        if ids:
            _delete_by_ids(cur, "record_versions", ids)
            removed_versions = len(ids)

    removed_log = 0
    if _table_exists(cur, "activity_log"):
        # Строка уходит, если она старее горизонта хранения ИЛИ выпала из
        # лимита на карточку; абсолютный пол FRESH_DAYS и защита комментариев
        # применяются к обоим правилам.
        cur.execute(
            "SELECT id FROM ("
            "  SELECT id, card_id, action, stamp,"
            "         ROW_NUMBER() OVER ("
            "             PARTITION BY card_id ORDER BY stamp DESC, id DESC"
            "         ) AS rn"
            "  FROM ("
            "    SELECT id, card_id, action, " + _stamp("created_at") + " AS stamp"
            "    FROM activity_log"
            "  )"
            ") WHERE action <> 'Комментарий'"
            "  AND stamp IS NOT NULL AND stamp <> '' AND stamp < ?"
            "  AND (stamp < ? OR (card_id IS NOT NULL AND rn > ?))",
            (fresh, horizon, KEEP_LOG_PER_CARD),
        )
        ids = [row[0] for row in cur.fetchall()]
        if ids:
            _delete_by_ids(cur, "activity_log", ids)
            removed_log = len(ids)

    print(f"  ротация журналов: record_versions -{removed_versions} "
          f"(лимит {KEEP_VERSIONS_PER_RECORD} на запись), "
          f"activity_log -{removed_log} "
          f"(лимит {KEEP_LOG_PER_CARD} на сделку / {LOG_RETENTION_DAYS} дней)")
