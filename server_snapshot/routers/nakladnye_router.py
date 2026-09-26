import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import models_tenant
import schemas
from auth import get_current_user, require_role
from database import get_db
from services.nakladnye import (
    commit_with_doc_key_guard,
    effective_amounts,
    ensure_amounts_consistent,
    nak_dict,
    parse_products,
)
from tenant_policy import (
    assign_tenant,
    get_tenant_object,
    tenant_query,
)

logger = logging.getLogger(__name__)

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.abspath(
    os.environ.get("CRM_UPLOADS_DIR")
    or os.path.join(os.environ.get("CRM_DATA_DIR") or _APP_DIR, "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
# Белый список Content-Type для отдачи фото. Отдельная карта, а не mimetypes:
# ответ отдаётся только как attachment с nosniff, поэтому значение, которое
# увидит браузер, обязано быть задано здесь, а не угадано по расширению.
PHOTO_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}
# Имя файла попадает в Content-Disposition, поэтому допускается только
# безопасный набор символов: кавычка или CRLF в имени — это header injection.
SAFE_PHOTO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
# Сколько строк накладных разбирается при проверке «это фото принадлежит мне».
# Запрос отсекается LIKE по photo_paths ещё в SQL, поэтому список короткий;
# предел нужен, чтобы кривой photo_paths не превращался в полный скан.
PHOTO_LOOKUP_LIMIT = 200
# Предел для bot/check-duplicate. Ветка «doc_number_norm IS NULL» нужна
# только для строк, записанных мимо ORM, и на боевой базе пуста (миграция
# 0005 пересчитывает ключи), поэтому предел не должен срабатывать.
DUP_SCAN_LIMIT = 2000

# Токен бота: совпадает с TELEGRAM_BOT_TOKEN в окружении.
# Эндпоинты с bot_auth не требуют JWT — бот не пользователь CRM.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Явный tenant для машинных bot-эндпоинтов (см. _bot_actor).
BOT_TENANT_ENV = "CRM_BOT_TENANT_ID"

router = APIRouter(
    prefix="/nakladnye",
    tags=["Накладные"],
)


def _bot_auth(request: Request) -> None:
    token = request.headers.get("X-Bot-Token", "")
    if not BOT_TOKEN or not token or not secrets.compare_digest(token, BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid bot token")
    return None


class _BotActor:
    """Машинный пользователь бота для tenant_policy.

    Бот аутентифицируется заголовком X-Bot-Token, а не JWT, поэтому у него нет
    строки в users и неоткуда взять tenant. Роль ``bot`` намеренно не равна
    ``superadmin``: бот не должен видеть чужие tenant'ы.
    """

    role = "bot"

    def __init__(self, tenant_id: int):
        self.tenant_id = int(tenant_id)


def _bot_actor(db: Session) -> _BotActor:
    """Tenant для bot-эндпоинтов или понятный отказ.

    Порядок определения:

    1. ``CRM_BOT_TENANT_ID`` — явная конфигурация развёртывания; значение
       проверяется по таблице tenants, чтобы опечатка в окружении не
       превращалась в запись в несуществующий tenant.
    2. Если переменной нет и в базе РОВНО один tenant — выбирать не из чего,
       конфигурация однозначна. Это не «дефолтный tenant»: при втором tenant
       путь закрывается.
    3. Иначе — отказ. Молча подставлять tenant нельзя: накладная уехала бы
       в чужую компанию, и заметить это можно было бы только на сверке.

    Раньше tenant не задавался вовсе, и INSERT с NULL отбивал триггер
    миграции 0018 — эндпоинт отдавал 500 на каждом приёме.
    """
    raw = os.environ.get(BOT_TENANT_ENV, "").strip()
    if raw:
        try:
            tenant_id = int(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"{BOT_TENANT_ENV} должен быть положительным целым числом",
            ) from exc
        exists = (
            db.query(models_tenant.Tenant.id)
            .filter(models_tenant.Tenant.id == tenant_id)
            .first()
        )
        if tenant_id <= 0 or exists is None:
            raise HTTPException(
                status_code=503,
                detail=f"Tenant {tenant_id} из {BOT_TENANT_ENV} не найден",
            )
        return _BotActor(tenant_id)

    ids = [row[0] for row in db.query(models_tenant.Tenant.id).limit(2).all()]
    if len(ids) == 1:
        logger.info(
            "bot endpoints: %s не задан, база содержит единственный tenant %s",
            BOT_TENANT_ENV, ids[0],
        )
        return _BotActor(int(ids[0]))
    raise HTTPException(
        status_code=503,
        detail=f"Tenant бота не настроен: задайте {BOT_TENANT_ENV}",
    )


def _photo_names(raw) -> tuple[list, bool]:
    """Разобрать photo_paths в список имён файлов.

    Возвращает ``(имена, разобрано_ли)``.

    Раньше здесь был ``except: pass`` / ``photos = []``: при битом значении
    photo_paths следующая загрузка фото переписывала поле одним новым файлом,
    и ранее сохранённые фото навсегда теряли ссылку (файлы оставались на диске
    без владельца). Молча затирать данные нельзя, поэтому поведение явное:

    * не-строки и мусорные элементы отбрасываются, потеря пишется в лог;
    * если верхний уровень JSON не список, разбор считается неудачным и
      вызывающий решает сам (см. _require_readable_photo_paths);
    * пустой photo_paths (NULL, "") — не поломка, а «фото не загружали».
    """
    if not raw:
        return [], True
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False
    if not isinstance(parsed, list):
        return [], False
    names, skipped = [], []
    for item in parsed:
        if isinstance(item, str) and item:
            names.append(item)
        else:
            skipped.append(item)
    if skipped:
        logger.warning(
            "nakladnye.photo_paths: отброшено %d непригодных значений (%r)",
            len(skipped), skipped[:5],
        )
    return names, True


def _require_readable_photo_paths(nak_id: int, raw) -> list:
    """photo_paths для операции, которая ПЕРЕЗАПИСЫВАЕТ список фото.

    Если поле не читается, дописывать в него нельзя: append перезаписал бы
    значение, которое мы не смогли прочитать, то есть стёр бы прежние фото.
    Поэтому такой запрос отклоняется с явным текстом, а оператор чинит запись
    вручную. Читающие пути (удаление, отдача файла) падать не должны.
    """
    names, ok = _photo_names(raw)
    if not ok:
        logger.error(
            "nakladnye id=%s: photo_paths не разобран (%r) — запись фото отклонена",
            nak_id, raw,
        )
        raise HTTPException(
            status_code=500,
            detail=("Список фото накладной повреждён и не может быть дополнен. "
                    "Запись нужно проверить вручную."),
        )
    return names


def _photo_file_in_uploads(filename: str) -> Optional[str]:
    """Абсолютный путь файла фото внутри UPLOAD_DIR или None.

    basename отсекает ``../`` и абсолютные пути, но не защищает от
    симлинка/вложенного каталога, поэтому результат сравнивается с
    realpath(UPLOAD_DIR). Вложенные каталоги не используются: все имена
    фото — плоские ``nak<id>_<hex><ext>``.
    """
    if not filename or filename != os.path.basename(filename):
        return None
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return None
    if not SAFE_PHOTO_NAME.match(filename):
        return None
    if os.path.splitext(filename)[1].lower() not in ALLOWED_IMAGE_EXT:
        return None
    root = os.path.realpath(UPLOAD_DIR)
    real = os.path.realpath(os.path.join(UPLOAD_DIR, filename))
    if os.path.dirname(real) != root or not os.path.isfile(real):
        return None
    return real


def _liked_photo_name(filename: str) -> str:
    """Экранировать имя файла для LIKE-префильтра."""
    escaped = filename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return '%"' + escaped + '"%'


def _photo_belongs_to_visible_nakladnaya(db: Session, filename: str, actor) -> bool:
    """Есть ли у доступных actor'у накладных этот файл в photo_paths.

    Только такой файл отдаётся. Раньше ручка отдавала ЛЮБОЙ файл из
    UPLOAD_DIR любому авторизованному пользователю: это IDOR (фото чужой
    накладной, в т.ч. чужого tenant) и Stored XSS — без
    Content-Disposition браузер рендерил содержимое inline, а HTML/SVG,
    загруженный как фото, выполнялся на домене CRM.
    """
    rows = (
        tenant_query(db, models.Nakladnaya, actor)
        .filter(models.Nakladnaya.photo_paths.isnot(None))
        .filter(models.Nakladnaya.photo_paths.like(_liked_photo_name(filename), escape="\\"))
        .limit(PHOTO_LOOKUP_LIMIT)
        .all()
    )
    for row in rows:
        names, _ok = _photo_names(row.photo_paths)
        for name in names:
            if os.path.basename(name) == filename:
                return True
    return False


# ============================================================
# CRM endpoints (JWT auth)
# ============================================================


@router.get("", dependencies=[Depends(get_current_user)])
def list_nakladnye(
    store: Optional[str] = None,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        # tenant_query, а не голый db.query: список накладных — tenant-owned
        # данные, и без фильтра пользователь видел документы других компаний.
        query = tenant_query(db, models.Nakladnaya, current_user)

        if store:
            query = query.filter(models.Nakladnaya.store == store)
        if doc_type:
            query = query.filter(models.Nakladnaya.doc_type == doc_type)
        if status:
            query = query.filter(models.Nakladnaya.status == status)
        if supplier_id:
            query = query.filter(models.Nakladnaya.supplier_id == supplier_id)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (models.Nakladnaya.supplier_name.ilike(like))
                | (models.Nakladnaya.doc_number.ilike(like))
                | (models.Nakladnaya.doc_series.ilike(like))
            )

        rows = query.order_by(models.Nakladnaya.created_at.desc()).limit(5000).all()
        result = [nak_dict(r) for r in rows]

        if date_from or date_to:
            filtered = []
            for r in result:
                d = r.get("doc_date") or ""
                if date_from and d < date_from:
                    continue
                if date_to and d > date_to:
                    continue
                filtered.append(r)
            result = filtered

        return result
    finally:
        db.close()


@router.post("", dependencies=[Depends(get_current_user)])
def create_nakladnaya(
    payload: schemas.NakladnayaCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:

        data = payload.model_dump(exclude_unset=True)
        if payload.supplier_id and not data.get("supplier_name"):
            sup = (
                tenant_query(db, models.Supplier, current_user)
                .filter(models.Supplier.id == payload.supplier_id)
                .first()
            )
            if sup:
                data["supplier_name"] = sup.name

        # Serialize products list to JSON text
        products = data.pop("products", None)
        if products:
            data["products_json"] = json.dumps(products, ensure_ascii=False)

        # assign_tenant, а не tenant_id=current_user.tenant_id: у superadmin
        # tenant_id IS NULL, и такой INSERT отбивал триггер миграции 0018 —
        # обычное создание отдавало 500. Теперь это 403 «Tenant пользователя
        # не настроен»: неполная настройка видна сразу и не пишет запись
        # ни в чей tenant.
        nak = assign_tenant(models.Nakladnaya(**data), current_user)
        db.add(nak)
        # дефект 7: уникальность гарантирует индекс uq_nakladnye_doc_key, а не
        # проверка перед вставкой. Значения ключа читаются ДО commit — после
        # rollback объект разобран и его атрибуты недоступны.
        commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number, nak.tenant_id)
        db.refresh(nak)
        return nak_dict(nak)
    finally:
        db.close()


@router.patch("/{nak_id}", dependencies=[Depends(get_current_user)])
def update_nakladnaya(
    nak_id: int,
    updates: schemas.NakladnayaUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        nak = get_tenant_object(
            db, models.Nakladnaya, nak_id, current_user,
            detail="Накладная не найдена",
        )

        update_data = updates.model_dump(exclude_unset=True)
        # дефект 11: НДС не больше суммы документа — по эффективным значениям,
        # потому что схема видит только присланные поля (см. ensure_amounts_consistent)
        ensure_amounts_consistent(*effective_amounts(nak, update_data))
        if update_data.get("supplier_id"):
            sup = (
                tenant_query(db, models.Supplier, current_user)
                .filter(models.Supplier.id == update_data["supplier_id"])
                .first()
            )
            if sup and "supplier_name" not in update_data:
                nak.supplier_name = sup.name

        # Serialize products
        products = update_data.pop("products", None)
        if products is not None:
            nak.products_json = json.dumps(products, ensure_ascii=False) if products else None

        for key, value in update_data.items():
            setattr(nak, key, value)
        nak.updated_at = datetime.now(timezone.utc)

        # дефект 7: правка серии/номера может увести запись на занятый ключ —
        # индекс это поймает, а guard переведёт в 400 вместо 500
        commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number, nak.tenant_id)
        db.refresh(nak)
        return nak_dict(nak)
    finally:
        db.close()


# V11 (пункт 17 плана v2): удаление входящей накладной необратимо — вместе со
# строкой стираются её фото с диска (основание приёма товара). Создание и правка
# остаются операционными (склад и бот работают без админа), удаление — админское.
# Ни один фронт этот роут не зовёт (grep по mockups/, шаблонам v2 и site/js —
# 0 обращений), поэтому гейт не ломает существующие сценарии.
@router.delete("/{nak_id}",
               dependencies=[Depends(require_role("admin", "superadmin"))])
def delete_nakladnaya(
    nak_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        nak = get_tenant_object(
            db, models.Nakladnaya, nak_id, current_user,
            detail="Накладная не найдена",
        )

        # Читающий путь: битое photo_paths не должно блокировать удаление
        # накладной. Непрочитанные имена логируются (файлы останутся на диске
        # сиротами — их уборка безопаснее вручную, чем догадка по имени).
        names, ok = _photo_names(nak.photo_paths)
        if nak.photo_paths and not ok:
            logger.warning(
                "nakladnye id=%s удаляется: photo_paths не разобран (%r), "
                "файлы с диска не тронуты", nak.id, nak.photo_paths,
            )
        for p in names:
            full = os.path.join(UPLOAD_DIR, os.path.basename(p))
            if os.path.isfile(full):
                os.remove(full)

        db.delete(nak)
        db.commit()
        return {"detail": "Накладная удалена"}
    finally:
        db.close()


@router.post("/{nak_id}/photos", dependencies=[Depends(get_current_user)])
async def upload_nakladnaya_photo(
    nak_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        nak = get_tenant_object(
            db, models.Nakladnaya, nak_id, current_user,
            detail="Накладная не найдена",
        )

        fname = (file.filename or "photo.jpg").strip()
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ALLOWED_IMAGE_EXT:
            raise HTTPException(status_code=400, detail=f"Допустимые форматы: {', '.join(ALLOWED_IMAGE_EXT)}")

        safe_name = f"nak{nak.id}_{uuid.uuid4().hex[:8]}{ext}"
        dest = os.path.join(UPLOAD_DIR, safe_name)

        # Список фото читаем ДО записи файла: при отказе иначе остался бы
        # сирота на диске, на который никто не ссылается.
        photos = _require_readable_photo_paths(nak.id, nak.photo_paths)

        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")

        # Запись уводим в поток: блокирующий open() внутри async-хендлера держал
        # весь event loop на время записи до 25 МБ, а server.py поднимает два
        # воркера — на это время вставали бы все остальные запросы.
        await run_in_threadpool(Path(dest).write_bytes, content)

        photos.append(safe_name)
        nak.photo_paths = json.dumps(photos)
        nak.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(nak)
        return nak_dict(nak)
    finally:
        db.close()


@router.get("/photos/{filename}", dependencies=[Depends(get_current_user)])
def serve_nakladnaya_photo(
    filename: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Отдать фото накладной, доступной текущему пользователю.

    FIX 2026-09-26: ручка отдавала ЛЮБОЙ файл из UPLOAD_DIR по имени, без
    проверки владельца и tenant (IDOR) и без Content-Disposition, то есть
    браузер рендерил содержимое inline — загруженный HTML/SVG выполнялся
    на домене CRM (Stored XSS). Аналогичная ручка GET /files/{filename}
    в card_details_router.py удалена как IDOR 2026-09-06.

    Теперь файл отдаётся только если его имя встречается в photo_paths
    накладной, видимой пользователю; ответ уходит как вложение с
    nosniff и явным image-Content-Type. Отсутствующий и чужой файл дают
    одинаковый 404.
    """
    try:
        full = _photo_file_in_uploads(filename)
        if not full:
            raise HTTPException(status_code=404, detail="Файл не найден")
        if not _photo_belongs_to_visible_nakladnaya(db, filename, current_user):
            raise HTTPException(status_code=404, detail="Файл не найден")
        return FileResponse(
            full,
            media_type=PHOTO_CONTENT_TYPES[os.path.splitext(filename)[1].lower()],
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        db.close()


@router.get("/{nak_id}/excel", dependencies=[Depends(get_current_user)])
def get_nakladnaya_excel(
    nak_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Генерирует и скачивает Excel для одной накладной."""
    try:
        # Проверка доступа идёт ДО импорта openpyxl: чужой накладной должен
        # отвечать 404 независимо от того, установлен ли необязательный
        # пакет, иначе порядок проверок превращал бы чужой объект в 500.
        nak = get_tenant_object(
            db, models.Nakladnaya, nak_id, current_user,
            detail="Накладная не найдена",
        )
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as e:
        raise HTTPException(status_code=500, detail="openpyxl не установлен") from e

    try:
        products = parse_products(nak)

        wb = Workbook()
        ws = wb.active
        ws.title = "Товары"

        # Шапка накладной
        info = [
            ("Поставщик", nak.supplier_name or ""),
            ("Тип", nak.doc_type or ""),
            ("Серия", nak.doc_series or ""),
            ("Номер", nak.doc_number or ""),
            ("Дата", nak.doc_date or ""),
            ("Сумма с НДС", float(nak.amount) if nak.amount else ""),
            ("НДС", float(nak.vat_amount) if nak.vat_amount else ""),
            ("Магазин", nak.store or ""),
        ]
        bold = Font(bold=True)
        for i, (label, val) in enumerate(info, 1):
            ws.cell(row=i, column=1, value=label).font = bold
            ws.cell(row=i, column=2, value=val)

        # Таблица товаров
        row_start = len(info) + 2
        headers = ["Товар", "Кол-во", "Ед.", "Цена без НДС", "Сумма без НДС"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=row_start, column=col, value=h)
            cell.font = bold
            cell.alignment = Alignment(horizontal="center")

        for i, p in enumerate(products, row_start + 1):
            qty = p.get("qty") or 0
            price = p.get("price_no_vat") or 0
            total = round(qty * price, 2)
            ws.cell(row=i, column=1, value=p.get("name", ""))
            ws.cell(row=i, column=2, value=qty)
            ws.cell(row=i, column=3, value=p.get("unit", ""))
            ws.cell(row=i, column=4, value=price)
            ws.cell(row=i, column=5, value=total)

        for col in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        fname = f"nak_{nak.doc_type or 'doc'}_{nak.doc_series or ''}{nak.doc_number or nak.id}.xlsx"
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        db.close()


@router.get("/export/products-excel", dependencies=[Depends(get_current_user)])
def export_products_excel(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    store: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Экспорт товаров из накладных в Excel."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError as e:
        raise HTTPException(status_code=500, detail="openpyxl не установлен") from e

    try:
        query = (
            tenant_query(db, models.Nakladnaya, current_user)
            .filter(models.Nakladnaya.products_json.isnot(None))
        )
        if store:
            query = query.filter(models.Nakladnaya.store == store)
        rows = query.order_by(models.Nakladnaya.doc_date.desc()).all()

        wb = Workbook()
        ws = wb.active
        ws.title = "Товары"
        headers = ["Дата", "Поставщик", "Тип", "Серия", "№ накладной", "Магазин",
                    "Товар", "Кол-во", "Ед.", "Цена без НДС", "Сумма без НДС"]
        header_font = Font(bold=True)
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        row_num = 2
        for nak in rows:
            d = nak.doc_date or ""
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
            products = parse_products(nak)
            for p in products:
                qty = p.get("qty") or 0
                price = p.get("price_no_vat") or 0
                total = round(qty * price, 2)
                ws.cell(row=row_num, column=1, value=nak.doc_date or "")
                ws.cell(row=row_num, column=2, value=nak.supplier_name or "")
                ws.cell(row=row_num, column=3, value=nak.doc_type or "")
                ws.cell(row=row_num, column=4, value=nak.doc_series or "")
                ws.cell(row=row_num, column=5, value=nak.doc_number or "")
                ws.cell(row=row_num, column=6, value=nak.store or "")
                ws.cell(row=row_num, column=7, value=p.get("name", ""))
                ws.cell(row=row_num, column=8, value=qty)
                ws.cell(row=row_num, column=9, value=p.get("unit", ""))
                ws.cell(row=row_num, column=10, value=price)
                ws.cell(row=row_num, column=11, value=total)
                row_num += 1

        for col in ws.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=nakladnye_products.xlsx"},
        )
    finally:
        db.close()


# ============================================================
# Bot endpoints (bot token auth, no JWT)
# ============================================================


@router.post("/bot/create", dependencies=[Depends(_bot_auth)])
def bot_create_nakladnaya(payload: schemas.NakladnayaCreate, db: Session = Depends(get_db)):
    # FIX 2026-09-26: tenant не задавался, INSERT с NULL отбивал триггер
    # миграции 0018 — приём накладной ботом отдавал 500 на каждом документе.
    # Tenant бота определяется явно (_bot_actor) и не угадывается.
    actor = _bot_actor(db)

    data = payload.model_dump(exclude_unset=True)
    if payload.supplier_id:
        sup = (
            tenant_query(db, models.Supplier, actor)
            .filter(models.Supplier.id == payload.supplier_id)
            .first()
        )
        if sup and not data.get("supplier_name"):
            data["supplier_name"] = sup.name

    products = data.pop("products", None)
    if products:
        data["products_json"] = json.dumps(products, ensure_ascii=False)

    nak = models.Nakladnaya(**data, tenant_id=actor.tenant_id, created_by_bot=True)
    db.add(nak)
    # FIX 2026-09-12 (Фаза 2, дефект 7): приём стал атомарным.
    #
    # Раньше бот делал два отдельных запроса — GET /bot/check-duplicate и
    # POST /bot/create, — а этот эндпоинт не проверял дубли вовсе. Между
    # проверкой и вставкой повторная отправка того же документа (сеть
    # не ответила, оператор нажал ещё раз, магазин прислал фото дважды)
    # плодила вторую запись: обе потом висели в сверке с поставщиком.
    # Проверкой перед INSERT гонка не закрывается в принципе, поэтому
    # уникальность обеспечивает индекс uq_nakladnye_doc_key (миграция 0005),
    # а guard переводит нарушение в 400 с id существующей записи.
    #
    # GET /bot/check-duplicate оставлен как есть: бот по-прежнему спрашивает
    # его, чтобы дописать фото и товары к существующей накладной вместо
    # создания новой. Он намеренно сравнивает сырые значения в Python, а не
    # читает *_norm — тогда его ответ не зависит от того, заполнены ли
    # производные колонки у старых строк.
    commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number, nak.tenant_id)
    db.refresh(nak)
    return nak_dict(nak)


@router.patch("/bot/{nak_id}", dependencies=[Depends(_bot_auth)])
def bot_update_nakladnaya(nak_id: int, updates: schemas.NakladnayaUpdate, db: Session = Depends(get_db)):
    nak = get_tenant_object(
        db, models.Nakladnaya, nak_id, _bot_actor(db),
        detail="Накладная не найдена",
    )

    update_data = updates.model_dump(exclude_unset=True)
    # дефект 11: то же правило, что и в CRM PATCH /nakladnye/{id}
    ensure_amounts_consistent(*effective_amounts(nak, update_data))
    products = update_data.pop("products", None)
    if products is not None:
        nak.products_json = json.dumps(products, ensure_ascii=False) if products else None

    for key, value in update_data.items():
        setattr(nak, key, value)
    nak.updated_at = datetime.now(timezone.utc)

    # дефект 7: то же, что в CRM PATCH — правка серии/номера не должна
    # уводить запись на уже занятый ключ
    commit_with_doc_key_guard(db, nak.doc_series, nak.doc_number, nak.tenant_id)
    db.refresh(nak)
    return nak_dict(nak)


@router.post("/bot/{nak_id}/photos", dependencies=[Depends(_bot_auth)])
async def bot_upload_photo(
    nak_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    nak = get_tenant_object(
        db, models.Nakladnaya, nak_id, _bot_actor(db),
        detail="Накладная не найдена",
    )

    fname = (file.filename or "photo.jpg").strip()
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        ext = ".jpg"

    safe_name = f"nak{nak.id}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(UPLOAD_DIR, safe_name)

    # Как и в upload_nakladnaya_photo: список читается до записи файла.
    photos = _require_readable_photo_paths(nak.id, nak.photo_paths)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")

    # См. комментарий в upload_nakladnaya_photo: не блокируем event loop.
    await run_in_threadpool(Path(dest).write_bytes, content)

    photos.append(safe_name)
    nak.photo_paths = json.dumps(photos)
    nak.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(nak)
    return nak_dict(nak)


@router.get("/bot/check-duplicate", dependencies=[Depends(_bot_auth)])
def bot_check_duplicate(
    doc_series: Optional[str] = None,
    doc_number: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Есть ли уже принятая накладная с таким ключом документа.

    FIX 2026-09-26: было ``db.query(models.Nakladnaya).all()`` — вся таблица
    грузилась в память на каждый вызов бота, и заодно проверка не была
    ограничена tenant'ом. Теперь выборка tenant-scoped и ограничена:

    * основная ветка идёт по doc_number_norm — это то самое значение, на
      котором построен уникальный индекс uq_nakladnye_doc_key, то есть
      вопрос «есть ли такой дубль» база уже умеет отвечать быстро;
    * ветка ``doc_number_norm IS NULL`` сохранена, потому что сравнение с
      сырым значением не зависит от заполненности производных колонок у
      старых строк (миграция 0005 их пересчитывает, но запись мимо ORM
      оставила бы ключ пустым). На боевой базе эта ветка пуста, поэтому
      предел DUP_SCAN_LIMIT служит страховкой от вырожденной таблицы, а не
      рабочим ограничением.
    """
    if not doc_number:
        return {"duplicate": False}
    norm_num = "".join(ch for ch in doc_number if ch.isdigit())
    norm_ser = (doc_series or "").strip().upper()
    if not norm_num:
        return {"duplicate": False}

    candidates = (
        tenant_query(db, models.Nakladnaya, _bot_actor(db))
        .filter(models.Nakladnaya.doc_number.isnot(None))
        .filter(or_(
            models.Nakladnaya.doc_number_norm == norm_num,
            models.Nakladnaya.doc_number_norm.is_(None),
        ))
        .order_by(models.Nakladnaya.id.desc())
    )
    rows = candidates.limit(DUP_SCAN_LIMIT + 1).all()
    if len(rows) > DUP_SCAN_LIMIT:
        logger.warning(
            "bot/check-duplicate: кандидатов больше %d (ключ %r/%r) — "
            "сравнение могло пропустить дубль; уникальный индекс убережёт",
            DUP_SCAN_LIMIT, norm_ser, norm_num,
        )
        rows = rows[:DUP_SCAN_LIMIT]
    for r in rows:
        r_num = "".join(ch for ch in (r.doc_number or "") if ch.isdigit())
        r_ser = (r.doc_series or "").strip().upper()
        if r_num == norm_num and r_ser == norm_ser:
            return {"duplicate": True, "id": r.id}
    return {"duplicate": False}
