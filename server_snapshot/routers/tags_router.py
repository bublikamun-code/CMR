from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

import models
import schemas
from auth import get_current_user, require_role
from database import get_scoped_session
from db_utils import cap_list

router = APIRouter(
    prefix="/tags",
    tags=["Теги"],
    dependencies=[Depends(get_current_user)]
)


def _scoped_db(current_user: models.User = Depends(get_current_user)):
    """Wrapper: get_scoped_session is a plain generator and must receive
    current_user explicitly; passing it straight to Depends() makes FastAPI
    treat current_user as a required query parameter (HTTP 422)."""
    yield from get_scoped_session(current_user)


@router.get("", response_model=List[schemas.TagResponse])
def list_tags(response: Response = None, db: Session = Depends(_scoped_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Tag)
    # Н11 (аудит 06.09): предохранитель от неограниченного списка
    return cap_list(query.order_by(models.Tag.name).all(), response)


@router.post("", response_model=schemas.TagResponse)
def create_tag(tag: schemas.TagCreate, db: Session = Depends(_scoped_db), current_user: models.User = Depends(get_current_user)):
    existing = db.query(models.Tag).filter(models.Tag.name == tag.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Тег уже существует")
    new_tag = models.Tag(**tag.model_dump())
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
def delete_tag(tag_id: int, db: Session = Depends(_scoped_db), current_user: models.User = Depends(get_current_user)):
    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Тег не найден")
    db.delete(tag)
    db.commit()
    return {"detail": "Тег удалён"}


@router.post("/cards/{card_id}/tags/{tag_id}")
def add_tag_to_card(card_id: int, tag_id: int, db: Session = Depends(_scoped_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not card or not tag:
        raise HTTPException(status_code=404, detail="Карточка или тег не найдены")
    if tag not in card.tags:
        card.tags.append(tag)
        db.commit()
    return {"detail": "Тег добавлен"}


@router.delete("/cards/{card_id}/tags/{tag_id}")
def remove_tag_from_card(card_id: int, tag_id: int, db: Session = Depends(_scoped_db), current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not card or not tag:
        raise HTTPException(status_code=404, detail="Карточка или тег не найдены")
    if tag in card.tags:
        card.tags.remove(tag)
        db.commit()
    return {"detail": "Тег удалён"}
