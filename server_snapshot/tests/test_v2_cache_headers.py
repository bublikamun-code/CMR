"""Кэш-политика /v2: middleware `v2_cache_headers` и `V2_IMMUTABLE_PREFIXES`.

Что здесь зафиксировано (main.py, блок «Кэш-политика /v2»):

* HTML v2 — `no-cache`: он держит штампы `?v=`, поэтому обязан
  перепроверяться по ETag при каждом входе. Иначе после деплоя браузер смешает
  новый HTML со старой статикой.
* Штампованная статика (`/v2/css/`, `/v2/js/`, `/v2/fonts/`) —
  `public, max-age=31536000, immutable`: URL несёт хэш сборки, любая правка
  меняет штамп и, значит, сам URL, поэтому «вечный» кэш безопасен.
* Правка 21.09.2026: раньше `no-cache` стоял на ВСЁМ `/v2*`, и каждый вход
  гонял ~20 условных запросов — на мобильном RTT это давало десятки секунд до
  отрисовки (жалоба «канбан появляется через 20 секунд»). Регресс этого
  сценария — тест на годовую длину кэша у ассетов.
* `no-cache` — это revalidation, а не запрет кэширования: `no-store`
  означал бы полную перекачку ассетов на каждый вход.

Запросы идут в реальные файлы site-v2 (на 404 заголовки тоже проставляются,
но тогда тест проверял бы не политику, а ошибку). Имена берутся glob'ом:
переименование модуля или файла шрифта не должно ронять тест.
"""
from pathlib import Path

import pytest


SITE_V2 = Path("site-v2")

# Ожидания по заголовкам — дословно из main.py.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def _asset(subdir: str, pattern: str) -> str:
    """URL первого подходящего файла из site-v2/<subdir>."""
    found = sorted((SITE_V2 / subdir).glob(pattern))
    if not found:
        pytest.skip(f"в {SITE_V2 / subdir} нет {pattern} — артефакт не собран")
    return f"/v2/{subdir}/{found[0].name}"


@pytest.fixture
def asset_urls():
    """По одному реальному ассету на каждый иммутабельный префикс."""
    return {
        "css": _asset("css", "*.css"),
        "js": _asset("js", "*.js"),
        "fonts": _asset("fonts", "*.woff2"),
    }


def test_v2_html_is_revalidated(client):
    """HTML v2 перепроверяется каждый раз: в нём зашиты штампы статики."""
    for path in ["/v2/", "/v2/index.html"]:
        header = client.get(path).headers["cache-control"]
        assert header == REVALIDATE, f"{path}: {header}"
        assert "no-store" not in header, f"{path}: no-store отменил бы кэш целиком"
        assert "immutable" not in header, f"{path}: HTML нельзя кэшировать навечно"


def test_stamped_assets_are_immutable_year_cache(client, asset_urls):
    """Годовой immutable-кэш на все три префикса — иначе вход гоняет ~20 запросов."""
    for subdir, url in asset_urls.items():
        response = client.get(url)
        assert response.status_code == 200, f"{url}: {response.status_code}"
        assert response.headers["cache-control"] == IMMUTABLE, f"{subdir}: {url}"


def test_immutable_prefixes_contract():
    """Список префиксов — часть контракта: всё в нём обязано нести штамп в URL.

    Новый префикс без `?v=` в ссылке закэшировал бы файл навсегда (ровно та
    ошибка, из-за которой /static/logo.svg в legacy отдаётся без immutable).
    """
    import main

    assert set(main.V2_IMMUTABLE_PREFIXES) == {"/v2/css/", "/v2/js/", "/v2/fonts/"}
    for prefix in main.V2_IMMUTABLE_PREFIXES:
        assert prefix.startswith("/v2/"), prefix


def test_v2_policy_overrides_general_static_policy(client, asset_urls):
    """Общий middleware ставит на /v2/css/... «no-cache, must-revalidate».

    Проверка на точное равенство ловит и склейку двух заголовков: nginx/браузер
    увидели бы «no-cache, must-revalidate, public, max-age=31536000, immutable»,
    где no-cache отменяет годовой кэш.
    """
    for url in asset_urls.values():
        assert client.get(url).headers["cache-control"] == IMMUTABLE, url


def test_non_v2_paths_keep_their_own_policy(client):
    """Middleware v2 не протекает на соседние пути: у legacy-политики свои значения."""
    legacy_css = client.get("/css/style.css").headers["cache-control"]
    assert legacy_css == "public, max-age=31536000, immutable"

    legacy_html = client.get("/legacy").headers["cache-control"]
    assert legacy_html == "no-cache, must-revalidate"


def test_root_redirect_to_v2_is_not_cached(client, monkeypatch):
    """/ → /v2/ не кэшируется: иначе переключатель фронта залип бы в браузере.

    Редирект (а не FileResponse) нужен потому, что ассеты v2 лежат
    относительными путями внутри /v2/ и на корне резолвились бы в маунты
    старого фронта /css и /js.
    """
    import main

    monkeypatch.setattr(main, "FRONTEND_MODE", "v2")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "/v2/"
    header = response.headers["cache-control"]
    assert "immutable" not in header, header
    assert "no-cache" in header, header
