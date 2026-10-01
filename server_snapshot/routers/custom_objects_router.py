"""
Роутер для кастомных объектов (динамическая модель данных).
"""
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from auth import get_current_user, require_admin
from database import get_db
from tenant_policy import (
    get_tenant_object,
    tenant_predicate,
    tenant_query,
    writable_tenant_id,
)

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

# Порог на число полей в одной записи кастомного объекта.
# Обоснование — в docstring RecordData ниже.
MAX_RECORD_FIELDS = 200


class RecordData(BaseModel):
    # FIX 2026-09-30 (аудит, С-5): ограничение на размер полезной нагрузки.
    #
    # Раньше data принимал словарь любого размера, и одна запись могла втянуть
    # в себя сколько угодно полей — то есть сколько угодно строк в
    # custom_field_values за одну операцию. Это уже не «пользователь ошибся»,
    # а способ положить таблицу одним запросом, и он не требовал ни прав, ни
    # понимания устройства системы. Лимит считается по числу КЛЮЧЕЙ, потому что
    # именно столько значений превратится в строки: неизвестные поля
    # отбрасываются, но место в лимите занимают.
    #
    # 200 полей — с большим запасом для реальных справочников (у самого
    # насыщенного объекта их единицы) и намеренно круглое, чтобы число
    # не выглядело точной гарантией, которой нет.
    data: dict = Field(..., max_length=MAX_RECORD_FIELDS)


