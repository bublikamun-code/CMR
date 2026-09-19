"""0010: гигиена внешних ключей после удаления пользователя.

Удаление пользователя выполнялось при выключенном PRAGMA foreign_keys,
поэтому объявленный ON DELETE SET NULL не сработал: в record_versions
остались 144 ссылки и в activity_log 58 ссылок на несуществующего
пользователя, плюс 3 ссылки activity_log на удалённые карточки
(PRGAMA foreign_key_check на проде выдавал 205 строк).

Миграция обнуляет только осиротевшие ссылки; сами записи истории
(тексты версий и событий) сохраняются. Идемпотентна: повторный прогон
не меняет ничего.
"""


def up(cur):
    cur.execute(
        "UPDATE record_versions SET changed_by = NULL "
        "WHERE changed_by IS NOT NULL "
        "AND changed_by NOT IN (SELECT id FROM users)"
    )
    cur.execute(
        "UPDATE activity_log SET user_id = NULL "
        "WHERE user_id IS NOT NULL "
        "AND user_id NOT IN (SELECT id FROM users)"
    )
    cur.execute(
        "UPDATE activity_log SET card_id = NULL "
        "WHERE card_id IS NOT NULL "
        "AND card_id NOT IN (SELECT id FROM cards)"
    )
