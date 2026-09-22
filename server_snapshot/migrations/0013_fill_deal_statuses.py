"""0013: справочник статусов сделок из фактических значений карточек.

deal_statuses на проде был пуст (0 строк), хотя карточки несли 6 статусов
(«Новый запрос» 657, «В работе» 187, «Закрыто» 179, «Сборка» 19,
«На списание» 17, «Ждет оплаты» 5). Из-за этого /dictionaries/statuses
отвечал [], и доска v2 строила колонки по клиентскому хардкоду STATUS_MAP:
статусы нельзя было ни переименовать из админки, ни добавить. Наполняем
справочник уникальными непустыми значениями cards.status — по образцу 0011
для магазинов.

position и color берём из клиентского канона (STATUS_MAP в
tools/v2-boot-template.js и дефолты migrate_v2_2) — так порядок колонок и
цвета не меняются при переходе с хардкода на справочник. В color пишем
имена CSS-токенов (--faint/--accent/--warn/--ok/--danger), а не смысловые
ключи вроде «new»: клиент подставляет var(--<color>), и несуществующий
токен дал бы битый цвет колонки. Статусу вне канона position растёт от 50
(после канонических колонок, по алфавиту), color остаётся NULL — клиент
сам откатится в faint.

Привязку карточек не трогаем намеренно: сервер хранит cards.status строкой
и сверяет её со schemas.CARD_STATUSES, поэтому карточки не мигрируют на id
справочника и существующие данные не переписываются. Идемпотентна: вставляет
только те имена, которых в справочнике ещё нет, и не перезаписывает
position/color созданных руками строк.

Применять ТОЛЬКО вместе с клиентской половиной пункта 12. Непустой
/dictionaries/statuses переключает v2 с хардкода STATUS_MAP на ветку словаря
(tools/v2-boot-template.js:124), и там «Закрыто» получает role='board':
на доске появляется пятая колонка со 179 закрытыми сделками, а её id 'done'
сталкивается с хардкодом «Списано» в списке этапов карточки
(tools/mockups/shell-v2-board.js:1289) и с очередью списания, где
stage === 'done' означает «Списано» (shell-v2-board.js:569).
"""

# Канон имени, порядка колонки и цвета — тот же, что в клиентском STATUS_MAP.
CANONICAL = (
    ("Новый запрос", 0, "faint"),
    ("В работе", 1, "accent"),
    ("Ждет оплаты", 2, "warn"),
    ("Сборка", 3, "ok"),
    ("На списание", 4, "danger"),
    ("Закрыто", 5, "faint"),
)

EXTRA_POSITION0 = 50


def up(cur):
    existing = {row[0] for row in cur.execute("SELECT name FROM deal_statuses")}
    actual = [row[0] for row in cur.execute(
        "SELECT DISTINCT trim(status) FROM cards "
        "WHERE status IS NOT NULL AND trim(status) <> ''"
    )]
    canonical = {name: (position, color) for name, position, color in CANONICAL}

    missing = [name for name in actual if name not in existing]
    extra = {name: EXTRA_POSITION0 + i
             for i, name in enumerate(sorted(n for n in missing if n not in canonical))}

    rows = []
    for name in missing:
        if name in canonical:
            position, color = canonical[name]
        else:
            position, color = extra[name], None
        rows.append((name, position, color))

    cur.executemany(
        "INSERT INTO deal_statuses (name, position, color, is_active) VALUES (?, ?, ?, 1)",
        sorted(rows, key=lambda row: (row[1], row[0])),
    )
