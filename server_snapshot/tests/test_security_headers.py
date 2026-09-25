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
    for path in ("/v2/", "/v2/js/v2/head.js"):
        policy = _csp(client.get(path))
        assert policy["default-src"] == ["'self'"]
        assert policy["object-src"] == ["'none'"]
        assert policy["frame-ancestors"] == ["'none'"]
        assert policy["base-uri"] == ["'self'"]
    for path in ("/v2", "/v2/", "/v2/index.html"):
        policy = _csp(client.get(path))
        assert policy["script-src"] == ["'self'"]
        assert policy["style-src"] == ["'self'"]
    assert "'unsafe-inline'" in _csp(client.get("/legacy"))["script-src"]
    assert "'unsafe-inline'" in _csp(client.get("/v20"))["script-src"]


def test_legacy_csp_keeps_inline_compatibility(client, monkeypatch):
    """Legacy — единственное исключение, где unsafe-inline допустим."""
    for path in ("/legacy", "/legacy/admin"):
        policy = _csp(client.get(path))
        assert set(policy["script-src"]) == {"'self'", "'unsafe-inline'"}
        assert "'unsafe-inline'" in policy["style-src"]
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "legacy")
    for path in ("/", "/admin"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200
        assert "'unsafe-inline'" in _csp(response)["script-src"]


def test_v2_mode_redirects_receive_strict_csp(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "v2")
    for path in ("/", "/admin"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert _csp(response)["script-src"] == ["'self'"]
        assert _csp(response)["style-src"] == ["'self'"]


def test_built_v2_document_is_strict_csp_clean():
    """Собранный документ не содержит inline-контента, который strict CSP запрещает."""
    from html.parser import HTMLParser
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    index_path = root / "site-v2" / "index.html"
    source = index_path.read_text(encoding="utf-8")
    found = {
        "style": [],
        "script": [],
        "style_attr": [],
        "event_attr": [],
        "stylesheets": [],
        "head_script": [],
    }

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "style":
                found["style"].append(self.getpos())
            if tag == "script" and values.get("src") is None:
                found["script"].append(self.getpos())
            if tag == "script" and values.get("src", "").startswith("js/v2/head.js?"):
                found["head_script"].append(values["src"])
            if tag == "link" and "stylesheet" in values.get("rel", "").split():
                found["stylesheets"].append(values.get("href"))
            for name, value in attrs:
                if name == "style":
                    found["style_attr"].append((self.getpos(), value))
                if name.startswith("on"):
                    found["event_attr"].append((self.getpos(), name, value))

    parser = Parser()
    parser.feed(source)
    assert found["style"] == []
    assert found["script"] == []
    assert found["style_attr"] == []
    assert found["event_attr"] == []
    assert len(found["head_script"]) == 1
    assert found["stylesheets"][0].startswith("css/shell-v2-base.css?v=")
    assert (root / "site-v2" / found["head_script"][0].split("?", 1)[0]).is_file()
    assert (root / "site-v2" / found["stylesheets"][0].split("?", 1)[0]).is_file()
    assert "V2_ASSET_VER" not in source


def test_other_security_headers(client):
    r = client.get("/v2/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
