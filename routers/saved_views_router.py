"""
Роутер для сохранённых представлений (Saved Views).
Позволяет сохранять наборы фильтров, сортировки и группировки.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from auth import get_current_user
from database import get_db

router = APIRouter(
    prefix="/views",
    tags=["Сохранённые представления"],
    dependencies=[Depends(get_current_user)]
)


class ViewCreate(BaseModel):
    name: str
    target_page: str  # 'kanban', 'payments', 'documents'
    view_type: str = "table"  # 'table', 'kanban', 'calendar'
    filters: Optional[dict] = None
    sort_by: Optional[str] = None
    sort_direction: str = "asc"
    group_by: Optional[str] = None
    is_default: bool = False


class ViewUpdate(BaseModel):
    name: Optional[str] = None
    filters: Optional[dict] = None
    sort_by: Optional[str] = None
    sort_direction: Optional[str] = None
    group_by: Optional[str] = None
    is_default: Optional[bool] = None


@router.get("/{target_page}")
def get_views(target_page: str, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    views = db.query(models.SavedView).filter(
        models.SavedView.target_page == target_page,
        models.SavedView.tenant_id == current_user.tenant_id
    ).order_by(models.SavedView.is_default.desc(), models.SavedView.name).all()

    return [{
        "id": v.id,
        "name": v.name,
        "view_type": v.view_type,
        "target_page": v.target_page,
        "filters": json.loads(v.filters) if v.filters else None,
        "sort_by": v.sort_by,
        "sort_direction": v.sort_direction,
        "group_by": v.group_by,
        "is_default": v.is_default
    } for v in views]


@router.post("/")
def create_view(view: ViewCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if view.is_default:
        db.query(models.SavedView).filter(
            models.SavedView.target_page == view.target_page,
            models.SavedView.tenant_id == current_user.tenant_id
        ).update({"is_default": False})

    new_view = models.SavedView(
        name=view.name,
        target_page=view.target_page,
        view_type=view.view_type,
        filters=json.dumps(view.filters) if view.filters else None,
        sort_by=view.sort_by,
        sort_direction=view.sort_direction,
        group_by=view.group_by,
        is_default=view.is_default,
        created_by=current_user.id,
        tenant_id=current_user.tenant_id
    )
    db.add(new_view)
    db.commit()
    db.refresh(new_view)

    return {"id": new_view.id, "name": new_view.name, "message": "Представление создано"}


@router.patch("/{view_id}")
def update_view(view_id: int, view: ViewUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    saved = db.query(models.SavedView).filter(
        models.SavedView.id == view_id,
        models.SavedView.tenant_id == current_user.tenant_id
    ).first()
    if not saved:
        raise HTTPException(status_code=404, detail="Представление не найдено")

    if view.name is not None:
        saved.name = view.name
    if view.filters is not None:
        saved.filters = json.dumps(view.filters)
    if view.sort_by is not None:
        saved.sort_by = view.sort_by
    if view.sort_direction is not None:
        saved.sort_direction = view.sort_direction
    if view.group_by is not None:
        saved.group_by = view.group_by
    if view.is_default is not None:
        if view.is_default:
            db.query(models.SavedView).filter(
                models.SavedView.target_page == saved.target_page,
                models.SavedView.tenant_id == current_user.tenant_id
            ).update({"is_default": False})
        saved.is_default = view.is_default

    db.commit()
    return {"message": "Обновлено"}


@router.delete("/{view_id}")
def delete_view(view_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    saved = db.query(models.SavedView).filter(
        models.SavedView.id == view_id,
        models.SavedView.tenant_id == current_user.tenant_id
    ).first()
    if not saved:
        raise HTTPException(status_code=404, detail="Представление не найдено")

    db.delete(saved)
    db.commit()
    return {"message": "Удалено"}
