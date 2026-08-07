"""
Роутер для кастомных объектов (динамическая модель данных).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import json

import models
from database import SessionLocal
from auth import get_current_user

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


def _get_db(user):
    from database import get_tenant_db as _gtdb, SessionLocal as _SL
    tid = getattr(user, 'tenant_id', None)
    if tid:
        return _gtdb(tid)
    return _SL()


# === ОБЪЕКТЫ ===

@router.get("/objects")
def list_objects(current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        objects = db.query(models.CustomObjectType).filter(
            models.CustomObjectType.tenant_id == current_user.tenant_id
        ).all()
        return [{"id": o.id, "name": o.name, "label": o.label, "icon": o.icon} for o in objects]
    finally:
        db.close()


@router.post("/objects")
def create_object(obj: ObjectTypeCreate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
    finally:
        db.close()


@router.delete("/objects/{obj_id}")
def delete_object(obj_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
    finally:
        db.close()


# === ПОЛЯ ===

@router.get("/objects/{obj_id}/fields")
def list_fields(obj_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        fields = db.query(models.CustomFieldDef).filter(
            models.CustomFieldDef.object_type_id == obj_id,
            models.CustomFieldDef.tenant_id == current_user.tenant_id
        ).order_by(models.CustomFieldDef.position).all()
        return [{"id": f.id, "name": f.name, "label": f.label, "field_type": f.field_type,
                 "is_required": f.is_required, "options": json.loads(f.options) if f.options else None,
                 "position": f.position} for f in fields]
    finally:
        db.close()


@router.post("/objects/{obj_id}/fields")
def create_field(obj_id: int, field: FieldCreate, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
    finally:
        db.close()


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
    finally:
        db.close()


# === ЗАПИСИ ===

@router.get("/objects/{obj_id}/records")
def list_records(obj_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        records = db.query(models.CustomRecord).filter(
            models.CustomRecord.object_type_id == obj_id,
            models.CustomRecord.tenant_id == current_user.tenant_id
        ).order_by(models.CustomRecord.id.desc()).all()

        fields = db.query(models.CustomFieldDef).filter(
            models.CustomFieldDef.object_type_id == obj_id
        ).all()
        field_map = {f.id: f for f in fields}

        result = []
        for r in records:
            values = db.query(models.CustomFieldValue).filter(
                models.CustomFieldValue.record_id == r.id
            ).all()
            data = {}
            for v in values:
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
    finally:
        db.close()


@router.post("/objects/{obj_id}/records")
def create_record(obj_id: int, record: RecordData, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
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
                fv.value_date = datetime.fromisoformat(value) if value else None
            else:
                fv.value_text = str(value) if value is not None else None
            db.add(fv)

        db.commit()
        return {"id": new_record.id, "message": "Запись создана"}
    finally:
        db.close()


@router.delete("/records/{record_id}")
def delete_record(record_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        rec = db.query(models.CustomRecord).filter(
            models.CustomRecord.id == record_id,
            models.CustomRecord.tenant_id == current_user.tenant_id
        ).first()
        if not rec:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        db.query(models.CustomFieldValue).filter(models.CustomFieldValue.record_id == record_id).delete()
        db.delete(rec)
        db.commit()
        return {"message": "Удалено"}
    finally:
        db.close()
