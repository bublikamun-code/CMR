from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload
from typing import List
from pydantic import BaseModel
import models
import schemas
from database import get_db, get_tenant_db
from auth import get_current_user
from db_utils import resolve_tenant_db as _db

# FIX 2026-08-30: предохранитель от неограниченной выборки (реестр и документы
# раньше читали ВСЮ таблицу транзакций). Это не пагинация: сейчас в базе 188
# транзакций, лимит в ~25 раз больше и в обычной работе недостижим; он нужен
# на случай аномального роста (зациклившийся импорт и т.п.). При срабатывании
# ответ помечается заголовками X-Total-Count / X-Truncated, чтобы фронтенд
# мог показать предупреждение, а не молча терять строки. ВАЖНО: реестр
# группирует транзакции по сделкам, поэтому обрезка может расщепить последнюю
# группу — при срабатывании лимита это сигнал чинить причину роста, а не
# поднимать константу.
REGISTRY_HARD_LIMIT = 5000

router = APIRouter(
    prefix="/payments",
    tags=["Реестр оплат (Страница 2)"],
    dependencies=[Depends(get_current_user)]
)

class PaymentTriggerRequest(BaseModel):
    store_location: str


class InvoiceCreateRequest(BaseModel):
    store_location: str | None = None
    amount: float | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None

@router.post("/trigger_from_card/{card_id}", response_model=schemas.TransactionResponse)
def trigger_payment(card_id: int, payload: PaymentTriggerRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Card).filter(models.Card.id == card_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Card).filter(models.Card.id == card_id)
        card = query.first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if card.writeoff_group_id is not None:
            raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Управляйте ей через группу.")
        # ИДЕМПОТЕНТНО: кнопки «В Сборку» / «В Списание» могут нажиматься многократно,
        # и каждое нажатие раньше плодило дубль. Если запись уже есть — возвращаем её.
        # Для осознанного добавления ВТОРОЙ накладной есть отдельный эндпоинт
        # POST /payments/cards/{card_id}/invoices.
        existing = session.query(models.Transaction).filter(
            models.Transaction.card_id == card.id,
            models.Transaction.is_document == False
        ).first()
        if existing:
            return _tx_dict(existing)
        new_tx = models.Transaction(company_name=card.title, amount=card.total_amount, store_location=payload.store_location, card_id=card.id)
        session.add(new_tx)
        session.commit()
        session.refresh(new_tx)
        # Версионирование
        from versioning import save_version as _save_version
        _save_version(session, "transactions", new_tx.id,
            {"card_id": new_tx.card_id, "amount": float(new_tx.amount or 0), "store_location": new_tx.store_location},
            user_id=current_user.id, change_type="create", tenant_id=current_user.tenant_id)
        # Webhook уведомление
        try:
            from routers.webhooks_router import notify_webhooks_async
            notify_webhooks_async(current_user.tenant_id, "payment.created",
                {"id": new_tx.id, "card_id": new_tx.card_id, "amount": float(new_tx.amount or 0)})
        except Exception: pass
        # Уведомление владельцу сделки о новой оплате (тип card_updated:
        # card_payment зарезервирован для просрочек из sync-overdue)
        if card.owner_id and card.owner_id != current_user.id:
            from notify import notify
            notify(db, [card.owner_id], actor_id=current_user.id,
                   type="card_updated", title=f"Новая оплата по сделке: {card.title}",
                   details=f"{float(new_tx.amount or 0):,.2f} BYN",
                   entity_type="card", entity_id=card.id)
        return _tx_dict(new_tx)
    finally:
        tdb.close()

