import models
import auth
from database import SessionLocal, engine

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

users_to_create = [
    {"username": "ManagerY", "password": os.environ.get("PW_MANAGER_Y", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerA", "password": os.environ.get("PW_MANAGER_A", DEFAULT_PW), "role": "manager"},
    {"username": "ManagerN", "password": os.environ.get("PW_MANAGER_N", DEFAULT_PW), "role": "warehouse"},
    {"username": "ManagerV", "password": os.environ.get("PW_MANAGER_V", DEFAULT_PW), "role": "warehouse"},
    {"username": "Dokumenty", "password": os.environ.get("PW_DOKUMENTY", DEFAULT_PW), "role": "documents"},
]

print("Начинаю создание пользователей...")

for u in users_to_create:
    # Проверяем, нет ли уже такого пользователя
    existing_user = db.query(models.User).filter(models.User.username == u["username"]).first()
    
    if not existing_user:
        hashed_pw = auth.get_password_hash(u["password"])
        new_user = models.User(username=u["username"], hashed_password=hashed_pw, role=u["role"])
        db.add(new_user)
        print(f"✅ Создан пользователь: {u['username']} (Роль: {u['role']})")
    else:
        print(f"⚠️ Пользователь {u['username']} уже существует.")

db.commit()
db.close()
print("Готово!")