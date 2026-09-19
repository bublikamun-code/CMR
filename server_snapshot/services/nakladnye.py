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
    """FIX 2026-09-12 (Фаза 2, дефект 7): commit с переводом дубля в 409.

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

    FIX 2026-09-19 (пакет A, дефект A4): статус 409 Conflict вместо 400 —
    клиент (v2 boot.js и бот) отличает дубль от ошибки валидации и показывает
    «уже существует» со ссылкой на существующую запись. Тело ответа содержит
    existing_id, чтобы клиент мог дать ссылку на дубль.
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
            status_code=409,
            detail={
                "message": (f"Накладная с серией «{series}» и номером {number} "
                            f"уже принята (id={existing.id}). Повторно тот же "
                            f"документ не создаётся — если это повторная "
                            f"отгрузка, откройте существующую запись."),
                "existing_id": existing.id,
            },
        ) from e


def nak_dict(n: models.Nakladnaya) -> dict:
    """Сериализация накладной в dict для ответа API.

    Вынесено из _nak_dict в routers/nakladnye_router.py (Фаза 4) —
    тело перенесено посимвольно.
    """
    photos = []
    if n.photo_paths:
        try:
            photos = json.loads(n.photo_paths)
        except (json.JSONDecodeError, TypeError):
            photos = []
    return {
        "id": n.id,
        "supplier_id": n.supplier_id,
        "supplier_name": n.supplier_name or "",
        "doc_type": n.doc_type,
        "doc_series": n.doc_series,
        "doc_number": n.doc_number,
        "doc_date": n.doc_date,
        "amount": float(n.amount) if n.amount is not None else None,
        "vat_amount": float(n.vat_amount) if n.vat_amount is not None else None,
        # дефект 14: поле доступно на запись, поэтому обязано читаться обратно
        "amount_no_vat": float(n.amount_no_vat) if n.amount_no_vat is not None else None,
        "unload_address": n.unload_address,
        "store": n.store,
        "is_verified": bool(n.is_verified),
        "is_arrived": bool(n.is_arrived) if n.is_arrived is not None else False,
        "is_paid": bool(n.is_paid),
        "status": n.status or "new",
        "photo_paths": photos,
        "products": parse_products(n),
        "excel_path": n.excel_path,
        "created_by_bot": bool(n.created_by_bot),
        "created_at": n.created_at,
        "supplier": {"id": n.supplier.id, "name": n.supplier.name} if n.supplier else None,
    }
