"""Бизнес-правила списаний и записей-остатков реестра оплат.

Вынесено из routers/payments_router.py и routers/writeoff_groups_router.py
(Фаза 4). Механический перенос без изменения логики.
"""

import models
from constants import MONEY_EPSILON


def writeoff_status_for(card, ledger):
    """Статус списания по факту выписки.

    Сделка закрыта, когда выписанными накладными покрыта вся сумма сделки
    (остатка к выписке нет). Непомеченные «списанием со склада» накладные
    статус не держат: выписка и складское списание — раздельные действия
    (фидбек 04.09), а в колонке «На списание» сделке нечего делать, когда
    выписано полностью (фидбек 09.09, «Свидеал»). Без суммы сделки эталона
    нет — работает старое правило по складским флажкам. Нет записей —
    «Сборка»: списывать нечего.
    """
    if not ledger:
        return "Сборка"
    if float(card.total_amount or 0) > 0:
        issued = round(sum(float(t.amount or 0) for t in ledger
                           if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
        rest = round(float(card.total_amount or 0) - issued, 2)
        return "На списание" if rest > MONEY_EPSILON else "Закрыто"
    return "На списание" if any(not t.is_warehouse_writeoff for t in ledger) else "Закрыто"


def ensure_registry_remainder(session, card):
    """Сделка в «Сборке» обязана иметь запись-остаток в реестре оплат.

    Запись создавали только кнопки карточки («В Сборку» / «В Списание» →
    trigger_from_card), а перетаскивание в «Сборку» и кнопки переноса
    запись не создавали — сделка пропадала из «Реестра оплат», хотя висела
    в «Сборке» (кейс «ТрансЛИДИЯсервис», фидбек 09.09). Функция идемпотентна:
    при существующих записях ничего не делает. Групповые сделки и сделки
    без суммы пропускаются — первыми управляет группа, вторые по правилам
    фронта дальше «Нового запроса» не двигаются.
    """
    if card is None or card.is_deleted or card.writeoff_group_id is not None:
        return None
    if float(card.total_amount or 0) <= 0:
        return None
    # FIX 2026-09-11 (Фаза 2): SessionLocal работает с autoflush=False, поэтому
    # SELECT ниже не видит строки, добавленные в этой же транзакции, но ещё не
    # отправленные в базу. При удалении накладной ветка возврата суммы уже
    # добавляла запись-остаток, этот SELECT её не находил и создавал вторую,
    # а частичный unique-индекс uq_remainder_per_card (миграция 0004) отвечал
    # IntegrityError → необработанный 500. flush() делает заявленную в докстринге
    # идемпотентность настоящей.
    session.flush()
    existing = session.query(models.Transaction).filter(
        models.Transaction.card_id == card.id,
        models.Transaction.is_document == False,
    ).first()
    if existing:
        return None
    tx = models.Transaction(
        company_name=card.title,
        amount=float(card.total_amount),
        store_location=card.store_location,
        card_id=card.id,
        # Запись-остаток живёт в tenant той же сделки.
        tenant_id=card.tenant_id,
    )
    session.add(tx)
    session.flush()
    return tx


def card_group_key(card: models.Card):
    """Ключ группировки: клиент + магазин."""
    return (card.client_id, card.store_location)


def recompute_group_total(group: models.WriteoffGroup):
    group.total_amount = round(
        sum(float(c.total_amount or 0) for c in group.cards), 2
    )
