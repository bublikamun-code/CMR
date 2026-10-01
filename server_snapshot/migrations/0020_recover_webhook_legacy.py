"""0020: восстановление данных вебхуков, если 0017 оборвался на середине.

Зачем. Миграция 0017 пересобирает таблицу ``webhooks``: переименовывает старую
в ``webhooks_legacy_ba11``, создаёт новую из ``WEBHOOKS_DDL`` и переносит
строки ``INSERT..SELECT``. При ``isolation_level=""`` модуля ``sqlite3`` DDL не
открывает транзакцию, поэтому rename и CREATE коммитятся НЕМЕДЛЕННО, а
перенос и DROP — уже внутри транзакции, которую коммитит только migrate.py.

Если процесс убит между этими шагами (рестарт PM2, OOM, Ctrl-C), в базе
остаётся свежая ПУСТАЯ ``webhooks`` рядом с ``webhooks_legacy_ba11``, где лежат
все настоящие данные. Приложение читает ``webhooks`` — то есть все интеграции
молча мертвы.

Почему повторный прогон 0017 не чинит. ``_secret_type()`` смотрит тип колонки
``secret`` в ``webhooks``. Новая таблица создана из ``WEBHOOKS_DDL`` с
``secret TEXT``, поэтому тип ``TEXT`` — и ветка ``_rebuild_webhooks``, которая
и умеет переносить данные, НЕ выполняется. Дальше идёт UPDATE по пустой
таблице, который ничего не находит. Оборванная миграция не чинится никогда.

Почему не правка 0017. Она уже применена на боевой базе, а применённая миграция
повторно не выполняется: правка внутри неё не сработала бы никогда. Нужна
отдельная, восстанавливающая.

Что делает 0020. Если рядом осталась ``webhooks_legacy_ba11`` — переносит её
строки в ``webhooks`` (те, которых там ещё нет), шифруя секрет тем же Fernet и
тем же разбором состояния, что применяла 0017, после чего удаляет остаток.
Строка считается уже перенесённой, если в ``webhooks`` есть webhook с тем же
id, — это делает миграцию безопасной и для полностью оборванного случая, и для
частичного переноса.

Если legacy-таблицы нет (штатный случай, включая свежую установку) — миграция
не делает ничего. Идемпотентна.
"""

# Колонки переноса. Порядок совпадает с INSERT в migrations/0017:96-120 —
# набор берётся оттуда, а не из текущей модели, чтобы миграция не зависела от
# будущих правок схемы.
_COLUMNS = ("id", "url", "secret", "events", "is_active", "tenant_id", "created_at")


def _table_exists(cur, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(cur, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def _stored_secret_is_plaintext(cur, legacy_table: str) -> bool:
    """Различать шифротекст и legacy-plaintext нужно по ТИПУ колонки.

    В боевой базе до 0017 секреты лежали BLOB; 0017 создала колонку TEXT. Если
    тип BLOB — это заведомо ещё не зашифрованные значения, их надо зашифровать
    перед переносом. Тип TEXT означает, что значения уже в формате 0017 и
    трогать их нельзя: повторное шифрование сделало бы секрет нечитаемым.
    """
    row = cur.execute(
        "SELECT type FROM pragma_table_info(?) WHERE name='secret'", (legacy_table,)
    ).fetchone()
    return bool(row) and row[0].upper() == "BLOB"


def _encrypt_if_needed(cur, secret, fernet, needs_encryption: bool):
    if secret is None or not needs_encryption:
        return secret
    if isinstance(secret, bytes):
        secret = secret.decode("utf-8")
    if not secret:
        return secret
    return _encrypt(secret, fernet)


def up(cur) -> None:
    legacy = "webhooks_legacy_ba11"
    if not _table_exists(cur, legacy):
        # Штатный случай: 0017 отработала целиком, legacy-таблица не осталась.
        return
    if not _table_exists(cur, "webhooks"):
        # Штатный случай наоборот: таблицы ещё нет (свежая установка поверх
        # create_all). Данных терять нечего, отдаём слово 0017.
        return

    target_cols = _columns(cur, "webhooks")
    source_cols = _columns(cur, legacy)
    shared = [c for c in _COLUMNS if c in target_cols and c in source_cols]
    if "id" not in shared:
        raise RuntimeError(
            "Webhook recovery failed code=legacy_table_unreadable"
        )

    # Секреты шифруются только если legacy-колонка ещё хранит их открытым
    # текстом. Fernet нужен лениво: если переносить нечего, требовать ключ
    # незачем — иначе миграция падала бы там, где просто нечего делать.
    needs_encryption = _stored_secret_is_plaintext(cur, legacy)
    fernet = None
    if needs_encryption:
        from webhook_security import obtain_fernet
        fernet = obtain_fernet()

    existing = {row[0] for row in cur.execute("SELECT id FROM webhooks")}
    column_list = ", ".join(shared)
    placeholders = ", ".join("?" for _ in shared)
    restored = 0

    for row in cur.execute(f"SELECT {column_list} FROM {legacy}").fetchall():
        record = dict(zip(shared, row))
        if record["id"] in existing:
            # Уже перенесён — либо 0017 успела долить часть строк до падения.
            continue
        if "secret" in record:
            record["secret"] = _encrypt_if_needed(
                cur, record["secret"], fernet, needs_encryption)
        values = [record[c] for c in shared]
        cur.execute(
            f"INSERT INTO webhooks ({column_list}) VALUES ({placeholders})",
            values,
        )
        existing.add(record["id"])
        restored += 1

    # DROP только после переноса: если что-то пойдёт не так, данные останутся
    # в legacy-таблице и следующий прогон миграции попробует снова.
    cur.execute(f"DROP TABLE {legacy}")
    print(f"  webhooks: восстановлено строк из webhooks_legacy_ba11 — {restored}")


def _encrypt(plain: str, fernet) -> str:
    # Импорт внутри функции: основной импорт нужен только когда реально есть
    # что шифровать, а сам модуль webhook_security тянет конфигурацию ключа.
    from webhook_security import encrypt_secret
    return encrypt_secret(plain, fernet)