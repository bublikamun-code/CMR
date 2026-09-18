"""Ключ дня в cron-просрочках — UTC, а не локальная дата сервера.

Дефект (починен 2026-09-12, коммит 3c276a9): в `sync_overdue` ключ дня
строился как `date.today().isoformat()`, то есть по ЛОКАЛЬНОЙ таймзоне
сервера. Сервер живёт в Europe/Minsk (UTC+3), поэтому локальные сутки
наступали в 21:00 UTC, а `now` при этом уже брался из UTC. Один и тот же
`day_key` используется двояко:

1. уходит в `notify(..., details=day_key)` — подпись дня в уведомлении о
   просрочке (по ней видно, за какие сутки сработал cron);
2. сравнивается СТРОКОЙ в фильтрах `Card.due_date < day_key` и
   `Card.payment_due_date < day_key` — то есть локальные сутки напрямую
   решали, какие сделки считаются просроченными.

Вечером (21:00–24:00 UTC) обе части расходились с реальностью: сделка со
сроком «сегодня по UTC» получала «Просрочена сделка» на три часа раньше
времени, а уведомления за сутки приходили дважды.

Тест заморожен во времени и разводит локальную дату с UTC: 22:00 UTC
12.09.2026 — это уже 01:00 13.09.2026 в Минске. Поэтому он детерминированный
и падает на старом коде на любой машине: наивное сравнение «day_key равен
сегодняшней UTC-дате» на хосте с TZ=UTC прошло бы и с багом.
"""
from datetime import date, datetime, timezone

import pytest

import models
from routers import notifications_router as nr

# 22:00 UTC = 01:00 следующих суток в Europe/Minsk (UTC+3)
FIXED_UTC_NOW = datetime(2026, 9, 12, 22, 0, 0, tzinfo=timezone.utc)
# локальная дата в этот же момент — то, что вернул бы date.today() на проде
LOCAL_TODAY = date(2026, 9, 13)

UTC_DAY_KEY = FIXED_UTC_NOW.date().isoformat()      # "2026-09-12" — правильно
LOCAL_DAY_KEY = LOCAL_TODAY.isoformat()             # "2026-09-13" — старый баг
UTC_YESTERDAY = date(2026, 9, 11)


class _FrozenDatetime(datetime):
    """`datetime` из пространства имён роутера с замороженным `now()`.

    Наследование от настоящего класса обязательно: sync_overdue использует
    не только now(), но и арифметику (`now - timedelta(days=30)` в блоке
    ретенции) и `.date()`. Возвращаем aware-момент, как того требует
    конвенция проекта (migrations/0003_normalise_datetimes_utc, Н7).
    """

    @classmethod
    def now(cls, tz=None):
        return FIXED_UTC_NOW


class _FrozenDate(date):
    """`date.today()` — локальные сутки сервера (Europe/Minsk)."""

    @classmethod
    def today(cls):
        return LOCAL_TODAY


@pytest.fixture
def frozen_clock(monkeypatch):
    """Подменяет datetime/date в МОДУЛЕ роутера, а не в datetime вообще.

    `date` подменяется с raising=False: после фикса модуль его не импортирует,
    а вот старый код импортировал и звал `date.today()` — без raising=False
    тест нельзя было бы проверить на старом поведении, а с ним подмена
    безвредна для нового кода и смертельна для старого.
    """
    monkeypatch.setattr(nr, "datetime", _FrozenDatetime)
    monkeypatch.setattr(nr, "date", _FrozenDate, raising=False)
    return FIXED_UTC_NOW


def _notifications(db):
    db.expire_all()
    return db.query(models.Notification).order_by(models.Notification.id).all()


def test_overdue_task_gets_utc_day_in_details(client, cron_headers, db, manager,
                                              frozen_clock):
    """Подпись дня в уведомлении о просрочке — UTC-дата, не локальные сутки.

    Задача живёт в ОСНОВНОЙ базе и фильтруется по `due_date < now` (не по
    day_key), поэтому это чистая проверка первого использования day_key:
    что именно cron кладёт в details. На старом коде здесь был бы
    "2026-09-13" — дата, которой в UTC ещё не наступило.
    """
    user, _ = manager
    task = models.Task(title="Заменить драйвер", status="todo",
                       due_date=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
                       assignee_id=user.id, creator_id=user.id)
    db.add(task)
    db.commit()
    task_id = task.id

    r = client.post("/notifications/sync-overdue", headers=cron_headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    notes = _notifications(db)
    assert len(notes) == 1
    note = notes[0]
    assert note.type == "task_overdue"
    assert note.entity_type == "task" and note.entity_id == task_id
    assert note.user_id == user.id
    assert note.details != LOCAL_DAY_KEY, \
        f"day_key снова берётся из локальной даты сервера ({LOCAL_DAY_KEY})"
    assert note.details == UTC_DAY_KEY, \
        f"ожидалась UTC-дата {UTC_DAY_KEY}, получено {note.details!r}"


def test_card_due_today_utc_is_not_overdue_yet(client, cron_headers, db, manager,
                                               make_card, frozen_clock):
    """Сделка со сроком «сегодня по UTC» вечером ещё НЕ просрочена.

    Второе использование day_key — строковое сравнение в фильтрах
    `Card.due_date < day_key` и `Card.payment_due_date < day_key`. Локальный
    ключ "2026-09-13" делал обе такие сделки просроченными в 21:00–24:00 UTC,
    то есть менеджер получал «Просрочена сделка» и «Истёк срок оплаты» за день
    до события. Вчерашняя сделка в том же тесте — контроль в другую сторону:
    фильтр обязан остаться фильтром, а не «никого не уведомлять».
    """
    user, _ = manager
    due_today = make_card(title="Сделка со сроком сегодня", owner_id=user.id,
                          due_date=FIXED_UTC_NOW.date(), status="Новый запрос")
    pay_today = make_card(title="Сделка с оплатой сегодня", owner_id=user.id,
                          payment_due_date=FIXED_UTC_NOW.date(),
                          payment_status="Не оплачен", status="Новый запрос")
    overdue = make_card(title="Сделка просрочена со вчера", owner_id=user.id,
                        due_date=UTC_YESTERDAY, status="Новый запрос")

    r = client.post("/notifications/sync-overdue", headers=cron_headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1, "уведомление должно быть ровно одно — по вчерашней сделке"

    notes = _notifications(db)
    assert len(notes) == 1
    note = notes[0]
    assert note.type == "card_overdue"
    assert note.entity_id == overdue.id
    assert note.details == UTC_DAY_KEY

    by_entity = {(n.type, n.entity_id) for n in notes}
    assert ("card_overdue", due_today.id) not in by_entity, \
        f"сделка со сроком {FIXED_UTC_NOW.date()} помечена просроченной до наступления суток UTC"
    assert ("card_payment", pay_today.id) not in by_entity, \
        f"оплата со сроком {FIXED_UTC_NOW.date()} помечена просроченной до наступления суток UTC"
