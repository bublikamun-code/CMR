#!/usr/bin/env python3
"""
Простановка версий статики по содержимому файла.

Зачем: в проекте нет сборщика, и параметры ?v=... в index.html
проставлялись руками. Они разъехались (часть файлов застряла на v=30,
часть получила таймстемпы), из-за чего браузер отдавал пользователю
старый JS при обновлённом HTML — источник плавающих багов «у меня
работает, у тебя нет».

Решение: версия = первые 10 символов sha1 от содержимого файла.
Изменился файл — изменилась ссылка, кеш сбрасывается только у него.
Не изменился — ссылка прежняя, кеш продолжает работать.

Запускать перед каждой выкладкой:
    python3 tools/stamp_assets.py           # проставить
    python3 tools/stamp_assets.py --check   # только проверить (для CI)
"""

import hashlib
import os
import re
import sys
from pathlib import Path

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "site")
SITE = os.path.normpath(SITE)

# В каких файлах искать ссылки на статику.
HTML_FILES = ["index.html", "admin.html", "settings.html",
              "workflows.html", "custom_objects.html"]

# src="js/foo.js?v=..." или href="css/style.css?v=..."
ASSET_RE = re.compile(
    r'((?:src|href)=")((?:js|css)/[^"?]+\.(?:js|css))(\?v=[^"]*)?(")'
)


def file_version(rel_path):
    """Версия файла = хеш содержимого. None, если файла нет."""
    full = os.path.join(SITE, rel_path)
    if not os.path.isfile(full):
        return None
    digest = hashlib.sha1()
    with open(full, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:10]


def process(html_name, check_only):
    path = os.path.join(SITE, html_name)
    if not os.path.isfile(path):
        return 0, 0, []

    with open(path, encoding="utf-8") as fh:
        original = fh.read()

    missing = []
    changed = [0]

    def replace(match):
        prefix, asset, old_query, suffix = match.groups()
        version = file_version(asset)
        if version is None:
            missing.append(asset)
            return match.group(0)
        new_query = "?v=" + version
        if old_query != new_query:
            changed[0] += 1
        return f"{prefix}{asset}{new_query}{suffix}"

    updated = ASSET_RE.sub(replace, original)

    if not check_only and updated != original:
        Path(path).write_text(updated, encoding="utf-8")

    total = len(ASSET_RE.findall(original))
    return total, changed[0], missing


def main():
    check_only = "--check" in sys.argv
    total_assets = total_changed = 0
    all_missing = []

    for name in HTML_FILES:
        total, changed, missing = process(name, check_only)
        if total:
            state = "требуют обновления" if check_only else "обновлено"
            print(f"{name}: ссылок {total}, {state} {changed}")
        total_assets += total
        total_changed += changed
        all_missing.extend((name, m) for m in missing)

    for name, asset in all_missing:
        print(f"ВНИМАНИЕ {name}: файл не найден на диске — {asset}", file=sys.stderr)

    print(f"\nВсего ссылок на статику: {total_assets}, расхождений: {total_changed}")

    if all_missing:
        return 2
    if check_only and total_changed:
        print("Версии статики устарели. Запустите tools/stamp_assets.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
