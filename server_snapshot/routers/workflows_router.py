"""
Роутер для воркфлоу — создание и управление автоматизациями.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from auth import get_current_user, require_admin
from database import get_db

router = APIRouter(
    prefix="/workflows",
    tags=["Воркфлоу"],
    # FIX 2026-08-30 (роли): шаги воркфлоу умеют http_request и code —
    # это конфигурация уровня администратора, а не исполнителя.
    # Вкладка «Воркфлоу» в UI всё равно помечена «В разработке».
    dependencies=[Depends(require_admin())]
)


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None

class TriggerCreate(BaseModel):
    trigger_type: str  # record_event, manual, schedule, webhook
    config: dict

class StepCreate(BaseModel):
    step_type: str  # action, condition, delay
    action_type: Optional[str] = None  # create_record, update_record, send_email, http_request, code
    config: dict
    position: int = 0

class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


# === ВОРКФЛОУ ===

@router.get("/")
def list_workflows(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    workflows = db.query(models.Workflow).filter(
        models.Workflow.tenant_id == current_user.tenant_id
    ).order_by(models.Workflow.id.desc()).all()
    return [{
        "id": w.id, "name": w.name, "description": w.description,
        "is_active": w.is_active, "created_at": w.created_at.isoformat()
    } for w in workflows]


@router.post("/")
def create_workflow(wf: WorkflowCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    new_wf = models.Workflow(
        name=wf.name, description=wf.description,
        created_by=current_user.id, tenant_id=current_user.tenant_id
    )
    db.add(new_wf)
    db.commit()
    db.refresh(new_wf)
    return {"id": new_wf.id, "name": new_wf.name}


@router.patch("/{wf_id}")
def update_workflow(wf_id: int, wf: WorkflowUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # 23.09 (пункт 15 плана v2): model_fields_set вместо `is not None` — тот же
    # перевод, что в webhooks_router.update_webhook и update_task: отсутствие
    # ключа не трогает значение, null очищает там, где колонка nullable.
    w = db.query(models.Workflow).filter(
        models.Workflow.id == wf_id, models.Workflow.tenant_id == current_user.tenant_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Воркфлоу не найден")
    if 'name' in wf.model_fields_set:
        # name — NOT NULL (models.py:320): null не очищает, а отвергается,
        # иначе commit дал бы IntegrityError и 500 в середине применения.
        name = (wf.name or "").strip()
        if not name:
            raise HTTPException(status_code=400,
                                detail="Укажите название сценария — оно не может быть пустым")
        w.name = name
    if 'description' in wf.model_fields_set:
        # nullable: null и пустая строка — одно и то же «описания нет»
        w.description = (wf.description or "").strip() or None
    if 'is_active' in wf.model_fields_set:
        if wf.is_active is None:
            raise HTTPException(status_code=400,
                                detail="Признак активности не может быть null: true или false")
        w.is_active = bool(wf.is_active)
    db.commit()
    return {"message": "Обновлено"}


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    w = db.query(models.Workflow).filter(
        models.Workflow.id == wf_id, models.Workflow.tenant_id == current_user.tenant_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Воркфлоу не найден")
    db.query(models.WorkflowStep).filter(models.WorkflowStep.workflow_id == wf_id).delete()
    db.query(models.WorkflowTrigger).filter(models.WorkflowTrigger.workflow_id == wf_id).delete()
    db.query(models.WorkflowRun).filter(models.WorkflowRun.workflow_id == wf_id).delete()
    db.delete(w)
    db.commit()
    return {"message": "Удалено"}


# === ТРИГГЕРЫ ===

def _get_own_workflow_or_404(db, wf_id: int, current_user):
    """Фикс аудита 10.09: создание триггера/шага с чужим или несуществующим
    wf_id раньше доезжало до FK и возвращало глобальный 409 «Дубликат».
    Проверяем принадлежность воркфлоу этой же базе (тот же фильтр, что в
    update/delete выше) и отдаём честную 404."""
    w = db.query(models.Workflow).filter(
        models.Workflow.id == wf_id, models.Workflow.tenant_id == current_user.tenant_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Воркфлоу не найден")
    return w


@router.get("/{wf_id}/triggers")
def list_triggers(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    triggers = db.query(models.WorkflowTrigger).filter(
        models.WorkflowTrigger.workflow_id == wf_id
    ).all()
    return [{"id": t.id, "trigger_type": t.trigger_type,
             "config": json.loads(t.config) if t.config else {}} for t in triggers]


@router.post("/{wf_id}/triggers")
def create_trigger(wf_id: int, trig: TriggerCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _get_own_workflow_or_404(db, wf_id, current_user)
    new_trig = models.WorkflowTrigger(
        workflow_id=wf_id, trigger_type=trig.trigger_type,
        config=json.dumps(trig.config), tenant_id=current_user.tenant_id
    )
    db.add(new_trig)
    db.commit()
    return {"id": new_trig.id, "trigger_type": new_trig.trigger_type}


@router.delete("/triggers/{trigger_id}")
def delete_trigger(trigger_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    t = db.query(models.WorkflowTrigger).filter(models.WorkflowTrigger.id == trigger_id).first()
    if not t: raise HTTPException(status_code=404, detail="Триггер не найден")
    db.delete(t)
    db.commit()
    return {"message": "Удалено"}


# === ШАГИ ===

@router.get("/{wf_id}/steps")
def list_steps(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    steps = db.query(models.WorkflowStep).filter(
        models.WorkflowStep.workflow_id == wf_id
    ).order_by(models.WorkflowStep.position).all()
    return [{"id": s.id, "step_type": s.step_type, "action_type": s.action_type,
             "config": json.loads(s.config) if s.config else {},
             "position": s.position} for s in steps]


@router.post("/{wf_id}/steps")
def create_step(wf_id: int, step: StepCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _get_own_workflow_or_404(db, wf_id, current_user)
    new_step = models.WorkflowStep(
        workflow_id=wf_id, step_type=step.step_type,
        action_type=step.action_type, config=json.dumps(step.config),
        position=step.position, tenant_id=current_user.tenant_id
    )
    db.add(new_step)
    db.commit()
    db.refresh(new_step)
    return {"id": new_step.id, "step_type": new_step.step_type, "action_type": new_step.action_type}


@router.delete("/steps/{step_id}")
def delete_step(step_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    s = db.query(models.WorkflowStep).filter(models.WorkflowStep.id == step_id).first()
    if not s: raise HTTPException(status_code=404, detail="Шаг не найден")
    # FIX 2026-09-11 (Фаза 2): у parent_step_id нет ON DELETE, поэтому
    # удаление шага с дочерними падало в IntegrityError (500). Дочерние шаги
    # поднимаем на верхний уровень вместо удаления — их настройки дороже,
    # чем потеря вложенности.
    db.query(models.WorkflowStep).filter(
        models.WorkflowStep.parent_step_id == step_id
    ).update({"parent_step_id": None}, synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"message": "Удалено"}


# === ЛОГИ ЗАПУСКОВ ===

@router.get("/{wf_id}/runs")
def list_runs(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    runs = db.query(models.WorkflowRun).filter(
        models.WorkflowRun.workflow_id == wf_id
    ).order_by(models.WorkflowRun.started_at.desc()).limit(20).all()
    return [{"id": r.id, "status": r.status, "error": r.error,
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "completed_at": r.completed_at.isoformat() if r.completed_at else None} for r in runs]
