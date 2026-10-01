"""Оркестрация money-эндпоинтов реестра оплат и сериализация транзакций.

Вынесено из routers/payments_router.py (Фаза 4): бизнес-логика эндпоинтов
add_invoice / issue_invoice / delete_transaction и сериализатор tx_dict.
Роутеры остались HTTP-обвязкой. HTTPException из сервиса пробрасывается
FastAPI как есть — коды ответов те же, что были в хендлерах.
"""

import contextlib

from fastapi import HTTPException

import models
from services.invoices import find_duplicate_invoice, find_invoice_twins
from services.sqlite_writer import reserve_model_rows
from services.writeoffs import (
    ensure_registry_remainder,
    money_is_present,
    money_is_zero,
    writeoff_status_for,
)
from tenant_policy import get_tenant_object, tenant_query


@contextlib.contextmanager
def rollback_on_error(session):
    """Откатить незавершённую операцию и поднять исходную ошибку дальше.

    FIX 2026-09-26 (аудит): многошаговые операции этого модуля (выписка,
    добавление накладной, отмена) состоят из десятка SELECT/INSERT/DELETE и
    завершаются одним commit. Любой сбой на промежуточном шаге оставлял сессию
    в состоянии pending-rollback, и ВСЕ последующие обращения к базе в том же
    запросе падали каскадом с «This Session's transaction has been rolled
    back due to a previous exception», маскируя исходную причину.

    Откат делаем в finally-подобном виде: swallow ничего, исходное исключение
    поднимается без изменений (в т.ч. HTTPException с его кодом и detail).
    """
    try:
        yield
    except Exception:
        session.rollback()
        raise


def _detect_advance(t) -> bool:
    """A5: признак аванса по записи реестра.

    Единственный надёжный сигнал без миграции — слово «аванс» или «advance»
    в поле note (регистр не важен). Старый фронт помечает авансы именно
    через примечание; v2 фильтрует по server-side is_advance.
    """
    note = (t.note or "").lower()
    return "аванс" in note or "advance" in note


def tx_dict(t, parts=1, invoices=0, paid=None, partial=False, paid_amount=None, payment_status=None):
    # UI FIX 2026-08-31: все ответные ветки этого роутера возвращают dict, а не
    # ORM-объект: сессия закрывается в finally до сериализации ответа,
    # и pydantic падал с DetachedInstanceError (is_warehouse_writeoff, date,
    # card_id, ...). Дополнено поле writeoff_group_id (есть в схеме ответа).
    return {
        "id": t.id, "date": t.date, "card_id": t.card_id,
        "writeoff_group_id": t.writeoff_group_id,
        "company_name": t.company_name, "amount": float(t.amount or 0),
        "store_location": t.store_location or "",
        "is_calculated": bool(t.is_calculated),
        "is_invoice_issued": bool(t.is_invoice_issued),
        "is_written_off": bool(t.is_written_off),
        # дефект 13: поле можно править через TransactionUpdate, поэтому оно
        # обязано и читаться обратно — иначе фронт не знает текущего значения
        "is_secondary_check": bool(t.is_secondary_check),
        "print_status": t.print_status, "note": t.note,
        "invoice_number": t.invoice_number, "invoice_date": t.invoice_date,
        "is_document": bool(t.is_document),
        # V7 (пункт 17 плана v2): is_invoice_doc / is_bill_doc — ОРИГИНАЛЫ
        # документов у нас: «оригинал ТН возвращён» (карточка v2) == «ТН у нас»
        # (финансы v2) == колонка «ТН» в «Документах» legacy. Один бизнес-факт
        # под тремя подписями, хранится на записи-документе (is_document=True);
        # отдельной колонки originals_returned нет и не будет — разбор решения
        # в docs/audits/V2-PLAN-RECON-2026-09-22.md §6. В кодировке проекта
        # «invoice» = накладная (invoice_number — № ТН), поэтому имя колонки
        # читается как «накладная-документ у нас» и не противоречит смыслу.
        "is_invoice_doc": bool(t.is_invoice_doc),
        "is_bill_doc": bool(t.is_bill_doc),
        "is_warehouse_writeoff": bool(t.is_warehouse_writeoff),
        "parts_count": parts, "invoices_count": invoices,
        "part_ids": None,
        "paid_amount": paid_amount,
        "payment_status": payment_status,
        "is_partial_payment": partial,
        # A5: серверный признак аванса — v2 фильтрует по нему.
        "is_advance": _detect_advance(t),
    }


