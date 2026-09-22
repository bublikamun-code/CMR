import contextlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

import models
import schemas
from auth import get_current_user, require_role
from constants import MONEY_EPSILON
from database import get_db
from services.invoices import find_duplicate_invoice, find_invoice_twins, norm_invoice_number
from services.payments import add_invoice as add_invoice_svc
from services.payments import delete_transaction as delete_transaction_svc
from services.payments import issue_invoice as issue_invoice_svc
from services.payments import tx_dict
from services.writeoffs import ensure_registry_remainder, writeoff_status_for


def _backup_db_snapshot(session):
    """Копия базы перед применением починки — страховка отката.

    Пишется рядом с базой (CRM_DATA_DIR/backups/before_repair_*.db) штатным
    backup-API SQLite, поэтому безопасна при живом приложении (WAL).
    Ошибка копии не отменяет починку — в отчёте будет backup: null.
    """
    try:
        rows = session.connection().exec_driver_sql("PRAGMA database_list").fetchall()
        db_path = next((row[2] for row in rows if row[1] == "main" and row[2]), None)
        if not db_path:
            return None
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        # UTC в имени бэкапа: таймзона общего хостинга нам не подконтрольна,
        # а имена обязаны быть монотонными — иначе перевод часов или смена TZ
        # сервера дали бы файл, который сортируется раньше уже существующих.
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        target = os.path.join(backup_dir, f"before_repair_{stamp}.db")
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    except Exception:
        return None
    else:
        return target

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
    try:
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
        existing = db.query(models.Transaction).filter(
            models.Transaction.card_id == card.id,
            models.Transaction.is_document == False
        ).first()
        if existing:
            return tx_dict(existing)
        new_tx = models.Transaction(company_name=card.title, amount=card.total_amount, store_location=payload.store_location, card_id=card.id)
        db.add(new_tx)
        try:
            db.commit()
        except IntegrityError:
            # Н9 (аудит 06.09): гонка двух конкурентных вызовов. Частичный
            # unique-индекс uq_remainder_per_card (миграция 0004) не даёт
            # создать второй остаток — проигравшая гонка возвращает запись
            # победителя вместо 500.
            db.rollback()
            winner = db.query(models.Transaction).filter(
                models.Transaction.card_id == card.id,
                models.Transaction.is_document == False
            ).first()
            if winner:
                return tx_dict(winner)
            raise
        db.refresh(new_tx)
        # Версионирование
        from versioning import save_version as _save_version
        _save_version(db, "transactions", new_tx.id,
            {"card_id": new_tx.card_id, "amount": float(new_tx.amount or 0), "store_location": new_tx.store_location},
            user_id=current_user.id, change_type="create", tenant_id=current_user.tenant_id)
        # Webhook уведомление
        with contextlib.suppress(Exception):
            from routers.webhooks_router import notify_webhooks_async
            notify_webhooks_async(current_user.tenant_id, "payment.created",
                {"id": new_tx.id, "card_id": new_tx.card_id, "amount": float(new_tx.amount or 0)})
        # Уведомление владельцу сделки о новой оплате (тип card_updated:
        # card_payment зарезервирован для просрочек из sync-overdue)
        if card.owner_id and card.owner_id != current_user.id:
            from notify import notify
            notify(db, [card.owner_id], actor_id=current_user.id,
                   type="card_updated", title=f"Новая оплата по сделке: {card.title}",
                   details=f"{float(new_tx.amount or 0):,.2f} BYN",
                   entity_type="card", entity_id=card.id)
        return tx_dict(new_tx)
    finally:
        db.close()


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
    try:
        # Фидбек 19.09: карточки из корзины (is_deleted) не должны показываться
        # в реестре — иначе удалённая карточка «оставалась» в реестре и не
        # открывалась. При восстановлении карточки записи возвращаются.
        query = db.query(models.Transaction).outerjoin(
            models.Card, models.Transaction.card_id == models.Card.id
        ).filter(
            or_(
                models.Card.is_deleted == False,  # noqa: E712
                models.Transaction.card_id.is_(None),
            )
        ).filter(models.Transaction.is_document == False)
        rows = query.options(selectinload(models.Transaction.card)).order_by(
            models.Transaction.card_id.desc(), models.Transaction.id.asc()
        ).limit(REGISTRY_HARD_LIMIT).all()
        if response is not None:
            response.headers["X-Total-Count"] = str(len(rows))
            if len(rows) >= REGISTRY_HARD_LIMIT:
                response.headers["X-Truncated"] = "true"

        if not grouped:
            # UI FIX 2026-08-31: dict вместо ORM — сессия закроется в finally
            return [tx_dict(r) for r in rows]

        # ВАЖНО: чек-лист карточки — это ЗАКУПКА У ПОСТАВЩИКОВ
        # (кому и сколько мы должны заплатить за товар для этого заказа).
        # К оплате заказа клиентом он отношения не имеет, поэтому реестр
        # оплат его больше не читает. Сумма строки — сумма счёта клиента.

        result, seen = [], {}
        for r in rows:
            if r.card_id is None:
                # старые записи без привязки — как есть
                result.append(tx_dict(r, parts=1, invoices=0, paid=None, partial=False))
                continue

            if r.card_id not in seen:
                seen[r.card_id] = {"base": r, "parts": [r]}
            else:
                seen[r.card_id]["parts"].append(r)

        for grp in seen.values():
            parts = grp["parts"]
            base = grp["base"]
            total = round(sum(float(p.amount or 0) for p in parts), 2)
            # сумма счёта берётся у карточки, если она заполнена
            card_total = float(base.card.total_amount or 0) if base.card else 0.0
            if card_total > MONEY_EPSILON:
                total = round(card_total, 2)

            invoices = [p for p in parts if (p.invoice_number or "").strip()]

            card_paid = float(base.card.paid_amount or 0) if base.card else 0.0
            card_payment_status = base.card.payment_status if base.card else None
            d = tx_dict(base, parts=len(parts), invoices=len(invoices),
                         paid=None, partial=False,
                         paid_amount=card_paid, payment_status=card_payment_status)
            d["amount"] = total
            # флаги — по всем частям сразу
            d["is_calculated"] = all(bool(p.is_calculated) for p in parts)
            d["is_invoice_issued"] = any(bool(p.is_invoice_issued) or (p.invoice_number or "").strip() for p in parts)
            d["is_written_off"] = all(bool(p.is_written_off) for p in parts)
            d["part_ids"] = [p.id for p in parts]
            # Сколько денег по счёту уже покрыто накладными/складскими
            # списаниями. Критерий тот же, что в чек-листе накладных и на
            # доске списания: строка реестра с галочкой «Выписка» на
            # частично отгруженном счёте читалась как «выписана вся сумма»
            # (фидбек 18.09, «Рацио Домус»: накладная на 38 824,52 при
            # счёте 57 046,38).
            issued_parts = [p for p in parts
                            if bool(p.is_warehouse_writeoff) or (p.invoice_number or "").strip()]
            d["invoiced_amount"] = round(sum(float(p.amount or 0) for p in issued_parts), 2)
            # обе прежние ветки (len == 1 и len > 1) делали одно и то же —
            # условие сводится к «есть хотя бы один счёт-фактура»
            if invoices:
                d["invoice_number"] = invoices[0].invoice_number
                d["invoice_date"] = invoices[0].invoice_date
            result.append(d)

        # Сортировка по дате: в базе даты и с таймзоной, и без — прямое
        # сравнение datetime уронило бы эндпоинт, поэтому сортируем по строке.
        result.sort(key=lambda x: (x["date"].isoformat() if x.get("date") else ""), reverse=True)
        return result
    finally:
        db.close()


