import os
import sys
import models
import auth
from database import SessionLocal, engine, get_tenant_db
import models_tenant

# Убеждаемся, что таблицы существуют
models.Base.metadata.create_all(bind=engine)
db = SessionLocal()

# Пароли берутся из переменных окружения или используют значения по умолчанию
# Для продакшена задайте CRM_DEFAULT_PASSWORD в .env
DEFAULT_PW = os.environ.get("CRM_DEFAULT_PASSWORD", "change_me_now!")

users_to_create = [
    {"username": "ManagerY", "password": os.environ.get("PW_MANAGER_Y", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerA", "password": os.environ.get("PW_MANAGER_A", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerN", "password": os.environ.get("PW_MANAGER_N", DEFAULT_PW), "role": "warehouse"},
    {"username": "ManagerV", "password": os.environ.get("PW_MANAGER_V", DEFAULT_PW), "role": "warehouse"},
    {"username": "Dokumenty", "password": os.environ.get("PW_DOKUMENTY", DEFAULT_PW), "role": "documents"},
]


def resolve_tenant_id():
    """Определяет tenant_id для новых пользователей.

    Порядок:
      1. Переменная окружения CRM_TENANT_ID.
      2. Единственный существующий тенант в БД.
      3. Если тенантов нет — создаётся тенант по умолчанию.
      4. Если тенантов несколько — требуется явный CRM_TENANT_ID.
    """
    env_tenant_id = os.environ.get("CRM_TENANT_ID")
    if env_tenant_id:
        return int(env_tenant_id)

    tenants = db.query(models_tenant.Tenant).order_by(models_tenant.Tenant.id).all()
    if len(tenants) == 1:
        return tenants[0].id

    if len(tenants) == 0:
        print("  Тенантов не найдено. Создаю тенант по умолчанию...")
        tenant = models_tenant.Tenant(name="Default", db_path="tenants/crm_default.db")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        # Создаём файл tenant БД и таблицы
        tdb = get_tenant_db(tenant.id)
        tdb.close()
        print(f"  Создан тенант id={tenant.id}")
        return tenant.id

    print("Ошибка: в БД несколько тенантов. Укажите целевой CRM_TENANT_ID.")
    print("Существующие тенанты:")
    for t in tenants:
        print(f"  id={t.id} name={t.name}")
    sys.exit(1)


print("Начинаю создание пользователей...")

tenant_id = resolve_tenant_id()
print(f"  Пользователи будут привязаны к tenant_id={tenant_id}")

for u in users_to_create:
    # Проверяем, нет ли уже такого пользователя
    existing_user = db.query(models.User).filter(models.User.username == u["username"]).first()

    if not existing_user:
        hashed_pw = auth.get_password_hash(u["password"])
        new_user = models.User(
            username=u["username"],
            hashed_password=hashed_pw,
            role=u["role"],
            tenant_id=tenant_id,
        )
        db.add(new_user)
        print(f"✅ Создан пользователь: {u['username']} (Роль: {u['role']}, tenant_id: {tenant_id})")
    else:
        # Если пользователь уже существует без tenant_id, исправляем
        if existing_user.tenant_id is None:
            existing_user.tenant_id = tenant_id
            print(f"🔄 Обновлён tenant_id для существующего пользователя: {u['username']} -> {tenant_id}")
        else:
            print(f"⚠️ Пользователь {u['username']} уже существует (tenant_id: {existing_user.tenant_id}).")

db.commit()
db.close()
print("Готово!")