def delete_transaction(session, transaction_id, actor):
    """Удаление записи реестра. Вынесено из delete_transaction в
    routers/payments_router.py (Фаза 4) — тело перенесено посимвольно.

    FIX 2026-09-26 (tenant): запись искалась по глобальному id, поэтому
    пользователь одного tenant отменял чужую накладную. Теперь и запись, и
    карточка сделки загружаются через tenant-policy, а чужой объект даёт тот
    же 404, что и отсутствующий. Ошибка на любом шаге откатывает сессию
    (rollback_on_error), иначе после IntegrityError весь запрос падал каскадом.
    """
    with rollback_on_error(session):
        return _delete_transaction(session, transaction_id, actor)


def _tenant_card(session, card_id, actor, *, detail="Карточка не найдена"):
    """Карточка сделки в tenant вызывающего. Отсутствующая и чужая — 404.

    Без актора (tenant-контекста) сделку не показываем никому: глобальный
    поиск по id здесь означал бы ровно тот дефект, который тут устраняется.
    """
    if actor is None:
        raise HTTPException(status_code=404, detail=detail)
    return get_tenant_object(session, models.Card, card_id, actor, detail=detail)


def _in_chunks(ids, size=500):
    """Разбить список id на куски под ограничение числа параметров SQLite.

    Один IN(...) на тысячу сделок — это тысяча связанных переменных в
    одном statement; у SQLite лимит на них исторически 999 (сейчас выше, но
    он версионный). Куски дают тот же результат без риска «too many SQL
    variables» на большой группе.
    """
    ordered = list(ids)
    for start in range(0, len(ordered), size):
        yield ordered[start:start + size]


def _delete_transaction(session, transaction_id, actor):
    query = tenant_query(session, models.Transaction, actor).filter(
        models.Transaction.id == transaction_id
    )
    tx = query.first()
    if not tx:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    if tx.card_id and not tx.is_document:
        card = _tenant_card(session, tx.card_id, actor)
        if card and card.writeoff_group_id is not None:
            raise HTTPException(status_code=400, detail="Сначала выведите карточку из группы")

    card_id = tx.card_id
    number = (tx.invoice_number or "").strip()
    amount = float(tx.amount or 0)
    store = tx.store_location
    was_invoice = bool(tx.is_warehouse_writeoff or number)

    # Накладная и её копия в «Документах» — ОДНА сущность.
    # Удаление с любой стороны должно снести обе, иначе разъезжается:
    # из «Документов» удаляли копию, а накладная оставалась в карточке.
    twins = find_invoice_twins(session, tx)

    for t in twins:
        session.delete(t)
    session.delete(tx)
    session.flush()

    # Сумма удалённой накладной возвращается в остаток по сделке
    if card_id is not None and was_invoice:
        card = _tenant_card(session, card_id, actor)
        if card:
            left = session.query(models.Transaction).filter(
                models.Transaction.card_id == card_id,
                models.Transaction.is_document == False,
            ).order_by(models.Transaction.id.asc()).all()

            rest_rows = [t for t in left
                         if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()]
            if rest_rows:
                # FIX 2026-09-26 (накопление float): сложение остатка и
                # возвращаемой суммы округляем на границе ЗАПИСИ. Внутри
                # цикла округлять нечего — цикла нет, а копейки должны
                # уехать в базу ровно, иначе ошибка растёт с каждой отменой
                # накладной и дрейфует в остатке сделки.
                rest_rows[0].amount = round(float(rest_rows[0].amount or 0) + amount, 2)
            else:
                session.add(models.Transaction(
                    company_name=card.title, amount=round(amount, 2),
                    store_location=store, card_id=card_id,
                    tenant_id=card.tenant_id,
                ))
            # По сделке снова есть что списывать -> она не может быть закрыта.
            if card.status == "Закрыто":
                card.status = "На списание"

    # Статус пересчитываем по факту (остаток к выписке), но только в
    # зонах списания: при переносе «Сборка → В работе/Ждет оплаты» фронт
    # меняет статус до удаления записи, и безусловный пересчёт возвращал
    # карточку обратно в «Сборку» поверх только что выставленного статуса.
    if card_id is not None:
        card = _tenant_card(session, card_id, actor)
        if card:
            left = session.query(models.Transaction).filter(
                models.Transaction.card_id == card_id,
                models.Transaction.is_document == False,
            ).all()
            if card.status in ("На списание", "Закрыто"):
                card.status = writeoff_status_for(card, left)
            # Сделка в «Сборке» без записи реестра пропадала бы из
            # «Реестра оплат» до первой ручной «пинки» (В Сборку туда-обратно).
            if card.status == "Сборка":
                ensure_registry_remainder(session, card)

    # Журнал: помечаем, что накладная отменена. Прошлая запись
    # «Выписана накладная» остаётся, но в карточке будет зачёркнута —
    # видно, что информация уже неактуальна.
    if card_id is not None and was_invoice:
        session.add(models.ActivityLog(
            user_id=actor.id, card_id=card_id,
            tenant_id=actor.tenant_id,
            action="Накладная отменена",
            details=(f"{number} на {amount:.2f} BYN" if number else f"запись на {amount:.2f} BYN")
                    + " — удалена, сумма возвращена в остаток",
        ))

    session.commit()
    return {"detail": "Запись удалена", "twins_deleted": len(twins), "card_id": card_id}


