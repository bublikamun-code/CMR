#!/usr/bin/env python3
"""Авто-штамповка кэш-бастинга статических ассетов.

`main.py` отдаёт `/css/` и `/js/` с заголовком
`Cache-Control: public, max-age=31536000, immutable`, поэтому URL ассета
обязан меняться вместе с содержимым файла. Хэш считается здесь, а не руками:

    <link rel="stylesheet" href="css/style.css?v=<первые 10 символов sha1>">

Запускается из `scripts/deploy.sh` до копирования на сервер и в проверках
в режиме `--check`.

    python3 tools/stamp_assets.py            # пересчитать и записать в HTML
    python3 tools/stamp_assets.py --check    # только сверить, выход 1 при расхождении
    python3 tools/stamp_assets.py --json     # машиночитаемый отчёт

Коды выхода: 0 — всё в порядке, 1 — `--check` нашёл расхождения,
2 — HTML ссылается на несуществующий файл (такая ссылка дала бы 404,
который браузер закэшировал бы так же агрессивно, как и сам ассет).

Не покрывает осознанно: ассеты, которые JS подгружает динамически без хэша
(`js/dashboard.js:ensureChartLib` — вендорский Chart.js, файл не меняется).
В CSS локальных `url()` нет, поэтому вложенные ассеты не учитываются.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ASSET_DIRS = ("css", "js")
HASH_LEN = 10
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# href/src="css/..." или "js/..." — с уже стоящим ?v=... или без него.
# Ведущий слэш допустим: /js/api.js и js/api.js — это один и тот же файл.
REF_RE = re.compile(
    r'(?P<attr>href|src)="(?P<slash>/?)(?P<path>(?:css|js)/[^"?#]+)(?:\?v=[^"]*)?"'
)
V_RE = re.compile(r'\?v=([^"]*)')


def hash_file(path: Path) -> str:
    """Первые HASH_LEN символов sha1 содержимого — контракт из main.py.

    `usedforsecurity=False`: хэш здесь метка версии для кэш-бастинга, а не
    средство защиты. Флаг обязателен — без него hashlib отказывается создавать
    sha1 на сборках Python с FIPS-политикой, хотя криптографической роли
    алгоритм здесь не играет.
    """
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()[:HASH_LEN]


def collect_hashes(root: Path) -> dict[str, str]:
    """Относительный posix-путь ассета -> его хэш. Обход рекурсивный (js/vendor/)."""
    hashes: dict[str, str] = {}
    for directory in ASSET_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                hashes[path.relative_to(root).as_posix()] = hash_file(path)
    return hashes


def find_pages(root: Path) -> list[Path]:
    """HTML-страницы в корне проекта. Других страниц приложение не отдаёт."""
    return sorted(root.glob("*.html"))


@dataclass
class Ref:
    """Одна ссылка на ассет в одной странице."""

    page: str
    path: str
    current: str | None  # None, если ?v= в ссылке вообще не было
    expected: str | None  # None, если файла на диске нет


@dataclass
class Report:
    refs: list[Ref] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)

    @property
    def missing(self) -> list[Ref]:
        """Ссылки на несуществующие файлы."""
        return [r for r in self.refs if r.expected is None]

    @property
    def stale(self) -> list[Ref]:
        """Ссылки, у которых хэш отсутствует или не совпадает с содержимым."""
        return [r for r in self.refs if r.expected is not None and r.current != r.expected]

    @property
    def unreferenced(self) -> list[str]:
        """Ассеты, на которые не ссылается ни одна страница."""
        used = {r.path.lstrip("/") for r in self.refs}
        return sorted(set(self.hashes) - used)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.stale


def scan(root: Path, pages: list[Path] | None = None) -> Report:
    """Собрать все ссылки на ассеты и сравнить их хэши с фактическими."""
    hashes = collect_hashes(root)
    report = Report(hashes=hashes)
    for page in pages if pages is not None else find_pages(root):
        text = page.read_text(encoding="utf-8")
        for match in REF_RE.finditer(text):
            path = match.group("path")
            versioned = V_RE.search(match.group(0))
            report.refs.append(
                Ref(
                    page=page.name,
                    path=path,
                    current=versioned.group(1) if versioned else None,
                    expected=hashes.get(path),
                )
            )
    return report


def rewrite(page: Path, report: Report) -> bool:
    """Проставить в странице верные хэши. Возвращает True, если файл изменён."""
    expected = {r.path: r.expected for r in report.refs if r.expected is not None}
    text = page.read_text(encoding="utf-8")

    def repl(match: re.Match[str]) -> str:
        path = match.group("path")
        if path not in expected:
            return match.group(0)  # битая ссылка: не трогаем, её вернёт scan()
        return f'{match.group("attr")}="{match.group("slash")}{path}?v={expected[path]}"'

    new_text = REF_RE.sub(repl, text)
    if new_text != text:
        page.write_text(new_text, encoding="utf-8")
        return True
    return False


def stamp(
    root: Path = DEFAULT_ROOT, pages: list[Path] | None = None
) -> tuple[Report, list[str]]:
    """Пересчитать хэши и записать их в HTML.

    Возвращает отчёт о состоянии ДО правки и имена изменённых страниц.
    """
    targets = pages if pages is not None else find_pages(root)
    report = scan(root, targets)
    changed = [page.name for page in targets if rewrite(page, report)]
    return report, changed


def _text_summary(report: Report) -> str:
    lines = [
        f"ассетов: {len(report.hashes)}, ссылок в HTML: {len(report.refs)}",
    ]
    lines.extend(f"  НЕТ ФАЙЛА  {ref.page}: {ref.path}" for ref in report.missing)
    for ref in report.stale:
        was = ref.current if ref.current is not None else "без ?v="
        lines.append(f"  хэш устарел {ref.page}: {ref.path} ({was} -> {ref.expected})")
    if not report.missing and not report.stale:
        lines.append("  все хэши совпадают с содержимым файлов")
    lines.extend(f"  без ссылки  {path}" for path in report.unreferenced)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="корень проекта (по умолчанию — родитель tools/)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="не записывать, только сверить хэши (выход 1 при расхождении)",
    )
    parser.add_argument("--json", action="store_true", help="отчёт в JSON")
    parser.add_argument("pages", nargs="*", type=Path, help="HTML-страницы (по умолчанию все *.html в корне)")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    pages = [p if p.is_absolute() else root / p for p in args.pages] or None

    if args.check:
        report = scan(root, pages)
        changed: list[str] = []
    else:
        report, changed = stamp(root, pages)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "assets": len(report.hashes),
                    "refs": len(report.refs),
                    "changed": changed,
                    "missing": [{"page": r.page, "path": r.path} for r in report.missing],
                    "stale": [
                        {"page": r.page, "path": r.path, "current": r.current, "expected": r.expected}
                        for r in report.stale
                    ],
                    "unreferenced": report.unreferenced,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        action = "проверка" if args.check else "штамповка"
        print(f"=== {action} ассетов ({root}) ===")
        print(_text_summary(report))
        if changed:
            print(f"  записано страниц: {len(changed)} ({', '.join(changed)})")

    if report.missing:
        return 2
    if args.check and report.stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
