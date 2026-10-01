import auth
import models
import models_tenant  # noqa: F401  # таблица tenants нужна мапперу
from database import SessionLocal, engine
from models_tenant import Tenant

# Убеждаемся, что таблицы существуют
models.Base.metadata.create_all(bind=engine)
db = SessionLocal()

# Список ваших сотрудников (Можете поменять логины и пароли здесь)
import os

# UI FIX 2026-08-26: фолбэк-пароль удалён — скрипт падает, если пароль
# не задан явно. Раньше молча создавались аккаунты с "change_me_now!".
DEFAULT_PW = os.environ.get("CRM_DEFAULT_PASSWORD")
if not DEFAULT_PW:
    raise SystemExit(
        "CRM_DEFAULT_PASSWORD не задан. Задайте переменную окружения "
        "(или PW_MANAGER_Y / PW_MANAGER_A / ... для отдельных аккаунтов) "
        "и повторите запуск."
    )


def resolve_tenant(session):
    """tenant_id для новых пользователей.

    users.tenant_id — NOT NULL с переезда на мультиарендность, поэтому скрипт
    без этого падал на IntegrityError ещё до создания первого аккаунта.
    Порядок: явный CRM_TENANT_ID → единственный существующий арендатор (у
    развёрнутой CRM он один) → новый арендатор по умолчанию.
    """
    explicit = os.environ.get("CRM_TENANT_ID")
    if explicit:
        tenant = session.get(Tenant, int(explicit))
        if not tenant:
            raise SystemExit(f"CRM_TENANT_ID={explicit}: такого арендатора нет в tenants")
        return tenant
    existing = session.query(Tenant).order_by(Tenant.id).all()
    if len(existing) == 1:
        return existing[0]
    if existing:
        names = ", ".join(f"{t.id}={t.name!r}" for t in existing)
        raise SystemExit(
            "В базе несколько арендаторов, скрипт не угадывает: "
            f"{names}. Задайте CRM_TENANT_ID."
        )
    tenant = Tenant(name="default", db_path="default")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    print(f"Создан арендатор по умолчанию: {tenant.name} (id={tenant.id})")
    return tenant


tenant = resolve_tenant(db)

users_to_create = [
    {"username": "ManagerY", "password": os.environ.get("PW_MANAGER_Y", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerA", "password": os.environ.get("PW_MANAGER_A", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerN", "password": os.environ.get("PW_MANAGER_N", DEFAULT_PW), "role": "warehouse"},
    {"username": "ManagerV", "password": os.environ.get("PW_MANAGER_V", DEFAULT_PW), "role": "warehouse"},
    {"username": "Dokumenty", "password": os.environ.get("PW_DOKUMENTY", DEFAULT_PW), "role": "documents"},
]

print(f"Арендатор: {tenant.name} (id={tenant.id})")
print("Начинаю создание пользователей...")

for u in users_to_create:
    # Проверяем, нет ли уже такого пользователя
    existing_user = db.query(models.User).filter(models.User.username == u["username"]).first()

    if not existing_user:
        hashed_pw = auth.get_password_hash(u["password"])
        new_user = models.User(
            username=u["username"], hashed_password=hashed_pw, role=u["role"],
            tenant_id=tenant.id,
        )
        db.add(new_user)
        print(f"✅ Создан пользователь: {u['username']} (Роль: {u['role']})")
    else:
        print(f"⚠️ Пользователь {u['username']} уже существует.")

db.commit()
db.close()
print("Готово!")