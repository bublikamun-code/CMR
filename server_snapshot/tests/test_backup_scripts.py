"""Изолированные backup-тесты: fake Docker, настоящие SQLite/WAL/gzip/tar."""
import gzip
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ('backup_db.sh', 'backup_crm_prod.sh')


def command(path, body):
    path.write_text(f'#!{sys.executable}\n' + body)
    path.chmod(0o700)


@pytest.fixture
def sandbox(tmp_path):
    app, data, dst, bins, remote = [tmp_path / n for n in ('app files', 'data files', 'backup files', 'bin', 'remote')]
    for path in (app, data, dst, bins, remote):
        path.mkdir()
    (data / 'uploads').mkdir()
    (data / 'uploads' / 'image.txt').write_text('attachment')
    (data / 'settings.json').write_text('secret settings')
    (app / 'server.py').write_text('print("code")')
    env = dict(os.environ, APP=str(app), DATA=str(data), DST=str(dst),
               CRM_DATA_DIR=str(data), CONTAINER='test-only', KEEP='2',
               PATH=str(bins) + os.pathsep + os.environ['PATH'],
               FAKE_REMOTE=str(remote), FAKE_LOG=str(tmp_path / 'docker.log'))
    # Docker никогда не делегируется настоящему бинарнику.
    command(bins / 'docker', '''import os, pathlib, random, shutil, subprocess, sys, tempfile
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_REMOTE'])
with open(os.environ['FAKE_LOG'], 'a') as log: log.write(repr(args) + '\\n')
def mapped(s):
    return str(root / s.removeprefix('/tmp/')) if s.startswith('/tmp/crm-backup.') else s
if args[0] == 'ps':
    print('test-only')
elif args[0] == 'exec':
    args = args[1:]
    if args[0] == '-i': args = args[1:]
    cmd = args[1:]
    if cmd[0] == 'mktemp':
        # Реальный mktemp XXXXXXXXXX даёт только буквы/цифры; underscore
        # сломал бы guard скрипта и дал бы ложные падения тестов.
        alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        suffix = ''.join(random.choice(alphabet) for _ in range(10))
        p = root / f'crm-backup.{suffix}'
        p.mkdir()
        print('/tmp/' + p.name)
    elif cmd[0] == 'python':
        sys.exit(subprocess.call([sys.executable] + [mapped(s) for s in cmd[1:]]))
    elif cmd[:2] == ['rm', '-rf']:
        shutil.rmtree(mapped(cmd[-1]))
    else: raise SystemExit('unexpected exec: ' + repr(cmd))
elif args[0] == 'cp':
    if os.environ.get('FAIL_COPY'): sys.exit(42)
    src = mapped(args[1].split(':', 1)[1])
    shutil.copytree(src, args[2], dirs_exist_ok=True)
else: raise SystemExit('unexpected docker: ' + repr(args))
''')
    return app, data, dst, bins, remote, env


def run(script, env):
    return subprocess.run(['bash', str(ROOT / 'scripts' / script)], env=env,
                          text=True, capture_output=True, timeout=30)


def database(path):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute('CREATE TABLE records (id INTEGER, value TEXT)')
    conn.executemany('INSERT INTO records VALUES (?, ?)', [(1, 'данные'), (2, 'wal commit')])
    conn.commit()
    assert Path(str(path) + '-wal').stat().st_size > 0
    return conn


def check_archive(path, tmp_path):
    restored = tmp_path / ('restored_' + path.stem)
    restored.write_bytes(gzip.decompress(path.read_bytes()))
    with sqlite3.connect(restored) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert conn.execute('SELECT * FROM records ORDER BY id').fetchall() == [(1, 'данные'), (2, 'wal commit')]


def old_archives(dst, script):
    prefixes = ('crm_app', 'tenant_alpha', 'tenant_alpha_extra') if script == SCRIPTS[0] else ('db', 'code', 'uploads', 'crmdata_secret')
    paths = []
    for prefix in prefixes:
        ext = 'db.gz' if script == SCRIPTS[0] else ('sqlite.gz' if prefix == 'db' else 'tar.gz')
        for day in range(1, 4):
            path = dst / f'{prefix}_2020010{day}_000000.{ext}'
            path.write_bytes(b'old archive must survive failed backup')
            os.utime(path, (day, day))
            paths.append(path)
    return paths


