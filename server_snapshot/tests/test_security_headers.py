"""Заголовки безопасности из middleware main.py: CSP и соседи.

Тестов на CSP раньше не было вовсе, а политика живёт в коде одной строкой и
ломается молча: браузер просто отказывается грузить ресурс, на сервере при
этом 200. Регресс, который здесь зафиксирован, — `blob:` в img-src (пункт 1
плана v2 от 22.09.2026): миниатюры фото входящих накладных и предпросмотр
выбранного файла в закупке рисуются через URL.createObjectURL, и без `blob:`
14 изображений были битыми с самой загрузки страницы.

Заголовок разбирается в словарь директив, чтобы проверять конкретную
директиву, а не подстроку во всём CSP: подстрока `blob:` совпала бы и с
`default-src blob:`, то есть с политикой гораздо шире нужной.
"""


def _csp(response):
    """CSP-заголовок → {директива: [источники]}."""
    raw = response.headers["content-security-policy"]
    parsed = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, sources = part.partition(" ")
        parsed[name.strip()] = sources.split()
    return parsed


def test_csp_present_on_html_static_and_api(client):
    """Middleware ставит CSP на всех путях, включая ответы без авторизации."""
    for path in ["/v2/", "/manifest.json", "/health", "/kanban/cards"]:
        assert "content-security-policy" in client.get(path).headers, path


def test_img_src_allows_blob(client):
    """Регресс пункта 1: изображения из createObjectURL обязаны грузиться."""
    img = _csp(client.get("/v2/"))["img-src"]
    assert "blob:" in img
    assert "'self'" in img, "локальные изображения не должны сломаться"
    assert "data:" in img, "инлайн-иконки в CSS/HTML не должны сломаться"


def test_blob_not_widened_beyond_images(client):
    """blob: добавлен ровно в img-src: в скриптах, XHR и плагинах его нет."""
    policy = _csp(client.get("/v2/"))
    for directive in ["default-src", "script-src", "connect-src",
                      "style-src", "font-src"]:
        assert "blob:" not in policy[directive], directive


def test_csp_keeps_strict_directives(client):
    """Ужесточения из аудита 31.08 не потеряны при правках политики."""
    policy = _csp(client.get("/v2/"))
    assert policy["default-src"] == ["'self'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["base-uri"] == ["'self'"]
    # script-src: только свои скрипты и инлайн (legacy-фронт на onclick).
    # Любая схема или '*' означали бы выполнение чужого кода.
    assert set(policy["script-src"]) == {"'self'", "'unsafe-inline'"}


def test_other_security_headers(client):
    r = client.get("/v2/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
