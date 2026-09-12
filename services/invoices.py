"""Бизнес-правила реестра оплат (номера накладных, дубли, пары «накладная + копия»).

Вынесено из routers/payments_router.py (Фаза 4). Функции чистые: работают
со списками/сессией tx, модели не меняют, только читают атрибуты.
"""

import models
from constants import MONEY_EPSILON


def norm_invoice_number(v):
    """Нормализованный номер накладной — только цифры.

    Номера пишут по-разному: «ТТН4881006», «ТТН 4881006» и «4881006» — это одна
    и та же накладная. В боевых данных оба написания соседствуют (см.
    tests/test_nakladnye_dedup.py), поэтому ЛЮБОЕ сравнение номеров обязано идти
    через эту функцию.

    FIX 2026-09-12 (Фаза 2, дефект 6): вынесена на уровень модуля. Раньше
    правило было продублировано вложенными _norm в _find_invoice_twins и в
    repair-эндпоинте, а третья копия понадобилась бы для проверки дублей.
    """
    return "".join(ch for ch in (v or "") if ch.isdigit())


def find_duplicate_invoice(txs, number):
    """Запись реестра ЭТОЙ ЖЕ сделки с тем же номером — настоящий дубль.

    Штатные сценарии, которые дублями НЕ являются и ломать их нельзя
    (подтверждено данными боевой БД, см. PROGRESS.md):
      - один номер на нескольких сделках — доли одной физической накладной;
      - несколько разных номеров на одной сделке — частичные отгрузки.
    Отсюда область поиска: только записи одной карточки.

    Сравнение по цифрам (norm_invoice_number). Если цифр в номере нет вовсе
    («б/н»), сравниваем строку после strip — иначе две содержательно разные
    записи без номера выглядели бы дублями друг друга.
    """
    key = norm_invoice_number(number)
    raw = (number or "").strip()
    for t in txs:
        if key:
            if norm_invoice_number(t.invoice_number) == key:
                return t
        elif (t.invoice_number or "").strip() == raw:
            return t
    return None


def find_invoice_twins(session, tx):
    """Пара накладной: её копия в «Документах» (или исходник, если правят копию).

    «Накладная + копия» — ОДНА сущность: правки и удаление должны касаться
    обеих сторон. Поиск по номеру без нецифровых символов, затем по сумме.
    Вызывать ДО изменения полей tx: пара ищется по текущим (старым) значениям.

    FIX 2026-09-11 (аудит): два исправления потери данных.

    1. Запись-остаток (без номера, не документ, не складское списание) больше
       не попадает в кандидаты. Она не может быть парой накладной по смыслу,
       а раньше удалялась вместе с документом, у которого номер не распознан.
    2. Убран запасной путь «если кандидат ровно один — это пара». Он срабатывал
       при нераспознанном номере и несовпавшей сумме и объявлял парой первую
       попавшуюся запись: при удалении документа так сносилась запись-остаток,
       а при удалении одной из двух накладных сделки — вторая накладная.
       «Кандидат один» не означает «кандидат — пара».
    """
    if tx.card_id is None:
        return []
    number = (tx.invoice_number or "").strip()
    amount = float(tx.amount or 0)
    was_doc = bool(tx.is_document)

    def _norm(v):
        """Номера накладных пишут по-разному: «ТТН4881006» и «4881006» —
        это одна накладная. Сравниваем только цифры."""
        return norm_invoice_number(v)

    def _is_invoice_like(t):
        return bool(t.is_document or t.is_warehouse_writeoff
                    or (t.invoice_number or "").strip())

    candidates = [
        c for c in session.query(models.Transaction).filter(
            models.Transaction.card_id == tx.card_id,
            models.Transaction.id != tx.id,
            models.Transaction.is_document == (not was_doc),
        ).all()
        if _is_invoice_like(c)
    ]

    key = _norm(number)
    twins = []
    if key:
        twins = [c for c in candidates if _norm(c.invoice_number) == key]
    # запасной путь: номер не совпал (или его нет) — ищем по сумме
    if not twins:
        twins = [c for c in candidates
                 if abs(float(c.amount or 0) - amount) < MONEY_EPSILON
                 and (not key or not _norm(c.invoice_number))]
    return twins
