"""Атомарная запись и проверка прав локальных файлов с секретами."""
import os
import pwd
import stat
import tempfile
from pathlib import Path


PRIVATE_FILE_MODE = 0o600


def atomic_write_private(path: str | os.PathLike, content: str | bytes) -> None:
    """Записать файл через временный файл в том же каталоге с правами 0600.

    Содержимое не логируется и не попадает во временное имя. ``os.replace``
    не оставляет окно, в котором новый секрет был бы виден с широкими правами.
    """
    target = Path(path)
    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with os.fdopen(fd, mode, **kwargs) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise

    # После замены режим задаёт новый inode, но повторная установка делает
    # контракт независимым от поведения конкретной файловой системы.
    os.chmod(target, PRIVATE_FILE_MODE)


def inspect_private_file(
    path: str | os.PathLike, expected_uid: int | None = None
) -> dict:
    """Собрать только метаданные приватного файла, не открывая его."""
    file_path = Path(path)
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    try:
        file_stat = file_path.lstat()
    except FileNotFoundError:
        return {
            "path": str(file_path),
            "exists": False,
            "type": "missing",
            "mode": None,
            "uid": None,
            "owner": None,
            "secure": True,
        }

    mode = stat.S_IMODE(file_stat.st_mode)
    try:
        owner = pwd.getpwuid(file_stat.st_uid).pw_name
    except KeyError:
        owner = str(file_stat.st_uid)
    file_type = "regular" if stat.S_ISREG(file_stat.st_mode) else "special"
    secure = (
        file_type == "regular"
        and mode == PRIVATE_FILE_MODE
        and file_stat.st_uid == expected_uid
    )
    return {
        "path": str(file_path),
        "exists": True,
        "type": file_type,
        "mode": f"{mode:04o}",
        "uid": file_stat.st_uid,
        "owner": owner,
        "secure": secure,
    }


def audit_private_files(
    paths: list[str | os.PathLike], *, production: bool = False
) -> list[dict]:
    """Проверить режимы без чтения содержимого.

    На dev широкие права — предупреждение, в production — ошибка с ненулевым
    кодом у вызывающего CLI. Отсутствующие опциональные файлы безопасны.
    """
    findings = []
    seen = set()
    for path in paths:
        resolved = str(Path(path))
        if resolved in seen:
            continue
        seen.add(resolved)
        metadata = inspect_private_file(resolved)
        metadata["level"] = "OK"
        if metadata["exists"] and not metadata["secure"]:
            metadata["level"] = "ERROR" if production else "WARNING"
        findings.append(metadata)
    return findings
