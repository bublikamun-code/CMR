"""Доступность документации API: /docs, /redoc, /openapi.json.

FastAPI по умолчанию поднимает их открытыми, и для CRM это полная карта
атакующего: все эндпоинты, параметры, структуры моделей, служебные подсистемы.
Теперь режим задаёт CRM_API_DOCS, а дефолт — superadmin (main.py:_api_docs_mode).

Фронт эти маршруты не использует — это тоже зафиксировано здесь отдельным
тестом, чтобы случайно открытая схема не выглядела как «нужная UI».
"""
import re
from pathlib import Path

import pytest

from runtime_config import RuntimeConfigError

# main импортируется только внутри тестов: на уровне модуля его create_all
# отработал бы ДО session-фикстуры схемы (tests/conftest.py:_init_schema) и
# сломал бы накатывание продового DDL.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_docs_are_closed_for_anonymous(client):
    """Без токена документация недоступна вовсе, а не отдаётся «для удобства»."""
    for path in DOCS_PATHS:
        response = client.get(path)
        assert response.status_code in (401, 403), f"{path} → {response.status_code}"


def test_docs_are_closed_for_regular_roles(client, manager, admin):
    """Роли manager и admin документацию не получают — только superadmin."""
    for _, headers in (manager, admin):
        for path in DOCS_PATHS:
            response = client.get(path, headers=headers)
            assert response.status_code == 403, f"{path} → {response.status_code}"


def test_docs_are_open_for_superadmin(client, superadmin):
    import main
    _, headers = superadmin
    docs = client.get("/docs", headers=headers)
    assert docs.status_code == 200
    schema = client.get("/openapi.json", headers=headers)
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == main.app.title
    assert client.get("/redoc", headers=headers).status_code == 200


def test_docs_routes_are_absent_from_the_schema(client, superadmin):
    """Сама документация не засоряет схему, которую отдаёт /openapi.json."""
    _, headers = superadmin
    paths = client.get("/openapi.json", headers=headers).json()["paths"]
    for path in DOCS_PATHS:
        assert path not in paths, path


def test_docs_csp_allows_swagger_assets(client, superadmin):
    """Отдельная политика для /docs: Swagger UI грузится с cdn.jsdelivr.net.

    Без неё страница отдавалась бы с 200 и оставалась пустой — браузер блокирует
    внешний скрипт, а сервер в логах ошибки не видно.
    """
    _, headers = superadmin
    policy = client.get("/docs", headers=headers).headers[
        "content-security-policy"]
    assert "https://cdn.jsdelivr.net" in policy
    assert "'unsafe-inline'" in policy
    # Политика v2 при этом не ослаблена.
    assert "cdn.jsdelivr.net" not in client.get("/v2/").headers[
        "content-security-policy"]


def test_mode_parser_is_fail_closed():
    import main
    assert main._api_docs_mode({}) == "superadmin"
    assert main._api_docs_mode({"CRM_API_DOCS": ""}) == "superadmin"
    assert main._api_docs_mode({"CRM_API_DOCS": "off"}) == "disabled"
    assert main._api_docs_mode({"CRM_API_DOCS": "1"}) == "superadmin"
    assert main._api_docs_mode(
        {"CRM_API_DOCS": "PUBLIC", "CRM_DEPLOYMENT": "test"}) == "public"
    # Опечатка в окружении закрывает процесс, а не включает документацию.
    with pytest.raises(RuntimeConfigError):
        main._api_docs_mode({"CRM_API_DOCS": "typo"})
    # На production публичная документация запрещена.
    with pytest.raises(RuntimeConfigError):
        main._api_docs_mode(
            {"CRM_API_DOCS": "public", "CRM_DEPLOYMENT": "production"})


@pytest.mark.parametrize("relative", ["site-v2", "site"])
def test_frontend_does_not_use_openapi(relative):
    """Фронт не ходит в /openapi.json — закрывать схему безопасно."""
    root = Path(__file__).resolve().parents[2] / relative
    pattern = re.compile(r"openapi\.json|swagger|/redoc")
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".js", ".html", ".css"}
        and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