@router.get("/transactions", response_model=List[schemas.TransactionResponse])
def get_transactions(grouped: bool = True, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user),
                     response: Response = None):
    """Реестр оплат.

    grouped=True (по умолчанию) — ОДНА строка на сделку с общей суммой.
    Выписка накладных дробит сделку на несколько записей списания
    (накладная + остаток), и раньше каждая из них лезла отдельной
    строкой в реестр, разбивая сумму счёта. Реестр оперирует счетами,
    а не отгрузками, поэтому части схлопываются обратно в одну строку.

    grouped=False — сырые записи (нужны доске списания и карточке).
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Transaction).filter(models.Transaction.is_document == False)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Transaction).filter(models.Transaction.is_document == False)

        rows = query.options(selectinload(models.Transaction.card)).order_by(
            models.Transaction.card_id.desc(), models.Transaction.id.asc()
        ).limit(REGISTRY_HARD_LIMIT).all()
        if response is not None:
            response.headers["X-Total-Count"] = str(len(rows))
            if len(rows) >= REGISTRY_HARD_LIMIT:
                response.headers["X-Truncated"] = "true"

        if not grouped:
            # UI FIX 2026-08-31: dict вместо ORM — сессия закроется в finally
            return [_tx_dict(r) for r in rows]

        # ВАЖНО: чек-лист карточки — это ЗАКУПКА У ПОСТАВЩИКОВ
        # (кому и сколько мы должны заплатить за товар для этого заказа).
        # К оплате заказа клиентом он отношения не имеет, поэтому реестр
        # оплат его больше не читает. Сумма строки — сумма счёта клиента.

        result, seen = [], {}
        for r in rows:
            if r.card_id is None:
                # старые записи без привязки — как есть
                result.append(_tx_dict(r, parts=1, invoices=0, paid=None, partial=False))
                continue

            if r.card_id not in seen:
                seen[r.card_id] = {"base": r, "parts": [r]}
            else:
                seen[r.card_id]["parts"].append(r)

        for card_id, grp in seen.items():
            parts = grp["parts"]
            base = grp["base"]
            total = round(sum(float(p.amount or 0) for p in parts), 2)
            # сумма счёта берётся у карточки, если она заполнена
            card_total = float(base.card.total_amount or 0) if base.card else 0.0
            if card_total > 0.01:
                total = round(card_total, 2)

            invoices = [p for p in parts if (p.invoice_number or "").strip()]

            card_paid = float(base.card.paid_amount or 0) if base.card else 0.0
            card_payment_status = base.card.payment_status if base.card else None
            d = _tx_dict(base, parts=len(parts), invoices=len(invoices),
                         paid=None, partial=False,
                         paid_amount=card_paid, payment_status=card_payment_status)
            d["amount"] = total
            # флаги — по всем частям сразу
            d["is_calculated"] = all(bool(p.is_calculated) for p in parts)
            d["is_invoice_issued"] = any(bool(p.is_invoice_issued) or (p.invoice_number or "").strip() for p in parts)
            d["is_written_off"] = all(bool(p.is_written_off) for p in parts)
            d["part_ids"] = [p.id for p in parts]
            if len(invoices) == 1:
                d["invoice_number"] = invoices[0].invoice_number
                d["invoice_date"] = invoices[0].invoice_date
            elif len(invoices) > 1:
                d["invoice_number"] = invoices[0].invoice_number
                d["invoice_date"] = invoices[0].invoice_date
            result.append(d)

        # Сортировка по дате: в базе даты и с таймзоной, и без — прямое
        # сравнение datetime уронило бы эндпоинт, поэтому сортируем по строке.
        result.sort(key=lambda x: (x["date"].isoformat() if x.get("date") else ""), reverse=True)
        return result
    finally:
        tdb.close()


def _tx_dict(t, parts=1, invoices=0, paid=None, partial=False, paid_amount=None, payment_status=None):
    # UI FIX 2026-08-31: все ответные ветки этого роутера возвращают dict, а не
    # ORM-объект: tenant-сессия закрывается в finally до сериализации ответа,
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
        "print_status": t.print_status, "note": t.note,
        "invoice_number": t.invoice_number, "invoice_date": t.invoice_date,
        "is_document": bool(t.is_document),
        "is_invoice_doc": bool(t.is_invoice_doc),
        "is_bill_doc": bool(t.is_bill_doc),
        "is_warehouse_writeoff": bool(t.is_warehouse_writeoff),
        "parts_count": parts, "invoices_count": invoices,
        "part_ids": None,
        "paid_amount": paid_amount,
        "payment_status": payment_status,
        "is_partial_payment": partial,
    }

@router.get("/documents", response_model=List[schemas.TransactionResponse])
def get_documents(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user),
                  response: Response = None):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Transaction).filter(models.Transaction.is_document == True)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Transaction).filter(models.Transaction.is_document == True)
        rows = query.options(selectinload(models.Transaction.card)).order_by(
            models.Transaction.date.desc()
        ).limit(REGISTRY_HARD_LIMIT).all()
        if response is not None:
            response.headers["X-Total-Count"] = str(len(rows))
            if len(rows) >= REGISTRY_HARD_LIMIT:
                response.headers["X-Truncated"] = "true"
        # UI FIX 2026-08-31: dict вместо ORM — сессия закроется в finally
        return [_tx_dict(r) for r in rows]
    finally:
        tdb.close()

@router.post("/transactions/{transaction_id}/duplicate_as_document", response_model=schemas.TransactionResponse)
def duplicate_as_document(transaction_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        original = query.first()
        if not original:
            raise HTTPException(status_code=404, detail="Транзакция не найдена")
        # Документ ищем по КОНКРЕТНОЙ накладной, а не по карточке: у одной сделки
        # может быть несколько накладных, и каждая должна попасть в «Документы» отдельно.
        if original.card_id is not None and original.invoice_number:
            existing_doc = session.query(models.Transaction).filter(
                models.Transaction.is_document == True,
                models.Transaction.card_id == original.card_id,
                models.Transaction.invoice_number == original.invoice_number
            ).first()
            if existing_doc:
                return _tx_dict(existing_doc)
        duplicate = models.Transaction(
            company_name=original.company_name, amount=original.amount, store_location=original.store_location,
            invoice_number=original.invoice_number, invoice_date=original.invoice_date,
            is_calculated=original.is_calculated, is_invoice_issued=original.is_invoice_issued,
            is_written_off=True, print_status=original.print_status, note=original.note,
            is_document=True, card_id=original.card_id,
        )
        session.add(duplicate)
        session.commit()
        session.refresh(duplicate)
        return _tx_dict(duplicate)
    finally:
        tdb.close()

@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Transaction).filter(models.Transaction.id == transaction_id)
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
        was_doc = bool(tx.is_document)
        was_invoice = bool(tx.is_warehouse_writeoff or number)

        # Накладная и её копия в «Документах» — ОДНА сущность.
        # Удаление с любой стороны должно снести обе, иначе разъезжается:
        # из «Документов» удаляли копию, а накладная оставалась в карточке.
        def _norm(v):
            """Номера накладных пишут по-разному: «ТТН4881006» и «4881006» —
            это одна накладная. Сравниваем только цифры."""
            return "".join(ch for ch in (v or "") if ch.isdigit())

        twins = []
        if card_id is not None:
            candidates = session.query(models.Transaction).filter(
                models.Transaction.card_id == card_id,
                models.Transaction.id != tx.id,
                models.Transaction.is_document == (not was_doc),
            ).all()

            key = _norm(number)
            if key:
                twins = [c for c in candidates if _norm(c.invoice_number) == key]
            # запасной путь: номер не совпал (или его нет) — ищем по сумме
            if not twins:
                twins = [c for c in candidates
                         if abs(float(c.amount or 0) - amount) < 0.01
                         and (not key or not _norm(c.invoice_number))]
            # у сделки ровно одна накладная — пара однозначна
            if not twins and len(candidates) == 1:
                twins = candidates

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

        # Статус ВСЕГДА пересчитываем по факту, даже если остатка не осталось.
        # Раньше при удалении последней записи карточка оставалась в «Закрыто»:
        # она исчезала из «Списания» и из «Сборки», но висела в «Списке».
        if card_id is not None:
            card = session.query(models.Card).filter(models.Card.id == card_id).first()
            if card:
                left = session.query(models.Transaction).filter(
                    models.Transaction.card_id == card_id,
                    models.Transaction.is_document == False,
                ).all()
                if not left:
                    card.status = "Сборка"          # списывать нечего
                elif any(not t.is_warehouse_writeoff for t in left):
                    card.status = "На списание"
                else:
                    card.status = "Закрыто"

        # Журнал: помечаем, что накладная отменена. Прошлая запись
        # «Выписана накладная» остаётся, но в карточке будет зачёркнута —
        # видно, что информация уже неактуальна.
        if card_id is not None and was_invoice:
            session.add(models.ActivityLog(
                user_id=current_user.id, card_id=card_id,
                action="Накладная отменена",
                details=(f"{number} на {amount:.2f} BYN" if number else f"запись на {amount:.2f} BYN")
                        + " — удалена, сумма возвращена в остаток",
            ))

        session.commit()
        return {"detail": "Запись удалена", "twins_deleted": len(twins), "card_id": card_id}
    finally:
        tdb.close()

@router.patch("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction_checkboxes(transaction_id: int, updates: schemas.TransactionUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tdb = _db(current_user, db)
    try:
        session = tdb
        query = tdb.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
            query = db.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        tx = query.first()
        if not tx:
            raise HTTPException(status_code=404, detail="Транзакция не найдена")
        update_data = updates.model_dump(exclude_unset=True) if hasattr(updates, 'model_dump') else updates.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(tx, key, value)
        if "print_status" in update_data and tx.card_id is not None:
            for twin in session.query(models.Transaction).filter(models.Transaction.card_id == tx.card_id, models.Transaction.id != tx.id).all():
                twin.print_status = update_data["print_status"]
        session.commit()
        session.refresh(tx)
        return _tx_dict(tx)
    finally:
        tdb.close()


class WriteoffStatus(BaseModel):
    card_id: int
    total_invoices: int
    written_off: int
    pending: int
    invoices_amount: float
    card_amount: float
    fully_covered: bool


@router.get("/cards/{card_id}/writeoff-status", response_model=WriteoffStatus)
def card_writeoff_status(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Сводка по накладным одной сделки: сколько выписано, сколько списано, покрыта ли сумма."""
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        txs = session.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False
        ).all()
        written = sum(1 for t in txs if t.is_warehouse_writeoff)
        inv_sum = sum(float(t.amount or 0) for t in txs)
        card_sum = float(card.total_amount or 0)
        return WriteoffStatus(
            card_id=card_id,
            total_invoices=len(txs),
            written_off=written,
            pending=len(txs) - written,
            invoices_amount=round(inv_sum, 2),
            card_amount=round(card_sum, 2),
            fully_covered=(card_sum > 0 and inv_sum >= card_sum - 0.01),
        )
    finally:
        tdb.close()


