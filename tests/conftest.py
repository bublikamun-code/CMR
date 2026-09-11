"""Инфраструктура тестов бэкенда CRM.

Главное решение: тесты по умолчанию работают на **фактической схеме боевой БД**
(`tests/fixtures/prod_schema.sql`, снята с прода 2026-09-11), а не на
`Base.metadata.create_all()`. Причина — расхождение моделей и боевого DDL:

    колонка                        models.py        прод DDL
    cards.total_amount             Numeric(12,2)    FLOAT
    transactions.amount            Numeric(12,2)    FLOAT
    writeoff_groups.total_amount   Numeric(12,2)    NUMERIC(12, 2)
    nakladnye.amount/vat_amount    Numeric(12,2)    REAL
    nakladnye.amount_no_vat        отсутствует      REAL

У SQLite разная type affinity у FLOAT/REAL и NUMERIC, поэтому поведение
округления и сравнения может отличаться. Тесты только на create_all в этих
местах врали бы.

Профиль переключается переменной окружения:
    CRM_TEST_SCHEMA=prod   (по умолчанию) — DDL прода + create_all досоздаёт недостающее
    CRM_TEST_SCHEMA=models — только create_all по models.py

Окружение выставляется ДО импорта database/main: engine, URL и create_all
привязаны к CRM_DATA_DIR на импорте модуля (database.py:10-13, main.py:33).
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- окружение до импорта приложения -------------------------------------
_TMP = tempfile.mkdtemp(prefix="crm_test_")
os.environ["CRM_DATA_DIR"] = _TMP
os.environ["CRM_SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ["CRM_CRON_TOKEN"] = "test-cron-token"
# nakladnye_router читает токен бота на импорте модуля (строка 28), поэтому
# переменная обязана быть выставлена до импорта main. Без неё bot-эндпоинты
# отвечают 403 всегда и протестировать их нельзя.
os.environ["TELEGRAM_BOT_TOKEN"] = "test-bot-token"
os.makedirs(os.path.join(_TMP, "uploads"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "tenants"), exist_ok=True)

import pytest                                              # noqa: E402
from fastapi.testclient import TestClient                  # noqa: E402
from sqlalchemy import text                                # noqa: E402

import database                                            # noqa: E402
import models                                              # noqa: E402
import models_tenant                                       # noqa: E402,F401
import auth                                                # noqa: E402

# models_tenant импортируется ради побочного эффекта: Tenant использует тот же
# Base, что и models, поэтому без импорта таблица `tenants` не зарегистрирована
# в metadata и create_all падает с NoReferencedTableError на users.tenant_id.
# В профиле prod эта ошибка не всплывает — там tenants приезжает из SQL-дампа.

SCHEMA_PROFILE = os.environ.get("CRM_TEST_SCHEMA", "prod")
SCHEMA_SQL = Path(__file__).parent / "fixtures" / "prod_schema.sql"

# sqlite_sequence — внутренняя таблица SQLite, её нельзя создавать руками.
_SQLITE_SEQUENCE = re.compile(
    r"CREATE TABLE sqlite_sequence\s*\([^)]*\)\s*;", re.IGNORECASE
)


def _apply_prod_schema():
    """Накатывает реальный DDL прода. executescript — потому что statements
    много, а SQLAlchemy по умолчанию не исполняет пакетные SQL-скрипты."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    sql = _SQLITE_SEQUENCE.sub("", sql)
    raw = database.engine.raw_connection()
    try:
        raw.executescript(sql)
        raw.commit()
    finally:
        raw.close()


