"""Проверка тестовой инфраструктуры и стартового состояния приложения.

Эти тесты не про бизнес-логику, а про то, что стенд собран правильно:
приложение стартует, аутентификация работает настоящими JWT, схема
соответствует выбранному профилю, FK включён. Без зелёного этого модуля
остальные тесты бессмысленны.
"""
import pytest
from conftest import SCHEMA_PROFILE
from sqlalchemy import text

import auth
import database


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_frontend_pages_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200


def test_dead_routes_removed(client):
    """Фаза 0: эти роуты отдавали 500 на проде — файлов там никогда не было."""
    for path in ["/settings.html", "/workflows.html", "/custom_objects.html"]:
        assert client.get(path).status_code == 404, path


def test_manifest_and_static_logo_served(client):
    """index.html ссылается на manifest.json, а манифест и admin.html — на /static/logo.svg.

    До Фазы 3 оба отдавали 404: StaticFiles был смонтирован только на /css и /js,
    отдельных роутов не было. Иконка манифеста оставалась битой, установка
    приложения как PWA не работала, логотип в шапке admin.html не грузился.
    """
    r = client.get("/manifest.json")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/manifest+json")
    icon_src = r.json()["icons"][0]["src"]

    logo = client.get(icon_src)
    assert logo.status_code == 200, f"{icon_src} не отдаётся"
    assert logo.headers["content-type"].startswith("image/svg+xml")


def test_unhashed_assets_are_not_immutable(client):
    """/css и /js кэшируются на год именно потому, что URL несёт хэш содержимого
    (tools/stamp_assets.py). У /static/logo.svg хэша нет, поэтому immutable
    там закэшировал бы файл навсегда — проверяем, что его нет."""
    assert "immutable" in client.get("/css/style.css").headers["cache-control"]
    assert "immutable" in client.get("/js/api.js").headers["cache-control"]
    assert "immutable" not in client.get("/static/logo.svg").headers["cache-control"]
    assert "immutable" not in client.get("/manifest.json").headers["cache-control"]


def test_protected_endpoints_require_auth(client):
    """Без токена — 401/403, но не 500: 500 означал бы поломку в обработчике."""
    for path in ["/kanban/cards", "/payments/transactions", "/nakladnye",
                 "/clients", "/tasks"]:
        code = client.get(path).status_code
        assert code in (401, 403), f"{path} вернул {code}"


def test_real_jwt_authenticates(client, manager):
    _user, headers = manager
    r = client.get("/clients", headers=headers)
    assert r.status_code == 200, r.text


def test_token_without_pv_claim_rejected(client, make_token):
    """После миграционного окна legacy-токен без pv больше не авторизует."""
    _, headers = make_token(claims=lambda u: {"sub": u.username, "tenant_id": None})
    assert client.get("/clients", headers=headers).status_code == 401


def test_wrong_pv_claim_rejected(client, make_token):
    """Токен, выпущенный до смены пароля, должен получать 401."""
    _, headers = make_token(
        claims=lambda u: {"sub": u.username, "tenant_id": None, "pv": "deadbeef"},
        username="rotated_user",
    )
    assert client.get("/clients", headers=headers).status_code == 401


def test_unknown_user_rejected(client, make_token):
    """Валидная подпись, но пользователя нет в БД — 401, не 500."""
    _, headers = make_token(claims=lambda u: {
        "sub": "ghost", "tenant_id": None,
        "pv": auth.password_version(u.hashed_password),
    })
    assert client.get("/clients", headers=headers).status_code == 401


def test_foreign_keys_enforced(db):
    """PRAGMA foreign_keys=ON — без неё CASCADE/SET NULL из models.py не работают."""
    with database.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_no_foreign_key_violations_at_start(foreign_key_violations):
    assert foreign_key_violations() == []


@pytest.mark.skipif(SCHEMA_PROFILE != "prod", reason="только для профиля prod")
def test_prod_schema_is_actually_applied(db):
    """Страховка от тихого отката на create_all: проверяем приметы боевого DDL."""
    with database.engine.connect() as conn:
        indexes = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index'")).fetchall()}
        # частичный unique-индекс из миграции 0004 — в models.py его нет
        assert "uq_remainder_per_card" in indexes
        assert "ux_record_versions_t_r_v" in indexes

        # cards.total_amount на проде FLOAT, а models.py объявляет Numeric(12,2)
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='cards'")).scalar()
        assert "total_amount FLOAT" in ddl

        # колонка из миграции, которой нет в моделях
        nakl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='nakladnye'")).scalar()
        assert "amount_no_vat" in nakl


def test_money_columns_are_float_not_decimal(db, make_card):
    """Документируем реальность: деньги хранятся и возвращаются как float.
    Решение владельца 2026-09-11 — оставить float, не переводить на Decimal."""
    card = make_card(total_amount=1234.56)
    db.refresh(card)
    assert isinstance(float(card.total_amount), float)
    assert round(float(card.total_amount), 2) == 1234.56