@router.post("/cards/{card_id}/invoices", response_model=schemas.TransactionResponse)
def add_invoice(card_id: int, payload: InvoiceCreateRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Добавляет ЕЩЁ ОДНУ накладную к сделке (частичная отгрузка).

    Отличие от trigger_from_card: тот идемпотентен и нужен для перевода
    сделки в реестр, а этот всегда создаёт новую запись.
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        if card.writeoff_group_id is not None:
            raise HTTPException(status_code=400, detail="Карточка объединена в групповое списание. Добавляйте накладную по группе.")

        existing = session.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False
        ).all()

        if payload.amount is not None:
            # сумму задал пользователь в форме
            amount = round(float(payload.amount), 2)
        else:
            # иначе — непокрытый остаток по сделке
            covered = sum(float(t.amount or 0) for t in existing)
            rest = round(float(card.total_amount or 0) - covered, 2)
            amount = rest if rest > 0 else 0.0

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
        session.commit()
        session.refresh(new_tx)
        return _tx_dict(new_tx)
    finally:
        tdb.close()


class IssueInvoiceRequest(BaseModel):
    invoice_number: str
    invoice_date: str | None = None
    amount: float
    store_location: str | None = None


def _issue_document(session, tx):
    """Копия накладной в раздел «Документы» (одна на каждый номер накладной)."""
    existing = None
    if tx.card_id is not None and tx.invoice_number:
        existing = session.query(models.Transaction).filter(
            models.Transaction.is_document == True,
            models.Transaction.card_id == tx.card_id,
            models.Transaction.invoice_number == tx.invoice_number,
        ).first()
    if existing:
        return existing
    doc = models.Transaction(
        company_name=tx.company_name, amount=tx.amount, store_location=tx.store_location,
        invoice_number=tx.invoice_number, invoice_date=tx.invoice_date,
        is_calculated=tx.is_calculated, is_invoice_issued=True, is_written_off=True,
        print_status=tx.print_status, note=tx.note, is_document=True, card_id=tx.card_id,
    )
    session.add(doc)
    return doc