def _existing_tables():
    with database.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )).fetchall()
    return [r[0] for r in rows]


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    """Схема создаётся один раз на сессию; данные чистятся между тестами."""
    if SCHEMA_PROFILE == "prod":
        if not SCHEMA_SQL.exists():
            pytest.skip(f"нет {SCHEMA_SQL} — не на чем строить продовый профиль")
        _apply_prod_schema()
    # checkfirst=True (по умолчанию): существующие таблицы не пересоздаются,
    # поэтому продовый DDL остаётся нетронутым, а недостающее досоздаётся.
    models.Base.metadata.create_all(bind=database.engine)
    yield
    database.engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_data():
    """Пустая БД перед каждым тестом. Удаление в порядке, обратном
    зависимостям FK, чтобы не упираться в ограничения."""
    yield
    ordered = [t.name for t in reversed(models.Base.metadata.sorted_tables)]
    # таблицы, которых нет в моделях (например schema_migrations)
    ordered += [t for t in _existing_tables() if t not in ordered]
    with database.engine.begin() as conn:
        for name in ordered:
            conn.execute(text(f'DELETE FROM "{name}"'))


@pytest.fixture
def db():
    """Сессия для подготовки данных и прямых проверок в обход API."""
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    import main
    with TestClient(main.app) as c:
        yield c


def _make_user(session, username, role="manager", password="Passw0rd!23"):
    user = models.User(
        username=username,
        hashed_password=auth.get_password_hash(password),
        role=role,
        tenant_id=None,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _token(user):
    # claim pv — первые 8 символов хэша: без него get_current_user даёт 401
    return auth.create_access_token({
        "sub": user.username,
        "tenant_id": None,
        "pv": user.hashed_password[:8],
    })


@pytest.fixture
def make_user(db):
    """Фабрика пользователей. Возвращает (user, headers)."""
    created = []

    def _factory(role="manager", username=None, password="Passw0rd!23"):
        username = username or f"{role}_{len(created) + 1}"
        user = _make_user(db, username, role, password)
        created.append(user)
        return user, {"Authorization": f"Bearer {_token(user)}"}

    return _factory


@pytest.fixture
def make_token(db):
    """Создать пользователя и выпустить ему токен с произвольными claims.

    Нужно для проверки самой схемы аутентификации: устаревшие токены без `pv`,
    токены с чужим `pv` после смены пароля.
    """
    def _factory(claims=None, role="manager", username=None, password="Passw0rd!23"):
        username = username or f"tok_{role}"
        user = _make_user(db, username, role, password)
        data = {"sub": user.username, "tenant_id": None,
                "pv": user.hashed_password[:8]}
        if claims is not None:
            data = claims(user)
        return user, {"Authorization": f"Bearer {auth.create_access_token(data)}"}
    return _factory


@pytest.fixture
def manager(make_user):
    return make_user("manager")


@pytest.fixture
def admin(make_user):
    return make_user("admin")


@pytest.fixture
def superadmin(make_user):
    return make_user("superadmin")


@pytest.fixture
def cron_headers():
    return {"X-Cron-Token": os.environ["CRM_CRON_TOKEN"]}


@pytest.fixture
def bot_headers():
    """Авторизация telegram-бота накладных (заголовок X-Bot-Token)."""
    return {"X-Bot-Token": os.environ["TELEGRAM_BOT_TOKEN"]}


# --- фабрики данных -------------------------------------------------------

@pytest.fixture
def make_card(db):
    def _factory(**kw):
        defaults = dict(title="Сделка", status="Новый запрос",
                        total_amount=0.0, paid_amount=0.0,
                        payment_status="Не оплачен", is_deleted=False)
        defaults.update(kw)
        card = models.Card(**defaults)
        db.add(card)
        db.commit()
        db.refresh(card)
        return card
    return _factory


@pytest.fixture
def make_transaction(db):
    def _factory(card, **kw):
        defaults = dict(card_id=card.id, company_name=card.title,
                        amount=0.0, is_document=False,
                        is_warehouse_writeoff=False, invoice_number=None)
        defaults.update(kw)
        tx = models.Transaction(**defaults)
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx
    return _factory


@pytest.fixture
def foreign_key_violations():
    """PRAGMA foreign_key_check — ноль нарушений после удалений."""
    def _check():
        with database.engine.connect() as conn:
            return conn.execute(text("PRAGMA foreign_key_check")).fetchall()
    return _check
