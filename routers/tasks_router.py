"""
Раздел «Задачи»: отдельные поручения с ответственным и сроком.

Хранение: таблица tasks в ОСНОВНОЙ базе (пользователи глобальны, компания
одна, а поручения свободно назначаются между сотрудниками). Видимость:
- admin/superadmin видят все задачи;
- остальные — только назначенные им и созданные ими.

Уведомления пишутся в базу получателя (см. notify.py).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from datetime import datetime

import models
import schemas
from database import get_db
from auth import get_current_user
from notify import notify

router = APIRouter(
    prefix="/tasks",
    tags=["Задачи"],
    dependencies=[Depends(get_current_user)],
)


def _snapshot_title(db: Session, model, obj_id):
    """Название сделки/клиента: ищем в основной базе, затем в tenant-базах.

    Сделка может жить в tenant-базе, недоступной читающему задачи, —
    поэтому храним снимок названия в самой задаче (см. migrate_task_snapshots).
    """
    if not obj_id:
        return None
    obj = db.get(model, obj_id)
    if obj is not None:
        return getattr(obj, "title", None) or getattr(obj, "name", None)
    from database import get_tenant_db
    tenant_ids = sorted({int(r[0]) for r in db.query(models.User.tenant_id)
                         .filter(models.User.tenant_id != None).distinct().all()})
    for tid in tenant_ids:
        sess = get_tenant_db(tid)
        try:
            obj = sess.get(model, obj_id)
            if obj is not None:
                return getattr(obj, "title", None) or getattr(obj, "name", None)
        finally:
            sess.close()
    return None


def _serialize(db: Session, task: models.Task) -> dict:
    def username(uid):
        if not uid:
            return None
        u = db.get(models.User, uid)
        return u.username if u else None

    # Снимки названий (заполняются при создании/изменении задачи);
    # lookup — фолбэк для записей, созданных до миграции
    card_title = task.card_title_snapshot or _snapshot_title(db, models.Card, task.card_id)
    client_name = task.client_name_snapshot or _snapshot_title(db, models.Client, task.client_id)
    checklist = [
        {"id": i.id, "title": i.title, "is_done": bool(i.is_done)}
        for i in sorted(task.checklist_items, key=lambda x: (x.position, x.id))
    ] if task.checklist_items else []
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "due_date": task.due_date,
        "priority": task.priority or 0,
        "assignee_id": task.assignee_id,
        "creator_id": task.creator_id,
        "card_id": task.card_id,
        "client_id": task.client_id,
        "completed_at": task.completed_at,
        "created_at": task.created_at,
        "assignee_username": username(task.assignee_id),
        "creator_username": username(task.creator_id),
        "card_title": card_title,
        "client_name": client_name,
        "checklist": checklist,
    }


def _get_visible_task_or_404(db: Session, task_id: int, current_user: models.User) -> models.Task:
    query = db.query(models.Task).filter(models.Task.id == task_id)
    if current_user.role not in ("admin", "superadmin"):
        query = query.filter(or_(models.Task.assignee_id == current_user.id,
                                 models.Task.creator_id == current_user.id))
    task = query.first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


@router.get("/assignees", response_model=List[schemas.TaskAssignee])
def list_assignees(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Кому можно поручить задачу — все пользователи системы."""
    return db.query(models.User.id, models.User.username).order_by(models.User.username).all()


@router.get("", response_model=List[schemas.TaskResponse])
def list_tasks(my: Optional[int] = None, status: Optional[str] = None,
               q: Optional[str] = None,
               db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.Task)
    if current_user.role not in ("admin", "superadmin"):
        query = query.filter(or_(models.Task.assignee_id == current_user.id,
                                 models.Task.creator_id == current_user.id))
    if my:
        query = query.filter(models.Task.assignee_id == current_user.id)
    if status in schemas.TASK_STATUSES:
        query = query.filter(models.Task.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Task.title.ilike(like),
                                 models.Task.description.ilike(like)))
    tasks = query.order_by(models.Task.status.desc(),
                           models.Task.due_date.is_(None),
                           models.Task.due_date.asc()).limit(500).all()
    return [_serialize(db, t) for t in tasks]


