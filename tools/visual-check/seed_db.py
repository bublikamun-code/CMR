#!/usr/bin/env python3
"""Детерминированная сид-БД для скриншот-регрессии (tools/visual-check).

Зачем: пиксельное сравнение имеет смысл только когда каждый прогон видит
одинаковое содержимое. Скрины с боевой БД флейкят от любых живых данных,
поэтому регрессия гоняется на полностью сгенерированной базе:

  * схема — tests/fixtures/prod_schema.sql + migrations/ (та же логика,
    что в tests/conftest.py: DDL прода, потом идемпотентные миграции);
  * данные — фиксированные id и абсолютные даты, без now()/relative.

Запуск (создаёт tmp/visual-seed/crm_app.db, перезаписывая старую):

    python3 tools/visual-check/seed_db.py

Выход 0 — база готова. Путь печатается в stdout последней строкой.

Креды для входа в UI:
  * visual_admin / VisualPass123! — роль admin;
  * Иванов Иван / VisualPass123! — роль manager.

Часовой пояс: в Python все отметки времени создаются aware UTC (см.
FIXED_NOW) — конвенция проекта и ruff-гейт DTZ. Диалект SQLite при
записи сбрасывает tzinfo, поэтому в строках БД(offset-суффикса нет) —
тот же результат, что дают ORM-дефолты models.py у живого приложения.
Часовой пояс отображения пинится в браузере раннером скриншотов.
"""

import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "tmp" / "visual-seed"
DB_PATH = OUT_DIR / "crm_app.db"

# Замороженные часы регрессии: «сегодня» для скринов — 2026-09-12, все
# события живут в сентябре 2026, чтобы календарь открывался на сентябре.
# tzinfo=UTC обязателен: конвенция проекта — aware UTC (миграция 0003
# нормализует именно к «+00:00», models.py пишет aware по умолчанию), а
# наивный datetime в SQLite-строке ломает лексикографическую сортировку
# рядом с aware-строками.
FIXED_NOW = datetime(2026, 9, 10, 8, 0, 0, tzinfo=timezone.utc)

ADMIN_USERNAME = "visual_admin"
ADMIN_PASSWORD = "VisualPass123!"  # noqa: S105 — фиксированные креды
# скриншот-регрессии, не секрет (см. docstring).
MANAGER_USERNAME = "Иванов Иван"

SCHEMA_SQL = ROOT / "tests" / "fixtures" / "prod_schema.sql"
MIGRATIONS_DIR = ROOT / "migrations"

# В каталоге миграций есть __init__.py — в перечень попадают только NNNN_*.py.
_MIGRATION_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")
# sqlite_sequence — внутренняя таблица SQLite, руками её создавать нельзя
# (регэксп скопирован из tests/conftest.py).
_SQLITE_SEQUENCE = re.compile(
    r"CREATE TABLE sqlite_sequence\s*\([^)]*\)\s*;", re.IGNORECASE
)


def _env():
    """Окружение ДО импорта database: engine и URL строятся на импорте модуля
    от CRM_DATA_DIR (database.py) — иначе скрипт писал бы в базу в корне
    репозитория, а auth.py молча создал бы .secret_key рядом с собой."""
    os.environ["CRM_DATA_DIR"] = str(OUT_DIR)
    os.environ["CRM_SECRET_KEY"] = "visual-regression-secret"  # noqa: S105
    os.environ["CRM_CRON_TOKEN"] = "visual-cron-token"  # noqa: S105
    # nakladnye_router читает токен бота на импорте; этот скрипт роутеры не
    # импортирует, но переменная стоит для симметрии с боевым запуском.
    os.environ["TELEGRAM_BOT_TOKEN"] = "visual-bot-token"  # noqa: S105
    # Значения выше — фиксированные заглушки для детерминированного
    # сид-прогона (аналог test-*-token в tests/conftest.py), не секреты.
    # Сервер создаёт эти каталоги при старте — создаём заранее, чтобы сид
    # не зависел от того, поднимался ли уже uvicorn.
    os.makedirs(OUT_DIR / "uploads", exist_ok=True)
    os.makedirs(OUT_DIR / "tenants", exist_ok=True)
    # Перезапись: гасим не только базу, но и её WAL/SHM. Осиротевший -wal
    # от прошлого прогона может воскресить удалённые страницы, и «чистый»
    # перегон молча получил бы смесь старых и новых данных.
    for stale in (DB_PATH,
                  Path(str(DB_PATH) + "-wal"),
                  Path(str(DB_PATH) + "-shm")):
        if stale.exists():
            stale.unlink()


