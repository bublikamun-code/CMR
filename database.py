from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool
import os

# Where the SQLite files live. Defaults to the app directory so the existing
# bare-metal server keeps working unchanged; Docker sets CRM_DATA_DIR=/app/data
# so the volume can hold data only, instead of being mounted over the code.
DATA_DIR = os.environ.get("CRM_DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'crm_app.db')}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    # UI FIX 2026-08-29: 50+50 соединений на процесс избыточны для SQLite
    # (один писатель на всю БД) — 10+10 хватает с запасом.
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=10,
    pool_timeout=120,
    pool_pre_ping=True,
    pool_recycle=3600,)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    # FIX 2026-08-29: безопасный уровень для WAL — заметно быстрее на записи,
    # чем FULL, при том же уровне сохранности.
    cursor.execute("PRAGMA synchronous=NORMAL")
    # FIX 2026-08-29: FK включён — без него все ondelete=CASCADE/SET NULL
    # из models.py не работали на уровне БД, и hard-delete оставлял сирот.
    # Перед включением выполнена инвентаризация и чистка сирот.
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

_active_sessions = {"count": 0}

# Tenant sessions opened during the current request, closed only after FastAPI has
# finished serialising the response.
#
# Why this exists:
# routers open a tenant session with _db(current_user, db) and close it in their
# own `finally:` block. That block runs when the handler returns - BEFORE the
# response model is serialised. Pydantic then touches relationship attributes
# (Card.owner, .client, .tags, ...) on a detached instance and the request dies
# with ResponseValidationError / DetachedInstanceError, even though the write
# itself succeeded. The user sees a 500 for an operation that actually worked.
#
# Superadmins never hit this: their session comes from get_db(), a yield
# dependency, which FastAPI closes after serialisation. This makes tenant
# sessions behave the same way.
#
# A plain module-level dict is not enough - requests run concurrently, so this is
# a ContextVar holding one list per request.
from contextvars import ContextVar

_deferred_sessions: ContextVar = ContextVar("crm_deferred_sessions", default=None)


def defer_close(session):
    """Register a session to be closed at the end of the request.

    Returns True when the close was deferred, False when there is no active
    request scope (background task, CLI script, tests) and the caller must close
    the session itself.
    """
    bucket = _deferred_sessions.get()
    if bucket is None:
        return False
    bucket.append(session)
    return True


async def get_db():
    # MUST stay `async`. FastAPI runs sync dependencies in a threadpool, where each
    # call gets a COPY of the context - a ContextVar set there is invisible to the
    # endpoint, so deferred closes were silently dropped. An async dependency runs
    # in the request's own context, and the endpoint (still sync, still in a
    # threadpool) inherits it and can append to the list object below.
    #
    # Creating a Session does no I/O, so this does not block the event loop.
    db = SessionLocal()
    _active_sessions["count"] += 1
    bucket = []
    _deferred_sessions.set(bucket)
    try:
        yield db
    finally:
        # Close tenant sessions first: they were kept open only for serialisation.
        #
        # `bucket` is captured directly rather than read back from the ContextVar,
        # and the token is never reset. FastAPI runs sync dependencies in a
        # threadpool, so the code before and after `yield` executes in different
        # contexts - ContextVar.reset() there raises
        # "Token was created in a different Context", and a fresh .get() can miss
        # the sessions entirely. Holding the list object is context-independent.
        for extra in bucket:
            try:
                _active_sessions["count"] -= 1
                extra.close()
            except Exception:
                pass
        _deferred_sessions.set(None)
        _active_sessions["count"] -= 1
        db.close()

def get_pool_status():
    return {
        "pool_size": engine.pool.size(),
        "pool_checked_in": engine.pool.checkedin(),
        "pool_checked_out": engine.pool.checkedout(),
        "pool_overflow": engine.pool.overflow(),
        "active_sessions": _active_sessions["count"],
    }

_tenant_engines = {}

def get_tenant_engine(tenant_id: int):
    if tenant_id not in _tenant_engines:
        tenants_dir = os.path.join(DATA_DIR, "tenants")
        db_path = os.path.join(tenants_dir, f"crm_{tenant_id}.db")
        os.makedirs(tenants_dir, exist_ok=True)
        url = f"sqlite:///{db_path}"
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=QueuePool,
            pool_size=50,
            max_overflow=50,
            pool_timeout=120,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        @event.listens_for(eng, "connect")
        def set_pragma(dbapi_conn, rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            # FIX 2026-09-03: паритет с основным движком — иначе каскады
            # ondelete=CASCADE/SET NULL из models.py в tenant-БД не работали.
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _tenant_engines[tenant_id] = eng
        Base.metadata.create_all(bind=eng)

    return _tenant_engines[tenant_id]

def _defer_first_close(session):
    """Turn the first session.close() into a deferred close.

    Routers close their tenant session in a `finally:` block, which runs before
    FastAPI serialises the response - detaching the ORM objects the response model
    still needs. Instead of editing 51 handlers, the first close() is recorded and
    the real close happens when the request ends (see get_db).

    Outside a request scope (background tasks, CLI scripts) defer_close returns
    False and the session closes immediately, exactly as before.
    """
    real_close = session.close

    def close(*args, **kwargs):
        session.close = real_close      # only the first call is deferred
        if defer_close(session):
            return None
        return real_close(*args, **kwargs)

    session.close = close
    return session


def get_tenant_db(tenant_id):
    if tenant_id is None:
        db = SessionLocal()
        _active_sessions["count"] += 1
        return _defer_first_close(db)

    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        db = SessionLocal()
        _active_sessions["count"] += 1
        return _defer_first_close(db)

    engine = get_tenant_engine(tenant_id)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    _active_sessions["count"] += 1
    return _defer_first_close(db)


def get_scoped_session(current_user):
    """Yield a session scoped to the caller's tenant.

    NOTE: this is a plain generator, NOT a FastAPI dependency. Passing it straight
    to Depends() makes FastAPI treat `current_user` as a required query parameter
    (HTTP 422). Routers must wrap it - see tags_router._scoped_db.
    """
    if current_user.role == "superadmin" and current_user.tenant_id is None:
        db = SessionLocal()
        _active_sessions["count"] += 1
        try:
            yield db
        finally:
            _active_sessions["count"] -= 1
            db.close()
    else:
        db = get_tenant_db(current_user.tenant_id)
        try:
            yield db
        finally:
            _active_sessions["count"] -= 1
            db.close()