def annul_group_writeoff(session, group, actor):
    """Отмена групповой накладной — зеркало delete_transaction для группы.

    P0-хотфикс 23.09 (часть B спеки docs/audits/P0-HOTFIX-GROUP-ANNUL-SPEC,
    дефекты 1 и 2 реестра V2-WORKPLAN-2026-09-22 «Дефекты доски, найденные
    пунктом 6»). Отдельная функция нужна потому, что документ группы — запись
    реестра с card_id=NULL, и одиночная отмена на ней обрывается: сумма не
    возвращается (card_id нет), written_off с группы не снимается, карточки
    остаются в «Закрыто», а UI рапортует «остатки возвращены».

    Разматывается ровно то, что сделал issue_group_invoice:
      1. запись-документ группы (is_document=True, writeoff_group_id=group.id)
         удаляется. Пары «накладная + копия в Документах» здесь нет:
         find_invoice_twins для card_id=NULL возвращает пустой список, а копия
         группового документа в штатных сценариях не создаётся;
      2. group.written_off / invoice_number / invoice_date снимаются;
      3. с записей карточек группы снимаются флаги, которые групповая выписка
         подняла скопом на ВСЕХ не-документных строках карточки
         (is_warehouse_writeoff / is_invoice_issued);
      4. остаток карточки приводится к инварианту «сумма сделки − выписанное»
         и к одной записи-остатку на карточку (uq_remainder_per_card,
         миграция 0004) — тот же порядок, что в repair-writeoffs;
      5. статус карточки пересчитывается writeoff_status_for: карточка выходит
         из «Закрыто» в «На списание» (или в «Сборку», если записей не осталось).

    Чего восстановить нельзя: групповая выписка поднимала флаги на всех строках
    карточки и прежнее состояние не сохраняла, поэтому ручная отметка «списано
    со склада» на одиночной накладной карточки из группы снимается вместе с
    групповой. На статус и остатки это не влияет (строка с номером накладной
    считается выписанной независимо от флажка), а след остаётся в журнале.
    Ручную галочку «Списание» (is_written_off) не трогаем — как и выписка
    (фидбек 18.09).

    FIX 2026-09-26: (а) тело обёрнуто в rollback_on_error — раньше сбой на
    промежуточном шаге (например, нарушение uq_remainder_per_card) оставлял
    сессию в pending-rollback и ломал весь запрос каскадом; (б) записи всех
    карточек группы читаются ОДНИМ запросом с IN(...) вместо запроса на
    карточку; (в) порог «остаток есть / остаток исчерпан» — округление до
    копеек (money_is_zero), а не MONEY_EPSILON: копейка не должна закрывать
    сделку и удаляться.
    """
    with rollback_on_error(session):
        return _annul_group_writeoff(session, group, actor)


