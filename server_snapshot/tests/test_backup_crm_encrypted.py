"""Изолированные тесты шифрованного серверного бэкапа scripts/backup_crm.sh.

Настоящие SQLite/WAL, gzip, tar и openssl; APP/DATA/DST/BK_DIR/KEEP уходят
в песочницу через окружение, ключ создаётся заранее, чтобы openssl rand
не вызывался.
"""
import gzip
import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'server_snapshot' / 'scripts' / 'backup_crm.sh'
PREFIXES = ('db', 'tenants', 'uploads', 'code', 'crmdata_secret')


def command(path, body):
    path.write_text(f'#!{sys.executable}\n' + body)
    path.chmod(0o700)


@pytest.fixture
def sandbox(tmp_path):
    app, data, dst, bins, keys = [tmp_path / n for n in ('app files', 'data files', 'backup files', 'bin', 'keys')]
    for path in (app, data, dst, bins, keys):
        path.mkdir()
    (data / 'uploads').mkdir()
    (data / 'uploads' / 'image.txt').write_text('attachment')
    (data / 'settings.json').write_text('secret settings')
    (app / 'server.py').write_text('print("code")')
    key = keys / '.backup_key'
    key.write_text('test key material\n')
    key.chmod(0o600)
    env = dict(os.environ, APP=str(app), DATA=str(data), DST=str(dst), BK_DIR=str(keys),
               KEEP='2', KEEP_UPLOADS='2', PATH=str(bins) + os.pathsep + os.environ['PATH'])
    return app, data, dst, bins, key, env


def run(env):
    return subprocess.run(['bash', str(SCRIPT)], env=env, text=True,
                          capture_output=True, timeout=120)


def database(path):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute('CREATE TABLE records (id INTEGER, value TEXT)')
    conn.executemany('INSERT INTO records VALUES (?, ?)', [(1, 'данные'), (2, 'wal commit')])
    conn.commit()
    assert Path(str(path) + '-wal').stat().st_size > 0
    return conn


def unseal(archive, key):
    encrypted = subprocess.run(
        ['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', str(archive),
         '-pass', 'file:' + str(key)], capture_output=True, check=True).stdout
    return gzip.decompress(encrypted)


def tar_member(raw, name):
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        return tar.extractfile(name).read()


def sqlite_records(raw, tmp_path, name):
    path = tmp_path / name
    path.write_bytes(raw)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        return conn.execute('SELECT * FROM records ORDER BY id').fetchall()


def old_archives(dst):
    paths = []
    for day in range(1, 4):
        for prefix in PREFIXES:
            ext = 'sqlite.gz.enc' if prefix == 'db' else 'tar.gz.enc'
            path = dst / f'{prefix}_2020010{day}_000000.{ext}'
            path.write_bytes(b'old archive must survive failed backup')
            os.utime(path, (day, day))
            paths.append(path)
    return paths


def test_invalid_keep_has_no_side_effects(sandbox):
    _, _, dst, _, _, env = sandbox
    old = old_archives(dst)
    for keep in ('', '0', '00', '01', '-1', 'abc', '1+1', '999999999999999999999'):
        result = run(dict(env, KEEP=keep))
        assert result.returncode != 0, keep
        assert 'KEEP' in result.stdout + result.stderr, keep
        assert all(p.exists() for p in old), keep
    assert set(dst.iterdir()) == set(old)


def test_missing_source_preserves_archives(sandbox):
    _, data, dst, _, _, env = sandbox
    old = old_archives(dst)
    result = run(env)
    assert result.returncode != 0
    assert 'missing source' in result.stdout + result.stderr
    assert not (data / 'crm_app.db').exists()
    assert set(dst.iterdir()) == set(old)


