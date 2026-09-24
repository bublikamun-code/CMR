"""0016: серверный список отозванных JWT-токенов.

В базе хранится только ключ токена (``jti:<jti>`` или SHA-256 полного JWT),
поэтому bearer-секреты не попадают в persistent storage. Создание таблицы и
индексов идемпотентно: миграция безопасно запускается поверх ORM create_all,
снимка боевой схемы и повторного запуска.
"""


def up(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS revoked_auth_tokens (
            id INTEGER PRIMARY KEY,
            token_key TEXT NOT NULL,
            expires_at DATETIME,
            revoked_at DATETIME NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_revoked_auth_tokens_token_key
        ON revoked_auth_tokens (token_key)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_revoked_auth_tokens_expires_at
        ON revoked_auth_tokens (expires_at)
        """
    )
