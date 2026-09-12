"""
Роутер для кастомных объектов (динамическая модель данных).
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from auth import get_current_user, require_admin
from database import get_db

router = APIRouter(
    prefix="/custom",
    tags=["Кастомные объекты"],
    dependencies=[Depends(get_current_user)]
)


class ObjectTypeCreate(BaseModel):
    name: str
    label: str
    icon: Optional[str] = None

class FieldCreate(BaseModel):
    name: str
    label: str
    field_type: str
    is_required: bool = False
    options: Optional[list] = None
    position: int = 0

class RecordData(BaseModel):
    data: dict


# === ОБЪЕКТЫ ===

@router.get("/objects")
def list_objects(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    objects = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.tenant_id == current_user.tenant_id
    ).all()
    return [{"id": o.id, "name": o.name, "label": o.label, "icon": o.icon} for o in objects]


# FIX 2026-08-30 (роли): создание/удаление типов и полей меняет модель данных —
# только администраторы. Чтение и CRUD записей остаются у всех пользователей:
# записи — рабочие данные, а не конфигурация.
_admin_only = Depends(require_admin())


@router.post("/objects")
def create_object(obj: ObjectTypeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    existing = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.name == obj.name,
        models.CustomObjectType.tenant_id == current_user.tenant_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Объект с таким именем уже существует")

    new_obj = models.CustomObjectType(
        name=obj.name, label=obj.label, icon=obj.icon, tenant_id=current_user.tenant_id
    )
    db.add(new_obj)
    db.commit()
    db.refresh(new_obj)
    return {"id": new_obj.id, "name": new_obj.name, "label": new_obj.label}


@router.delete("/objects/{obj_id}")
def delete_object(obj_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    obj = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.id == obj_id,
        models.CustomObjectType.tenant_id == current_user.tenant_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Объект не найден")

    records = db.query(models.CustomRecord).filter(models.CustomRecord.object_type_id == obj_id).all()
    for r in records:
        db.query(models.CustomFieldValue).filter(models.CustomFieldValue.record_id == r.id).delete()
        db.delete(r)
    db.query(models.CustomFieldDef).filter(models.CustomFieldDef.object_type_id == obj_id).delete()
    db.delete(obj)
    db.commit()
    return {"message": "Удалено"}


# === ПОЛЯ ===

@router.get("/objects/{obj_id}/fields")
def list_fields(obj_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    fields = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == obj_id,
        models.CustomFieldDef.tenant_id == current_user.tenant_id
    ).order_by(models.CustomFieldDef.position).all()
    return [{"id": f.id, "name": f.name, "label": f.label, "field_type": f.field_type,
             "is_required": f.is_required, "options": json.loads(f.options) if f.options else None,
             "position": f.position} for f in fields]


@router.post("/objects/{obj_id}/fields")
def create_field(obj_id: int, field: FieldCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    obj = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.id == obj_id,
        models.CustomObjectType.tenant_id == current_user.tenant_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Объект не найден")

    new_field = models.CustomFieldDef(
        object_type_id=obj_id, name=field.name, label=field.label,
        field_type=field.field_type, is_required=field.is_required,
        options=json.dumps(field.options) if field.options else None,
        position=field.position, tenant_id=current_user.tenant_id
    )
    db.add(new_field)
    db.commit()
    db.refresh(new_field)
    return {"id": new_field.id, "name": new_field.name, "label": new_field.label}


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    field = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.id == field_id,
        models.CustomFieldDef.tenant_id == current_user.tenant_id
    ).first()
    if not field:
        raise HTTPException(status_code=404, detail="Поле не найдено")
    db.query(models.CustomFieldValue).filter(models.CustomFieldValue.field_def_id == field_id).delete()
    db.delete(field)
    db.commit()
    return {"message": "Удалено"}


# === ЗАПИСИ ===

@router.get("/objects/{obj_id}/records")
def list_records(obj_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    records = db.query(models.CustomRecord).filter(
        models.CustomRecord.object_type_id == obj_id,
        models.CustomRecord.tenant_id == current_user.tenant_id
    ).order_by(models.CustomRecord.id.desc()).all()

    fields = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == obj_id
    ).all()
    field_map = {f.id: f for f in fields}

    # Н12 (аудит 06.09): значения всех записей одним запросом IN —
    # раньше был SELECT на каждую запись (запрос в цикле).
    record_ids = [r.id for r in records]
    values_by_record = {}
    if record_ids:
        all_values = db.query(models.CustomFieldValue).filter(
            models.CustomFieldValue.record_id.in_(record_ids)
        ).all()
        for v in all_values:
            values_by_record.setdefault(v.record_id, []).append(v)

    result = []
    for r in records:
        data = {}
        for v in values_by_record.get(r.id, []):
            field = field_map.get(v.field_def_id)
            if field:
                if v.value_text is not None:
                    data[field.name] = v.value_text
                elif v.value_number is not None:
                    data[field.name] = float(v.value_number)
                elif v.value_boolean is not None:
                    data[field.name] = v.value_boolean
                elif v.value_date is not None:
                    data[field.name] = v.value_date.isoformat()
                elif v.value_json is not None:
                    data[field.name] = json.loads(v.value_json)
        result.append({"id": r.id, "data": data, "created_at": r.created_at.isoformat()})

    return result


@router.post("/objects/{obj_id}/records")
def create_record(obj_id: int, record: RecordData, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    obj = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.id == obj_id,
        models.CustomObjectType.tenant_id == current_user.tenant_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Объект не найден")

    new_record = models.CustomRecord(
        object_type_id=obj_id, created_by=current_user.id, tenant_id=current_user.tenant_id
    )
    db.add(new_record)
    db.flush()

    fields = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == obj_id
    ).all()
    field_map = {f.name: f for f in fields}

    for field_name, value in record.data.items():
        field = field_map.get(field_name)
        if not field:
            continue
        fv = models.CustomFieldValue(record_id=new_record.id, field_def_id=field.id)
        if field.field_type in ('text', 'select'):
            fv.value_text = str(value) if value is not None else None
        elif field.field_type in ('number', 'currency'):
            fv.value_number = float(value) if value is not None else None
        elif field.field_type == 'boolean':
            fv.value_boolean = bool(value)
        elif field.field_type == 'date':
            # Фикс аудита 10.09: произвольная строка из формы падала
            # ValueError'ом и уходила в глобальный 500. Отдаём 400.
            try:
                fv.value_date = datetime.fromisoformat(value) if value else None
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=400, detail=f"Некорректная дата в поле «{field.name}»: {value!r}") from e
        else:
            fv.value_text = str(value) if value is not None else None
        db.add(fv)

    db.commit()
    return {"id": new_record.id, "message": "Запись создана"}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rec = db.query(models.CustomRecord).filter(
        models.CustomRecord.id == record_id,
        models.CustomRecord.tenant_id == current_user.tenant_id
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    # FIX 2026-09-11 (Фаза 2): на эту запись могут ссылаться ДРУГИЕ записи
    # через value_relation, а у FK нет ON DELETE — удаление падало в
    # IntegrityError (500). Отвязываем ссылки, а не сносим чужие записи:
    # поле-ссылка опустеет, но сама запись и её остальные поля уцелеют.
    db.query(models.CustomFieldValue).filter(
        models.CustomFieldValue.value_relation == record_id
    ).update({"value_relation": None}, synchronize_session=False)
    db.query(models.CustomFieldValue).filter(models.CustomFieldValue.record_id == record_id).delete()
    db.delete(rec)
    db.commit()
    return {"message": "Удалено"}
