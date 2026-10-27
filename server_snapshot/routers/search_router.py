"""Глобальный поиск: клиенты, сделки (карточки), поставщики.

Сопоставление — Python-side casefold, а не SQL ilike: lower() в SQLite
складывает регистр только для ASCII, кириллица в другом регистре не
находится (дефект задокументирован тестом поиска поставщиков). Объёмы
в тенанте малы (сотни строк), поэтому перебор в Python одновременно
точнее и дешевле индекса. Лёгкие выборки: load_only без связей.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, load_only

import models
import schemas
from auth import get_current_user
from database import get_db
from tenant_policy import tenant_predicate

router = APIRouter(
    prefix="/search",
    tags=["Поиск"],
    dependencies=[Depends(get_current_user)],
)

# Кап на группу: панель поиска всё равно показывает несколько строк,
# полный список — в своём разделе.
LIMIT_PER_GROUP = 5


def _matches(texts, needle) -> bool:
    return any(needle in t.casefold() for t in texts if t)


@router.get("", response_model=schemas.SearchResponse)
def global_search(
    q: str = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    needle = (q or "").strip().casefold()
    raw_q = q or ""
    if not needle:
        return {"q": raw_q, "clients": [], "cards": [], "suppliers": []}
    raw_stripped = q.strip()

    clients = []
    for c in (
        db.query(models.Client)
        .filter(tenant_predicate(models.Client, current_user))
        .order_by(models.Client.name)
        .all()
    ):
        if _matches([c.name, c.phone, c.unp, c.email], needle):
            clients.append({"id": c.id, "name": c.name, "phone": c.phone, "unp": c.unp})
            if len(clients) == LIMIT_PER_GROUP:
                break

    suppliers = []
    for s in (
        db.query(models.Supplier)
        .filter(tenant_predicate(models.Supplier, current_user))
        .order_by(models.Supplier.name)
        .all()
    ):
        if _matches([s.name, s.phone, s.unp], needle):
            suppliers.append({"id": s.id, "name": s.name, "phone": s.phone, "unp": s.unp})
            if len(suppliers) == LIMIT_PER_GROUP:
                break

    # Имена клиентов одним запросом — карточки ищутся и по ним (как фильтр
    # доски: заголовок + клиент + строковый id).
    client_names = dict(
        db.query(models.Client.id, models.Client.name)
        .filter(tenant_predicate(models.Client, current_user))
        .all()
    )
    cards = []
    for card in (
        db.query(models.Card)
        .options(load_only(models.Card.id, models.Card.title, models.Card.status, models.Card.client_id))
        .filter(models.Card.is_deleted == False, tenant_predicate(models.Card, current_user))  # noqa: E712
        .order_by(models.Card.id)
        .all()
    ):
        client_name = client_names.get(card.client_id)
        if (
            _matches([card.title, client_name], needle)
            or raw_stripped == str(card.id)
        ):
            cards.append({
                "id": card.id,
                "title": card.title,
                "status": card.status,
                "client_name": client_name,
            })
            if len(cards) == LIMIT_PER_GROUP:
                break

    return {"q": raw_q, "clients": clients, "cards": cards, "suppliers": suppliers}
