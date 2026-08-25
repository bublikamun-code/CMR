import models
import auth
from database import SessionLocal, engine

# Убеждаемся, что таблицы существуют
models.Base.metadata.create_all(bind=engine)
db = SessionLocal()

# Список ваших сотрудников (Можете поменять логины и пароли здесь)
import os

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