def _annul_group_writeoff(session, group, actor):
    actor_id = actor.id if actor is not None else None
    actor_tenant_id = actor.tenant_id if actor is not None else None
    number = (group.invoice_number or "").strip()
    doc_amount = 0.0

    # 1. Документ группы. Берём все записи-документы группы: штатно она одна,
    # но повторная выписка поверх старой (или ручная копия) оставляла несколько.
    docs = session.query(models.Transaction).filter(
        models.Transaction.writeoff_group_id == group.id,
        models.Transaction.is_document == True,  # noqa: E712
    ).order_by(models.Transaction.id.asc()).all()
    doc_id = docs[0].id if docs else None
    for d in docs:
        doc_amount = round(doc_amount + float(d.amount or 0), 2)
        session.delete(d)
    session.flush()

    # 2. Состояние группы.
    group.written_off = False
    group.invoice_number = None
    group.invoice_date = None

    # FIX 2026-09-26 (N+1): записи карточек группы читаем одним запросом.
    # Раньше на каждую карточку уходил свой SELECT — на группе из 30 сделок
    # это 30 запросов плюс по одному на остальные шаги цикла.
    group_cards = list(group.cards)
    card_ids = [c.id for c in group_cards]
    rows_by_card = {cid: [] for cid in card_ids}
    if card_ids:
        for chunk in _in_chunks(card_ids):
            ledger_rows = session.query(models.Transaction).filter(
                models.Transaction.card_id.in_(chunk),
                models.Transaction.is_document == False,  # noqa: E712
            ).order_by(models.Transaction.id.asc()).all()
            for row in ledger_rows:
                rows_by_card.setdefault(row.card_id, []).append(row)

    cards_report = []
    for card in group_cards:
        rows = rows_by_card.get(card.id, [])

        # «Выписано» после отмены групповой ТН — только настоящие накладные
        # карточки (строки с номером): групповая выписка своих строк по
        # карточкам не создавала, она лишь поднимала флаги на существующих.
        issued = round(sum(float(r.amount or 0) for r in rows
                           if (r.invoice_number or "").strip()), 2)
        card_total = float(card.total_amount or 0)
        # Без суммы сделки эталона остатка нет (как в repair-writeoffs) —
        # суммы записей не пересчитываем.
        rest = max(0.0, round(card_total - issued, 2)) if card_total > 0 else None

        # 4. Сначала удаления, потом снятие флагов: uq_remainder_per_card
        # уникален по «не-документ, без номера, не списан», и UPDATE раньше
        # DELETE на мгновение дал бы две записи-остатка на карточку.
        remainders = [r for r in rows if not (r.invoice_number or "").strip()]
        dropped = []
        if rest is not None and money_is_zero(rest):
            # остаток покрыт накладными — нулевые записи не оставляем
            dropped = list(remainders)
        elif len(remainders) > 1:
            keep = remainders[0]
            if rest is None:
                keep.amount = round(sum(float(r.amount or 0) for r in remainders), 2)
            dropped = remainders[1:]
        for r in dropped:
            session.delete(r)
        if dropped:
            session.flush()

        # 3. Снимаем флаги групповой выписки. Строки с номером накладной —
        # одиночные ТН карточки: у них остаётся «Выписка» (это факт), а
        # складское списание снимается вместе с групповым.
        kept = [r for r in rows if r not in dropped]
        for r in kept:
            if (r.invoice_number or "").strip():
                r.is_warehouse_writeoff = False
                r.is_invoice_issued = True
            else:
                r.is_warehouse_writeoff = False
                r.is_invoice_issued = False
                if rest is not None:
                    r.amount = rest

        # Остаток некуда вернуть (записей по карточке не было вовсе), но
        # непокрытая сумма есть — запись-остаток создаётся, как это делает
        # delete_transaction: сделка должна быть видна в «Реестре оплат».
        created = None
        if rest is not None and money_is_present(rest) and not [
                r for r in kept if not (r.invoice_number or "").strip()]:
            created = models.Transaction(
                company_name=card.title, amount=rest,
                store_location=card.store_location or group.store_location,
                card_id=card.id,
                tenant_id=card.tenant_id,
            )
            session.add(created)
            session.flush()
            kept = kept + [created]

        # 5. Статус — по факту остатка, но только в зонах списания (зеркало
        # delete_transaction: перенос «Сборка → В работе» не перетираем).
        if card.status in ("На списание", "Закрыто"):
            card.status = writeoff_status_for(card, kept)

        session.add(models.ActivityLog(
            user_id=actor_id, card_id=card.id, tenant_id=actor_tenant_id,
            action="Групповая накладная отменена",
            details=(f"{number} на {doc_amount:.2f} BYN (группа #{group.id}) — удалена; "
                     f"статус «{card.status}»"
                     + (f", остаток {rest:.2f} BYN" if rest is not None else "")),
        ))
        cards_report.append({"id": card.id, "status": card.status,
                             "rest": rest, "issued": issued})

    session.commit()
    return {
        "detail": "Групповая накладная отменена",
        "group_id": group.id,
        "invoice_transaction_id": doc_id,
        "invoice_number": number,
        "amount": doc_amount,
        "documents_deleted": len(docs),
        "cards": cards_report,
    }


