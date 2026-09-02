"""
Роутер для воркфлоу — создание и управление автоматизациями.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import json

import models
from database import SessionLocal
from auth import get_current_user, require_admin
from db_utils import resolve_tenant_db_standalone as _get_db

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
def list_workflows(current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        workflows = db.query(models.Workflow).filter(
            models.Workflow.tenant_id == current_user.tenant_id
        ).order_by(models.Workflow.id.desc()).all()
        return [{
            "id": w.id, "name": w.name, "description": w.description,
            "is_active": w.is_active, "created_at": w.created_at.isoformat()
        } for w in workflows]
    finally:
        db.close()


@router.post("/")
def create_workflow(wf: WorkflowCreate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        new_wf = models.Workflow(
            name=wf.name, description=wf.description,
            created_by=current_user.id, tenant_id=current_user.tenant_id
        )
        db.add(new_wf)
        db.commit()
        db.refresh(new_wf)
        return {"id": new_wf.id, "name": new_wf.name}
    finally:
        db.close()


@router.patch("/{wf_id}")
def update_workflow(wf_id: int, wf: WorkflowUpdate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        w = db.query(models.Workflow).filter(
            models.Workflow.id == wf_id, models.Workflow.tenant_id == current_user.tenant_id
        ).first()
        if not w:
            raise HTTPException(status_code=404, detail="Воркфлоу не найден")
        if wf.name is not None: w.name = wf.name
        if wf.description is not None: w.description = wf.description
        if wf.is_active is not None: w.is_active = wf.is_active
        db.commit()
        return {"message": "Обновлено"}
    finally:
        db.close()


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
    finally:
        db.close()


# === ТРИГГЕРЫ ===

@router.get("/{wf_id}/triggers")
def list_triggers(wf_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        triggers = db.query(models.WorkflowTrigger).filter(
            models.WorkflowTrigger.workflow_id == wf_id
        ).all()
        return [{"id": t.id, "trigger_type": t.trigger_type,
                 "config": json.loads(t.config) if t.config else {}} for t in triggers]
    finally:
        db.close()


@router.post("/{wf_id}/triggers")
def create_trigger(wf_id: int, trig: TriggerCreate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        new_trig = models.WorkflowTrigger(
            workflow_id=wf_id, trigger_type=trig.trigger_type,
            config=json.dumps(trig.config), tenant_id=current_user.tenant_id
        )
        db.add(new_trig)
        db.commit()
        return {"id": new_trig.id, "trigger_type": new_trig.trigger_type}
    finally:
        db.close()


@router.delete("/triggers/{trigger_id}")
def delete_trigger(trigger_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        t = db.query(models.WorkflowTrigger).filter(models.WorkflowTrigger.id == trigger_id).first()
        if not t: raise HTTPException(status_code=404, detail="Триггер не найден")
        db.delete(t)
        db.commit()
        return {"message": "Удалено"}
    finally:
        db.close()


# === ШАГИ ===

@router.get("/{wf_id}/steps")
def list_steps(wf_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        steps = db.query(models.WorkflowStep).filter(
            models.WorkflowStep.workflow_id == wf_id
        ).order_by(models.WorkflowStep.position).all()
        return [{"id": s.id, "step_type": s.step_type, "action_type": s.action_type,
                 "config": json.loads(s.config) if s.config else {},
                 "position": s.position} for s in steps]
    finally:
        db.close()


@router.post("/{wf_id}/steps")
def create_step(wf_id: int, step: StepCreate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        new_step = models.WorkflowStep(
            workflow_id=wf_id, step_type=step.step_type,
            action_type=step.action_type, config=json.dumps(step.config),
            position=step.position, tenant_id=current_user.tenant_id
        )
        db.add(new_step)
        db.commit()
        db.refresh(new_step)
        return {"id": new_step.id, "step_type": new_step.step_type, "action_type": new_step.action_type}
    finally:
        db.close()


@router.delete("/steps/{step_id}")
def delete_step(step_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        s = db.query(models.WorkflowStep).filter(models.WorkflowStep.id == step_id).first()
        if not s: raise HTTPException(status_code=404, detail="Шаг не найден")
        db.delete(s)
        db.commit()
        return {"message": "Удалено"}
    finally:
        db.close()


# === ЛОГИ ЗАПУСКОВ ===

@router.get("/{wf_id}/runs")
def list_runs(wf_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        runs = db.query(models.WorkflowRun).filter(
            models.WorkflowRun.workflow_id == wf_id
        ).order_by(models.WorkflowRun.started_at.desc()).limit(20).all()
        return [{"id": r.id, "status": r.status, "error": r.error,
                 "started_at": r.started_at.isoformat() if r.started_at else None,
                 "completed_at": r.completed_at.isoformat() if r.completed_at else None} for r in runs]
    finally:
        db.close()