@pytest.mark.parametrize('script', SCRIPTS)
@pytest.mark.parametrize('keep', ['', '0', '00', '01', '-1', 'abc', '1+1', '999999999999999999999'])
def test_invalid_keep_has_no_side_effects(sandbox, script, keep):
    _, data, dst, _, remote, env = sandbox
    old = old_archives(dst, script)
    result = run(script, dict(env, KEEP=keep))
    assert result.returncode != 0
    assert 'KEEP' in result.stdout + result.stderr
    assert all(p.exists() for p in old)
    assert not (data / 'crm_app.db').exists()
    assert not list(remote.iterdir())
    assert not Path(env['FAKE_LOG']).exists()


@pytest.mark.parametrize('script', SCRIPTS)
def test_missing_source_preserves_archives(sandbox, script):
    _, data, dst, _, remote, env = sandbox
    old = old_archives(dst, script)
    result = run(script, env)
    assert result.returncode != 0
    assert 'missing source' in result.stdout + result.stderr
    assert not (data / 'crm_app.db').exists()
    assert set(dst.iterdir()) == set(old)
    assert not list(remote.iterdir())


@pytest.mark.parametrize('script', SCRIPTS)
def test_wal_roundtrip_retention_and_permissions(sandbox, tmp_path, script):
    _, data, dst, _, remote, env = sandbox
    connections = [database(data / 'crm_app.db')]
    if script == SCRIPTS[0]:
        (data / 'tenants').mkdir()
        connections += [database(data / 'tenants' / f'{name}.db') for name in ('alpha', 'alpha_extra')]
    old = old_archives(dst, script)
    try:
        result = run(script, env)
        assert result.returncode == 0, result.stdout + result.stderr
        new = set(dst.iterdir()) - set(old)
        for archive in new:
            assert archive.stat().st_mode & 0o777 == 0o600
            if archive.name.startswith(('crm_app_', 'tenant_', 'db_')):
                check_archive(archive, tmp_path)
            else:
                with tarfile.open(archive) as tar:
                    expected = ('uploads/image.txt', b'attachment') if archive.name.startswith('uploads_') else (('./server.py', b'print("code")') if archive.name.startswith('code_') else ('./settings.json', b'secret settings'))
                    assert tar.extractfile(expected[0]).read() == expected[1]
        assert len(new) == (3 if script == SCRIPTS[0] else 4)
        # KEEP=2 для КАЖДОЙ БД/части, включая tenant с общим префиксом.
        assert all(p.exists() == ('20200103_' in p.name) for p in old)
        assert not list(remote.iterdir())
        assert not list(dst.glob('.crm-backup.*'))
    finally:
        for conn in connections:
            conn.close()


@pytest.mark.parametrize('script,stage', [(SCRIPTS[0], 'gzip'), (SCRIPTS[0], 'copy'),
                                        (SCRIPTS[1], 'gzip'), (SCRIPTS[1], 'uploads'),
                                        (SCRIPTS[1], 'code'), (SCRIPTS[1], 'crmdata_secret')])
def test_failure_before_rotation(sandbox, script, stage):
    _, data, dst, bins, remote, env = sandbox
    conn = database(data / 'crm_app.db')
    old = old_archives(dst, script)
    try:
        if stage == 'copy':
            env['FAIL_COPY'] = '1'
        else:
            tool = 'gzip' if stage == 'gzip' else 'tar'
            real = shutil.which(tool)
            command(bins / tool, f'''import os, sys
if {stage!r} == 'gzip' or {stage!r} in sys.argv[2]:
    sys.exit(42)
os.execv({real!r}, [{real!r}] + sys.argv[1:])
''')
        result = run(script, env)
        assert result.returncode == 42, result.stdout + result.stderr
        assert set(dst.iterdir()) == set(old)
        assert all(p.read_bytes() == b'old archive must survive failed backup' for p in old)
        assert not list(remote.iterdir())
    finally:
        conn.close()


def test_concurrent_container_runs_keep_own_temp(sandbox, tmp_path):
    _, data, dst, _, remote, env = sandbox
    conn = database(data / 'crm_app.db')
    sentinel = remote / 'crm-backup.unrelated'
    sentinel.mkdir()
    (sentinel / 'keep').write_text('untouched')
    try:
        processes = [subprocess.Popen(['bash', str(ROOT / 'scripts' / SCRIPTS[0])],
                                     env=dict(env, KEEP='14'), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for proc in processes:
            out, err = proc.communicate(timeout=30)
            assert proc.returncode == 0, out + err
        archives = list(dst.glob('crm_app_*.db.gz'))
        assert len(archives) == 2
        for archive in archives:
            check_archive(archive, tmp_path)
        assert list(remote.iterdir()) == [sentinel]
        assert (sentinel / 'keep').read_text() == 'untouched'
    finally:
        conn.close()