@router.post("", response_model=schemas.TaskResponse)
def create_task(data: schemas.TaskCreate, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    if not data.title or not data.title.strip():
        raise HTTPException(status_code=400, detail="Укажите название задачи")
    if data.status and data.status not in schemas.TASK_STATUSES:
        raise HTTPException(status_code=400, detail="Недопустимый статус")

    task = models.Task(
        title=data.title.strip()[:255],
        description=data.description,
        status=data.status or "todo",
        due_date=data.due_date,
        assignee_id=data.assignee_id,
        creator_id=current_user.id,
        card_id=data.card_id,
        client_id=data.client_id,
        completed_at=datetime.utcnow() if data.status == "done" else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Снимки названий сделки/клиента (могут жить в tenant-базах)
    if task.card_id:
        task.card_title_snapshot = _snapshot_title(db, models.Card, task.card_id)
    if task.client_id:
        task.client_name_snapshot = _snapshot_title(db, models.Client, task.client_id)
    if task.card_title_snapshot is not None or task.client_name_snapshot is not None:
        db.commit()
        db.refresh(task)

    # Уведомление назначенному исполнителю (в его базу)
    if data.assignee_id:
        notify(db, [data.assignee_id], actor_id=current_user.id,
               type="task_assigned", title=f"Вам поручена задача: {task.title}",
               details=(task.due_date.strftime("%d.%m.%Y %H:%M") if task.due_date else None),
               entity_type="task", entity_id=task.id)
    return _serialize(db, task)


@router.patch("/{task_id}", response_model=schemas.TaskResponse)
def update_task(task_id: int, data: schemas.TaskUpdate, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    task = _get_visible_task_or_404(db, task_id, current_user)
    old_assignee = task.assignee_id
    old_due = task.due_date
    old_status = task.status

    if data.title is not None and data.title.strip():
        task.title = data.title.strip()[:255]
    if data.description is not None:
        task.description = data.description
    if data.due_date is not None:
        task.due_date = data.due_date
    if data.assignee_id is not None:
        task.assignee_id = data.assignee_id or None
    if data.card_id is not None:
        task.card_id = data.card_id or None
    if data.client_id is not None:
        task.client_id = data.client_id or None
    if data.status is not None:
        if data.status not in schemas.TASK_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        task.status = data.status
        task.completed_at = datetime.utcnow() if data.status == "done" else None

    db.commit()
    db.refresh(task)

    # Обновить снимки названий при смене привязки
    if data.card_id is not None or data.client_id is not None:
        if task.card_id:
            task.card_title_snapshot = _snapshot_title(db, models.Card, task.card_id)
        else:
            task.card_title_snapshot = None
        if task.client_id:
            task.client_name_snapshot = _snapshot_title(db, models.Client, task.client_id)
        else:
            task.client_name_snapshot = None
        db.commit()
        db.refresh(task)

    # Уведомления об изменениях (пишутся в базу получателя)
    if data.assignee_id is not None and task.assignee_id and task.assignee_id != old_assignee:
        notify(db, [task.assignee_id], actor_id=current_user.id,
               type="task_assigned", title=f"Вам поручена задача: {task.title}",
               entity_type="task", entity_id=task.id)
    if data.due_date is not None and task.due_date != old_due:
        notify(db, [r for r in {task.assignee_id, task.creator_id}],
               actor_id=current_user.id,
               type="task_due", title=f"Изменён срок задачи: {task.title}",
               details=task.due_date.strftime("%d.%m.%Y %H:%M") if task.due_date else "срок снят",
               entity_type="task", entity_id=task.id)
    if data.status is not None and data.status == "done" and old_status != "done":
        # Автору задачи сообщают о выполнении его поручения
        notify(db, [task.creator_id], actor_id=current_user.id,
               type="task_done", title=f"Задача выполнена: {task.title}",
               entity_type="task", entity_id=task.id)

    return _serialize(db, task)


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    task = _get_visible_task_or_404(db, task_id, current_user)
    # Удалять может автор или администратор
    if task.creator_id != current_user.id and current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Удалять задачу может её автор или администратор")
    db.delete(task)
    db.commit()
    return {"ok": True}


# --- Чек-лист (подзадачи) ---

def _get_item_or_404(db: Session, item_id: int, current_user: models.User) -> models.TaskChecklistItem:
    item = db.query(models.TaskChecklistItem).filter(
        models.TaskChecklistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Пункт не найден")
    # Доступ к пункту = доступ к его задаче
    _get_visible_task_or_404(db, item.task_id, current_user)
    return item


@router.post("/{task_id}/checklist", response_model=schemas.TaskChecklistItemResponse)
def add_checklist_item(task_id: int, data: schemas.TaskChecklistItemCreate,
                       db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    task = _get_visible_task_or_404(db, task_id, current_user)
    title = (data.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Пустой пункт чек-листа")
    last_pos = db.query(models.TaskChecklistItem.position).filter(
        models.TaskChecklistItem.task_id == task.id).order_by(
        models.TaskChecklistItem.position.desc()).first()
    item = models.TaskChecklistItem(
        task_id=task.id, title=title[:255],
        position=(last_pos[0] + 1) if last_pos and last_pos[0] is not None else 0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/checklist/{item_id}", response_model=schemas.TaskChecklistItemResponse)
def update_checklist_item(item_id: int, data: schemas.TaskChecklistItemUpdate,
                          db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = _get_item_or_404(db, item_id, current_user)
    if data.title is not None and data.title.strip():
        item.title = data.title.strip()[:255]
    if data.is_done is not None:
        item.is_done = data.is_done
    db.commit()
    db.refresh(item)
    return item


@router.delete("/checklist/{item_id}")
def delete_checklist_item(item_id: int, db: Session = Depends(get_db),
                          current_user: models.User = Depends(get_current_user)):
    item = _get_item_or_404(db, item_id, current_user)
    db.delete(item)
    db.commit()
    return {"ok": True}
