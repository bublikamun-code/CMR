"""Тесты tools/stamp_assets.py — кэш-бастинг статических ассетов.

Проверяют контракт из main.py: `/css/` и `/js/` отдаются с
`Cache-Control: max-age=31536000, immutable`, значит хэш в URL обязан быть
ровно первыми 10 символами sha1 содержимого файла и меняться вместе с ним.

Отдельно закреплён гейт на реальном репозитории
(`test_repo_html_hashes_match_assets`): именно его отсутствие позволило хэшам
в HTML разъехаться с файлами и сделать деплой невидимым для пользователей.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Модуль регистрируется в sys.modules ДО exec_module: в stamp_assets есть
# @dataclass, а dataclasses резолвит аннотации через sys.modules[cls.__module__]
# и падает с AttributeError, если загружаемого по пути модуля там нет.
_spec = importlib.util.spec_from_file_location("stamp_assets", ROOT / "tools" / "stamp_assets.py")
stamp_assets = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stamp_assets
_spec.loader.exec_module(stamp_assets)


def _tree(tmp_path: Path, *, files: dict[str, str], pages: dict[str, str]) -> Path:
    """Мини-проект: css/, js/ и HTML-страницы в корне."""
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name, content in pages.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _sha1_10(text: str) -> str:
    # usedforsecurity=False — как в tools/stamp_assets.py: метка версии, не защита
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:10]


# --- хэш -------------------------------------------------------------------


def test_hash_is_first_ten_of_sha1(tmp_path):
    path = tmp_path / "a.css"
    path.write_text("body { color: red; }", encoding="utf-8")
    assert stamp_assets.hash_file(path) == _sha1_10("body { color: red; }")


def test_vendor_subdirectory_is_covered(tmp_path):
    """js/vendor/ обходится рекурсивно: вендорские файлы кэшируются так же агрессивно."""
    root = _tree(
        tmp_path,
        files={"js/vendor/chart.js": "/* chart */"},
        pages={"index.html": '<script src="js/vendor/chart.js"></script>'},
    )
    report = stamp_assets.scan(root)
    assert report.hashes == {"js/vendor/chart.js": _sha1_10("/* chart */")}
    assert report.refs[0].expected == _sha1_10("/* chart */")


# --- штамповка -------------------------------------------------------------


def test_stamp_writes_correct_hash(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}", "js/api.js": "// api"},
        pages={
            "index.html": '<link rel="stylesheet" href="css/style.css?v=61b8bfe2d1">\n'
            '<script defer src="js/api.js?v=b135bf0fbe"></script>'
        },
    )
    report, changed = stamp_assets.stamp(root)
    assert changed == ["index.html"]
    # отчёт stamp() описывает состояние ДО правки — обе ссылки были устаревшими
    assert len(report.stale) == 2
    html = (root / "index.html").read_text(encoding="utf-8")
    assert f'href="css/style.css?v={_sha1_10("a{}")}"' in html
    assert f'src="js/api.js?v={_sha1_10("// api")}"' in html
    assert stamp_assets.scan(root).ok


def test_stamp_replaces_non_hash_version_values(tmp_path):
    """Реальные случаи из репозитория: ?v=6 (ручной) и ?v=1789080185 (timestamp)."""
    root = _tree(
        tmp_path,
        files={"js/nakladnye.js": "// n", "js/boot.js": "// b"},
        pages={
            "index.html": '<script src="js/nakladnye.js?v=6"></script>'
            '<script src="js/boot.js?v=1789080185"></script>'
        },
    )
    stamp_assets.stamp(root)
    html = (root / "index.html").read_text(encoding="utf-8")
    assert f'js/nakladnye.js?v={_sha1_10("// n")}' in html
    assert f'js/boot.js?v={_sha1_10("// b")}' in html


def test_stamp_adds_version_to_bare_reference(tmp_path):
    root = _tree(
        tmp_path,
        files={"js/api.js": "// api"},
        pages={"index.html": '<script src="js/api.js"></script>'},
    )
    report = stamp_assets.scan(root)
    assert report.refs[0].current is None
    stamp_assets.stamp(root)
    assert f'src="js/api.js?v={_sha1_10("// api")}"' in (root / "index.html").read_text(encoding="utf-8")


def test_stamp_handles_leading_slash(tmp_path):
    """/js/api.js и js/api.js — один файл, оба получают верный хэш."""
    root = _tree(
        tmp_path,
        files={"js/api.js": "// api"},
        pages={"index.html": '<script src="/js/api.js?v=deadbeef00"></script>'},
    )
    stamp_assets.stamp(root)
    assert f'src="/js/api.js?v={_sha1_10("// api")}"' in (root / "index.html").read_text(encoding="utf-8")


def test_stamp_is_idempotent(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}", "js/api.js": "// api"},
        pages={"index.html": '<link href="css/style.css?v=old0000000">\n<script src="js/api.js"></script>'},
    )
    stamp_assets.stamp(root)
    first = (root / "index.html").read_text(encoding="utf-8")

    _report, changed = stamp_assets.stamp(root)
    assert changed == []
    assert (root / "index.html").read_text(encoding="utf-8") == first


def test_stamp_changes_hash_when_asset_content_changes(tmp_path):
    """Смысл кэш-бастинга: правка одной строки CSS меняет URL ассета."""
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={"index.html": '<link href="css/style.css?v=x"></link>'},
    )
    stamp_assets.stamp(root)
    before = (root / "index.html").read_text(encoding="utf-8")

    (root / "css" / "style.css").write_text("a{} b{}", encoding="utf-8")
    stamp_assets.stamp(root)
    after = (root / "index.html").read_text(encoding="utf-8")

    assert before != after
    assert f'href="css/style.css?v={_sha1_10("a{} b{}")}"' in after


def test_every_page_is_stamped_separately(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={
            "index.html": '<link href="css/style.css?v=1111111111">',
            "admin.html": '<link href="css/style.css?v=2222222222">',
        },
    )
    _report, changed = stamp_assets.stamp(root)
    assert changed == ["admin.html", "index.html"]
    expected = f'css/style.css?v={_sha1_10("a{}")}'
    assert expected in (root / "index.html").read_text(encoding="utf-8")
    assert expected in (root / "admin.html").read_text(encoding="utf-8")


def test_text_outside_href_src_is_not_touched(tmp_path):
    """Упоминание пути в тексте или в инлайн-скрипте не является ссылкой на ассет."""
    body = (
        '<p>see css/style.css and js/api.js</p>\n'
        "<script>var note = 'js/api.js загружается ниже';</script>\n"
        '<script src="js/api.js?v=stale00000"></script>'
    )
    root = _tree(tmp_path, files={"js/api.js": "// api", "css/style.css": "a{}"}, pages={"index.html": body})
    stamp_assets.stamp(root)
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "<p>see css/style.css and js/api.js</p>" in html
    assert "var note = 'js/api.js загружается ниже';" in html
    assert f'src="js/api.js?v={_sha1_10("// api")}"' in html


# --- битые ссылки и мёртвые ассеты ----------------------------------------


def test_missing_asset_file_is_reported(tmp_path):
    root = _tree(
        tmp_path,
        files={"js/api.js": "// api"},
        pages={"index.html": '<script src="js/gone.js?v=aaaaaaaaaa"></script>'},
    )
    report = stamp_assets.scan(root)
    assert [r.path for r in report.missing] == ["js/gone.js"]
    assert not report.ok


def test_missing_asset_file_fails_cli_with_code_2(tmp_path):
    """Битая ссылка — остановка деплоя: браузер получил бы 404 и закэшировал его."""
    root = _tree(
        tmp_path,
        files={"js/api.js": "// api"},
        pages={"index.html": '<script src="js/gone.js"></script>'},
    )
    assert stamp_assets.main(["--root", str(root)]) == 2
    assert stamp_assets.main(["--root", str(root), "--check"]) == 2


def test_unreferenced_assets_are_reported_but_not_fatal(tmp_path):
    root = _tree(
        tmp_path,
        files={"js/api.js": "// api", "js/saved_views.js": "// dead"},
        pages={"index.html": '<script src="js/api.js?v=x"></script>'},
    )
    report = stamp_assets.scan(root)
    assert report.unreferenced == ["js/saved_views.js"]
    stamp_assets.stamp(root)
    assert stamp_assets.main(["--root", str(root), "--check"]) == 0


# --- режим --check ---------------------------------------------------------


def test_check_mode_exits_1_on_stale_hash_and_does_not_write(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={"index.html": '<link href="css/style.css?v=61b8bfe2d1">'},
    )
    before = (root / "index.html").read_text(encoding="utf-8")
    assert stamp_assets.main(["--root", str(root), "--check"]) == 1
    assert (root / "index.html").read_text(encoding="utf-8") == before


def test_check_mode_exits_0_when_hashes_match(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={"index.html": f'<link href="css/style.css?v={_sha1_10("a{}")}">'},
    )
    assert stamp_assets.main(["--root", str(root), "--check"]) == 0


def test_stamp_mode_exits_0_after_fixing_stale_hash(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={"index.html": '<link href="css/style.css?v=61b8bfe2d1">'},
    )
    assert stamp_assets.main(["--root", str(root)]) == 0
    assert stamp_assets.main(["--root", str(root), "--check"]) == 0


def test_explicit_page_list_limits_scope(tmp_path):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={
            "index.html": '<link href="css/style.css?v=stale00000">',
            "admin.html": '<link href="css/style.css?v=stale00000">',
        },
    )
    stamp_assets.main(["--root", str(root), "index.html"])
    assert _sha1_10("a{}") in (root / "index.html").read_text(encoding="utf-8")
    assert "stale00000" in (root / "admin.html").read_text(encoding="utf-8")


def test_json_output_is_machine_readable(tmp_path, capsys):
    root = _tree(
        tmp_path,
        files={"css/style.css": "a{}"},
        pages={"index.html": '<link href="css/style.css?v=stale00000">'},
    )
    stamp_assets.main(["--root", str(root), "--check", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["assets"] == 1
    assert payload["stale"][0]["path"] == "css/style.css"
    assert payload["stale"][0]["expected"] == _sha1_10("a{}")


# --- гейт на реальном репозитории -----------------------------------------


def test_repo_html_hashes_match_assets():
    """Хэши в index.html/admin.html обязаны совпадать с файлами css/ и js/.

    Рассинхрон здесь = деплой невиден пользователям: main.py отдаёт ассеты
    с max-age=31536000, immutable, поэтому браузер продолжит брать старую
    версию файла по URL с устаревшим хэшем.
    """
    report = stamp_assets.scan(ROOT)
    stale = [(r.page, r.path, r.current, r.expected) for r in report.stale]
    missing = [(r.page, r.path) for r in report.missing]
    assert not missing, f"HTML ссылается на несуществующие файлы: {missing}"
    assert not stale, f"хэши в HTML не совпадают с содержимым файлов: {stale}"


def test_repo_has_at_least_two_pages_and_assets():
    """Гейт выше не должен молча проходить на пустом наборе файлов."""
    report = stamp_assets.scan(ROOT)
    assert {r.page for r in report.refs} >= {"index.html", "admin.html"}
    assert len(report.hashes) >= 10


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
