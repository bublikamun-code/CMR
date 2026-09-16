"""Изолированные регрессии restart: только bash и локальные заглушки.

Запускать этот файл с pytest --noconftest: общий conftest поднимает API/БД.
PATH не содержит системных ssh, pm2, sleep или rsync; HOME и REMOTE_DIR
всегда находятся в tmp_path, настоящий deploy ничего не отправляет.
"""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "deploy.sh"

_FAKE_COMMAND = r'''
import json
import os
from pathlib import Path
import subprocess
import sys

name = Path(sys.argv[0]).name
root = Path(os.environ["TEST_ROOT"]).resolve()
for key in ("HOME", "REMOTE_DIR", "TEST_LOG"):
    if not Path(os.environ[key]).resolve().is_relative_to(root):
        raise SystemExit("Небезопасный путь: " + key)
entry = {"command": name, "args": sys.argv[1:]}
if name == "pm2":
    entry["env"] = {key: os.environ.get(key) for key in
                    ("CRM_DATA_DIR", "CRM_UPLOADS_DIR", "DEPLOY_TEST_VALUE")}
with open(os.environ["TEST_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\n")
if name == "ssh":
    args = sys.argv[1:]
    while args and args[0] in ("-i", "-o"):
        args = args[2:]
    if len(args) != 2 or args[0] != "deploy-test@localhost":
        raise SystemExit("Неожиданные аргументы ssh")
    command = args[1]
    if "/var/www/" in command or "87.232.64.12" in command:
        raise SystemExit("Боевой адрес запрещён")
    result = subprocess.run(["/bin/bash", "-c", command], cwd=root,
                            env=os.environ.copy(), timeout=5)
    raise SystemExit(result.returncode)
if name == "pm2":
    if sys.argv[1:2] == ["restart"]:
        raise SystemExit(int(os.environ.get("TEST_PM2_EXIT", "0")))
    raise SystemExit(0)
if name == "sleep":
    raise SystemExit(0)
raise SystemExit("Запрещённая команда: " + name)
'''


@pytest.fixture
def deploy_sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    remote_dir = tmp_path / "remote directory"
    remote_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    # Только необходимые утилиты; исходный PATH и окружение не наследуем.
    for name, target in (("bash", "/bin/bash"), ("dirname", "/usr/bin/dirname")):
        (bin_dir / name).symlink_to(target)
    for name in ("ssh", "pm2", "sleep", "rsync", "scp"):
        executable = bin_dir / name
        executable.write_text("#!" + sys.executable + "\n" + _FAKE_COMMAND)
        executable.chmod(0o700)
    env = {
        "PATH": str(bin_dir),
        "HOME": str(home),
        "REMOTE_DIR": str(remote_dir),
        "SSH_KEY": str(home / "unused-test-key"),
        "SSH_USER": "deploy-test",
        "SSH_HOST": "localhost",
        "PM2_APP": "crm-test",
        "TEST_ROOT": str(tmp_path),
        "TEST_LOG": str(log),
    }

    def run(*args):
        result = subprocess.run(
            ["/bin/bash", str(DEPLOY_SCRIPT), *args], cwd=tmp_path,
            env=env, capture_output=True, text=True, timeout=10,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return remote_dir, env, run


def _pm2_calls(calls):
    return [call for call in calls if call["command"] == "pm2"]


def test_dry_run_restart_has_no_external_calls(deploy_sandbox):
    _, _, run = deploy_sandbox
    result, calls = run("--dry-run", "restart")
    assert result.returncode == 0, result.stderr
    assert calls == [], "Dry-run не должен вызывать ssh, pm2, sleep или передачу файлов"


@pytest.mark.parametrize("failure", ["missing_directory", "missing_env", "source_failure"])
def test_restart_aborts_without_valid_environment(deploy_sandbox, failure):
    remote_dir, env, run = deploy_sandbox
    if failure == "missing_directory":
        env["REMOTE_DIR"] = str(remote_dir / "nonexistent")
    elif failure == "source_failure":
        (remote_dir / ".pm2.env").write_text("DEPLOY_TEST_VALUE=partial\nreturn 23\n")
    result, calls = run("restart")
    assert result.returncode != 0, result.stdout
    assert any(call["command"] == "ssh" for call in calls)
    assert _pm2_calls(calls) == [], "Ошибка подготовки окружения не должна запускать PM2"
    assert not any(call["command"] == "sleep" for call in calls)


def test_restart_exports_environment_and_updates_pm2(deploy_sandbox):
    remote_dir, _, run = deploy_sandbox
    expected = {
        "CRM_DATA_DIR": str(remote_dir / "data directory"),
        "CRM_UPLOADS_DIR": str(remote_dir / "uploads"),
        "DEPLOY_TEST_VALUE": "значение с пробелами и $literal",
    }
    # Без export: именно deploy обязан экспортировать переменные дочернему PM2.
    (remote_dir / ".pm2.env").write_text("".join(
        f"{key}={shlex.quote(value)}\n" for key, value in expected.items()
    ))
    result, calls = run("restart")
    assert result.returncode == 0, result.stderr
    restarts = [call for call in _pm2_calls(calls) if call["args"][:1] == ["restart"]]
    assert len(restarts) == 1
    assert restarts[0]["args"] == ["restart", "crm-test", "--update-env"]
    assert restarts[0]["env"] == expected
    assert not any(call["command"] in ("rsync", "scp") for call in calls)


def test_restart_preserves_pm2_failure(deploy_sandbox):
    remote_dir, env, run = deploy_sandbox
    (remote_dir / ".pm2.env").write_text("DEPLOY_TEST_VALUE=ready\n")
    env["TEST_PM2_EXIT"] = "37"
    result, calls = run("restart")
    assert result.returncode == 37, (result.stdout, result.stderr)
    assert [call["args"] for call in _pm2_calls(calls)] == [
        ["restart", "crm-test", "--update-env"]
    ]
    assert not any(call["command"] == "sleep" for call in calls)
