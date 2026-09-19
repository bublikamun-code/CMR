"""Вычисляемые денежные поля карточки (A12 — сервер единственный источник правды).

Все формулы в одном месте; роутеры вызывают compute_card_money и не дублируют
логику. Правило определения issued/remaining повторяет writeoff_status_for
(services/writeoffs.py) и логику доски списания: «выписано» = записи реестра
с номером накладной ИЛИ складским флагом; «остаток» = сумма сделки минус
выписанное.

v2-контракт (план V2-FIX-PLAN-2026-09-19):
  remaining       — остаток к выписке в рублях (float);
  remaining_kop   — то же в копейках (int);
  writeoff_status — not_written | partially_written | written;
  issued_total    — сумма непустых документов сделки.
"""

from constants import MONEY_EPSILON


def _is_issued_row(t) -> bool:
    """Запись реестра считается «выписанным документом», если у неё есть
    номер накладной или складской флаг. Запись-остаток — не документ."""
    return bool(t.is_warehouse_writeoff) or bool((t.invoice_number or "").strip())


def compute_card_money(card, ledger_rows: list) -> dict:
    """Вычисляет денежные поля карточки по её записям реестра (без документов-копий).

    Parameters
    ----------
    card : models.Card
        ORM-объект карточки. Нужны total_amount.
    ledger_rows : list[models.Transaction]
        Записи Transaction с is_document=False для этой карточки.

    Returns
    -------
    dict с полями: remaining, remaining_kop, writeoff_status, issued_total.
    """
    total = round(float(card.total_amount or 0), 2)
    issued = round(sum(float(t.amount or 0) for t in ledger_rows if _is_issued_row(t)), 2)

    if total > 0:
        remaining = max(0.0, round(total - issued, 2))
    else:
        remaining = 0.0

    remaining_kop = int(round(remaining * 100))

    if not ledger_rows or issued <= MONEY_EPSILON:
        status = "not_written"
    elif remaining <= MONEY_EPSILON:
        status = "written"
    else:
        status = "partially_written"

    return {
        "remaining": remaining,
        "remaining_kop": remaining_kop,
        "writeoff_status": status,
        "issued_total": issued,
    }