@router.get("/documents", response_model=List[schemas.TransactionResponse])
def get_documents(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user),
                  response: Response = None):
    try:
        query = db.query(models.Transaction).filter(models.Transaction.is_document == True)
        rows = query.options(selectinload(models.Transaction.card)).order_by(
            models.Transaction.date.desc()
        ).limit(REGISTRY_HARD_LIMIT).all()
        if response is not None:
            response.headers["X-Total-Count"] = str(len(rows))
            if len(rows) >= REGISTRY_HARD_LIMIT:
                response.headers["X-Truncated"] = "true"
        # UI FIX 2026-08-31: dict вместо ORM — сессия закроется в finally
        return [tx_dict(r) for r in rows]
    finally:
        db.close()


@router.post("/transactions/{transaction_id}/duplicate_as_document", response_model=schemas.TransactionResponse)
def duplicate_as_document(transaction_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        query = db.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        original = query.first()
        if not original:
            raise HTTPException(status_code=404, detail="Транзакция не найдена")
        # Документ ищем по КОНКРЕТНОЙ накладной, а не по карточке: у одной сделки
        # может быть несколько накладных, и каждая должна попасть в «Документы» отдельно.
        if original.card_id is not None and original.invoice_number:
            existing_doc = db.query(models.Transaction).filter(
                models.Transaction.is_document == True,
                models.Transaction.card_id == original.card_id,
                models.Transaction.invoice_number == original.invoice_number
            ).first()
            if existing_doc:
                return tx_dict(existing_doc)
        duplicate = models.Transaction(
            company_name=original.company_name, amount=original.amount, store_location=original.store_location,
            invoice_number=original.invoice_number, invoice_date=original.invoice_date,
            is_calculated=original.is_calculated, is_invoice_issued=original.is_invoice_issued,
            is_written_off=True, print_status=original.print_status, note=original.note,
            is_document=True, card_id=original.card_id,
        )
        db.add(duplicate)
        db.commit()
        db.refresh(duplicate)
        return tx_dict(duplicate)
    finally:
        db.close()


# V11 (пункт 17 плана v2): удаление записи реестра — это отмена накладной/оплаты.
# Операция необратима и тянет за собой пересчёт остатка, статуса сделки и пары
# «накладная + копия в Документах» (services/payments.delete_transaction),
# поэтому она админская. До гейта любую запись реестра удалял каждый
# аутентифицированный пользователь, включая роли warehouse и documents.
# Отмена групповой ТН идёт этим же роутом: документ группы — обычная запись
# реестра с is_document=True и writeoff_group_id.
@router.delete("/transactions/{transaction_id}",
               dependencies=[Depends(require_role("admin", "superadmin"))])
def delete_transaction(transaction_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        return delete_transaction_svc(db, transaction_id, current_user)
    finally:
        db.close()


@router.patch("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction_checkboxes(transaction_id: int, updates: schemas.TransactionUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        query = db.query(models.Transaction).filter(models.Transaction.id == transaction_id)
        tx = query.first()
        if not tx:
            raise HTTPException(status_code=404, detail="Транзакция не найдена")
        update_data = updates.model_dump(exclude_unset=True) if hasattr(updates, 'model_dump') else updates.dict(exclude_unset=True)
        # V7 (пункт 17 плана v2): is_invoice_doc / is_bill_doc — флаги
        # «оригинал ТН у нас» и «оригинал счёта у нас». Каноническое место
        # хранения — запись-документ (is_document=True): её читают «Документы»
        # legacy (site/js/documents.js:185), «ТН у нас» в финансах v2
        # (shell-v2-fin.js:198) и «Оригинал ТН возвращён» в карточке v2
        # (shell-v2-board.js:918). Это ОДИН бизнес-факт под тремя подписями,
        # отдельной колонки не будет — решение и разбор в V2-PLAN-RECON §6.
        # На строке реестра оплат (оплата/остаток) флаг не читает ни один
        # экран, поэтому запись туда — данные, потерянные молча. Отклоняем
        # сразу: 400 с внятной причиной вместо зелёной галочки в никуда.
        doc_only_fields = {"is_invoice_doc", "is_bill_doc"} & update_data.keys()
        if doc_only_fields and not tx.is_document:
            raise HTTPException(
                status_code=400,
                detail=("Флаги «ТН у нас» и «Счёт у нас» ставятся только на записи "
                        "раздела «Документы», а запись "
                        f"#{transaction_id} — строка реестра оплат"))
        # Пара «накладная + копия в Документах» ищется по СТАРЫМ значениям
        # (как при удалении) — поэтому до применения правок.
        invoice_fields = {"invoice_date", "invoice_number"} & update_data.keys()
        invoice_twins = find_invoice_twins(db, tx) if invoice_fields else []
        # Дата оплаты: присланную дату соединяем со временем исходной записи —
        # меняется только день, порядок записей внутри дня не скачет.
        # Копии в «Документах» дата не касается: там своя, документная.
        #
        # FIX 2026-09-12 (Фаза 2, дефект 13): поле date вернулось в
        # TransactionUpdate, и этот разбор стал достижим. Поэтому он защищён:
        # js/payments.js шлёт "YYYY-MM-DD", но прямой запрос к API может
        # прислать полный ISO — раньше strptime упал бы в 500.
        # Таймзона исходной записи сохраняется: модели пишут aware UTC, а
        # datetime.combine даёт naive, и смешение форматов в одной колонке
        # ломает сортировку строковым сравнением (это же чинила миграция 0003).
        if update_data.get("date"):
            raw = str(update_data["date"]).strip()
            new_day = None
            # DTZ007 подавлен осознанно: наивный результат strptime здесь
            # промежуточный — ниже он получает tzinfo исходной записи (см. FIX
            # выше). Делать его aware сразу означало бы приписать чужой UTC
            # записям, которые миграция 0003 оставила наивными.
            for parser in (lambda s: datetime.strptime(s, "%Y-%m-%d"),  # noqa: DTZ007
                           datetime.fromisoformat):
                try:
                    new_day = parser(raw)
                    break
                except ValueError:
                    continue
            if new_day is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Непонятный формат даты: {raw!r}. Ожидается ГГГГ-ММ-ДД")
            old_ts = tx.date or datetime.now(timezone.utc)
            merged = datetime.combine(new_day.date(), old_ts.time())
            update_data["date"] = (merged.replace(tzinfo=old_ts.tzinfo)
                                   if old_ts.tzinfo else merged)
        for key, value in update_data.items():
            setattr(tx, key, value)
        # Дата/номер — часть пары «накладная + копия»: правим с любой
        # стороны, копия подтягивается, иначе в разделах разъезжается.
        for twin in invoice_twins:
            if "invoice_date" in invoice_fields:
                twin.invoice_date = tx.invoice_date
            if "invoice_number" in invoice_fields:
                twin.invoice_number = tx.invoice_number
        if "print_status" in update_data and tx.card_id is not None:
            for twin in db.query(models.Transaction).filter(models.Transaction.card_id == tx.card_id, models.Transaction.id != tx.id).all():
                twin.print_status = update_data["print_status"]
        # V4 (аудит прода 19.09): правка номера накладной — тот же замок, что
        # в issue_invoice/add_invoice: второй раз один и тот же номер на одной
        # сделке поставить нельзя, иначе реестр расходится с напечатанными ТН.
        # Область поиска — записи ЭТОЙ ЖЕ сделки (доли одной физической
        # накладной и частичные отгрузки дублями не являются — см. комментарий
        # к find_duplicate_invoice в services/invoices.py и инварианты в
        # tests/test_nakladnye_dedup.py). Документы не проверяем: их номер
        # повторяет оригинал по дизайну, пара синхронизирована выше.
        if (not tx.is_document and tx.card_id is not None
                and "invoice_number" in update_data
                and (tx.invoice_number or "").strip()):
            others = db.query(models.Transaction).filter(
                models.Transaction.card_id == tx.card_id,
                models.Transaction.id != tx.id,
                models.Transaction.is_document == False,
            ).all()
            duplicate = find_duplicate_invoice(others, tx.invoice_number)
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Накладная {tx.invoice_number.strip()} уже есть по этой сделке "
                            f"(запись #{duplicate.id}). Второй раз тот же номер поставить нельзя."),
                )
        # V4 (аудит прода 19.09): правка суммы ТН раньше не пересчитывала
        # запись-остаток и статус карточки — реестр расходился с напечатанными
        # накладными. Формула и ветки — как в add_invoice (services/payments.py,
        # блок «Инварианты как во всей системе») и в PATCH /cards/{id}
        # (card_details_router.update_card): запись-остаток = сумма сделки −
        # выписанное; вторую копию формулы плодить нельзя, ветка создания
        # остатка — только из «Сборки» и дальше, как в PATCH /cards/{id}.
        # Документы и записи без карточки правятся как раньше, без пересчёта.
        if "amount" in update_data and not tx.is_document and tx.card_id is not None:
            db.flush()  # autoflush=False: SELECT ниже обязан увидеть правку суммы
            card = db.query(models.Card).filter(models.Card.id == tx.card_id).first()
            ledger = db.query(models.Transaction).filter(
                models.Transaction.card_id == tx.card_id,
                models.Transaction.is_document == False,
            ).all()
            # Валидация против остатка — только для записей-накладных
            # (складское списание или номер): сумму остатка сам остаток
            # не ограничивает, он производный.
            invoice_like = bool(tx.is_warehouse_writeoff or (tx.invoice_number or "").strip())
            if invoice_like and card is not None:
                others_issued = round(sum(float(t.amount or 0) for t in ledger
                                          if t.id != tx.id
                                          and (t.is_warehouse_writeoff or (t.invoice_number or "").strip())), 2)
                available = round(float(card.total_amount or 0) - others_issued, 2)
                if float(tx.amount or 0) > available + MONEY_EPSILON:
                    raise HTTPException(status_code=400,
                        detail=f"Сумма накладной ({round(float(tx.amount or 0), 2)}) больше остатка по сделке ({available})")
            if card is not None:
                issued_total = round(sum(float(t.amount or 0) for t in ledger
                                         if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
                new_rest = round(float(card.total_amount or 0) - issued_total, 2)
                remainder = next((t for t in ledger
                                  if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()), None)
                if remainder is not None:
                    if new_rest <= MONEY_EPSILON:
                        db.delete(remainder)
                    else:
                        remainder.amount = new_rest
                elif new_rest > MONEY_EPSILON and card.status in ("Сборка", "На списание", "Закрыто"):
                    db.add(models.Transaction(
                        company_name=card.title, amount=new_rest,
                        store_location=card.store_location, card_id=card.id,
                    ))
                if card.status in ("На списание", "Закрыто"):
                    card.status = writeoff_status_for(card, ledger)
        db.commit()
        db.refresh(tx)
        return tx_dict(tx)
    finally:
        db.close()


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
    try:
        card = db.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        txs = db.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False
        ).all()
        written = sum(1 for t in txs if t.is_warehouse_writeoff)
        # Фикс аудита 10.09: в счёт выписанного идут только накладные
        # (номер или складская запись). Запись-остаток — это ещё НЕ
        # выписанная часть сделки; раньше она попадала в inv_sum, и
        # fully_covered был почти всегда True, а total_invoices считал
        # остаток как накладную.
        issued_rows = [t for t in txs if t.is_warehouse_writeoff or (t.invoice_number or "").strip()]
        inv_sum = sum(float(t.amount or 0) for t in issued_rows)
        card_sum = float(card.total_amount or 0)
        return WriteoffStatus(
            card_id=card_id,
            total_invoices=len(issued_rows),
            written_off=written,
            pending=len(issued_rows) - written,
            invoices_amount=round(inv_sum, 2),
            card_amount=round(card_sum, 2),
            fully_covered=(card_sum > 0 and inv_sum >= card_sum - MONEY_EPSILON),
        )
    finally:
        db.close()


@router.post("/cards/{card_id}/invoices", response_model=schemas.TransactionResponse)
def add_invoice(card_id: int, payload: InvoiceCreateRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        return add_invoice_svc(db, card_id, payload, current_user)
    finally:
        db.close()


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
    try:
        return issue_invoice_svc(db, card_id, payload, current_user)
    finally:
        db.close()


@router.get("/cards/{card_id}/invoices")
def list_card_invoices(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Чек-лист накладных по сделке: выписанные пункты + текущий остаток."""
    try:
        card = db.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        txs = db.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False,
        ).order_by(models.Transaction.id.asc()).all()

        issued = [
            {
                "id": t.id,
                "invoice_number": t.invoice_number or "",
                "invoice_date": t.invoice_date or "",
                "amount": round(float(t.amount or 0), 2),
                "store_location": t.store_location or "",
                "written_off": bool(t.is_warehouse_writeoff),
            }
            for t in txs
            if t.is_warehouse_writeoff or (t.invoice_number or "").strip()
        ]

        card_amount = round(float(card.total_amount or 0), 2)
        issued_sum = round(sum(i["amount"] for i in issued), 2)
        # Остаток — от суммы сделки, как на доске списания и в выписке
        # накладной: сумма «хвостатых» записей со временем расходилась со
        # сделкой, и форма выписки подставляла неверную (иногда сумашедшую)
        # сумму. Если записей нет вообще — остаток равен всей сумме сделки.
        rest = max(0.0, round(card_amount - issued_sum, 2)) if txs else card_amount
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
        db.close()


@router.delete("/cards/{card_id}/writeoff",
               dependencies=[Depends(require_role("admin", "superadmin"))])
def remove_card_from_writeoff(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Полностью убрать сделку из складского списания.

    Сносим ВСЁ, что с ней связано: запись-остаток, все выписанные накладные
    и все их копии в разделе «Документы». Карточка возвращается в «Сборку».
    Раньше удалялась одна запись, поэтому плитка оставалась в «На списание»,
    а документы продолжали висеть.

    V11 (пункт 17 плана v2): массовое удаление записей реестра — операция
    админская, как и DELETE /transactions/{id}. Ручка осталась только в legacy
    («Списание», site/js/writeoffs.js:401 и site/js/payments.js:297) — v2 её
    не зовёт; если владелец решит вернуть складу право отмены списания,
    гейт снимается одной строкой (см. V2-PLAN-RECON §6).
    """
    try:
        rows = db.query(models.Transaction).filter(
            models.Transaction.card_id == card_id
        ).all()

        card = db.query(models.Card).filter(models.Card.id == card_id).first()
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
            db.delete(r)
        db.flush()

        if card:
            # Фикс аудита 10.09: карточку, которую владелец отправил в корзину,
            # эта ручка раньше насильно возвращала на доску («Сборка» + снятие
            # is_deleted). В корзине — значит в корзине: записи чистим, статус
            # и реестр не трогаем.
            restored = not card.is_deleted
            if restored:
                card.status = "Сборка"
                # Запись-остаток создаём заново: «Сборка» = сделка в реестре
                # оплат, а раньше после удаления записей сделка исчезала и
                # из реестра, пока её не «пинали» вручную (В Сборку туда-обратно).
                ensure_registry_remainder(db, card)
            db.add(models.ActivityLog(
                user_id=current_user.id, card_id=card_id,
                tenant_id=current_user.tenant_id,
                action="Накладные отменены",
                details=(
                    f"Удалено записей: {len(rows)} (из них документов: {docs})."
                    + (" Возврат в «Сборку»." if restored else " Карточка осталась в корзине.")
                ),
            ))

        db.commit()
        return {"detail": "Сделка убрана из списания", "deleted": len(rows), "documents_deleted": docs}
    finally:
        db.close()


@router.post("/cards/{card_id}/sync-writeoff-status")
def sync_writeoff_status(card_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Привести статус сделки в соответствие с её накладными.

    Фронт раньше при любом удалении молча переводил карточку в «Сборку»,
    даже если по ней оставались выписанные накладные — карточка исчезала
    с доски списания, а внутри накладные оставались. Теперь статус
    вычисляется по данным, а не назначается вслепую.
    """
    try:
        card = db.query(models.Card).filter(models.Card.id == card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")

        txs = db.query(models.Transaction).filter(
            models.Transaction.card_id == card_id,
            models.Transaction.is_document == False
        ).all()

        status = writeoff_status_for(card, txs)

        changed = card.status != status
        if changed:
            card.status = status
            db.commit()
        return {"card_id": card_id, "status": status, "changed": changed}
    finally:
        db.close()


@router.post("/repair-writeoffs")
def repair_writeoffs(dry_run: bool = True, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Разовая починка расхождений, накопленных старой логикой удаления.

    1. Документы-сироты — копии накладных, у которых больше нет оригинала
       в списании (сделку удалили с доски, документ остался).
    2. Статусы сделок со списанием — по факту выписки: накладными покрыта
       вся сумма сделки → «Закрыто» (складские флажки статус не держат),
       остался остаток к выписке → «На списание». «Сборка» не перекидывается:
       сделка с записью реестра в «Сборке» — штатное состояние.
    3. Группы списания без участников — плитки-призраки с устаревшей
       суммой на доске списания.
    4. Записи-остатки, разошедшиеся с суммой сделки (сумму правили уже
       после создания записи, встречаются и старые опечатки ×100):
       остаток приводится к «сумма сделки − выписанные накладные»,
       лишние записи-остатки удаляются.
    5. Сделки в «Сборке» без записи реестра оплат — создавалась заново
       на полную сумму сделки.
    6. Нулевые записи-остатки (мусор в реестре) — удаляются.

    Перед применением (dry_run=false) сохраняется копия базы в
    backups/before_repair_*.db рядом с файлом базы — страховка отката.
    """
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    try:
        # Страховка отката: перед применением сохраняем копию базы
        backup_path = None if dry_run else _backup_db_snapshot(db)

        def _norm(v):
            return norm_invoice_number(v)

        all_tx = db.query(models.Transaction).filter(
            models.Transaction.card_id.isnot(None)
        ).all()

        by_card = {}
        for t in all_tx:
            by_card.setdefault(t.card_id, []).append(t)

        orphan_docs, fixed_status = [], []
        remainder_fixes, orphan_groups = [], []
        zero_rows_removed = []

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
                if (_norm(d.invoice_number) and _norm(d.invoice_number) not in live_keys
                        and not any(abs(float(t.amount or 0) - float(d.amount or 0)) < MONEY_EPSILON for t in live)):
                    orphan_docs.append({"id": d.id, "card_id": card_id,
                                        "invoice_number": d.invoice_number,
                                        "amount": float(d.amount or 0)})

            card = db.query(models.Card).filter(models.Card.id == card_id).first()

            # Сначала остатки, потом статус: после удаления лишних записей
            # сделка может оказаться полностью списанной, и статус должен
            # считаться уже по факту починки.
            will_delete_ids = set()
            # Остаток = сумма сделки − выписанные накладные. Без суммы сделки
            # эталона нет — такие записи не трогаем. Карточки в группах
            # списания управляются групповыми сценариями — тоже пропускаем.
            if card and live and (float(card.total_amount or 0) > 0) and card.writeoff_group_id is None:
                issued_sum = round(sum(float(t.amount or 0) for t in live
                                       if t.is_warehouse_writeoff or (t.invoice_number or "").strip()), 2)
                expected_rest = max(0.0, round(float(card.total_amount or 0) - issued_sum, 2))
                pending_rows = [t for t in live
                                if not t.is_warehouse_writeoff and not (t.invoice_number or "").strip()]
                current_rest = round(sum(float(t.amount or 0) for t in pending_rows), 2)
                if abs(expected_rest - current_rest) > MONEY_EPSILON:
                    remainder_fixes.append({
                        "card_id": card_id, "title": card.title,
                        "was": current_rest, "will_be": expected_rest,
                        "extra_rows": len(pending_rows) - 1,
                    })
                    if not dry_run:
                        if expected_rest <= MONEY_EPSILON:
                            # остаток исчерпан — записи-остатки удаляются,
                            # а не остаются нулевыми
                            for p in pending_rows:
                                db.delete(p)
                        elif pending_rows:
                            pending_rows[0].amount = expected_rest
                            for extra in pending_rows[1:]:
                                db.delete(extra)
                        else:
                            db.add(models.Transaction(
                                company_name=card.title, amount=expected_rest,
                                store_location=card.store_location, card_id=card_id,
                            ))
                    if pending_rows:
                        if expected_rest <= MONEY_EPSILON:
                            will_delete_ids = {p.id for p in pending_rows}
                        else:
                            will_delete_ids = {p.id for p in pending_rows[1:]}
                else:
                    # Остаток сходится, но нулевые записи-остатки — мусор,
                    # засоряющий реестр: выписанного покрытие не оставляет.
                    zero_rows = [t for t in pending_rows if float(t.amount or 0) <= 0.005]
                    if zero_rows:
                        zero_rows_removed.append({"card_id": card_id, "title": card.title,
                                                  "rows": len(zero_rows)})
                        if not dry_run:
                            for z in zero_rows:
                                db.delete(z)
                        will_delete_ids = {z.id for z in zero_rows}

            if card and live:
                live_eff = [t for t in live if t.id not in will_delete_ids]
                want = writeoff_status_for(card, live_eff)
                # «Сборку» не трогаем: сделка с записью реестра в «Сборке» —
                # штатное состояние (кнопка «В Сборку»), её туда поставили
                # руками, и перекидывать в «На списание» нельзя.
                if want != card.status and card.status in ("На списание", "Закрыто"):
                    fixed_status.append({"card_id": card_id, "title": card.title,
                                         "from": card.status, "to": want})
                    if not dry_run:
                        card.status = want

        for g in db.query(models.WriteoffGroup).options(selectinload(models.WriteoffGroup.cards)).all():
            if not g.cards:
                orphan_groups.append({"id": g.id, "name": g.name,
                                      "total_amount": float(g.total_amount or 0)})
                if not dry_run:
                    db.delete(g)

        # 5. Сделки в «Сборке» без записи реестра оплат — в «Реестре оплат»
        # их не было видно, пока статус не «пинали» туда-обратно
        # (кейс «ТрансЛИДИЯсервис», фидбек 09.09).
        missing_registry = []
        for c in db.query(models.Card).filter(
            models.Card.status == "Сборка",
            models.Card.is_deleted == False,
        ).all():
            if c.writeoff_group_id is not None or float(c.total_amount or 0) <= 0:
                continue
            has_tx = db.query(models.Transaction).filter(
                models.Transaction.card_id == c.id,
                models.Transaction.is_document == False,
            ).first()
            if not has_tx:
                missing_registry.append({"card_id": c.id, "title": c.title,
                                         "amount": float(c.total_amount or 0)})
                if not dry_run:
                    ensure_registry_remainder(db, c)

        if not dry_run:
            for o in orphan_docs:
                row = db.query(models.Transaction).filter(models.Transaction.id == o["id"]).first()
                if row:
                    db.delete(row)
            db.commit()

        return {
            "dry_run": dry_run,
            "orphan_documents": orphan_docs,
            "orphan_documents_count": len(orphan_docs),
            "status_fixes": fixed_status,
            "status_fixes_count": len(fixed_status),
            "remainder_fixes": remainder_fixes,
            "remainder_fixes_count": len(remainder_fixes),
            "orphan_groups": orphan_groups,
            "orphan_groups_count": len(orphan_groups),
            "missing_registry": missing_registry,
            "missing_registry_count": len(missing_registry),
            "zero_rows_removed": zero_rows_removed,
            "zero_rows_removed_count": len(zero_rows_removed),
            "backup": backup_path,
        }
    finally:
        db.close()