@router.post("/cards/{card_id}/issue-invoice")
def issue_invoice(card_id: int, payload: IssueInvoiceRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Выписать накладную по сделке — модель чек-листа.

    Логика (по согласованному сценарию):
      1. По сделке всегда существует максимум ОДНА «пустая» запись-остаток,
         которая висит в колонке «На списание».
      2. Выписанная накладная сразу уходит в «Списано» и в «Документы».
      3. Если накладная закрыла остаток не полностью — остаток уменьшается
         на её сумму и остаётся ждать следующую накладную.
      4. Остаток закрыт полностью — запись-остаток исчезает, сделка закрывается.
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

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
        rest_before = round(float(remainder.amount or 0), 2) if remainder else round(card_amount - issued_total, 2)

        if amount > rest_before + 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"Сумма накладной {amount:.2f} больше остатка по сделке {rest_before:.2f}"
            )

        rest_after = round(rest_before - amount, 2)

        if remainder is not None and rest_after <= 0.01:
            # накладная закрывает остаток целиком — превращаем саму запись-остаток в накладную
            invoice = remainder
            invoice.invoice_number = number
            invoice.invoice_date = payload.invoice_date or None
            invoice.amount = amount
            invoice.store_location = store
            invoice.is_warehouse_writeoff = True
            invoice.is_written_off = True
            invoice.is_invoice_issued = True
            new_remainder = None
        else:
            # частичная отгрузка: накладная — отдельной записью, остаток уменьшается
            invoice = models.Transaction(
                company_name=card.title, amount=amount, store_location=store,
                invoice_number=number, invoice_date=(payload.invoice_date or None),
                is_warehouse_writeoff=True, is_written_off=True, is_invoice_issued=True,
                card_id=card_id,
            )
            session.add(invoice)
            if remainder is not None:
                if rest_after <= 0.01:
                    # остаток исчерпан — удаляем пустую запись вместо нулевой
                    session.delete(remainder)
                    new_remainder = None
                else:
                    remainder.amount = rest_after
                    remainder.store_location = store
                    new_remainder = remainder
            else:
                if rest_after > 0.01:
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
            user_id=current_user.id, card_id=card_id, action="Выписана накладная",
            details=f"{number} на {amount:.2f} BYN ({store}). Остаток: {max(rest_after, 0):.2f}",
        ))
        session.commit()
        session.refresh(invoice)

        return {
            "success": True,
            "invoice_id": invoice.id,
            "invoice_number": number,
            "amount": amount,
            "rest": max(rest_after, 0.0),
            "card_amount": card_amount,
            "card_closed": card_closed,
            "message": (f"Накладная {number} на {amount:.2f} BYN списана. "
                        + ("Сделка закрыта." if card_closed else f"Остаток {rest_after:.2f} BYN.")),
        }
    finally:
        tdb.close()


