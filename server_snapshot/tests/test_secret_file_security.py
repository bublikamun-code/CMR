"""Безопасная запись и preflight локальных файлов с секретами."""
import os
from pathlib import Path
import subprocess
import sys

import auth
from tools import check_secret_permissions
from secure_files import (
    PRIVATE_FILE_MODE,
    atomic_write_private,
    audit_private_files,
    inspect_private_file,
)

ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = ROOT / "tools" / "check_secret_permissions.py"


def test_atomic_private_write_replaces_file_without_broad_mode(tmp_path):
    target = tmp_path / "secret.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)

    atomic_write_private(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o777 == PRIVATE_FILE_MODE
    assert list(tmp_path.glob(".secret.json.*.tmp")) == []


def test_auth_key_creation_uses_private_mode_only_in_temp(tmp_path, monkeypatch):
    target = tmp_path / ".secret_key"
    monkeypatch.delenv("CRM_TEST_UNUSED_SECRET_ENV", raising=False)

    value = auth._load_or_create_key(str(target), "CRM_TEST_UNUSED_SECRET_ENV")

    assert value
    assert target.read_text(encoding="utf-8") == value
    assert target.stat().st_mode & 0o777 == PRIVATE_FILE_MODE


def test_preflight_discovers_canonical_email_key_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))

    paths = check_secret_permissions.discover_paths()

    assert (tmp_path / ".email_secret_key") in paths


def test_metadata_check_reports_only_mode_and_owner(tmp_path):
    private = tmp_path / "private"
    broad = tmp_path / "broad"
    private.write_text("not inspected", encoding="utf-8")
    broad.write_text("also not inspected", encoding="utf-8")
    private.chmod(0o600)
    broad.chmod(0o644)

    assert inspect_private_file(private)["secure"] is True
    assert inspect_private_file(broad)["secure"] is False
    metadata = inspect_private_file(broad)
    assert set(metadata) == {
        "path", "exists", "type", "mode", "uid", "owner", "secure"
    }
    assert "not inspected" not in str(metadata)
    assert metadata["uid"] == os.geteuid()


def test_audit_uses_warning_in_dev_and_error_in_production(tmp_path):
    broad = tmp_path / "broad"
    broad.write_text("secret", encoding="utf-8")
    broad.chmod(0o644)

    dev = audit_private_files([broad], production=False)[0]
    prod = audit_private_files([broad], production=True)[0]
    assert dev["level"] == "WARNING"
    assert prod["level"] == "ERROR"


def test_preflight_cli_exit_codes_on_temp_paths_only(tmp_path):
    private = tmp_path / "private"
    broad = tmp_path / "broad"
    private.write_text("private", encoding="utf-8")
    broad.write_text("broad", encoding="utf-8")
    private.chmod(0o600)
    broad.chmod(0o644)

    original_mode = broad.stat().st_mode & 0o777
    dev = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), str(private), str(broad)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    prod = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--production", str(private), str(broad)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert dev.returncode == 0, dev.stderr
    assert "WARNING" in dev.stdout
    assert prod.returncode == 1
    assert "ERROR" in prod.stdout
    assert "private\n" not in dev.stdout
    assert "broad\n" not in dev.stdout
    assert broad.stat().st_mode & 0o777 == original_mode
