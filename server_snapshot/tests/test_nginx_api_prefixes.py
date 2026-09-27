"""Сверка nginx-прокси с реальными префиксами API.

Регулярка в nginx/snippets/app-locations.conf — это allowlist префиксов
роутеров. Всё, чего в ней нет, проваливается в `location /` → `try_files`
и отдаёт index.html вместо JSON: ровно тот «API возвращает HTML», о котором
предупреждает шапка самого файла. Два префикса (`dictionaries`,
`nakladnye`) так и выпали, и ни один тест этого не ловил.

Сверяемся с `main.app.routes`, а не с grep по routers/*.py: часть
префиксов задаётся переменной (`APIRouter(prefix=...)` не всегда литерал),
и такой grep их не находит.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SNIPPET = ROOT / "nginx" / "snippets" / "app-locations.conf"

# Префиксы, которые отдаёт не API, а сама статика nginx: их отсутствие в
# списке — норма, а не пропуск.
_NOT_API = {"css", "js", "v2", "legacy", "static", "manifest.json"}

_LOCATION_RE = re.compile(
    r"location\s+~\s+\^/\((?P<prefixes>[^)]*)\)\(/\|\$\)", re.MULTILINE
)


def _nginx_prefixes() -> set[str]:
    match = _LOCATION_RE.search(SNIPPET.read_text(encoding="utf-8"))
    assert match, "в app-locations.conf нет location ~ ^/(...)(/|$) — regex разъехался"
    return {p.replace("\\", "") for p in match.group("prefixes").split("|")}


def _app_prefixes() -> set[str]:
    # main импортируется здесь, а не в начале модуля: на импорте он зовёт
    # create_all, и на уровне модуля это случилось бы ДО session-фикстуры
    # conftest, который накатывает продовый DDL на пустую базу — и упал бы
    # с «table users already exists».
    import main

    prefixes = set()
    for route in main.app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        first = path.strip("/").split("/")[0]
        # {param} в корне — монтирование StaticFiles, не API-роут.
        if first and not first.startswith("{"):
            prefixes.add(first)
    return prefixes - _NOT_API


def test_every_api_prefix_is_proxied_by_nginx():
    listed = _nginx_prefixes()
    missing = _app_prefixes() - listed
    assert not missing, (
        "префиксы API не попали в nginx-регулярку и будут отдавать index.html: "
        + ", ".join(sorted(missing))
    )


def test_nginx_prefix_list_has_no_dead_entries():
    dead = _nginx_prefixes() - _app_prefixes()
    assert not dead, (
        "в nginx-регулярке остались префиксы без роутера: " + ", ".join(sorted(dead))
    )