def _reserve_card_write(session, card_id, actor):
    """Захватывает единственный писатель SQLite до чтения остатка.

    FastAPI уже прочитал пользователя этой же сессией, поэтому общий сервис
    резервирования завершает read-транзакцию до UPDATE. Значения актора
    копируются заранее, потому что rollback делает ORM-объект expired.

    FIX 2026-09-26 (tenant): резервирование идёт по ГЛОБАЛЬНОМУ id карточки,
    поэтому до него обязателен tenant-lookup — иначе no-op UPDATE на чужой
    строке. Порядок: сначала 404 для чужой/отсутствующей сделки, потом
    резервирование, потом повторное чтение карточки (reserve_model_rows
    делает session.rollback(), которым ORM-объект инвалидируется).
    """
    actor_id = actor.id if actor is not None else None
    actor_tenant_id = actor.tenant_id if actor is not None else None
    _tenant_card(session, card_id, actor)
    reserve_model_rows(
        session,
        models.Card,
        [card_id],
        not_found_detail="Карточка не найдена",
        busy_detail="Сделка занята другой операцией. Повторите попытку.",
    )
    return actor_id, actor_tenant_id


def add_invoice(session, card_id, payload, actor=None):
    """Добавляет ЕЩЁ ОДНУ накладную к сделке (частичная отгрузка).

    Отличие от trigger_from_card: тот идемпотентен и нужен для перевода
    сделки в реестр, а этот всегда создаёт новую запись.

    Вынесено из add_invoice в routers/payments_router.py (Фаза 4) —
    тело перенесено посимвольно.

    FIX 2026-09-26: карточка и записи реестра берутся в tenant вызывающего
    (раньше — по глобальному id, поэтому накладную можно было выписать по
    ЧУЖОЙ сделке); ошибка на промежуточном шаге откатывает сессию.
    """
    with rollback_on_error(session):
        return _add_invoice(session, card_id, payload, actor)


