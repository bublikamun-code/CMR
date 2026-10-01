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
from tenant_policy import (
    assign_tenant,
    get_tenant_object,
    tenant_predicate,
    writable_tenant_id,
)

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
    # tenant_predicate, а не сравнение с current_user.tenant_id: у superadmin
    # tenant_id IS NULL, и список молчно оказывался пустым (FIX 2026-09-26).
    workflows = db.query(models.Workflow).filter(
        tenant_predicate(models.Workflow, current_user)
    ).order_by(models.Workflow.id.desc()).all()
    return [{
        "id": w.id, "name": w.name, "description": w.description,
        "is_active": w.is_active, "created_at": w.created_at.isoformat()
    } for w in workflows]


@router.post("/")
def create_workflow(wf: WorkflowCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # FIX 2026-09-30 (аудит, С-7): имя проверяется на создании той же проверкой,
    # что и в PATCH. Раньше POST её не делал, и схема (name: str) пропускала
    # пробельное имя: в БД попадал воркфлоу с невидимым названием из одних
    # пробелов — NOT NULL доволен, в списке сценарий выглядит безымянным, и
    # редактор уезжал за пределы видимой области. Расхождение было тем заметнее,
    # что PATCH ровно эту проверку уже выполнял.
    name = (wf.name or "").strip()
    if not name:
        raise HTTPException(status_code=400,
                            detail="Укажите название сценария — оно не может быть пустым")
    new_wf = models.Workflow(
        name=name,
        description=(wf.description or "").strip() or None,
        created_by=current_user.id, tenant_id=writable_tenant_id(current_user),
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
    # FIX 2026-09-26: выборка идёт через tenant_policy — тот же 404 для
    # чужого и отсутствующего, что и у остальных объектных роутеров.
    w = get_tenant_object(
        db, models.Workflow, wf_id, current_user, detail="Воркфлоу не найден"
    )
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
    w = get_tenant_object(
        db, models.Workflow, wf_id, current_user, detail="Воркфлоу не найден"
    )
    if not w:
        raise HTTPException(status_code=404, detail="Воркфлоу не найден")
    # FIX 2026-09-26: дочерние строки удаляем по tenant того же воркфлоу.
    # Раньше фильтр был только по workflow_id, а сам workflow искался по
    # tenant — при одинаковых id в двух базах это давало бы удаление
    # чужих строк. Все четыре таблицы имеют собственный tenant_id, поэтому
    # одного get_tenant_object достаточно (get_tenant_child тут не нужен).
    for child_model in (models.WorkflowStep, models.WorkflowTrigger,
                        models.WorkflowRun):
        db.query(child_model).filter(
            child_model.workflow_id == wf_id,
            child_model.tenant_id == w.tenant_id,
        ).delete(synchronize_session=False)
    db.delete(w)
    db.commit()
    return {"message": "Удалено"}


# === ТРИГГЕРЫ ===

def _get_own_workflow_or_404(db, wf_id: int, current_user):
    """Фикс аудита 10.09: создание триггера/шага с чужим или несуществующим
    wf_id раньше доезжало до FK и возвращало глобальный 409 «Дубликат».
    Проверяем принадлежность воркфлоу этой же базе (тот же фильтр, что в
    update/delete выше) и отдаём честную 404."""
    # FIX 2026-09-26: tenant_policy вместо ручного фильтра по
    # current_user.tenant_id — для superadmin (глобальная видимость) это
    # единственный вариант, который вообще осмыслен.
    return get_tenant_object(
        db, models.Workflow, wf_id, current_user, detail="Воркфлоу не найден"
    )


@router.get("/{wf_id}/triggers")
def list_triggers(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # FIX 2026-09-26: ручка читала триггеры по глобальному wf_id без
    # tenant-фильтра — админ одного tenant видел конфигурацию чужого.
    _get_own_workflow_or_404(db, wf_id, current_user)
    # tenant_predicate, а не `tenant_id == current_user.tenant_id`: у
    # superadmin tenant_id IS NULL, и ручное сравнение молча превращало бы
    # выборку в «tenant_id IS NULL» — воркфлоу виден, а его триггеры нет.
    triggers = db.query(models.WorkflowTrigger).filter(
        models.WorkflowTrigger.workflow_id == wf_id,
        tenant_predicate(models.WorkflowTrigger, current_user),
    ).all()
    return [{"id": t.id, "trigger_type": t.trigger_type,
             "config": json.loads(t.config) if t.config else {}} for t in triggers]


@router.post("/{wf_id}/triggers")
def create_trigger(wf_id: int, trig: TriggerCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _get_own_workflow_or_404(db, wf_id, current_user)
    new_trig = models.WorkflowTrigger(
        workflow_id=wf_id, trigger_type=trig.trigger_type,
        config=json.dumps(trig.config),
    )
    db.add(assign_tenant(new_trig, current_user))
    db.commit()
    return {"id": new_trig.id, "trigger_type": new_trig.trigger_type}


@router.delete("/triggers/{trigger_id}")
def delete_trigger(trigger_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # FIX 2026-09-26: удаление было по глобальному id, любой админ мог
    # снести чужой триггер, просто угадав номер.
    t = get_tenant_object(
        db, models.WorkflowTrigger, trigger_id, current_user,
        detail="Триггер не найден",
    )
    if not t:
        raise HTTPException(status_code=404, detail="Триггер не найден")
    db.delete(t)
    db.commit()
    return {"message": "Удалено"}


# === ШАГИ ===

@router.get("/{wf_id}/steps")
def list_steps(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _get_own_workflow_or_404(db, wf_id, current_user)
    steps = db.query(models.WorkflowStep).filter(
        models.WorkflowStep.workflow_id == wf_id,
        tenant_predicate(models.WorkflowStep, current_user),
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
        position=step.position,
    )
    db.add(assign_tenant(new_step, current_user))
    db.commit()
    db.refresh(new_step)
    return {"id": new_step.id, "step_type": new_step.step_type, "action_type": new_step.action_type}


@router.delete("/steps/{step_id}")
def delete_step(step_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # FIX 2026-09-26: то же, что в delete_trigger — глобальный id на удалении.
    s = get_tenant_object(
        db, models.WorkflowStep, step_id, current_user, detail="Шаг не найден"
    )
    if not s:
        raise HTTPException(status_code=404, detail="Шаг не найден")
    # FIX 2026-09-11 (Фаза 2): у parent_step_id нет ON DELETE, поэтому
    # удаление шага с дочерними падало в IntegrityError (500). Дочерние шаги
    # поднимаем на верхний уровень вместо удаления — их настройки дороже,
    # чем потеря вложенности.
    db.query(models.WorkflowStep).filter(
        models.WorkflowStep.parent_step_id == step_id,
        models.WorkflowStep.tenant_id == s.tenant_id,
    ).update({"parent_step_id": None}, synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"message": "Удалено"}


# === ЛОГИ ЗАПУСКОВ ===

@router.get("/{wf_id}/runs")
def list_runs(wf_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _get_own_workflow_or_404(db, wf_id, current_user)
    runs = db.query(models.WorkflowRun).filter(
        models.WorkflowRun.workflow_id == wf_id,
        tenant_predicate(models.WorkflowRun, current_user),
    ).order_by(models.WorkflowRun.started_at.desc()).limit(20).all()
    return [{"id": r.id, "status": r.status, "error": r.error,
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "completed_at": r.completed_at.isoformat() if r.completed_at else None} for r in runs]
