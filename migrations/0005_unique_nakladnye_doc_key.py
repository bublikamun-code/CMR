"""0005: уникальность накладной по нормализованному номеру (дефект 7, TOCTOU).

Приём накладных шёл через check-then-insert двумя отдельными запросами: бот
спрашивал GET /nakladnye/bot/check-duplicate, затем делал
POST /nakladnye/bot/create. CRM-создание не проверяло дубли вовсе. Между
проверкой и вставкой любая параллельная отправка того же документа плодила
вторую запись — и обе потом висели в сверке с поставщиком. Проверкой в коде
гонка не закрывается в принципе: нужен уникальный индекс в базе.

Почему индекс по НОРМАЛИЗОВАННОМУ ключу, а не по сырым (doc_series, doc_number):
в боевых данных соседствуют «ТТН 4881030» и «ТТН4881030», а бот присылает серию
в другом регистре и с пробелами. Индекс по сырым строкам пропустил бы ровно тот
дубль, который встречается в жизни. Правила те же, что уже применял
bot_check_duplicate: серия — strip+upper, номер — только цифры
(копия функции models.nakladnaya_doc_key; миграции не импортируют код
приложения, поэтому правило продублировано здесь).

Порядок (данные важнее схемы):
  1. Добавляются колонки doc_series_norm / doc_number_norm — только отсутствующие.
  2. Ключ заполняется для существующих строк. Пересчёт идемпотентен: строка,
     у которой ключ уже верный, не трогается.
  3. ПЕРЕД созданием индекса ищутся нарушения. Если они есть — миграция
     печатает их построчно и ПАДАЕТ: чистить дубли накладных молча нельзя,
     это уничтожение документов. Решение по каждой паре принимает человек.
     Миграция не отмечается применённой, поэтому следующий запуск повторит её.
     Проверено на копии боевой схемы: индекс не создаётся, строки nakladnye
     не удаляются, PRAGMA foreign_key_check чист. Колонки из шага 1 при этом
     в базе ОСТАЮТСЯ (sqlite3 коммитит DDL отдельно от DML) — это безопасно,
     шаг 1 добавляет только отсутствующие колонки.
  4. Создаётся частичный уникальный индекс: строки без номера исключены.

Что индекс защищает и чего НЕ защищает: значения *_norm считает слушатель
models._nakladnaya_sync_doc_key на уровне ORM, поэтому вставка сырым SQL
мимо ORM оставит ключи пустыми и под индекс не попадёт. В приложении все
записи nakladnye создаются через ORM (nakladnye_router: create_nakladnaya
и bot_create_nakladnaya — других мест нет), так что гонка приёма закрыта.
Правило «пишем только через ORM» зафиксировано в докстринге слушателя.

Индекс частичный нарочно: telegram-бот при нераспознанном документе создаёт
запись без номера («Не распознано»), и таких записей может быть много.
Номер без единой цифры даёт doc_number_norm = NULL, и под индекс строка
не попадает.

Серия при отсутствии нормализуется в ПУСТУЮ СТРОКУ, а не в NULL: в уникальном
индексе SQLite NULL не равен NULL, поэтому две накладные с одним номером
и незаполненной серией дублями бы не считались.

Идемпотентна: колонки добавляются только отсутствующие, пересчёт ключа
сходится за один проход, индекс IF NOT EXISTS. Проверено двойным прогоном
migrate.py на копии боевой схемы.
"""


def _doc_key(doc_series, doc_number):
    """Копия models.nakladnaya_doc_key — миграции не импортируют код приложения."""
    series = (doc_series or "").strip().upper()
    number = "".join(ch for ch in (doc_number or "") if ch.isdigit())
    return series, (number or None)


def up(cur):
    if not cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='nakladnye'"
    ).fetchone():
        print("  nakladnye: таблицы нет — пропускаю (её создаст create_all)")
        return

    columns = {row[1] for row in cur.execute("PRAGMA table_info(nakladnye)")}
    for column in ("doc_series_norm", "doc_number_norm"):
        if column not in columns:
            cur.execute(f"ALTER TABLE nakladnye ADD COLUMN {column} VARCHAR(50)")
            print(f"  nakladnye: добавлена колонка {column}")

    rows = cur.execute(
        "SELECT id, doc_series, doc_number, doc_series_norm, doc_number_norm "
        "FROM nakladnye ORDER BY id"
    ).fetchall()
    filled = 0
    for row_id, series, number, series_norm, number_norm in rows:
        want_series, want_number = _doc_key(series, number)
        if (series_norm, number_norm) != (want_series, want_number):
            cur.execute(
                "UPDATE nakladnye SET doc_series_norm = ?, doc_number_norm = ? "
                "WHERE id = ?",
                (want_series, want_number, row_id),
            )
            filled += 1
    print(f"  nakladnye: ключ заполнен/обновлён у {filled} из {len(rows)} записей")

    duplicates = cur.execute(
        "SELECT doc_series_norm, doc_number_norm, COUNT(*) AS c, GROUP_CONCAT(id) AS ids "
        "FROM nakladnye "
        "WHERE doc_number_norm IS NOT NULL AND doc_number_norm <> '' "
        "GROUP BY doc_series_norm, doc_number_norm HAVING c > 1 ORDER BY ids"
    ).fetchall()
    if duplicates:
        lines = []
        for series_norm, number_norm, count, ids in duplicates:
            details = cur.execute(
                "SELECT id, doc_series, doc_number, doc_date, amount, supplier_name, "
                "created_at, created_by_bot FROM nakladnye WHERE id IN ("
                + ",".join("?" * len(str(ids).split(",")))
                + ") ORDER BY id",
                tuple(int(i) for i in str(ids).split(",")),
            ).fetchall()
            lines.append(
                f"  серия {series_norm!r} номер {number_norm!r} — {count} записи: "
                + "; ".join(
                    f"id={d[0]} «{d[1] or ''}{d[2] or ''}» от {d[3] or '—'} "
                    f"на {float(d[4]) if d[4] is not None else 0:.2f} BYN, "
                    f"поставщик {d[5] or '—'}, создана {d[6] or '—'}"
                    + (" ботом" if d[7] else "")
                    for d in details
                )
            )
        raise RuntimeError(
            "0005: в nakladnye есть дубли по нормализованному номеру — "
            "уникальный индекс НЕ создан, данные НЕ изменены.\n"
            "Разберите пары вручную (оставить одну запись, лишнюю удалить через "
            "UI, чтобы вместе с ней ушли фото и Excel), затем повторите миграцию:\n"
            + "\n".join(lines)
        )

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_nakladnye_doc_key "
        "ON nakladnye(doc_series_norm, doc_number_norm) "
        "WHERE doc_number_norm IS NOT NULL AND doc_number_norm <> ''"
    )
    print("  nakladnye: создан уникальный индекс uq_nakladnye_doc_key")
