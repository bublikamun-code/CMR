from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool
import os

SQLALCHEMY_DATABASE_URL = "sqlite:///./crm_app.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=50,
    max_overflow=50,
    pool_timeout=120,
    pool_pre_ping=True,
    pool_recycle=3600,)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

_active_sessions = {"count": 0}

def get_db():
    db = SessionLocal()
    _active_sessions["count"] += 1
    try:
        yield db
    finally:
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
        db_path = f"tenants/crm_{tenant_id}.db"
        os.makedirs("tenants", exist_ok=True)
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
            cur.close()

        _tenant_engines[tenant_id] = eng
        Base.metadata.create_all(bind=eng)

    return _tenant_engines[tenant_id]

def get_tenant_db(tenant_id):
    if tenant_id is None:
        db = SessionLocal()
        _active_sessions["count"] += 1
        return db

    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        db = SessionLocal()
        _active_sessions["count"] += 1
        return db

    engine = get_tenant_engine(tenant_id)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    _active_sessions["count"] += 1
    return db


def get_scoped_session(current_user):
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
