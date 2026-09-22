"""Оркестрация money-эндпоинтов реестра оплат и сериализация транзакций.

Вынесено из routers/payments_router.py (Фаза 4): бизнес-логика эндпоинтов
add_invoice / issue_invoice / delete_transaction и сериализатор tx_dict.
Роутеры остались HTTP-обвязкой. HTTPException из сервиса пробрасывается
FastAPI как есть — коды ответов те же, что были в хендлерах.
"""

from fastapi import HTTPException

import models
from constants import MONEY_EPSILON
from services.invoices import find_duplicate_invoice, find_invoice_twins
from services.writeoffs import ensure_registry_remainder, writeoff_status_for


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
    routers/payments_router.py (Фаза 4) — тело перенесено посимвольно."""
    query = session.query(models.Transaction).filter(models.Transaction.id == transaction_id)
    tx = query.first()
    if not tx:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    if tx.card_id and not tx.is_document:
        card = session.query(models.Card).filter(models.Card.id == tx.card_id).first()
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
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if card:
            left = session.query(models.Transaction).filter(
                models.Transaction.card_id == card_id,
                models.Transaction.is_document == False,
            ).order_by(models.Transaction.id.asc()).all()

            rest_rows = [t for t in left
                         if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()]
            if rest_rows:
                rest_rows[0].amount = float(rest_rows[0].amount or 0) + amount
            else:
                session.add(models.Transaction(
                    company_name=card.title, amount=amount,
                    store_location=store, card_id=card_id,
                ))
            # По сделке снова есть что списывать -> она не может быть закрыта.
            if card.status == "Закрыто":
                card.status = "На списание"

    # Статус пересчитываем по факту (остаток к выписке), но только в
    # зонах списания: при переносе «Сборка → В работе/Ждет оплаты» фронт
    # меняет статус до удаления записи, и безусловный пересчёт возвращал
    # карточку обратно в «Сборку» поверх только что выставленного статуса.
    if card_id is not None:
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
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


def add_invoice(session, card_id, payload, actor=None):
    """Добавляет ЕЩЁ ОДНУ накладную к сделке (частичная отгрузка).

    Отличие от trigger_from_card: тот идемпотентен и нужен для перевода
    сделки в реестр, а этот всегда создаёт новую запись.

    Вынесено из add_invoice в routers/payments_router.py (Фаза 4) —
    тело перенесено посимвольно.
    """
    card = session.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.writeoff_group_id is not None:
        raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Добавляйте накладную по группе.")

    existing = session.query(models.Transaction).filter(
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
        if amount > rest_before + MONEY_EPSILON:
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
    )
    session.add(new_tx)
    session.flush()
    # Инварианты как во всей системе: запись-остаток = сумма сделки −
    # выписанное, статус — по остатку. Без этого дописанная извне
    # накладная оставляла остаток разъехавшимся, а сделку — в старом
    # статусе.
    ledger = session.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,
    ).all()
    issued_total = round(sum(float(t.amount or 0) for t in ledger
                             if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
    new_rest = round(float(card.total_amount or 0) - issued_total, 2)
    remainder = next((t for t in ledger
                      if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()), None)
    if remainder is not None:
        if new_rest <= MONEY_EPSILON:
            session.delete(remainder)
        else:
            remainder.amount = new_rest
    elif new_rest > MONEY_EPSILON:
        session.add(models.Transaction(
            company_name=card.title, amount=new_rest,
            store_location=new_tx.store_location, card_id=card_id,
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
    """
    # Ленивый импорт: _issue_document остался в routers.payments_router
    # (явно вне среза Фазы 4), а прямой импорт верхнего уровня дал бы
    # цикл router → services → router.
    from routers.payments_router import _issue_document

    card = session.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if card.writeoff_group_id is not None:
        raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Выписывайте накладную по группе.")

    amount = round(float(payload.amount or 0), 2)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма накладной должна быть больше нуля")

    number = (payload.invoice_number or "").strip()
    if not number:
        raise HTTPException(status_code=400, detail="Укажите номер накладной")

    txs = session.query(models.Transaction).filter(
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

    issued_total = sum(float(t.amount or 0) for t in txs
                       if t.is_warehouse_writeoff or (t.invoice_number or "").strip())
    card_amount = round(float(card.total_amount or 0), 2)
    # Остаток считаем ОТ СУММЫ СДЕЛКИ, а не от записи-остатка: запись —
    # производное состояние и расходилась с сделкой (сумму правили уже
    # после создания записи; в двух сделках в записях остались опечатки
    # ×100 — и модалка предлагала выписать миллионы). Остаток на доске
    # списания считается так же, теперь и модалка показывает то же число.
    rest_before = max(0.0, round(card_amount - issued_total, 2))

    if amount > rest_before + MONEY_EPSILON:
        raise HTTPException(
            status_code=400,
            detail=f"Сумма накладной {amount:.2f} больше остатка по сделке {rest_before:.2f}"
        )

    rest_after = round(rest_before - amount, 2)

    if remainder is not None and rest_after <= MONEY_EPSILON:
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
        )
        session.add(invoice)
        if remainder is not None:
            if rest_after <= MONEY_EPSILON:
                # остаток исчерпан — удаляем пустую запись вместо нулевой
                session.delete(remainder)
                new_remainder = None
            else:
                remainder.amount = rest_after
                remainder.store_location = store
                new_remainder = remainder
        else:
            if rest_after > MONEY_EPSILON:
                new_remainder = models.Transaction(
                    company_name=card.title, amount=rest_after,
                    store_location=store, card_id=card_id,
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
        user_id=actor.id, card_id=card_id,
        tenant_id=actor.tenant_id, action="Выписана накладная",
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
