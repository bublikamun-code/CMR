"""Каталог миграций схемы CRM (применяет migrate.py, см. его докстринг)."""
import importlib.util
import os

_DIR = os.path.dirname(os.path.abspath(__file__))


def import_migration(filename):
    """Загрузить модуль миграции по имени файла (для тестов и раннера)."""
    spec = importlib.util.spec_from_file_location(
        "crm_migration_" + filename[:-3], os.path.join(_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