@router.get("/cards/{card_id}/invoices")
def list_card_invoices(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Чек-лист накладных по сделке: выписанные пункты + текущий остаток."""
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db
        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        txs = session.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False,
        ).order_by(models.Transaction.id.asc()).all()

        issued, rest = [], 0.0
        for t in txs:
            if t.is_warehouse_writeoff or (t.invoice_number or "").strip():
                issued.append({
                    "id": t.id,
                    "invoice_number": t.invoice_number or "",
                    "invoice_date": t.invoice_date or "",
                    "amount": round(float(t.amount or 0), 2),
                    "store_location": t.store_location or "",
                    "written_off": bool(t.is_warehouse_writeoff),
                })
            else:
                rest += float(t.amount or 0)

        card_amount = round(float(card.total_amount or 0), 2)
        issued_sum = round(sum(i["amount"] for i in issued), 2)
        if not txs:
            rest = card_amount
        return {
            "card_id": card_id,
            "card_amount": card_amount,
            "issued": issued,
            "issued_amount": issued_sum,
            "rest": round(rest, 2),
            "store_location": card.store_location or (txs[0].store_location if txs else None),
            "closed": card.status == "Закрыто",
        }
    finally:
        tdb.close()


@router.delete("/cards/{card_id}/writeoff")
def remove_card_from_writeoff(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Полностью убрать сделку из складского списания.

    Сносим ВСЁ, что с ней связано: запись-остаток, все выписанные накладные
    и все их копии в разделе «Документы». Карточка возвращается в «Сборку».
    Раньше удалялась одна запись, поэтому плитка оставалась в «На списание»,
    а документы продолжали висеть.
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

        rows = session.query(models.Transaction).filter(
            models.Transaction.card_id == card_id
        ).all()

        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card and not rows:
            raise HTTPException(status_code=404, detail="Сделка не найдена")
        if card and card.writeoff_group_id is not None:
            raise HTTPException(status_code=400, detail="Сначала выведите карточку из группы")

        # 404 больше не бросаем: если записей нет, значит их уже удалили
        # (двойной клик, параллельная вкладка). Карточку всё равно нужно
        # вернуть в «Сборку» — иначе она застревала в «Закрыто» и пропадала
        # со всех досок, оставаясь только в «Списке».
        docs = sum(1 for r in rows if r.is_document)
        for r in rows:
            session.delete(r)
        session.flush()

        if card:
            card.status = "Сборка"
            card.is_deleted = False
            session.add(models.ActivityLog(
                user_id=current_user.id, card_id=card_id,
                action="Накладные отменены",
                details=f"Удалено записей: {len(rows)} (из них документов: {docs}). Возврат в «Сборку».",
            ))

        session.commit()
        return {"detail": "Сделка убрана из списания", "deleted": len(rows), "documents_deleted": docs}
    finally:
        tdb.close()


@router.post("/cards/{card_id}/sync-writeoff-status")
def sync_writeoff_status(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Привести статус сделки в соответствие с её накладными.

    Фронт раньше при любом удалении молча переводил карточку в «Сборку»,
    даже если по ней оставались выписанные накладные — карточка исчезала
    с доски списания, а внутри накладные оставались. Теперь статус
    вычисляется по данным, а не назначается вслепую.
    """
    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

        card = session.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        txs = session.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False,
        ).all()

        if not txs:
            status = "Сборка"          # списывать нечего
        else:
            pending = [t for t in txs if not t.is_warehouse_writeoff]
            status = "Закрыто" if not pending else "На списание"

        changed = card.status != status
        if changed:
            card.status = status
            session.commit()
        return {"card_id": card_id, "status": status, "changed": changed}
    finally:
        tdb.close()


