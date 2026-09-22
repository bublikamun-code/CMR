"""Проверки локального gate на заглушках, без рекурсивного запуска pytest."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
JSC = Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc')


@pytest.fixture()
def gate_sandbox(tmp_path):
    root = tmp_path / 'project with spaces'
    for name in ('scripts', 'tools', 'server_snapshot/scripts', 'server_snapshot/tests',
                 'server_snapshot/tools', 'server_snapshot/.venv/bin', 'bin'):
        (root / name).mkdir(parents=True)
    script = root / 'scripts/check.sh'
    shutil.copyfile(ROOT / 'scripts/check.sh', script)
    (root / 'server_snapshot/tests/conftest.py').touch()
    (root / 'tools/check_handlers.js').touch()
    (root / 'tools/stamp_assets.py').touch()
    # Gate требует наличия сборщика v2 и гейта свежести ещё в preflight.
    (root / 'server_snapshot/tools/build_site_v2.py').touch()
    (root / 'server_snapshot/tools/check_site_v2_fresh.py').touch()
    (root / 'tools/check_js.sh').write_text('exit "${JS_EXIT:-0}"\n')
    # Исполнение обслуживаемого shell-скрипта вместо bash -n сломает тест.
    (root / 'server_snapshot/scripts/backup.sh').write_text('exit 99\n')
    for name in ('bash', 'dirname'):
        (root / 'bin' / name).symlink_to(shutil.which(name))
    node = root / 'bin/node'
    node.write_text('#!/bin/bash\n'
                    'if [[ "$1" == --version ]]; then exit 0; fi\n'
                    'printf "total handlers: 49 unresolved: %s\\n" "${UNRESOLVED:-0}"\n'
                    'exit "${NODE_EXIT:-0}"\n')
    node.chmod(0o755)
    python = root / 'server_snapshot/.venv/bin/python'
    python.write_text('#!' + sys.executable + '\n' + '''
import json
import os
from pathlib import Path
import sys
if sys.argv[1] == '-c':
    print(Path(__file__).resolve())
    raise SystemExit(0)
source = sys.stdin.read() if sys.argv[1] == '-' else ''
if 'import importlib.util' in source:
    raise SystemExit(int(os.environ.get('PREREQ_EXIT', '0')))
# Этапы различаются первым аргументом: '-' — heredoc с pytest,
# check_site_v2_fresh.py — свежесть site-v2, прочее — stamp_assets.py.
script = Path(sys.argv[1]).name
if sys.argv[1] == '-':
    step = 'pytest'
elif script == 'check_site_v2_fresh.py':
    step = 'v2fresh'
else:
    step = 'stamp'
with open(os.environ['GATE_LOG'], 'a') as log:
    log.write(json.dumps({'step': step, 'args': sys.argv[1:]}) + '\\n')
raise SystemExit(int(os.environ.get(step.upper() + '_EXIT', '0')))
''')
    python.chmod(0o755)
    log = root / 'calls.jsonl'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHON'}
    env.update(PATH=str(root / 'bin'), GATE_LOG=str(log))

    def run(**overrides):
        result = subprocess.run(
            ['/bin/bash', str(script)], cwd=tmp_path,
            env={**env, **overrides}, capture_output=True, text=True, timeout=15,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return root, run


@pytest.mark.skipif(not os.access(JSC, os.X_OK), reason='Gate явно требует macOS JavaScriptCore')
@pytest.mark.parametrize(('overrides', 'code'), [
    ({}, 0),
    ({'JS_EXIT': '7'}, 7),
    ({'NODE_EXIT': '8'}, 8),
    ({'UNRESOLVED': '24'}, 1),
    ({'V2FRESH_EXIT': '4'}, 4),
    ({'V2FRESH_EXIT': '1', 'PYTEST_EXIT': '5'}, 1),
    ({'STAMP_EXIT': '3'}, 3),
    ({'PYTEST_EXIT': '5'}, 5),
    ({'JS_EXIT': '7', 'PYTEST_EXIT': '5'}, 7),
])
def test_gate_runs_all_checks_and_preserves_failure(gate_sandbox, overrides, code):
    root, run = gate_sandbox
    result, calls = run(**overrides)
    assert result.returncode == code, result.stdout + result.stderr
    assert [call['step'] for call in calls] == ['v2fresh', 'stamp', 'pytest']
    assert calls[0]['args'] == [
        str(root / 'server_snapshot/tools/check_site_v2_fresh.py')]
    assert calls[1]['args'] == [str(root / 'tools/stamp_assets.py'), '--check']
    assert f'Локальный gate завершён: exit {code}' in result.stdout


def test_missing_python_fails_before_checks(gate_sandbox):
    _, run = gate_sandbox
    result, calls = run(PYTHON='/nonexistent/python')
    assert result.returncode == 2
    assert 'Python недоступен' in result.stderr
    assert calls == []


def test_missing_node_is_not_silently_skipped(gate_sandbox):
    root, run = gate_sandbox
    (root / 'bin/node').unlink()
    result, calls = run()
    assert result.returncode == 2
    assert 'не найден node' in result.stderr
    assert calls == []


@pytest.mark.skipif(not os.access(JSC, os.X_OK), reason='Gate явно требует macOS JavaScriptCore')
def test_python_override_and_missing_dependencies(gate_sandbox):
    root, run = gate_sandbox
    override = root / 'custom python'
    (root / 'server_snapshot/.venv/bin/python').rename(override)
    result, calls = run(PYTHON=str(override), PREREQ_EXIT='1')
    assert result.returncode == 2
    assert 'отсутствуют зависимости' in result.stderr
    assert calls == []


@pytest.mark.skipif(not os.access(JSC, os.X_OK), reason='Gate явно требует macOS JavaScriptCore')
def test_shell_syntax_failure_is_reported_without_execution(gate_sandbox):
    root, run = gate_sandbox
    (root / 'scripts/broken.sh').write_text('if then\n')
    result, calls = run()
    assert result.returncode != 0
    assert 'FAIL: shell syntax: scripts/broken.sh' in result.stderr
    assert [call['step'] for call in calls] == ['v2fresh', 'stamp', 'pytest']


@pytest.mark.skipif(not os.access(JSC, os.X_OK), reason='Gate явно требует macOS JavaScriptCore')
@pytest.mark.parametrize('missing', [
    'server_snapshot/tools/build_site_v2.py',
    'server_snapshot/tools/check_site_v2_fresh.py',
])
def test_missing_v2_freshness_tools_fail_preflight(gate_sandbox, missing):
    """Без сборщика/гейта v2 проверка свежести невозможна — это предпосылка,
    а не «пропустили этап»: gate обязан упасть до запуска проверок."""
    root, run = gate_sandbox
    (root / missing).unlink()
    result, calls = run()
    assert result.returncode == 2
    assert f'нет {missing}' in result.stderr
    assert calls == []