# Вызов на импорте обязателен: переменные окружения должны стоять ДО
# `import database` ниже. Вызов из main() идемпотентен (makedirs exist_ok,
# старой базы к этому моменту уже нет).
_env()

# Импорты приложения — строго после _env(): engine привязан к CRM_DATA_DIR
# на импорте модуля. models_tenant нужен ради побочного эффекта — Tenant
# объявлен на том же Base, что и models, без импорта таблицы tenants не
# регистрируется в metadata и flush падает с NoReferencedTableError
# (тот же приём, что в tests/conftest.py и main.py).
import auth
import database
import models
import models_tenant  # noqa: F401


def _apply_schema():
    """Схема: DDL прода, затем миграции — зеркально tests/conftest.py
    (_apply_prod_schema + _apply_migrations), но БЕЗ create_all: прод-дамп
    уже содержит все таблицы моделей, недостающее доезжает миграциями.

    executescript — потому что statements много, а SQLAlchemy по умолчанию
    не исполняет пакетные SQL-скрипты. DDL и миграции идут через одну
    raw-сессию и фиксируются одним commit().
    """
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    sql = _SQLITE_SEQUENCE.sub("", sql)
    raw = database.engine.raw_connection()
    try:
        raw.executescript(sql)
        cur = raw.cursor()
        files = sorted(
            p for p in MIGRATIONS_DIR.iterdir()
            if _MIGRATION_RE.match(p.name)
        ) if MIGRATIONS_DIR.exists() else []
        for path in files:
            spec = importlib.util.spec_from_file_location(
                "crm_migration_" + path.stem, str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "up", None)):
                raise TypeError(f"{path}: нет функции up(cur)")
            mod.up(cur)
        raw.commit()
    finally:
        raw.close()