@router.post("/repair-writeoffs")
def repair_writeoffs(dry_run: bool = True, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Разовая починка расхождений, накопленных старой логикой удаления.

    1. Документы-сироты — копии накладных, у которых больше нет оригинала
       в списании (сделку удалили с доски, документ остался).
    2. Сделки со списанием, у которых статус не «На списание»/«Закрыто» —
       они не видны ни в одной колонке, хотя записи по ним есть.
    """
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    tdb = _db(current_user, db)
    try:
        session = tdb
        if current_user.role == "superadmin" and current_user.tenant_id is None:
            session = db

        def _norm(v):
            return "".join(ch for ch in (v or "") if ch.isdigit())

        all_tx = session.query(models.Transaction).filter(
            models.Transaction.card_id.isnot(None)
        ).all()

        by_card = {}
        for t in all_tx:
            by_card.setdefault(t.card_id, []).append(t)

        orphan_docs, fixed_status = [], []

        for card_id, rows in by_card.items():
            live = [t for t in rows if not t.is_document]
            docs = [t for t in rows if t.is_document]

            live_keys = {_norm(t.invoice_number) for t in live}
            for d in docs:
                if not live:
                    orphan_docs.append({"id": d.id, "card_id": card_id,
                                        "invoice_number": d.invoice_number,
                                        "amount": float(d.amount or 0)})
                    continue
                if _norm(d.invoice_number) and _norm(d.invoice_number) not in live_keys:
                    if not any(abs(float(t.amount or 0) - float(d.amount or 0)) < 0.01 for t in live):
                        orphan_docs.append({"id": d.id, "card_id": card_id,
                                            "invoice_number": d.invoice_number,
                                            "amount": float(d.amount or 0)})

            card = session.query(models.Card).filter(models.Card.id == card_id).first()
            if card and live:
                pending = [t for t in live if not t.is_warehouse_writeoff]
                want = "Закрыто" if not pending else "На списание"
                if card.status != want and card.status not in ("Отменено",):
                    fixed_status.append({"card_id": card_id, "title": card.title,
                                         "from": card.status, "to": want})
                    if not dry_run:
                        card.status = want

        if not dry_run:
            for o in orphan_docs:
                row = session.query(models.Transaction).filter(models.Transaction.id == o["id"]).first()
                if row:
                    session.delete(row)
            session.commit()

        return {
            "dry_run": dry_run,
            "orphan_documents": orphan_docs,
            "orphan_documents_count": len(orphan_docs),
            "status_fixes": fixed_status,
            "status_fixes_count": len(fixed_status),
        }
    finally:
        tdb.close()
