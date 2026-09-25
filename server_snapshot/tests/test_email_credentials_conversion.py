"""BA-12: явная CLI-конвертация настроек почты."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet

from email_credentials import decrypt_credential


ROOT = Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "tools" / "convert_email_settings_fernet.py"
CURRENT_KEY = bytes(range(32)).hex()
LEGACY_RAW_KEY = bytes(reversed(range(32)))
LEGACY_KEY = base64.urlsafe_b64encode(LEGACY_RAW_KEY).decode("ascii")
PASSWORD = "conversion-password-do-not-print"


def _settings(path: Path, password: str) -> Path:
    path.write_text(json.dumps({
        "email": "mailbox@example.test",
        "password": password,
        "imap_server": "imap.example.test",
        "target_status": "Новый запрос",
        "last_sync": None,
    }), encoding="utf-8")
    path.chmod(0o644)
    return path


def _run(paths, *args, key=CURRENT_KEY):
    env = os.environ.copy()
    env["CRM_EMAIL_SECRET_KEY"] = key
    env["CRM_DEPLOYMENT"] = "test"
    return subprocess.run(
        [sys.executable, str(CONVERTER), "--apply", *map(str, paths), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_apply_flag_is_required_without_reading_or_writing(tmp_path):
    target = _settings(tmp_path / "settings.json", PASSWORD)
    before = target.read_bytes()

    result = subprocess.run(
        [sys.executable, str(CONVERTER), str(target)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--apply обязателен" in result.stderr
    assert target.read_bytes() == before


def test_conversion_success_is_idempotent_and_private(tmp_path):
    target = _settings(tmp_path / "settings.json", PASSWORD)

    first = _run([target])
    assert first.returncode == 0, first.stderr
    first_payload = target.read_bytes()
    first_token = json.loads(first_payload)["password"]
    fernet = Fernet(base64.urlsafe_b64encode(bytes.fromhex(CURRENT_KEY)))
    assert decrypt_credential(first_token, fernet) == PASSWORD
    assert target.stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in first.stdout + first.stderr
    assert first_token not in first.stdout + first.stderr

    second = _run([target])
    assert second.returncode == 0, second.stderr
    assert target.read_bytes() == first_payload
    assert "уже зашифрован" in second.stdout


def test_valid_legacy_key_ciphertext_is_unchanged(tmp_path):
    target = _settings(tmp_path / "settings.json", PASSWORD)
    legacy_fernet = Fernet(base64.urlsafe_b64encode(LEGACY_RAW_KEY))
    token = legacy_fernet.encrypt(PASSWORD.encode()).decode()
    _settings(target, token)
    legacy_path = tmp_path / "legacy.key"
    legacy_path.write_text(LEGACY_KEY, encoding="ascii")
    legacy_path.chmod(0o600)
    before = target.read_bytes()

    result = _run([target], "--legacy-key-path", str(legacy_path))

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == before
    assert "legacy-ключом" in result.stdout


def test_malformed_token_fails_without_prints(tmp_path):
    malformed = "gAAAA-malformed-secret-token"
    target = _settings(tmp_path / "settings.json", malformed)
    before = target.read_bytes()

    result = _run([target])

    assert result.returncode == 1
    assert before == target.read_bytes()
    assert malformed not in result.stdout + result.stderr


def test_all_or_nothing_validation_happens_before_writes(tmp_path):
    first = _settings(tmp_path / "first.json", PASSWORD)
    second = _settings(tmp_path / "second.json", "gAAAA-broken")
    first_before = first.read_bytes()
    second_before = second.read_bytes()

    result = _run([first, second])

    assert result.returncode == 1
    assert first.read_bytes() == first_before
    assert second.read_bytes() == second_before


def test_invalid_current_key_blocks_conversion(tmp_path):
    target = _settings(tmp_path / "settings.json", PASSWORD)
    before = target.read_bytes()

    result = _run([target], key=os.environ["CRM_SECRET_KEY"])

    assert result.returncode == 1
    assert target.read_bytes() == before
    assert os.environ["CRM_SECRET_KEY"] not in result.stdout + result.stderr


def test_explicit_legacy_key_must_use_documented_format(tmp_path):
    target = _settings(tmp_path / "settings.json", PASSWORD)
    legacy = tmp_path / "legacy.key"
    legacy.write_text("not-a-supported-key", encoding="ascii")
    before = target.read_bytes()

    result = _run([target], "--legacy-key-path", str(legacy))

    assert result.returncode == 1
    assert target.read_bytes() == before
    assert "not-a-supported-key" not in result.stdout + result.stderr