def _coerce_number(raw, field):
    """Число из значения формы — 400 вместо 500.

    Раньше здесь стоял голый float(value): строка «abc» из поля ввода роняла
    ValueError прямо в хендлере, то есть в 500, и клиент видел «внутренняя
    ошибка сервера» вместо «в это поле надо число». Отдельная функция нужна и
    потому, что разбор повторяется в трёх местах (создание записи, правка
    значения, калькулятор), и раньше каждое место вело себя по-своему.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        # bool — подкласс int, но «да/нет» в денежном поле бессмысленно:
        # молча превращать его в 1.0 значит записать в поле значение, которого
        # никто не вводил.
        raise HTTPException(
            status_code=400,
            detail=f"Нужно число в поле «{field.name}», а получено да/нет")
    try:
        return float(raw)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Некорректное число в поле «{field.name}»: {raw!r}") from e


def _check_select_option(field, value):
    """Значение поля «список» должно быть одним из объявленных вариантов.

    Раньше любая строка молча ложилась в select как есть. Человек (или
    подставляющий скрипт) мог записать в поле «Статус» что угодно, и потом
    фильтры по этому полю переставали работать — то есть данные становились
    невидимыми для всех, кто ими пользуется, без единого признака ошибки.
    """
    if value is None or value == "":
        return None
    if not field.options:
        raise HTTPException(
            status_code=400,
            detail=f"У поля «{field.name}» не задано ни одного варианта")
    try:
        options = json.loads(field.options)
    except (TypeError, ValueError):
        # Список вариантов испорчен настройкой справочника. Это не ошибка
        # пользователя, но и пропускать значение нельзя — иначе мусор снова
        # окажется в данных. Отказ с внятным текстом честнее.
        raise HTTPException(
            status_code=400,
            detail=f"Варианты поля «{field.name}» заданы некорректно — обратитесь к администратору")
    if value not in options:
        raise HTTPException(
            status_code=400,
            detail=f"Значение {str(value)!r} отсутствует в списке поля «{field.name}». "
                   f"Допустимо: {', '.join(str(o) for o in options[:10])}")
    return value

# 23.09 (пункт 15 плана v2): правка существующего типа и его поля. Тело —
# только изменяемые атрибуты; отсутствие ключа означает «не трогать», а null —
# «очистить» там, где колонка nullable (тот же контракт, что у update_card).
class ObjectTypeUpdate(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    icon: Optional[str] = None

class FieldUpdate(BaseModel):
    name: Optional[str] = None
    label: Optional[str] = None
    field_type: Optional[str] = None
    is_required: Optional[bool] = None
    options: Optional[list] = None
    position: Optional[int] = None


# Системное имя типа и поля — не подпись для человека, а ключ: по name из
# CustomFieldDef create_record собирает field_map, а list_records отдаёт data
# записей, где ключи — те же name. Поэтому набор символов закрыт (латиница, с
# буквы), а не «любые 100 символов».
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Типы значения, которые сервер умеет сохранять: см. ветки create_record.
# 'relation' и 'file' в ней падают в fallback (value_text) — link на запись
# сервером не различается, но такие поля уже описаны в модели (models.py:264),
# поэтому запрещать их в правке нельзя.
FIELD_TYPES = ("text", "number", "currency", "select", "date", "boolean",
               "relation", "file")

# Длины колонок берутся из модели: дублировать их числами — способ разойтись с
# models.py при первом же изменении схемы.
OBJ_COLS = models.CustomObjectType.__table__.columns
FLD_COLS = models.CustomFieldDef.__table__.columns


def _text(raw, *, label, limit, blank_clears=False):
    """Стрижет и ограничивает длиной колонки.

    SQLite за VARCHAR(...) не следит: значение длиннее колонки легло бы в базу
    молча и пропало бы при переносе на движок, который следит (тот же приём,
    что с sender_email в card_details_router.update_card). blank_clears — для
    nullable-колонок: пустая строка и null означают одно и то же «значения
    нет», различать их клиенту не на чем.
    """
    value = (raw or "").strip()
    if not value:
        if not blank_clears:
            raise HTTPException(status_code=400,
                                detail=f"{label}: значение не может быть пустым")
        return None
    if limit and len(value) > limit:
        raise HTTPException(status_code=400,
                            detail=f"{label}: длиннее {limit} символов")
    return value


def _system_name(raw, *, label, limit):
    name = _text(raw, label=label, limit=limit)
    if not NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=(
            f"{label}: системное имя — латиницей, начинается с буквы, без пробелов "
            "(например, equipment)"))
    return name


def _checked_field_type(raw):
    field_type = (raw or "").strip()
    if field_type not in FIELD_TYPES:
        raise HTTPException(status_code=400, detail=(
            f"Недопустимый тип значения «{raw}». Доступны: "
            + ", ".join(FIELD_TYPES)))
    return field_type


def _dump_options(options):
    """Варианты списка в JSON-колонку. [] и null — одно и то же «вариантов нет»:
    list_fields отдаёт null в обоих случаях, различать их клиенту нечем."""
    return json.dumps(options) if options else None


def _field_name_taken(db: Session, object_type_id, name: str, exclude_id=None) -> bool:
    """Занято ли системное имя поля внутри одного типа объекта."""
    query = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == object_type_id,
        models.CustomFieldDef.name == name
    )
    if exclude_id is not None:
        query = query.filter(models.CustomFieldDef.id != exclude_id)
    return query.first() is not None


def _object_dict(obj) -> dict:
    return {"id": obj.id, "name": obj.name, "label": obj.label, "icon": obj.icon}


def _field_dict(field) -> dict:
    return {"id": field.id, "name": field.name, "label": field.label,
            "field_type": field.field_type, "is_required": field.is_required,
            "options": json.loads(field.options) if field.options else None,
            "position": field.position}


def _values_in_use(db: Session, field_id: int) -> int:
    """Сколько значений записей реально занято у поля.

    Строки custom_field_values со всеми пустыми столбцами не считаются: смена
    типа такое значение не ломает.
    """
    filled = [getattr(models.CustomFieldValue, c) for c in
              ("value_text", "value_number", "value_boolean", "value_date",
               "value_json", "value_relation")]
    return db.query(func.count(models.CustomFieldValue.id)).filter(
        models.CustomFieldValue.field_def_id == field_id,
        or_(*[c.isnot(None) for c in filled])).scalar() or 0


def _get_object_or_404(db: Session, obj_id: int, current_user) -> models.CustomObjectType:
    return get_tenant_object(
        db, models.CustomObjectType, obj_id, current_user, detail="Объект не найден"
    )


def _get_field_or_404(db: Session, field_id: int, current_user) -> models.CustomFieldDef:
    return get_tenant_object(
        db, models.CustomFieldDef, field_id, current_user, detail="Поле не найдено"
    )


def _get_record_or_404(db: Session, record_id: int, current_user) -> models.CustomRecord:
    return get_tenant_object(
        db, models.CustomRecord, record_id, current_user, detail="Запись не найдена"
    )


# === ОБЪЕКТЫ ===

@router.get("/objects")
def list_objects(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    objects = tenant_query(db, models.CustomObjectType, current_user).all()
    return [_object_dict(o) for o in objects]


# FIX 2026-08-30 (роли): создание/удаление типов и полей меняет модель данных —
# только администраторы. Чтение и CRUD записей остаются у всех пользователей:
# записи — рабочие данные, а не конфигурация.
_admin_only = Depends(require_admin())


@router.post("/objects")
def create_object(obj: ObjectTypeCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    # Тот же набор проверок, что у PATCH: имя — ключ, по которому правка потом
    # будет искать коллизию. Разрешить create'у «имя с пробелом» означало бы
    # оставить в базе строку, которую PATCH не смог бы ни сохранить, ни починить.
    name = _system_name(obj.name, label="Системное имя типа",
                        limit=OBJ_COLS["name"].type.length)
    label = _text(obj.label, label="Название типа", limit=OBJ_COLS["label"].type.length)
    icon = _text(obj.icon, label="Иконка", limit=OBJ_COLS["icon"].type.length,
                 blank_clears=True)
    existing = db.query(models.CustomObjectType).filter(
        models.CustomObjectType.name == name,
        tenant_predicate(models.CustomObjectType, current_user),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Объект с таким именем уже существует")

    # writable_tenant_id, а не сырой current_user.tenant_id: у superadmin tenant
    # пуст, и запись без него отбивал бы BEFORE INSERT триггер миграции 0018 —
    # то есть 500 вместо внятного отказа.
    new_obj = models.CustomObjectType(
        name=name, label=label, icon=icon,
        tenant_id=writable_tenant_id(current_user),
    )
    db.add(new_obj)
    db.commit()
    db.refresh(new_obj)
    return {"id": new_obj.id, "name": new_obj.name, "label": new_obj.label}


@router.patch("/objects/{obj_id}")
def update_object(obj_id: int, upd: ObjectTypeUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    """Правка типа кастомного объекта: системное имя, пользовательское название, иконка."""
    obj = _get_object_or_404(db, obj_id, current_user)
    old_name = obj.name

    if 'name' in upd.model_fields_set:
        name = _system_name(upd.name, label="Системное имя типа",
                            limit=OBJ_COLS["name"].type.length)
        clash = db.query(models.CustomObjectType).filter(
            models.CustomObjectType.name == name,
            tenant_predicate(models.CustomObjectType, current_user),
            models.CustomObjectType.id != obj_id
        ).first()
        if clash:
            raise HTTPException(status_code=400, detail="Объект с таким именем уже существует")
        obj.name = name
    if 'label' in upd.model_fields_set:
        # NOT NULL в обеих схемах (models.py:250, prod_schema.sql:153): null не
        # очищает, а отвергается — иначе commit дал бы IntegrityError и 500.
        obj.label = _text(upd.label, label="Название типа",
                          limit=OBJ_COLS["label"].type.length)
    if 'icon' in upd.model_fields_set:
        obj.icon = _text(upd.icon, label="Иконка", limit=OBJ_COLS["icon"].type.length,
                         blank_clears=True)

    if obj.name != old_name:
        # CustomFieldDef.relation_target хранит ИМЯ типа, а не id (models.py:264),
        # поэтому переименование переставляет и ссылки: иначе поле-связка
        # указывала бы в несуществующий тип объекта.
        #
        # FIX 2026-09-30 (аудит прав доступа): фильтр БЫЛ без tenant_id, а это
        # UPDATE по всей таблице. Миграция 0018 заменила глобальную уникальность
        # имени типа на UNIQUE (tenant_id, name), то есть одинаковое системное
        # имя у разных компаний стало законным — и переименование типа у
        # tenant A переписывало relation_target у полей ВСЕХ tenant'ов, где стояло
        # прежнее имя. У чужой компании поля-связки молча начинали указывать на
        # тип чужого типа: тихая порча данных, без лога и без отката.
        db.query(models.CustomFieldDef).filter(
            models.CustomFieldDef.relation_target == old_name,
            tenant_predicate(models.CustomFieldDef, current_user),
        ).update({"relation_target": obj.name}, synchronize_session=False)

    try:
        db.commit()
    except IntegrityError as exc:
        # unique на custom_object_types.name глобальная, а проверка выше —
        # в границах tenant: при мультитенантной базе строка чужого tenant
        # сюда не попадает, но индекс всё равно нарушится.
        db.rollback()
        raise HTTPException(status_code=400, detail="Такое системное имя уже занято") from exc
    db.refresh(obj)
    return _object_dict(obj)


@router.delete("/objects/{obj_id}")
def delete_object(obj_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    obj = _get_object_or_404(db, obj_id, current_user)

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
        tenant_predicate(models.CustomFieldDef, current_user),
    ).order_by(models.CustomFieldDef.position).all()
    return [_field_dict(f) for f in fields]


@router.post("/objects/{obj_id}/fields")
def create_field(obj_id: int, field: FieldCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    obj = _get_object_or_404(db, obj_id, current_user)
    name = _system_name(field.name, label="Системное имя поля",
                        limit=FLD_COLS["name"].type.length)
    if _field_name_taken(db, obj.id, name):
        raise HTTPException(status_code=400,
                            detail="Поле с таким именем уже существует в этом объекте")
    label = _text(field.label, label="Метка поля", limit=FLD_COLS["label"].type.length)
    field_type = _checked_field_type(field.field_type)
    if field_type == 'select' and not field.options:
        # Тот же инвариант, что в PATCH: список без вариантов — пустой select в форме.
        raise HTTPException(status_code=400,
                            detail="Для типа «список» укажите хотя бы один вариант")

    new_field = models.CustomFieldDef(
        object_type_id=obj.id, name=name, label=label,
        field_type=field_type, is_required=bool(field.is_required),
        options=_dump_options(field.options),
        position=field.position, tenant_id=writable_tenant_id(current_user)
    )
    db.add(new_field)
    db.commit()
    db.refresh(new_field)
    return {"id": new_field.id, "name": new_field.name, "label": new_field.label}


@router.patch("/fields/{field_id}")
def update_field(field_id: int, upd: FieldUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    """Правка поля: имя, метка, тип, обязательность, варианты списка, порядок."""
    field = _get_field_or_404(db, field_id, current_user)

    if 'name' in upd.model_fields_set:
        name = _system_name(upd.name, label="Системное имя поля",
                            limit=FLD_COLS["name"].type.length)
        # Имя поля — ключ в data записей этого типа (list_records), поэтому
        # дубликат в пределах объекта превращает правку в лотерею: create_record
        # строит field_map по name и второе поле с тем же именем просто
        # перестает получать значения.
        if _field_name_taken(db, field.object_type_id, name, exclude_id=field_id):
            raise HTTPException(status_code=400,
                                detail="Поле с таким именем уже существует в этом объекте")
        field.name = name
    if 'label' in upd.model_fields_set:
        field.label = _text(upd.label, label="Метка поля",
                            limit=FLD_COLS["label"].type.length)
    new_type = field.field_type
    if 'field_type' in upd.model_fields_set:
        new_type = _checked_field_type(upd.field_type)
        if new_type != field.field_type:
            used = _values_in_use(db, field_id)
            if used:
                raise HTTPException(status_code=400, detail=(
                    f"Нельзя сменить тип поля: у него уже есть значения в записях "
                    f"(строк: {used}). Либо удалите поле и создайте его заново "
                    f"с нужным типом, либо сначала очистите записи."))
        field.field_type = new_type
    if 'options' in upd.model_fields_set:
        field.options = _dump_options(upd.options)
    if new_type == 'select' and ('field_type' in upd.model_fields_set or 'options' in upd.model_fields_set):
        # Инвариант проверяется по ИТОГУ запроса: перевести поле в «список» без
        # вариантов можно, только прислав их в том же теле (или они уже лежат в
        # базе) — иначе клиент получил бы пустой выпадающий список.
        if not (field.options or '').strip() or field.options == '[]':
            raise HTTPException(status_code=400,
                                detail="Для типа «список» укажите хотя бы один вариант")
    if 'is_required' in upd.model_fields_set:
        if upd.is_required is None:
            raise HTTPException(status_code=400,
                                detail="Признак обязательности не может быть null: true или false")
        field.is_required = bool(upd.is_required)
    if 'position' in upd.model_fields_set:
        # null = «без порядка»: position сортирует список (list_fields), NULL в
        # SQLite уехал бы в начало, а 0 — тот же дефолт колонки при создании.
        field.position = 0 if upd.position is None else int(upd.position)

    db.commit()
    db.refresh(field)
    return _field_dict(field)


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user), _=_admin_only):
    field = _get_field_or_404(db, field_id, current_user)
    db.query(models.CustomFieldValue).filter(models.CustomFieldValue.field_def_id == field_id).delete()
    db.delete(field)
    db.commit()
    return {"message": "Удалено"}


# === ЗАПИСИ ===

@router.get("/objects/{obj_id}/records")
def list_records(obj_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    records = db.query(models.CustomRecord).filter(
        models.CustomRecord.object_type_id == obj_id,
        tenant_predicate(models.CustomRecord, current_user),
    ).order_by(models.CustomRecord.id.desc()).all()

    fields = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == obj_id,
        tenant_predicate(models.CustomFieldDef, current_user),
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
    obj = _get_object_or_404(db, obj_id, current_user)

    new_record = models.CustomRecord(
        object_type_id=obj_id, created_by=current_user.id,
        tenant_id=writable_tenant_id(current_user),
    )
    db.add(new_record)
    db.flush()

    fields = db.query(models.CustomFieldDef).filter(
        models.CustomFieldDef.object_type_id == obj_id,
        tenant_predicate(models.CustomFieldDef, current_user),
    ).all()
    field_map = {f.name: f for f in fields}

    for field_name, value in record.data.items():
        field = field_map.get(field_name)
        if not field:
            continue
        fv = models.CustomFieldValue(record_id=new_record.id, field_def_id=field.id)
        if field.field_type == 'select':
            # С-5: значение сверяется со списком вариантов поля, а не
            # кладётся как есть. Прежние полотна и сводки по этому полю
            # строились из объявленных вариантов, и неучтённое значение делало
            # запись невидимой для фильтров без единого признака ошибки.
            fv.value_text = (str(_check_select_option(field, value))
                             if value is not None else None)
        elif field.field_type == 'text':
            fv.value_text = str(value) if value is not None else None
        elif field.field_type in ('number', 'currency'):
            fv.value_number = _coerce_number(value, field)
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
    rec = _get_record_or_404(db, record_id, current_user)
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