def _seed():
    """Данные: фиксированные id и абсолютные даты сентября 2026.

    id карточек/транзакций/задач/накладных назначаются ЯВНО — порядок строк
    на любом прогоне один и тот же (у автоинкрементных таблиц SQLite
    подтягивает sqlite_sequence до максимума явных id). Один commit в конце:
    база либо видна целиком, либо не видна вовсе.
    """
    s = database.SessionLocal()
    try:
        # --- пользователи ----------------------------------------------
        admin = models.User(
            id=1, username=ADMIN_USERNAME,
            hashed_password=auth.get_password_hash(ADMIN_PASSWORD),
            role="admin", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        manager = models.User(
            id=2, username=MANAGER_USERNAME,
            # Пароль тот же: под менеджером тоже приходится логиниться,
            # чтобы снять скрины вида «задачи только мои».
            hashed_password=auth.get_password_hash(ADMIN_PASSWORD),
            role="manager", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        s.add_all([admin, manager])

        # --- клиенты (правдоподобные минские реквизиты) ------------------
        vesnatorg = models.Client(
            id=1, name="ООО «Веснаторг»", unp="190111222",
            phone="+375 29 611-22-33", email="info@vesnatorg.by",
            address="г. Минск, ул. Тимирязева, д. 65",
            contact_person="Петрова Анна", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        kovalev = models.Client(
            id=2, name="ИП Ковалёв С.М.", unp="291333444",
            phone="+375 33 455-67-89", email="kovalev.sm@mail.ru",
            address="г. Минск, ул. Одинцова, д. 42",
            contact_person="Ковалёв Сергей", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        svetlogorsk = models.Client(
            id=3, name="ЗАО «СветлогорскДом»", unp="100555666",
            phone="+375 17 302-45-67", email="zakaz@svetlogorskomdom.by",
            address="г. Минск, пр-т Дзержинского, д. 104",
            contact_person="Лукашевич Ирина", note="Отсрочка 5 дней",
            tenant_id=None, created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        remontcity = models.Client(
            id=4, name="ООО «РемонтСити»", unp="191777888",
            phone="+375 29 777-31-22", email="dima@remontcity.by",
            address="г. Минск, ул. Кошевого, д. 8",
            contact_person="Гончарук Дмитрий", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        s.add_all([vesnatorg, kovalev, svetlogorsk, remontcity])

        # --- поставщики ---------------------------------------------------
        lyustra = models.Supplier(
            id=1, name="ОДО «Люстра Опт»", unp="191222333",
            phone="+375 29 123-45-67", email="opt@lyustra-opt.by",
            address="г. Минск, ул. Кальварийская, д. 17",
            contact_person="Савицкий Игорь", tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        armatura = models.Supplier(
            id=2, name="ООО «Арматура Плюс»", unp="191444555",
            phone="+375 17 502-33-11", email="sales@armatura-plus.by",
            address="г. Минск, ул. Багратиона, д. 54",
            contact_person="Мельник Ольга", note="Работаем по предоплате",
            tenant_id=None, created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        s.add_all([lyustra, armatura])

        # --- теги ----------------------------------------------------------
        tag_svet = models.Tag(id=1, name="светильники", color="#3b82f6",
                              tenant_id=None)
        tag_opt = models.Tag(id=2, name="опт", color="#10b981",
                             tenant_id=None)
        s.add_all([tag_svet, tag_opt])

        # --- группа списания (карточка 7 живёт в ней) ----------------------
        # client_id, а не client: у WriteoffGroup нет relationship к Client
        # (только колонка client_id в models.py) — id клиента уже известен.
        wgroup = models.WriteoffGroup(
            id=1, name="Списание Веснаторг сентябрь", client_id=vesnatorg.id,
            store_location="Матусевича", total_amount=1500.00,
            invoice_number="ВТ-0091", invoice_date="2026-09-05",
            written_off=False, tenant_id=None,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        )
        s.add(wgroup)

        # --- карточки: по колонкам канбана, position 0,1,2… внутри колонки --
        # Общие поля всех сделок вынесены сюда; отклонения — в самих строках.
        def card(cid, title, status, total, paid, pay_status,
                 due_day, store=None, owner=None, client=None,
                 position=0, writeoff_group=None):
            return models.Card(
                id=cid, title=title, status=status,
                description="Внутренние работы по сделке",
                total_amount=total, paid_amount=paid,
                payment_status=pay_status,
                payment_due_date=date(2026, 9, due_day),
                due_date=date(2026, 9, due_day),
                store_location=store, owner=owner, client=client,
                priority=0, position=position, is_deleted=False,
                sender_email=None, tenant_id=None,
                writeoff_group=writeoff_group,
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            )

        cards = [
            card(1, "Освещение склада Веснаторг", "Новый запрос",
                 3850.00, 0, "Не оплачен", 18, "Матусевича",
                 owner=manager, client=vesnatorg, position=0),
            card(2, "Светильники для офиса РемонтСити", "Новый запрос",
                 1200.50, 0, "Не оплачен", 22, "Богдановича",
                 client=remontcity, position=1),
            card(3, "БРА-светильники для Ковалёва", "В работе",
                 5400.00, 1500, "Частично", 15, "Домбровская",
                 owner=manager, client=kovalev, position=0),
            card(4, "Освещение подъезда СветлогорскДом", "В работе",
                 8900.00, 0, "Отсрочка", 25, "Матусевича",
                 client=svetlogorsk, position=1),
            card(5, "Прожекторы для Веснаторг", "Ждет оплаты",
                 2200.00, 2200, "Оплачен", 8, "БН",
                 client=vesnatorg, position=0),
            card(6, "LED-ленты для офиса РемонтСити", "Сборка",
                 3100.00, 500, "Частично", 13, "Богдановича",
                 owner=manager, client=remontcity, position=0),
            # «На списание»/«Закрыто» — не колонки канбана, а табы Финансов,
            # но статус хранится тот же, что и у доски.
            card(7, "Точечные светильники Веснаторг", "На списание",
                 1500.00, 1500, "Оплачен", 5,
                 client=vesnatorg, position=0, writeoff_group=wgroup),
            card(8, "УФ-лампа Ковалёв (закрыта)", "Закрыто",
                 760.00, 760, "Оплачен", 1,
                 client=kovalev, position=0),
            # Просроченная относительно 12.09 — красная точка в календаре.
            card(9, "Аварийные светильники РемонтСити", "В работе",
                 4400.00, 0, "Не оплачен", 3,
                 client=remontcity, position=2),
        ]
        s.add_all(cards)
        # Тег «светильники» — на паре карточек колонки «Новый запрос».
        cards[0].tags.append(tag_svet)
        cards[1].tags.append(tag_svet)

        # flush (НЕ commit: единственный commit остаётся в самом конце, база
        # для внешних читателей всё ещё атомарна). Зачем: задачи и уведомления
        # ниже ссылаются на users/cards/clients ГОЛЫМИ id (creator_id,
        # card_id, client_id, user_id), а unit of work SQLAlchemy
        # упорядочивает вставки только по relationship-связям — у Notification
        # и Task к Card/User таких связей нет, поэтому без flush INSERT
        # notifications приезжал бы раньше INSERT users и падал на FK.
        s.flush()

        # --- чек-лист карточки 3: закупка у поставщика двумя частями --------
        s.add_all([
            models.CardChecklist(
                company_name="ИП Ковалёв С.М.", amount=1500.00,
                is_paid=True, is_secondary_check=False, note="аванс",
                supplier_id=None, card=cards[2],
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            ),
            models.CardChecklist(
                company_name="ИП Ковалёв С.М.", amount=3900.00,
                is_paid=False, is_secondary_check=False,
                note="остаток после поставки",
                supplier_id=None, card=cards[2],
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            ),
        ])

        # --- транзакции реестра оплат --------------------------------------
        # Общие флаги: «сырая» оплата без счёт-фактуры и списания. Уникальный
        # индекс uq_remainder_per_card допускает одну такую запись на сделку
        # — у нас каждая транзакция на своей карточке, конфликтов нет.
        def tx(tid, card_obj, company, amount, store, day,
               is_document=False, **kw):
            return models.Transaction(
                id=tid, card=card_obj, company_name=company,
                amount=amount, store_location=store,
                # +00:00 — aware UTC, как пишет живое приложение (models default).
                date=datetime.fromisoformat(f"2026-09-{day:02d}T10:30:00+00:00"),
                is_calculated=False, is_invoice_issued=False,
                is_secondary_check=False, is_document=is_document,
                is_bill_doc=False, is_warehouse_writeoff=False,
                print_status="Печать", invoice_number=None,
                invoice_date=None, tenant_id=None,
                created_at=FIXED_NOW, updated_at=FIXED_NOW, **kw,
            )

        s.add_all([
            tx(1, cards[4], "ООО «Веснаторг»", 2200.00, "БН", 7),
            tx(2, cards[2], "ИП Ковалёв С.М.", 1500.00, "Матусевича", 9),
            tx(3, cards[5], "ООО «РемонтСити»", 500.00, "Богдановича", 10),
            # Документ (таб «Документы» в Финансах): уходит из реестра оплат.
            tx(4, cards[7], "ИП Ковалёв С.М.", 760.00, "Домбровская", 2,
               is_document=True, note="закрывающие"),
            # Списание со склада: привязано к группе, помечено исполненным.
            tx(5, cards[6], "ООО «Веснаторг»", 1500.00, "Матусевича", 5,
               writeoff_group=wgroup, is_written_off=True),
            tx(6, cards[3], "ЗАО «СветлогорскДом»", 3000.00, "Матусевича", 14),
            tx(7, cards[8], "ООО «РемонтСити»", 1200.00, "БН", 16),
        ])

        # --- задачи ---------------------------------------------------------
        def task(tid, title, status, assignee, due, card_obj=None,
                 client_obj=None, priority=0, completed_at=None):
            return models.Task(
                id=tid, title=title, description="", status=status,
                # +00:00 — aware UTC (см. FIXED_NOW).
                due_date=datetime.fromisoformat(due + "+00:00"),
                priority=priority, assignee=assignee, creator_id=admin.id,
                card_id=card_obj.id if card_obj else None,
                client_id=client_obj.id if client_obj else None,
                card_title_snapshot=None, client_name_snapshot=None,
                completed_at=completed_at, tenant_id=None,
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            )

        task2 = task(2, "Согласовать смету Ковалёв", "in_work", manager,
                     "2026-09-14T18:00", cards[2], kovalev)
        s.add_all([
            task(1, "Замер освещения на Веснаторг", "todo", manager,
                 "2026-09-17T18:00", cards[0], vesnatorg),
            task2,
            task(3, "Отгрузка LED-лент РемонтСити", "in_work", manager,
                 "2026-09-13T18:00", cards[5], remontcity, priority=1),
            task(4, "Проверить акт Ковалёв", "done", manager,
                 "2026-09-04T18:00", cards[7], kovalev,
                 completed_at=datetime.fromisoformat("2026-09-04T16:00+00:00")),
            # Без исполнителя и без сделки — только контекст клиента.
            task(5, "Позвонить СветлогорскДом", "todo", None,
                 "2026-09-11T18:00", None, svetlogorsk),
        ])
        s.add_all([
            models.TaskChecklistItem(
                task=task2, title="Уточнить артикулы", is_done=True,
                position=0, tenant_id=None, created_at=FIXED_NOW,
            ),
            models.TaskChecklistItem(
                task=task2, title="Отправить на почту", is_done=False,
                position=1, tenant_id=None, created_at=FIXED_NOW,
            ),
        ])

        # --- накладные --------------------------------------------------------
        # doc_series_norm/doc_number_norm НЕ задаём: ORM-слушатель
        # _nakladnaya_sync_doc_key заполняет их сам на каждой вставке.
        # is_verified/is_arrived/is_paid держим консистентно со статусом —
        # та же связка, что переключают чекбоксы в js/nakladnye.bundle.js.
        def nak(nid, supplier, doc_type, series, number, day,
                amount, vat, no_vat, store, status, by_bot=False):
            return models.Nakladnaya(
                id=nid, supplier=supplier, supplier_name=supplier.name,
                doc_type=doc_type, doc_series=series, doc_number=number,
                doc_date=f"2026-09-{day:02d}",
                amount=amount, vat_amount=vat, amount_no_vat=no_vat,
                unload_address=supplier.address if nid == 1 else None,
                store=store,
                is_verified=status in ("verified", "arrived", "paid"),
                is_arrived=status in ("arrived", "paid"),
                is_paid=status == "paid",
                status=status, photo_paths=None, excel_path=None,
                created_by_bot=by_bot, tenant_id=None,
                products_json=json.dumps(
                    [{"name": "Светильник LX-20", "qty": 10,
                      "price_no_vat": 150.00},
                     {"name": "Крепёж набор", "qty": 5,
                      "price_no_vat": 0.00}],
                    ensure_ascii=False) if nid == 1 else None,
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            )

        s.add_all([
            nak(1, lyustra, "ТН", "АБ", "12345678", 8,
                1800.00, 300.00, 1500.00, "Матусевича", "arrived"),
            nak(2, armatura, "ТТН", "ВГ", "0044332", 9,
                4200.00, 700.00, 3500.00, "Богдановича", "paid",
                by_bot=True),
            nak(3, lyustra, "УПД", None, "9182736", 10,
                900.00, 150.00, 750.00, "Домбровская", "new"),
            nak(4, armatura, "ТН", "АБ", "12349999", 11,
                2600.00, 433.33, 2166.67, "Матусевича", "verified"),
        ])

        # --- уведомления админа ------------------------------------------------
        s.add_all([
            models.Notification(
                user_id=admin.id, type="task_assigned", title="Новая задача",
                details="«Замер освещения на Веснаторг» назначена на вас",
                entity_type="task", entity_id=1, is_read=False,
                read_at=None, tenant_id=None, created_at=FIXED_NOW,
            ),
            models.Notification(
                user_id=admin.id, type="card_payment",
                title="Оплата по сделке",
                details="ИП Ковалёв С.М. — 1 500,00 руб. (аванс)",
                entity_type="card", entity_id=3, is_read=False,
                read_at=None, tenant_id=None, created_at=FIXED_NOW,
            ),
            models.Notification(
                user_id=admin.id, type="card_created", title="Новая сделка",
                details="Освещение склада Веснаторг",
                entity_type="card", entity_id=1, is_read=True,
                read_at=FIXED_NOW, tenant_id=None, created_at=FIXED_NOW,
            ),
        ])

        # Единственный commit: база появляется атомарно.
        s.commit()
    finally:
        s.close()


def main():
    # Идемпотентный повтор _env (первый — на импорте, до `import database`).
    _env()
    _apply_schema()
    _seed()
    print(DB_PATH)


if __name__ == "__main__":
    main()
