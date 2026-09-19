"""Пакет G: v2 — основной фронт на «/», старый заморожен на /legacy.

Дефолт CRM_FRONTEND зафиксирован в КОДЕ как "v2" (решение владельца
19.09.2026): потеря или сброс .pm2.env не должна молча откатывать корень
на legacy. Осознанный откат — CRM_FRONTEND=legacy в .pm2.env + рестарт
pm2; эти тесты фиксируют обе стороны контракта.

ВАЖНО: main импортируется внутри тестов, а не на уровне модуля — импорт
main создаёт таблицы (create_all на импорте), и на этапе коллекции он
сработал бы ДО session-фикстуры с продовым DDL, уронив её на
«table users already exists» (см. conftest).
"""


def test_default_frontend_is_v2():
    """Сброс окружения = v2, а не legacy: откат только явным CRM_FRONTEND."""
    import main
    assert main.FRONTEND_MODE == "v2"


def test_root_redirects_to_v2(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "v2")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/v2/"


def test_root_serves_legacy_on_explicit_rollback(client, monkeypatch):
    """CRM_FRONTEND=legacy возвращает старое поведение корня."""
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "legacy")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]


def test_legacy_paths_alive_in_v2_mode(client, monkeypatch):
    """Заморозка ≠ удаление: /legacy и /legacy/admin работают и в v2-режиме."""
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "v2")
    assert client.get("/legacy").status_code == 200
    assert client.get("/legacy/admin").status_code == 200


def test_admin_follows_front_mode(client, monkeypatch):
    """В v2-режиме /admin — в SPA (закладки не ведут в замороженный UI),
    в legacy-режиме — старая админка, как раньше."""
    import main
    monkeypatch.setattr(main, "FRONTEND_MODE", "v2")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/v2/"

    monkeypatch.setattr(main, "FRONTEND_MODE", "legacy")
    assert client.get("/admin", follow_redirects=False).status_code == 200
