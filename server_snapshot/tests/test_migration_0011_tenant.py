"""0011 должна накатываться на базу, где tenant_id уже NOT NULL (аудит 2026-09-30).

Миграция объявлена идемпотентной и применялась на боевой базе — но тогда, когда
store_locations.tenant_id ещё был про nullable. На СВЕЖЕЙ копии боевой базы
(развёртывание с нуля, восстановление из бекапа, стенд) её накатывают впервые,
и она падала:

    sqlite3.IntegrityError: NOT NULL constraint failed: store_locations.tenant_id

Право вставлять задаётся сделкой, из которой берётся название: магазин
принадлежит той же компании, что и карточка.
"""

import importlib.util
import os
import sqlite3

import pytest

# Путь строится от файла теста, а не от текущего каталога: pytest запускается
# из корня репозитория, и относительный "migrations/..." указывал в никуда.
_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


def _load_migration(name):
    spec = importlib.util.spec_from_file_location(
        f"mig_{name}", os.path.join(_MIGRATIONS_DIR, name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def schema_with_notnull_tenant():
    """Схема в том виде, в каком она есть на боевой базе СЕЙЧАС.

    Именно этот вид и ломал накатывание: колонка уже NOT NULL, а миграция
    в неё писать не умела.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE store_locations (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            tenant_id INTEGER NOT NULL
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            store_location VARCHAR,
            tenant_id INTEGER NOT NULL
        );
        """
    )
    con.execute("INSERT INTO store_locations (name, is_active, tenant_id) "
                "VALUES ('Матусевича', 1, 1)")
    con.execute("INSERT INTO cards (store_location, tenant_id) VALUES "
                "('Богдановича', 1)")
    con.execute("INSERT INTO cards (store_location, tenant_id) VALUES "
                "('Серебрянская', 1)")
    yield con
    con.close()


def test_0011_applies_to_database_with_notnull_tenant(schema_with_notnull_tenant):
    """Главное: миграция не падает на боевой форме схемы."""
    mod = _load_migration("0011_fill_store_locations.py")
    # Раньше здесь вылетал IntegrityError.
    mod.up(schema_with_notnull_tenant)

    names = {r[0] for r in schema_with_notnull_tenant.execute(
        "SELECT name FROM store_locations")}
    assert "Богдановича" in names, names
    assert "Серебрянская" in names, names


def test_0011_inserted_rows_carry_the_tenant(schema_with_notnull_tenant):
    """Магазин наследует компанию тех карточек, из которых он собран."""
    mod = _load_migration("0011_fill_store_locations.py")
    mod.up(schema_with_notnull_tenant)

    rows = dict(schema_with_notnull_tenant.execute(
        "SELECT name, tenant_id FROM store_locations"))
    assert rows["Богдановича"] == 1, rows


def test_0011_stays_idempotent(schema_with_notnull_tenant):
    """Второй прогон не добавляет ничего нового."""
    mod = _load_migration("0011_fill_store_locations.py")
    mod.up(schema_with_notnull_tenant)
    first = schema_with_notnull_tenant.execute(
        "SELECT COUNT(*) FROM store_locations").fetchone()[0]

    mod.up(schema_with_notnull_tenant)
    second = schema_with_notnull_tenant.execute(
        "SELECT COUNT(*) FROM store_locations").fetchone()[0]

    assert first == second == 3, f"было {first}, стало {second}"


def test_0011_handles_same_name_in_two_tenants():
    """Одинаковое название у двух компаний — два магазина, а не один.

    Если схлопнуть их в одну запись, магазин одной компании оказался бы виден
    другой, то есть появилась бы межтенантная утечка справочника.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE store_locations (
            id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
            is_active BOOLEAN DEFAULT 1, tenant_id INTEGER NOT NULL);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, store_location VARCHAR,
            tenant_id INTEGER NOT NULL);
        INSERT INTO cards (store_location, tenant_id) VALUES ('Центр', 1);
        INSERT INTO cards (store_location, tenant_id) VALUES ('Центр', 2);
        """
    )
    mod = _load_migration("0011_fill_store_locations.py")
    mod.up(con)

    rows = con.execute(
        "SELECT name, tenant_id FROM store_locations ORDER BY tenant_id").fetchall()
    assert rows == [("Центр", 1), ("Центр", 2)], rows
    con.close()


def test_0011_skips_cards_without_tenant():
    """Карточка без компании не должна ронять миграцию.

    Данных с NULL tenant_id быть не должно (0018 нормализует), но если они
    появятся, миграция обязана их пропустить, а не падать.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE store_locations (
            id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
            is_active BOOLEAN DEFAULT 1, tenant_id INTEGER NOT NULL);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, store_location VARCHAR,
            tenant_id INTEGER);
        INSERT INTO cards (store_location, tenant_id) VALUES ('Ничьи владения', NULL);
        INSERT INTO cards (store_location, tenant_id) VALUES ('Свои', 7);
        """
    )
    mod = _load_migration("0011_fill_store_locations.py")
    mod.up(con)

    rows = con.execute("SELECT name, tenant_id FROM store_locations").fetchall()
    assert rows == [("Свои", 7)], rows
    con.close()