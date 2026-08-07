#!/usr/bin/env python3
"""
Синхронизация файлов CRM с FTP-сервером.

Режимы:
  pull  — скачать всё дерево с сервера в локальную папку
  push  — загрузить указанные файлы на сервер
  ls    — вывести дерево сервера с размерами и датами
  rm    — удалить указанные файлы на сервере

Учётные данные берутся из переменных окружения:
  CRM_FTP_HOST, CRM_FTP_USER, CRM_FTP_PASS, CRM_FTP_ROOT
"""

import ftplib
import os
import posixpath
import sys

HOST = os.environ.get("CRM_FTP_HOST", "")
USER = os.environ.get("CRM_FTP_USER", "")
PASS = os.environ.get("CRM_FTP_PASS", "")
ROOT = os.environ.get("CRM_FTP_ROOT", "/")

# Каталоги, которые не нужны в рабочей копии.
# uploads — 220 МБ счетов и накладных клиентов: это бизнес-данные, им нужен
# отдельный бэкап, в репозитории кода им не место.
SKIP_DIRS = {".venv", "__pycache__", "node_modules", ".git", "logs", "tmp",
             "uploads"}

# Расширения/файлы, которые не тянем при pull (большие или бинарные).
SKIP_PULL_SUFFIX = (".pyc", ".so", ".whl")

# Ручные бэкапы, накопленные на сервере (35 копий style.css и т.п.).
# Для точки отката важно текущее состояние, а не история чужих правок;
# качать их — это десятки мегабайт и минуты ожидания впустую.
SKIP_PULL_MARKERS = (".bak", ".backup_")


def connect():
    if not (HOST and USER and PASS):
        sys.exit("Не заданы CRM_FTP_HOST / CRM_FTP_USER / CRM_FTP_PASS")
    ftp = ftplib.FTP(HOST, timeout=60)
    ftp.login(USER, PASS)
    ftp.set_pasv(True)
    return ftp


def entries(ftp, path):
    """Вернуть список (имя, тип, размер) для каталога path."""
    out = []
    try:
        for name, facts in ftp.mlsd(path):
            if name in (".", ".."):
                continue
            out.append((name, facts.get("type", ""), int(facts.get("size", 0) or 0)))
        return out
    except ftplib.error_perm:
        pass
    # Fallback для серверов без MLSD.
    lines = []
    ftp.retrlines(f"LIST {path}", lines.append)
    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) < 9:
            continue
        name = parts[8]
        if name in (".", ".."):
            continue
        is_dir = line.startswith("d")
        size = 0 if is_dir else int(parts[4])
        out.append((name, "dir" if is_dir else "file", size))
    return out


def walk(ftp, path, prefix=""):
    """Рекурсивно обойти дерево, вернуть список (относительный путь, размер)."""
    result = []
    for name, kind, size in entries(ftp, path):
        rel = posixpath.join(prefix, name) if prefix else name
        if kind == "dir":
            if name in SKIP_DIRS:
                continue
            result.extend(walk(ftp, posixpath.join(path, name), rel))
        elif kind == "file":
            result.append((rel, size))
    return result


def cmd_ls(ftp, args):
    total = 0
    for rel, size in sorted(walk(ftp, ROOT)):
        total += size
        print(f"{size:>12}  {rel}")
    print(f"\nВсего файлов: подсчитано, суммарный размер {total/1048576:.1f} MB")


def _should_skip(rel):
    if rel.endswith(SKIP_PULL_SUFFIX):
        return True
    return any(marker in os.path.basename(rel) for marker in SKIP_PULL_MARKERS)


def cmd_pull(ftp, args):
    dest = args[0] if args else "."
    files = sorted(walk(ftp, ROOT))
    ok = failed = skipped = 0
    for rel, size in files:
        if _should_skip(rel):
            skipped += 1
            continue
        local = os.path.join(dest, rel)
        if os.path.isfile(local) and os.path.getsize(local) == size:
            skipped += 1
            continue
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        try:
            with open(local, "wb") as fh:
                ftp.retrbinary(f"RETR {posixpath.join(ROOT, rel)}", fh.write)
            ok += 1
            print(f"OK   {rel}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {rel}: {exc}")
    print(f"\nСкачано: {ok}, пропущено: {skipped}, ошибок: {failed}")


def cmd_push(ftp, args):
    if not args:
        sys.exit("push требует список файлов относительно корня проекта")
    base = os.environ.get("CRM_LOCAL_ROOT", ".")
    for rel in args:
        local = os.path.join(base, rel)
        if not os.path.isfile(local):
            print(f"SKIP {rel}: локального файла нет")
            continue
        remote = posixpath.join(ROOT, rel)
        # Создаём отсутствующие каталоги.
        parts = posixpath.dirname(rel).split("/") if posixpath.dirname(rel) else []
        cur = ROOT
        for part in parts:
            cur = posixpath.join(cur, part)
            try:
                ftp.mkd(cur)
            except ftplib.error_perm:
                pass
        with open(local, "rb") as fh:
            ftp.storbinary(f"STOR {remote}", fh)
        print(f"PUSH {rel}")


def cmd_rm(ftp, args):
    if not args:
        sys.exit("rm требует список файлов относительно корня проекта")
    for rel in args:
        remote = posixpath.join(ROOT, rel)
        try:
            ftp.delete(remote)
            print(f"DEL  {rel}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {rel}: {exc}")


COMMANDS = {"ls": cmd_ls, "pull": cmd_pull, "push": cmd_push, "rm": cmd_rm}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"Использование: {sys.argv[0]} {{{'|'.join(COMMANDS)}}} [аргументы]")
    ftp = connect()
    try:
        COMMANDS[sys.argv[1]](ftp, sys.argv[2:])
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()


if __name__ == "__main__":
    main()
