"""Бизнес-правила накладных: консистентность сумм, ключ документа, commit-охрана.

Вынесено из routers/nakladnye_router.py (Фаза 4). Механический перенос
без изменения логики.
"""

import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import models


def parse_products(n) -> list:
    if not n.products_json:
        return []
    try:
        return json.loads(n.products_json)
    except (json.JSONDecodeError, TypeError):
        return []


def ensure_amounts_consistent(amount, vat_amount):
    """FIX 2026-09-12 (Фаза 2, дефект 11): НДС не может превышать сумму с НДС.

    Схема NakladnayaUpdate проверяет пару только когда оба значения присланы
    вместе. Частичная правка одного поля (vat_amount=500 при сохранённом
    amount=100) схеме неподвластна: второе значение лежит в базе. Поэтому
    здесь сравниваются ЭФФЕКТИВНЫЕ значения — присланное поверх сохранённого.

    Отказ 400, а не 422: это проверка бизнес-правила по данным из базы,
    а не ошибка формата запроса. HTTPException поднимается из service-слоя
    как есть — FastAPI корректно превращает его в HTTP-ответ.
    """
    if amount is None or vat_amount is None:
        return
    if round(float(amount), 2) < round(float(vat_amount), 2):
        raise HTTPException(
            status_code=400,
            detail=(f"НДС ({round(float(vat_amount), 2):.2f}) не может превышать "
                    f"сумму документа с НДС ({round(float(amount), 2):.2f})"),
        )


def effective_amounts(nak: models.Nakladnaya, update_data: dict):
    """Сумма и НДС, которые получатся после применения update_data к nak."""
    amount = update_data.get("amount", nak.amount)
    vat = update_data.get("vat_amount", nak.vat_amount)
    return amount, vat


def find_by_doc_key(session, doc_series, doc_number):
    """Существующая накладная с тем же нормализованным ключом документа."""
    series, number = models.nakladnaya_doc_key(doc_series, doc_number)
    if not number:
        return None
    return session.query(models.Nakladnaya).filter(
        models.Nakladnaya.doc_series_norm == series,
        models.Nakladnaya.doc_number_norm == number,
    ).first()


def commit_with_doc_key_guard(session, doc_series, doc_number):
    """FIX 2026-09-12 (Фаза 2, дефект 7): commit с переводом дубля в 400.

    Уникальность обеспечивает частичный индекс uq_nakladnye_doc_key
    (миграция 0005), поэтому проверка АТОМАРНА: гонка check-then-insert,
    из-за которой параллельная отправка одного документа плодила две записи,
    закрыта на уровне базы, а не ещё одним SELECT перед INSERT.

    Здесь IntegrityError превращается в понятный ответ вместо 500. Значения
    ключа передаются аргументами, а не читаются с объекта: после rollback
    объект разобран, и обращение к его атрибутам подняло бы новую ошибку.

    Если нарушение НЕ про уникальный ключ документа (например, битый FK),
    ошибка пробрасывается дальше: объявить её дублем значило бы соврать
    пользователю и увести разбор в сторону.
    """
    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        existing = find_by_doc_key(session, doc_series, doc_number)
        if existing is None:
            raise
        series, number = models.nakladnaya_doc_key(doc_series, doc_number)
        raise HTTPException(
            status_code=400,
            detail=(f"Накладная с серией «{series}» и номером {number} уже принята "
                    f"(id={existing.id}). Повторно тот же документ не создаётся — "
                    f"если это повторная отгрузка, откройте существующую запись."),
        ) from e