def _add_invoice(session, card_id, payload, actor=None):
    _reserve_card_write(session, card_id, actor)
    card = _tenant_card(session, card_id, actor)
    if card.writeoff_group_id is not None:
        raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Добавляйте накладную по группе.")

    existing = tenant_query(session, models.Transaction, actor).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False
    ).all()

    # FIX 2026-09-12 (Фаза 2, дефект 6): та же защита от дубля номера, что
    # и в issue_invoice. Без неё дубль обходился бы через соседнюю дверь —
    # этот эндпоинт тоже создаёт запись реестра с номером накладной,
    # а issued_total в обоих местах суммирует все записи с номером.
    # Пустой номер пропускаем: сюда можно добавить и запись без номера.
    if (payload.invoice_number or "").strip():
        duplicate = find_duplicate_invoice(existing, payload.invoice_number)
        if duplicate is not None:
            raise HTTPException(
                status_code=400,
                detail=(f"Накладная {payload.invoice_number.strip()} уже есть по этой "
                        f"сделке (запись #{duplicate.id}). Второй раз тот же номер "
                        f"добавить нельзя."),
            )

    if payload.amount is not None:
        # сумму задал пользователь в форме — проверяем её против остатка
        # (фикс аудита 10.09): раньше можно было выписать накладную
        # больше непокрытого остатка, остаток уходил в минус и молча
        # удалялся, сделка закрывалась. Паритет с issue_invoice ниже.
        amount = round(float(payload.amount), 2)
        issued_before = round(sum(float(t.amount or 0) for t in existing
                                  if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
        rest_before = round(float(card.total_amount or 0) - issued_before, 2)
        # FIX 2026-09-30 (аудит денег): MONEY_EPSILON стоял здесь ради хвостов
        # float, но обе величины уже округлены до копеек — хвоста на них не бывает.
        # Вредил он так: при rest_before = 0.00 проверка 0.01 > 0.00 + 0.01 даёт
        # False, накладная на копейку проходит, остаток уходит в минус и молча
        # удаляется — сделка оказывается выставлена на копейку больше, и нигде
        # это не отражено.
        if amount > rest_before:
            raise HTTPException(
                status_code=400,
                detail=f"Сумма накладной ({amount}) больше остатка по сделке ({rest_before})",
            )
    else:
        # иначе — непокрытый остаток по сделке: сумма сделки минус
        # уже выписанное (как в форме выписки и на доске)
        issued = round(sum(float(t.amount or 0) for t in existing
                           if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
        amount = round(float(card.total_amount or 0) - issued, 2)

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма накладной должна быть больше нуля: остаток по сделке уже покрыт")

    store = payload.store_location or card.store_location
    if not store and existing:
        store = existing[0].store_location

    new_tx = models.Transaction(
        company_name=card.title,
        amount=amount,
        store_location=store,
        invoice_number=(payload.invoice_number or None),
        invoice_date=(payload.invoice_date or None),
        card_id=card_id,
        tenant_id=card.tenant_id,
    )
    session.add(new_tx)
    session.flush()
    # Инварианты как во всей системе: запись-остаток = сумма сделки −
    # выписанное, статус — по остатку. Без этого дописанная извне
    # накладная оставляла остаток разъехавшимся, а сделку — в старом
    # статусе.
    ledger = tenant_query(session, models.Transaction, actor).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    issued_total = round(sum(float(t.amount or 0) for t in ledger
                             if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
    new_rest = round(float(card.total_amount or 0) - issued_total, 2)
    remainder = next((t for t in ledger
                      if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()), None)
    if remainder is not None:
        # Копейка — это остаток, а не «почти ноль»: `new_rest <= MONEY_EPSILON`
        # закрывал сделку на одну копейку и УДАЛЯЛ запись-остаток, то есть
        # деньги исчезали из реестра без возврата (аудит 2026-09-26).
        if money_is_zero(new_rest):
            session.delete(remainder)
        else:
            remainder.amount = new_rest
    elif money_is_present(new_rest):
        session.add(models.Transaction(
            company_name=card.title, amount=new_rest,
            store_location=new_tx.store_location, card_id=card_id,
            tenant_id=card.tenant_id,
        ))
    if card.status in ("На списание", "Закрыто"):
        card.status = writeoff_status_for(card, ledger)
    session.commit()
    session.refresh(new_tx)
    return tx_dict(new_tx)


def issue_invoice(session, card_id, payload, actor):
    """Выписать накладную по сделке — модель чек-листа.

    Логика (по согласованному сценарию):
      1. По сделке всегда существует максимум ОДНА «пустая» запись-остаток,
         которая висит в колонке «На списание».
      2. Выписка ставит только «Выписку» (is_invoice_issued); сделка с
         накладной остаётся в «На списание» до явного «Списать» с доски
         (фидбек 2026-09-04: «Списание» не должно ставиться вместе
         с выпиской). Копия накладной при этом уходит в «Документы».
      3. Если накладная закрыла остаток не полностью — остаток уменьшается
         на её сумму и остаётся ждать следующую накладную.
      4. Остаток закрыт полностью — запись-остаток исчезает, сделка закрывается.

    Вынесено из issue_invoice в routers/payments_router.py (Фаза 4) —
    тело перенесено посимвольно.

    FIX 2026-09-26: tenant-фильтр на карточку и записи реестра, откат
    сессии при любой ошибке и денежный порог «остаток исчерпан» по
    округлению до копеек вместо MONEY_EPSILON (см. services.writeoffs).
    """
    with rollback_on_error(session):
        return _issue_invoice(session, card_id, payload, actor)


def _issue_invoice(session, card_id, payload, actor):
    actor_id, actor_tenant_id = _reserve_card_write(session, card_id, actor)
    # Ленивый импорт: _issue_document остался в routers.payments_router
    # (явно вне среза Фазы 4), а прямой импорт верхнего уровня дал бы
    # цикл router → services → router.
    from routers.payments_router import _issue_document

    card = _tenant_card(session, card_id, actor)
    if card.writeoff_group_id is not None:
        raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Выписывайте накладную по группе.")

    amount = round(float(payload.amount or 0), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма накладной должна быть больше нуля")

    number = (payload.invoice_number or "").strip()
    if not number:
        raise HTTPException(status_code=400, detail="Укажите номер накладной")

    txs = tenant_query(session, models.Transaction, actor).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).order_by(models.Transaction.id.asc()).all()

    # FIX 2026-09-12 (Фаза 2, дефект 6): один и тот же номер на ОДНОЙ сделке
    # выписывался сколько угодно раз — в реестре появлялась вторая строка
    # с тем же номером, и остаток к выписке считался дважды (issued_total
    # ниже суммирует все записи с номером). Сделка при этом закрывалась
    # раньше времени или, наоборот, показывала несуществующий остаток.
    #
    # Область проверки — записи ЭТОЙ карточки, потому что штатные сценарии
    # «2 карточки — 1 накладная» (доли одной физической накладной) и
    # «1 карточка — 2 накладных» (частичные отгрузки) ломать нельзя:
    # они подтверждены данными боевой БД и закреплены инвариантами в
    # tests/test_nakladnye_dedup.py.
    #
    # Сравнение по цифрам: в данных соседствуют «ТТН 4881030» и «ТТН4881030».
    # Именно поэтому защита делается в коде, а не уникальным индексом по
    # (card_id, invoice_number): правило «только цифры» средствами SQLite
    # в индекс не выражается, а индекс по сырой строке пропустил бы ровно
    # тот дубль, который встречается в жизни.
    duplicate = find_duplicate_invoice(txs, number)
    if duplicate is not None:
        raise HTTPException(
            status_code=400,
            detail=(f"Накладная {number} уже выписана по этой сделке "
                    f"(запись #{duplicate.id} на {float(duplicate.amount or 0):.2f} BYN). "
                    f"Чтобы исправить её, удалите существующую и выпишите заново."),
        )

    # запись-остаток: не списана и без номера накладной
    remainder = next(
        (t for t in txs if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()),
        None
    )
    store = payload.store_location or (remainder.store_location if remainder else None) or card.store_location
    if not store:
        raise HTTPException(status_code=400, detail="Не выбран магазин для списания")

    # FIX 2026-09-26 (накопление float): сумму округляем на границе расчёта,
    # дальше все сравнения идут по копейкам (round идёт до max(0.0, ...)).
    issued_total = round(sum(float(t.amount or 0) for t in txs
                             if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
    card_amount = round(float(card.total_amount or 0), 2)
    # Остаток считаем ОТ СУММЫ СДЕЛКИ, а не от записи-остатка: запись —
    # производное состояние и расходилась с сделкой (сумму правили уже
    # после создания записи; в двух сделках в записях остались опечатки
    # ×100 — и модалка предлагала выписать миллионы). Остаток на доске
    # списания считается так же, теперь и модалка показывает то же число.
    rest_before = max(0.0, round(card_amount - issued_total, 2))

    # FIX 2026-09-30 (аудит денег): см. замечание выше по add_invoice. Здесь хвост
    # float тем более невозможен — rest_before уже прошёл max(0.0, round(..., 2)),
    # а amount округлён на входе. С допуском проверка при остатке 0.00 пропускала
    # накладную на 0.01 и закрывала сделку с переизбытком.
    if amount > rest_before:
        raise HTTPException(
            status_code=400,
            detail=f"Сумма накладной {amount:.2f} больше остатка по сделке {rest_before:.2f}"
        )

    rest_after = round(rest_before - amount, 2)

    # Копейка остатка — это деньги, а не «почти закрыто»: при
    # rest_after = 0.01 накладная не должна ПЕРЕПИСЫВАТЬ запись-остаток
    # (иначе 0.01 исчезают), остаток должен остаться отдельной записью
    # (аудит 2026-09-26).
    if remainder is not None and money_is_zero(rest_after):
        # накладная закрывает остаток целиком — превращаем саму запись-остаток в накладную
        invoice = remainder
        invoice.invoice_number = number
        invoice.invoice_date = payload.invoice_date or None
        invoice.amount = amount
        invoice.store_location = store
        # Фидбек 2026-09-04: выписка накладной ставит ТОЛЬКО «Выписку».
        # «Списание» — отдельное действие с доски списания (кнопка
        # «Списать» → is_warehouse_writeoff/is_written_off).
        invoice.is_invoice_issued = True
        new_remainder = None
    else:
        # частичная отгрузка: накладная — отдельной записью, остаток уменьшается
        invoice = models.Transaction(
            company_name=card.title, amount=amount, store_location=store,
            invoice_number=number, invoice_date=(payload.invoice_date or None),
            is_invoice_issued=True,
            card_id=card_id,
            tenant_id=card.tenant_id,
        )
        session.add(invoice)
        if remainder is not None:
            if money_is_zero(rest_after):
                # остаток исчерпан — удаляем пустую запись вместо нулевой
                session.delete(remainder)
                new_remainder = None
            else:
                remainder.amount = rest_after
                remainder.store_location = store
                new_remainder = remainder
        else:
            if money_is_present(rest_after):
                new_remainder = models.Transaction(
                    company_name=card.title, amount=rest_after,
                    store_location=store, card_id=card_id,
                    tenant_id=card.tenant_id,
                )
                session.add(new_remainder)
            else:
                new_remainder = None  # do not create a zero-amount remainder

    session.flush()
    _issue_document(session, invoice)

    card_closed = False
    if new_remainder is None:
        card.status = "Закрыто"
        card_closed = True
    else:
        # Если остаток остался — сделка должна быть видна на доске списания.
        # Раньше карточка могла зависнуть в «Сборке» и пропасть со списания.
        card.status = "На списание"

    session.add(models.ActivityLog(
        user_id=actor_id, card_id=card_id,
        tenant_id=actor_tenant_id, action="Выписана накладная",
        details=f"{number} на {amount:.2f} BYN ({store}). Остаток: {max(rest_after, 0):.2f}",
    ))
    session.commit()
    session.refresh(invoice)

    # Фикс аудита 10.09: суммы в сообщении для пользователя в русском
    # формате («2 500,75»), а не «2500.75» из :.2f.
    def _fmt_byn(v):
        return f"{v:,.2f}".replace(",", " ").replace(".", ",")

    return {
        "success": True,
        "invoice_id": invoice.id,
        "invoice_number": number,
        "amount": amount,
        "rest": max(rest_after, 0.0),
        "card_amount": card_amount,
        "card_closed": card_closed,
        "message": (f"Накладная {number} на {_fmt_byn(amount)} BYN списана. "
                    + ("Сделка закрыта." if card_closed else f"Остаток {_fmt_byn(max(rest_after, 0.0))} BYN.")),
    }
