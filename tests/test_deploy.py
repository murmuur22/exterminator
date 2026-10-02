"""Portable controller tests. Never run systemctl, sudo, or touch real install paths."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def controller():
    path = ROOT / 'deploy' / 'controller.py'
    assert path.is_file(), 'Deployment controller missing'
    spec = importlib.util.spec_from_file_location('controller', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive(tmp_path, entries):
    path = tmp_path / 'package.tar.gz'
    with tarfile.open(path, 'w:gz') as tar:
        for name, content, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(content) if kind == tarfile.REGTYPE else 0
            item.linkname = '/etc/passwd' if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ''
            tar.addfile(item, io.BytesIO(content) if item.size else None)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE),
    ('/absolute', tarfile.REGTYPE), ('a/../../escape', tarfile.REGTYPE),
    ('./app.py', tarfile.REGTYPE), ('a//b', tarfile.REGTYPE),
    ('app.py', tarfile.SYMTYPE), ('app.py', tarfile.LNKTYPE),
    ('app.py', tarfile.FIFOTYPE), ('app.py', tarfile.CHRTYPE),
    ('data/private.db', tarfile.REGTYPE)])
def test_archive_refuses_unsafe_members(tmp_path, name, kind):
    ctl = controller()
    path, digest = archive(tmp_path, [(name, b'bad', kind)])
    with pytest.raises(ValueError):
        ctl.read_package(path, digest)
    assert not (tmp_path.parent / 'escape').exists()


def test_builder_produces_verified_explicit_payload(tmp_path):
    import subprocess
    ctl = controller()
    script = ROOT / 'deploy/build.py'
    assert script.is_file(), 'Release builder missing'
    subprocess.run([__import__('sys').executable, str(script), '--output', str(tmp_path)], check=True)
    packages = list(tmp_path.glob('*.tar.gz'))
    assert len(packages) == 1
    package = packages[0]
    sha = package.with_suffix(package.suffix + '.sha256').read_text().split()[0]
    manifest, files = ctl.read_package(package, sha)
    assert manifest['version'] == '0.2.0'
    assert set(files) == ctl.PAYLOAD_FILES
    assert 'pytest==' not in files['requirements-production.txt'].decode()
    assert 'gunicorn==' in files['requirements-production.txt'].decode()
    assert 'playwright==' not in files['requirements-production.txt'].decode()
    assert not any(name.startswith(('data/', 'tests/', 'artifacts/')) for name in files)
    assert (tmp_path / 'exterminatorctl.py').read_bytes() == files['deploy/controller.py']


def test_path_guards_and_atomic_switch(tmp_path):
    ctl = controller()
    assert hasattr(ctl, 'assert_plain_path'), 'Filesystem guards missing'
    real = tmp_path / 'real'
    real.mkdir()
    (tmp_path / 'link').symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        ctl.assert_plain_path(tmp_path / 'link' / 'new')
    with pytest.raises(ValueError):
        ctl.assert_absent(real)
    ctl.assert_absent(tmp_path / 'new')
    root = real / 'app'
    releases = root / 'releases'
    releases.mkdir(parents=True)
    release = releases / ('0.2.0-' + 'a' * 12)
    release.mkdir()
    ctl.switch_current(root, release.name)
    assert (root / 'current').readlink() == Path('releases') / release.name
    assert ctl.current_release(root) == release.name
    (root / 'current').unlink()
    (root / 'current').symlink_to('/etc')
    with pytest.raises(ValueError):
        ctl.current_release(root)


def test_snapshot_pairs_database_config_and_release_and_detects_tampering(tmp_path):
    from store import Store
    ctl = controller()
    assert hasattr(ctl, 'save_snapshot'), 'Restorable snapshot missing'
    data = tmp_path / 'data'
    data.mkdir()
    db = data / 'reports.sqlite3'
    Store(db).create({'title': 'must survive'})
    config = tmp_path / 'config.json'
    config.write_text('{}')
    backups = tmp_path / 'backups'
    backups.mkdir()
    release = '0.2.0-' + 'a' * 12
    snapshot = ctl.save_snapshot(backups, db, config, release)
    metadata = ctl.read_snapshot(backups, snapshot.name)
    assert metadata['release'] == release
    assert Store(snapshot / 'reports.sqlite3').list()[0]['title'] == 'must survive'
    assert (snapshot / 'config.json').read_text() == '{}'
    (snapshot / 'config.json').write_text('{"admin": {}}')
    with pytest.raises(ValueError):
        ctl.read_snapshot(backups, snapshot.name)
    with pytest.raises(ValueError):
        ctl.read_snapshot(backups, '../escape')


def test_transition_stops_backs_up_switches_then_health_checks(tmp_path):
    ctl = controller()
    assert hasattr(ctl, 'Controller'), 'Lifecycle controller missing'
    from store import Store
    events = []
    class LocalController(ctl.Controller):
        def systemctl(self, *args):
            events.append(args[0])
        def validate_data_paths(self):
            pass
        def check_ports(self, settings):
            events.append('ports')
        def initialize_database(self, release):
            events.append('init')
        def write_units(self, release):
            events.append('units')
        def health(self, release, settings):
            events.append('health')
            assert self.database.exists()
            assert ctl.current_release(self.base) == release
        def write_state(self):
            events.append('state')
    instance = LocalController(tmp_path)
    instance.owner = (__import__('os').getuid(), __import__('os').getgid())
    for path in (instance.releases, instance.config.parent, instance.database.parent, instance.backups):
        path.mkdir(parents=True)
    old, new = '0.2.0-' + 'a' * 12, '0.2.0-' + 'b' * 12
    for release in (old, new):
        (instance.releases / release).mkdir()
    ctl.switch_current(instance.base, old)
    Store(instance.database).create({'title': 'existing report'})
    instance.config.write_text('{}')
    instance.activate(new, b'{"admin": {}}', {})
    assert events == ['stop', 'ports', 'init', 'units', 'state', 'daemon-reload', 'reset-failed', 'enable', 'start', 'health']
    snapshots = list(instance.backups.iterdir())
    assert len(snapshots) == 1
    assert ctl.read_snapshot(instance.backups, snapshots[0].name)['release'] == old
    assert (snapshots[0] / 'config.json').read_text() == '{}'
    assert Store(instance.database).list()[0]['title'] == 'existing report'
    assert not instance.transaction.exists()
    # A failed post-switch health check must NOT silently restore older data.
    saved_snapshot = snapshots[0]
    Store(instance.database).create({'title': 'newer report'})
    instance.health = lambda *args: (_ for _ in ()).throw(ValueError('health failed'))
    with pytest.raises(ValueError, match='health failed'):
        instance.activate(old, b'{}', {})
    assert events[-1] == 'stop'
    assert instance.transaction.exists()
    assert len(Store(instance.database).list()) == 2
    # Explicit rollback restores the paired DB/config and preserves the newer DB first.
    instance.preflight_managed = lambda: None
    instance.verify_release = lambda _: {'version': '0.2.0'}
    instance.validate_config = lambda *args: {}
    instance.health = lambda *args: None
    instance.rollback(saved_snapshot.name, True)
    assert len(Store(instance.database).list()) == 1
    assert instance.config.read_text() == '{}'
    assert ctl.current_release(instance.base) == old
    assert any(len(Store(path / 'reports.sqlite3').list()) == 2 for path in instance.backups.iterdir())
    assert not instance.transaction.exists()


def test_rollback_requires_explicit_data_restore_and_matching_snapshot(tmp_path):
    ctl = controller()
    assert hasattr(ctl, 'Controller'), 'Lifecycle controller missing'
    instance = ctl.Controller(tmp_path)
    with pytest.raises(ValueError, match='restore-data'):
        instance.rollback('anything', False)


def test_reset_failed_only_targets_failed_instances(tmp_path, monkeypatch):
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    calls = []
    monkeypatch.setattr(instance, 'run', lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(instance, 'unit_property', lambda unit, prop: 'inactive')
    instance.systemctl('reset-failed', *ctl.UNITS)
    assert calls == [], 'Resetting an unloaded fresh instance fails on Debian'
    monkeypatch.setattr(instance, 'unit_property', lambda unit, prop: 'failed' if unit == ctl.UNITS[0] else 'inactive')
    instance.systemctl('reset-failed', *ctl.UNITS)
    assert calls == [['/usr/bin/systemctl', 'reset-failed', ctl.UNITS[0]]]


def test_template_is_checked_as_file_not_as_instance(tmp_path, monkeypatch):
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    def properties(unit, prop):
        assert unit in ctl.UNITS, 'systemctl show refuses an uninstantiated template'
        return 'not-found' if prop == 'LoadState' else ''
    monkeypatch.setattr(instance, 'unit_property', properties)
    instance.check_unit_conflicts(install=True)
    instance.unit.parent.mkdir(parents=True)
    instance.unit.write_text('unrelated template')
    with pytest.raises(ValueError, match='unit'):
        instance.check_unit_conflicts(install=True)


def test_install_preflight_refuses_existing_paths_accounts_units(tmp_path, monkeypatch):
    import pwd
    import grp
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    assert hasattr(instance, 'preflight_install'), 'Install safety preflight missing'
    instance.base.mkdir(parents=True)
    with pytest.raises(ValueError, match='Existing path'):
        instance.preflight_install()
    instance.base.rmdir()
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: object())
    with pytest.raises(ValueError, match='account'):
        instance.preflight_install()
    def missing(_):
        raise KeyError
    monkeypatch.setattr(pwd, 'getpwnam', missing)
    monkeypatch.setattr(grp, 'getgrnam', missing)
    monkeypatch.setattr(instance, 'unit_property', lambda unit, prop: 'loaded')
    with pytest.raises(ValueError, match='unit'):
        instance.preflight_install()
    monkeypatch.setattr(instance, 'unit_property', lambda unit, prop: 'not-found' if prop == 'LoadState' else '')
    instance.preflight_install()


def test_release_staging_checks_manifest_and_never_reuses_paths(tmp_path, monkeypatch):
    ctl = controller()
    instance = ctl.Controller(tmp_path / 'host')
    assert hasattr(instance, 'stage'), 'Release staging missing'
    output = tmp_path / 'output'
    import subprocess
    import sys
    subprocess.run([sys.executable, str(ROOT / 'deploy/build.py'), '--output', str(output)], check=True)
    package = next(output.glob('*.tar.gz'))
    sha = ctl.digest(package.read_bytes())
    manifest, files = ctl.read_package(package, sha)
    instance.releases.mkdir(parents=True)
    commands = []
    monkeypatch.setattr(instance, 'run', lambda args, **kw: commands.append(args))
    release = instance.stage(manifest, files, sha)
    assert instance.verify_release(release)['version'] == '0.2.0'
    assert commands[0][-2:] == ['venv', str(instance.releases / release / 'venv')]
    assert '--require-hashes' in commands[1]
    assert '--only-binary=:all:' in commands[1]
    assert '--isolated' in commands[1]
    assert 'https://pypi.org/simple' in commands[1]
    assert instance.stage(manifest, files, sha) == release  # Verified managed releases may be reused.
    (instance.releases / release / '.ready').unlink()
    with pytest.raises(ValueError, match='Incomplete'):
        instance.stage(manifest, files, sha)
    (instance.releases / release / '.ready').write_text('1\n')
    (instance.releases / release / 'app.py').write_text('tampered')
    with pytest.raises(ValueError, match='changed'):
        instance.verify_release(release)


def test_data_path_guard_rejects_symlinks_and_unrelated_files(tmp_path):
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    assert hasattr(instance, 'validate_data_paths'), 'Data guards missing'
    instance.database.parent.mkdir(parents=True)
    (instance.database.parent / 'other-service.db').touch()
    with pytest.raises(ValueError):
        instance.validate_data_paths()
    (instance.database.parent / 'other-service.db').unlink()
    instance.database.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        instance.validate_data_paths()


def test_managed_preflight_refuses_missing_marker_and_wrong_account(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    import pwd
    import grp
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    assert hasattr(instance, 'preflight_managed'), 'Managed ownership checks missing'
    for path in (instance.releases, instance.config.parent, instance.database.parent, instance.backups, instance.unit.parent):
        path.mkdir(parents=True, exist_ok=True)
    instance.config.write_text('{}')
    instance.unit.write_text('managed unit')
    with pytest.raises((ValueError, FileNotFoundError)):
        instance.preflight_managed()
    instance.owner = (123, 456)
    instance.write_state()
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=789, pw_gid=456, pw_shell='/usr/sbin/nologin', pw_dir='/nonexistent'))
    monkeypatch.setattr(grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=456, gr_mem=[]))
    with pytest.raises(ValueError, match='identity'):
        instance.preflight_managed()


def test_config_validation_runs_as_service_user_and_unit_is_hardened(tmp_path, monkeypatch):
    from types import SimpleNamespace
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    assert hasattr(instance, 'validate_config'), 'Unprivileged configuration validation missing'
    instance.config.parent.mkdir(parents=True)
    instance.owner = (__import__('os').getuid(), __import__('os').getgid())
    calls = []
    def run(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(stdout='{}')
    monkeypatch.setattr(instance, 'run', run)
    assert instance.validate_config('0.2.0-' + 'a' * 12, b'{}') == {}
    assert calls[0][0][:4] == ['/usr/sbin/runuser', '-u', 'exterminator', '--']
    assert not list(instance.config.parent.glob('.candidate-*'))
    unit = (ROOT / 'deploy/exterminator@.service').read_text()
    for expected in ('User=exterminator', 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                     'ProtectHome=yes', 'ReadWritePaths=/var/lib/exterminator', 'MemoryMax=256M',
                     'CapabilityBoundingSet=', 'UMask=0077', 'KillMode=control-group'):
        assert expected in unit


def test_controller_cli_requires_checksum_and_debian_root(tmp_path):
    import subprocess
    import sys
    ctl = controller()
    assert hasattr(ctl, 'main'), 'Operator CLI missing'
    result = subprocess.run([sys.executable, str(ROOT / 'deploy/controller.py'), 'install', '--package', '/missing'],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert '--sha256' in result.stderr
    result = subprocess.run([sys.executable, str(ROOT / 'deploy/controller.py'), 'install', '--package', '/missing',
                             '--sha256', '0' * 64], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Debian 13 amd64' in result.stderr


def test_install_and_update_orchestration_do_not_stop_before_staging(tmp_path, monkeypatch):
    import subprocess
    import sys
    ctl = controller()
    instance = ctl.Controller(tmp_path / 'host')
    assert hasattr(instance, 'deploy'), 'Install/update orchestration missing'
    output = tmp_path / 'output'
    subprocess.run([sys.executable, str(ROOT / 'deploy/build.py'), '--output', str(output)], check=True)
    package = next(output.glob('*.tar.gz'))
    events = []
    monkeypatch.setattr(instance, 'preflight_install', lambda: events.append('preflight'))
    monkeypatch.setattr(instance, 'bootstrap', lambda: events.append('bootstrap'))
    monkeypatch.setattr(instance, 'stage', lambda *args: events.append('stage') or '0.2.0-' + 'a' * 12)
    monkeypatch.setattr(instance, 'validate_config', lambda *args: events.append('validate') or {})
    monkeypatch.setattr(instance, 'write_units', lambda *args: events.append('units'))
    monkeypatch.setattr(instance, 'systemctl', lambda *args: events.append(args[0]))
    monkeypatch.setattr(instance, 'activate', lambda *args: events.append('activate'))
    instance.deploy('install', package, ctl.digest(package.read_bytes()), None)
    assert events == ['preflight', 'bootstrap', 'stage', 'validate', 'units', 'daemon-reload', 'activate']
    events.clear()
    monkeypatch.setattr(instance, 'preflight_managed', lambda: events.append('managed'))
    instance.config.parent.mkdir(parents=True)
    instance.config.write_text('{}')
    instance.deploy('update', package, ctl.digest(package.read_bytes()), None)
    assert events == ['managed', 'stage', 'validate', 'activate']


def test_health_checks_both_surfaces_version_and_source_address(tmp_path, monkeypatch):
    import http.client
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    assert hasattr(instance, 'health'), 'Controller health verification missing'
    monkeypatch.setattr(instance, 'verify_release', lambda _: {'version': '0.2.0'})
    monkeypatch.setattr(instance, 'unit_property', lambda *args: 'active')
    calls = []
    class Response:
        status = 200
        def __init__(self, surface):
            self.surface = surface
        def read(self, *args):
            return json.dumps({'ok': True, 'surface': self.surface, 'version': '0.2.0'}).encode()
    class Connection:
        def __init__(self, host, port, **kw):
            calls.append((host, port, kw))
            self.surface = 'admin' if port == 8741 else 'submission'
        def request(self, method, path):
            assert (method, path) == ('GET', '/healthz')
        def getresponse(self):
            return Response(self.surface)
        def close(self):
            pass
    monkeypatch.setattr(http.client, 'HTTPConnection', Connection)
    instance.health('0.2.0-' + 'a' * 12, {'admin': {'bind': '127.0.0.1', 'port': 8741},
                                         'submission': {'bind': '192.168.50.10', 'port': 8742}})
    assert [c[2]['source_address'] for c in calls] == [('127.0.0.1', 0), ('192.168.50.10', 0)]


def test_release_refuses_writable_code_and_symlink_ready_marker(tmp_path, monkeypatch):
    import subprocess
    import sys
    ctl = controller()
    output = tmp_path / 'output'
    subprocess.run([sys.executable, str(ROOT / 'deploy/build.py'), '--output', str(output)], check=True)
    package = next(output.glob('*.tar.gz'))
    sha = ctl.digest(package.read_bytes())
    manifest, files = ctl.read_package(package, sha)
    instance = ctl.Controller(tmp_path / 'host')
    instance.releases.mkdir(parents=True)
    monkeypatch.setattr(instance, 'run', lambda *args, **kw: None)
    release = instance.stage(manifest, files, sha)
    path = instance.releases / release
    (path / 'app.py').chmod(0o666)
    with pytest.raises(ValueError, match='permissions'):
        instance.verify_release(release)
    (path / 'app.py').chmod(0o644)
    (path / '.ready').unlink()
    (path / '.ready').symlink_to(path / 'app.py')
    with pytest.raises(ValueError, match='Symlink'):
        instance.verify_release(release)


def test_builder_refuses_symlink_parent_from_another_working_directory(tmp_path):
    import shutil
    import subprocess
    import sys
    ctl = controller()
    source = tmp_path / 'source'
    for name in ctl.PAYLOAD_FILES | {'deploy/build.py', 'uv.lock'}:
        if name.startswith('templates/'):
            continue
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (source / 'templates').symlink_to(ROOT / 'templates', target_is_directory=True)
    result = subprocess.run([sys.executable, str(source / 'deploy/build.py'), '--output', str(tmp_path / 'output')],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Symlink' in result.stderr


def test_unloaded_template_dropin_refused_on_update(tmp_path, monkeypatch):
    ctl = controller()
    instance = ctl.Controller(tmp_path)
    instance.unit.parent.mkdir(parents=True)
    instance.unit.with_name(instance.unit.name + '.d').mkdir()
    monkeypatch.setattr(instance, 'unit_property', lambda *args: '')
    with pytest.raises(ValueError, match='drop-in'):
        instance.check_unit_conflicts()


def test_archive_refuses_wrong_hash_duplicate_and_missing_manifest(tmp_path):
    ctl = controller()
    path, digest = archive(tmp_path, [('app.py', b'one', tarfile.REGTYPE), ('app.py', b'two', tarfile.REGTYPE)])
    for checksum in ('0' * 64, digest, 'invalid'):
        with pytest.raises(ValueError):
            ctl.read_package(path, checksum)
    path, digest = archive(tmp_path, [('app.py', b'one', tarfile.REGTYPE)])
    with pytest.raises(ValueError):
        ctl.read_package(path, digest)
