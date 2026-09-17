"""Проверка анализатора на синтетических файлах без выполнения JS приложения."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(('source', 'handler', 'expected'), [
    ('const UI = { run() {} }; window.UI = UI;', 'UI.run', 0),
    ('const UI = { async run() {} }; window.UI = UI;', 'UI.run', 0),
    ('window.UI = { padding() {' + 'x();' * 2000 + '}, run() {} };', 'UI.run', 0),
    ('window.UI = { other() {} }; window.Other = { run() {} };', 'UI.run', 1),
    ('window.UI = { nested: { run() {} } };', 'UI.run', 1),
    ('window.UI = { run: 42 };', 'UI.run', 1),
    ('window.UI = { run: async () => {} };', 'UI.run', 0),
    ('window.UI = {}; UI.run = function() {};', 'UI.run', 0),
    ('window.UI = { run() { const s = `outer ${`inner ${1}`} end`; }, next() {} };', 'UI.next', 0),
    ('window.UI = { run() { const x = a / b / c; }, next() {} };', 'UI.next', 0),
    ('window.UI = { run() { const re = /[{}]/g; }, next() {} };', 'UI.next', 0),
    ('function run() {}', 'run', 0),
    ('function run() {} window.UI = { run };', 'UI.run', 0),
    ('window.UI = { text: "run() {}" };', 'UI.run', 1),
    ('window.UI = { run() {} };', 'UI.run.extra', 1),
])
def test_handler_resolution(tmp_path, source, handler, expected):
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'site').mkdir()
    shutil.copyfile(ROOT / 'tools/check_handlers.js', tmp_path / 'tools/check_handlers.js')
    (tmp_path / 'site/app.js').write_text(source)
    (tmp_path / 'site/index.html').write_text(f'<button data-handler="{handler}"></button>')
    result = subprocess.run(['node', str(tmp_path / 'tools/check_handlers.js')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stdout + result.stderr
    assert f'unresolved: {expected}' in result.stdout
