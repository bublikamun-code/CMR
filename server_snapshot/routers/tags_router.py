from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user, require_role
from database import get_db
from db_utils import cap_list
from tenant_policy import assign_tenant, get_tenant_object, tenant_query

router = APIRouter(
    prefix="/tags",
    tags=["Теги"],
    dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[schemas.TagResponse])
def list_tags(response: Response = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = tenant_query(db, models.Tag, current_user)
    # Н11 (аудит 06.09): предохранитель от неограниченного списка
    return cap_list(query.order_by(models.Tag.name).all(), response)


@router.post("", response_model=schemas.TagResponse)
def create_tag(tag: schemas.TagCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    existing = tenant_query(db, models.Tag, current_user).filter(
        models.Tag.name == tag.name
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Тег уже существует")
    new_tag = assign_tenant(models.Tag(**tag.model_dump()), current_user)
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    return new_tag


# V11 (пункт 17 плана v2): удаление тега снимает его со ВСЕХ сделок сразу
# (каскад по card_tags), то есть это правка справочника, а не своей карточки.
# Поэтому — админ, как остальные справочники. Создание тега и снятие/установка
# его на конкретной сделке остаются операционными действиями любого
# аутентифицированного пользователя.
@router.delete("/{tag_id}",
               dependencies=[Depends(require_role("admin", "superadmin"))])
def delete_tag(tag_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    tag = get_tenant_object(
        db, models.Tag, tag_id, current_user, detail="Тег не найден"
    )
    if not tag:
        raise HTTPException(status_code=404, detail="Тег не найден")
    db.delete(tag)
    db.commit()
    return {"detail": "Тег удалён"}


@router.post("/cards/{card_id}/tags/{tag_id}")
def add_tag_to_card(card_id: int, tag_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    tag = get_tenant_object(
        db, models.Tag, tag_id, current_user, detail="Тег не найден"
    )
    if not card or not tag:
        raise HTTPException(status_code=404, detail="Карточка или тег не найдены")
    if tag not in card.tags:
        card.tags.append(tag)
        db.commit()
    return {"detail": "Тег добавлен"}


@router.delete("/cards/{card_id}/tags/{tag_id}")
def remove_tag_from_card(card_id: int, tag_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    card = get_tenant_object(
        db, models.Card, card_id, current_user, detail="Карточка не найдена"
    )
    tag = get_tenant_object(
        db, models.Tag, tag_id, current_user, detail="Тег не найден"
    )
    if not card or not tag:
        raise HTTPException(status_code=404, detail="Карточка или тег не найдены")
    if tag in card.tags:
        card.tags.remove(tag)
        db.commit()
    return {"detail": "Тег удалён"}
