#!/usr/bin/env python3
"""Гейт свежести site-v2: артефакт обязан совпадать с источниками.

`server_snapshot/site-v2/` — генерируемый каталог (см. `build_site_v2.py`), но
в git он лежит обычными файлами, и расхождение с источниками
(`tools/mockups/` + шаблоны `tools/v2-{api,boot}-template.js` + сам сборщик)
не ловил ни один существующий гейт: `stamp_assets.py` проверяет хэши ссылок
legacy-фронта, а `tools/check_js.sh` — только синтаксис.

Цена расхождения — тихий разъезд прода и репозитория: `tools/deploy.sh v2`
пересобирает артефакт перед выкладкой, а docker-образ бэкенда
(`COPY server_snapshot/ .`) и ручная выкладка берут то, что лежит в git.

Рабочее дерево проверка НЕ трогает: эталон собирается во временный каталог
и сравнивается с артефактом по хэшам содержимого файлов.

    python3 tools/check_site_v2_fresh.py            # из server_snapshot/
    python3 tools/check_site_v2_fresh.py --json     # машиночитаемый отчёт
    python3 tools/check_site_v2_fresh.py --root DIR # другой корень проекта

Коды выхода: 0 — артефакт свежий, 1 — рассинхрон (нужна пересборка),
2 — эталон не собрался (нет источников или упал `build_site_v2.py`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

BUILDER = Path("tools") / "build_site_v2.py"
OUT_DIR = Path("site-v2")
# Входы сборщика: всё, чья правка обязана сопровождаться пересборкой.
# Прочие файлы tools/ (stamp_assets.py и пр.) на site-v2 не влияют.
INPUTS = (
    Path("tools") / "mockups",
    Path("tools") / "v2-api-template.js",
    Path("tools") / "v2-boot-template.js",
    BUILDER,
)
# Служебные файлы, которые не являются частью артефакта: macOS кладёт
# .DS_Store в любой открытый в Finder каталог, .mimosa/ — состояние
# агентских инструментов (оба исключены из rsync в tools/deploy.sh).
IGNORED_PARTS = (".DS_Store", ".mimosa")


def _is_ignored(rel: str) -> bool:
    """Служебный путь: точечные имена или любой скрытый компонент пути."""
    parts = rel.split("/")
    return any(part in IGNORED_PARTS or part.startswith(".") for part in parts)


def tree_hashes(root: Path) -> tuple[dict[str, str], list[str]]:
    """Относительный posix-путь файла -> sha1 содержимого; плюс игнорируемые."""
    hashes: dict[str, str] = {}
    ignored: list[str] = []
    if not root.is_dir():
        return hashes, ignored
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_ignored(rel):
            ignored.append(rel)
            continue
        hashes[rel] = hashlib.sha1(
            path.read_bytes(), usedforsecurity=False
        ).hexdigest()
    return hashes, ignored


def build_reference(root: Path, workdir: Path) -> Path:
    """Собрать эталонный site-v2 во временный каталог, не трогая репозиторий.

    `build_site_v2.py` вычисляет ROOT от собственного `__file__`, поэтому
    достаточно положить его копию в `<workdir>/tools/` и дать туда же ссылки
    на настоящие входы: сборщик прочитает их и запишет результат в
    `<workdir>/site-v2`.
    """
    tools = workdir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / BUILDER, tools / BUILDER.name)
    for rel in INPUTS:
        if rel == BUILDER:
            continue
        source = root / rel
        target = tools / rel.name
        if source.is_dir():
            os.symlink(source, target, target_is_directory=True)
        else:
            os.symlink(source, target)

    result = subprocess.run(
        [sys.executable, str(tools / BUILDER.name)],
        cwd=workdir, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{BUILDER} завершился с кодом {result.returncode}\n"
            f"{(result.stderr or result.stdout).strip()}"
        )
    return workdir / OUT_DIR


def compare(root: Path) -> tuple[dict[str, list[str]], list[str], int]:
    """Собрать эталон и сравнить с артефактом.

    Возвращает отчёт по категориям, проигнорированные служебные пути и число
    учтённых файлов артефакта.
    """
    missing_inputs = [str(rel) for rel in INPUTS if not (root / rel).exists()]
    if missing_inputs:
        raise RuntimeError("нет источников v2: " + ", ".join(missing_inputs))

    with tempfile.TemporaryDirectory(prefix="site-v2-fresh-") as tmp:
        reference = build_reference(root, Path(tmp))
        expected, _ = tree_hashes(reference)
    actual, ignored = tree_hashes(root / OUT_DIR)

    report = {
        "missing": sorted(set(expected) - set(actual)),
        "extra": sorted(set(actual) - set(expected)),
        "differs": sorted(
            rel for rel in set(expected) & set(actual)
            if expected[rel] != actual[rel]
        ),
    }
    return report, ignored, len(actual)


def _text_report(root: Path, report: dict[str, list[str]], ignored: list[str],
                 total: int) -> str:
    lines = [f"=== свежесть {root / OUT_DIR} ===",
             f"  файлов в артефакте: {total}"]
    labels = (("differs", "содержимое расходится"),
              ("missing", "нет в артефакте"),
              ("extra", "лишний в артефакте"))
    for key, label in labels:
        lines.extend(f"  {label}: {rel}" for rel in report[key])
    if not any(report[key] for key, _ in labels):
        lines.append("  артефакт совпадает с источниками")
    lines.extend(f"  проигнорировано (служебное): {rel}" for rel in ignored)
    return "\n".join(lines)


REBUILD_HINT = (
    "Пересоберите артефакт и закоммитьте его ВМЕСТЕ с правкой источников:\n"
    "    cd server_snapshot && python3 tools/build_site_v2.py\n"
    "Источники: tools/mockups/, tools/v2-api-template.js, "
    "tools/v2-boot-template.js, tools/build_site_v2.py.\n"
    "Руками server_snapshot/site-v2/ не править — каталог генерируется."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="корень проекта (по умолчанию — родитель tools/)")
    parser.add_argument("--json", action="store_true", help="отчёт в JSON")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        report, ignored, total = compare(root)
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"НЕ УДАЛОСЬ собрать эталон site-v2: {exc}", file=sys.stderr)
        return 2

    stale = any(report[key] for key in ("missing", "extra", "differs"))

    if args.json:
        print(json.dumps({"ok": not stale, "files": total, **report,
                          "ignored": ignored}, ensure_ascii=False, indent=2))
    else:
        print(_text_report(root, report, ignored, total))
        if stale:
            # При перенаправлении вывода (CI-логи, scripts/check.sh > file)
            # stdout буферизуется, а stderr — нет: без явного flush подсказка
            # «пересоберите» уезжала ПЕРЕД списком расхождений и сообщение
            # читалось задом наперёд.
            sys.stdout.flush()
            print("\nРАССИНХРОН: site-v2 не соответствует источникам v2.",
                  file=sys.stderr)
            print(REBUILD_HINT, file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