def test_roundtrip_retention_and_permissions(sandbox, tmp_path):
    _, data, dst, _, key, env = sandbox
    connections = [database(data / 'crm_app.db')]
    (data / 'tenants').mkdir()
    connections.append(database(data / 'tenants' / 'alpha.db'))
    old = old_archives(dst)
    try:
        result = run(env)
        assert result.returncode == 0, result.stdout + result.stderr
        new = sorted(set(dst.iterdir()) - set(old))
        assert len(new) == len(PREFIXES)
        for archive in new:
            assert archive.stat().st_mode & 0o777 == 0o600
            raw = unseal(archive, key)
            if archive.name.startswith('db_'):
                assert sqlite_records(raw, tmp_path, 'restored.db') == [(1, 'данные'), (2, 'wal commit')]
            elif archive.name.startswith('tenants_'):
                assert sqlite_records(tar_member(raw, './alpha.db'), tmp_path, 'alpha.db') == [(1, 'данные'), (2, 'wal commit')]
            elif archive.name.startswith('uploads_'):
                assert tar_member(raw, 'uploads/image.txt') == b'attachment'
            elif archive.name.startswith('code_'):
                assert tar_member(raw, './server.py') == b'print("code")'
            else:
                assert tar_member(raw, './settings.json') == b'secret settings'
        # KEEP=2 для каждого вида: из трёх старых поколений выживает одно.
        assert all(p.exists() == ('20200103_' in p.name) for p in old)
        # В DST нет ни открытых копий, ни остатков staging.
        assert not list(dst.glob('.crm-backup.*'))
        assert not [p for p in dst.iterdir() if not p.name.endswith('.enc')]
    finally:
        for conn in connections:
            conn.close()


def test_db_failure_publishes_nothing(sandbox):
    _, data, dst, bins, _, env = sandbox
    conn = database(data / 'crm_app.db')
    old = old_archives(dst)
    # Заглушка падает на любом вызове: снимок основной БД не создаётся.
    command(bins / 'sqlite3', 'import sys\nsys.exit(42)\n')
    try:
        result = run(env)
        assert result.returncode != 0, result.stdout + result.stderr
        assert 'FAIL: sqlite3 .backup' in result.stdout + result.stderr
        assert set(dst.iterdir()) == set(old)
        assert not list(dst.glob('.crm-backup.*'))
    finally:
        conn.close()


def test_encryption_failure_is_explicit_and_partial(sandbox):
    _, data, dst, bins, _, env = sandbox
    conn = database(data / 'crm_app.db')
    (data / 'tenants').mkdir()
    tenant = database(data / 'tenants' / 'alpha.db')
    old = old_archives(dst)
    real = shutil.which('openssl')
    # Падает только шифрование uploads; расшифровка и остальные части честные.
    command(bins / 'openssl', f'''import os, sys
args = sys.argv[1:]
if '-salt' in args and '-in' in args and 'uploads_' in args[args.index('-in') + 1]:
    sys.exit(42)
os.execv({real!r}, [{real!r}] + args)
''')
    try:
        result = run(env)
        assert result.returncode != 0, result.stdout + result.stderr
        assert 'FAIL: шифрование uploads' in result.stdout + result.stderr
        published = {p.name for p in dst.iterdir()} - {p.name for p in old}
        # uploads не публикуется, остальные части — публикуются.
        assert not any(name.startswith('uploads_') for name in published)
        for prefix in ('db_', 'tenants_', 'code_', 'crmdata_secret_'):
            assert any(name.startswith(prefix) for name in published), published
        # Открытых копий в DST нет даже при частичном сбое.
        assert not [p for p in dst.iterdir() if not p.name.endswith('.enc')]
        assert not list(dst.glob('.crm-backup.*'))
    finally:
        conn.close()
        tenant.close()


def test_concurrent_runs_publish_both(sandbox):
    _, data, dst, _, _, env = sandbox
    conn = database(data / 'crm_app.db')
    (data / 'tenants').mkdir()
    tenant = database(data / 'tenants' / 'alpha.db')
    try:
        processes = [subprocess.Popen(['bash', str(SCRIPT)], env=dict(env, KEEP='14'),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for _ in range(2)]
        for proc in processes:
            out, err = proc.communicate(timeout=120)
            assert proc.returncode == 0, out + err
        for prefix in PREFIXES:
            ext = 'sqlite.gz.enc' if prefix == 'db' else 'tar.gz.enc'
            archives = list(dst.glob(f'{prefix}_*.{ext}'))
            assert len(archives) == 2, prefix
            assert len({a.name for a in archives}) == 2, prefix
        assert not list(dst.glob('.crm-backup.*'))
    finally:
        conn.close()
        tenant.close()
