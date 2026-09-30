"""pytest-плагин: учёт покрытия HTTP-эндпоинтов (route coverage).

Зачем: покрытие строк (`pytest --cov`) не отвечает на вопрос «а весь ли
функционал CRM вообще дёргается тестами». Один и тот же процент строк можно
набрать, ни разу не вызвав половину ручек. Этот плагин записывает каждый
вызов APIRoute по шаблону пути (`/cards/{card_id}`) и в конце прогона печатает
список непокрытых маршрутов.

Включается не всегда, а только явным флагом — иначе лишняя нагрузка на каждый
тестовый запуск:

    .venv/bin/python -m pytest -q -p tests.route_coverage
    .venv/bin/python -m pytest -q -p tests.route_coverage \\
        --route-coverage-report=/tmp/routes.md --route-coverage-allowlist=tests/route_coverage_allow.json

`--route-coverage-strict` превращает непокрытые маршруты в падение прогона
(удобно как гейт в CI); по умолчанию гейт выключен, потому что в allowlist
есть и маршруты, требующие живого железа (SMTP-отправка, вебхуки наружу).

Плагин ничего не знает про фикстуры приложения: он подменяет
`fastapi.routing.APIRoute.get_route_handler` и пишет в потокобезопасный
счётчик. Отчёты и сам allowlist — единственные новые файлы, которые он
порождает; состояние между тестами не протекает.
"""
from __future__ import annotations

import json
import threading

import pytest
from collections import Counter
from pathlib import Path

# --- состояние плагина -----------------------------------------------------
_LOCK = threading.Lock()
_HITS: Counter = Counter()
# (method, path) -> set(reason) из докстрингов-исключений ниже
_IGNORED: set[tuple[str, str]] = set()

# Маршруты, которые осмысленно не дёргаются из автотестов. Формат:
#   "<METHOD> <path>" -> причина
_IMPORT_RULES: dict[str, str] = {}


def _ignore(method: str, path: str, reason: str) -> None:
    _IGNORED.add((method.upper(), path))


def _load_allowlist(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.get("skipped", {}).items()}


def _iter_routes(app):
    """Все маршруты приложения (Mount'ы статики методов не имеют — они отсеются).

    Префикс /api в коде не нужен: nginx его срезает, а роутеры объявлены без
    него, поэтому фильтр по префиксу тихо терял бы половину API.
    """
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            yield method, path


def _patch_apiroute() -> None:
    """Патчит класс APIRoute, НЕ импортируя main.

    Импорт `main` здесь был бы ошибкой: он тянет `create_all` (main.py:33), а
    сессионная фикстура tests/conftest.py::_init_schema накатывает прод-DDL
    поверх пустой базы и падает «table users already exists». Маршруты
    создаются при импорте main, поэтому достаточно пропатчить класс заранее —
    обёртка подхватится при конструировании каждого APIRoute.
    """
    from fastapi.routing import APIRoute

    if getattr(APIRoute, "_crm_route_coverage_patched", False):
        return
    original = APIRoute.get_route_handler

    def get_route_handler(self):
        handler = original(self)
        template = self.path
        methods = {m for m in (self.methods or set()) if m not in ("HEAD", "OPTIONS")}

        # Именно async def с той же сигнатурой (request), что у обработчика
        # FastAPI: starlette.routing.request_response выбирает между прямым
        # await и run_in_threadpool по is_async_callable, а sync-обёртка
        # вернула бы «coroutine is not callable».
        async def instrumented(request):
            if request.method in methods:
                with _LOCK:
                    _HITS[(request.method, template)] += 1
            return await handler(request)

        return instrumented

    APIRoute.get_route_handler = get_route_handler
    APIRoute._crm_route_coverage_patched = True


# --- опции pytest ----------------------------------------------------------
def pytest_addoption(parser):
    group = parser.getgroup("route-coverage")
    group.addoption(
        "--route-coverage-report",
        action="store",
        default=None,
        help="куда записать markdown-отчёт по покрытию маршрутов",
    )
    group.addoption(
        "--route-coverage-allowlist",
        action="store",
        default=str(Path(__file__).with_name("route_coverage_allow.json")),
        help="json с осознанно непокрытыми маршрутами",
    )
    group.addoption(
        "--route-coverage-strict",
        action="store_true",
        default=False,
        help="падать, если остались непокрытые маршруты вне allowlist",
    )


def pytest_configure(config):
    config._route_coverage_enabled = True


def pytest_sessionstart(session):
    _patch_apiroute()


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not getattr(config, "_route_coverage_enabled", False):
        return

    import main

    routes = sorted(set(_iter_routes(main.app)))
    allow = _load_allowlist(Path(config.getoption("--route-coverage-allowlist")))

    covered, uncovered = [], []
    for method, path in routes:
        key = f"{method} {path}"
        if _HITS.get((method, path), 0) > 0:
            covered.append((method, path, _HITS[(method, path)]))
        elif key in allow or key in _IMPORT_RULES:
            uncovered.append((method, path, allow.get(key) or _IMPORT_RULES[key]))
        else:
            uncovered.append((method, path, None))

    report_path = config.getoption("--route-coverage-report")
    lines = [
        "# Покрытие HTTP-маршрутов тестами",
        "",
        "Снимок прогона `pytest -p tests.route_coverage`. Число в скобках —",
        "сколько раз маршрут вызвали тесты за прогон: единица означает, что",
        "ручка задета, но почти не проверена.",
        "",
        f"- Всего маршрутов (метод+путь): **{len(routes)}**",
        f"- Покрыто: **{len(covered)}**",
        f"- Не покрыто: **{len(uncovered)}**",
        "",
        "## Не покрыто",
        "",
        "| Маршрут | Почему |",
        "| --- | --- |",
    ]
    for method, path, reason in uncovered:
        lines.append(f"| `{method} {path}` | {reason or '— TODO —'} |")
    if not uncovered:
        lines.append("| — | все маршруты покрыты |")
    lines += [
        "",
        "## Покрыто",
        "",
        "| Маршрут | Вызовов |",
        "| --- | --- |",
    ]
    for method, path, hits in sorted(covered, key=lambda row: (row[1], row[0])):
        lines.append(f"| `{method} {path}` | {hits} |")
    text = "\n".join(lines) + "\n"

    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")

    print("\n=== route coverage ===")
    print(f"маршрутов: {len(routes)}, покрыто: {len(covered)}, не покрыто: {len(uncovered)}")
    for method, path, reason in uncovered:
        print(f"  MISS {method} {path}" + (f"  ({reason})" if reason else ""))

    if config.getoption("--route-coverage-strict"):
        unexpected = [(m, p) for m, p, reason in uncovered if not reason]
        if unexpected:
            raise pytest.fail(
                "Непокрытые маршруты вне allowlist: "
                + ", ".join(f"{m} {p}" for m, p in unexpected)
            )
