#!/usr/bin/env python3
"""Auditable, stdlib-only Debian controller. No network/service changes on import."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tomllib

PAYLOAD_FILES = frozenset({
    'app.py', 'store.py', 'production.py', 'pyproject.toml', 'requirements-production.txt',
    'templates/index.html', 'templates/submission.html',
    'static/app.js', 'static/submission.js', 'static/style.css', 'static/submission.css',
    'static/pest.svg', 'static/pixel-bug.svg', 'static/wallpaper.svg',
    'deploy/controller.py', 'deploy/exterminator@.service', 'deploy/config.example.json',
    'docs/deployment.md', 'README.md', 'CHANGELOG.md',
})
MAX_PACKAGE = 100 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path, limit=MAX_PACKAGE):
    # Open exactly once, without following a final symlink; reject device/FIFO input.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Expected a bounded regular file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('File too large')
    return data


def read_package(path, expected_hash):
    if not re.fullmatch('[0-9a-f]{64}', expected_hash):
        raise ValueError('An explicit lowercase SHA-256 is required')
    raw = regular_bytes(path)
    if digest(raw) != expected_hash:
        raise ValueError('Package checksum mismatch')
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        for member in tar:
            name = member.name
            canonical = str(PurePosixPath(name))
            wheel = name.startswith('wheels/') and re.fullmatch(r'wheels/[A-Za-z0-9_.+-]+\.whl', name)
            if (not member.isfile() or member.issparse() or member.pax_headers
                    or name != canonical or name.startswith('/') or '..' in PurePosixPath(name).parts
                    or name in files or (name not in PAYLOAD_FILES | {'manifest.json'} and not wheel)):
                raise ValueError('Unsafe or unexpected archive member')
            total += member.size
            if member.size < 0 or total > MAX_PACKAGE or len(files) >= 256:
                raise ValueError('Archive limits exceeded')
            files[name] = tar.extractfile(member).read()
    if not PAYLOAD_FILES <= files.keys() or 'manifest.json' not in files:
        raise ValueError('Incomplete release')
    manifest = json.loads(files.pop('manifest.json'))
    if (set(manifest) != {'format', 'version', 'target', 'files'} or manifest['format'] != 1
            or manifest['target'] != 'debian13-amd64'
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', manifest['version'])
            or manifest['files'] != {name: digest(data) for name, data in files.items()}):
        raise ValueError('Manifest mismatch')
    if tomllib.loads(files['pyproject.toml'].decode())['project']['version'] != manifest['version']:
        raise ValueError('Version mismatch')
    for name in files:
        if name.startswith('wheels/') and not (name.endswith('-none-any.whl') or ('manylinux' in name and name.endswith('x86_64.whl'))):
            raise ValueError('Only universal or Linux amd64 wheels are supported')
    return manifest, files


RELEASE_ID = r'[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}'
SNAPSHOT_ID = r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}'


def assert_plain_path(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError(f'Symlink refused: {part}')
    return path


def assert_owned(path, uid=None):
    # CLI enforces root; default follows euid to allow isolated, unprivileged unit tests.
    uid = os.geteuid() if uid is None else uid
    assert_plain_path(path)
    info = path.stat()
    if info.st_uid != uid or info.st_mode & 0o022 or (not path.is_dir() and info.st_nlink != 1):
        raise ValueError(f'Unsafe ownership or permissions: {path}')


def assert_absent(path):
    assert_plain_path(path)
    if path.exists():
        raise ValueError(f'Existing path refused: {path}')


def atomic_write(path, data, mode=0o600, owner=None):
    import uuid
    assert_plain_path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if owner:
                os.fchown(stream.fileno(), *owner)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def current_release(root):
    current = root / 'current'
    if not current.is_symlink():
        raise ValueError('current must be a managed release symlink')
    target = str(current.readlink())
    if not re.fullmatch('releases/' + RELEASE_ID, target):
        raise ValueError('Unmanaged current symlink')
    assert_plain_path(root / target)
    if not (root / target).is_dir():
        raise ValueError('Current release missing')
    return Path(target).name


def switch_current(root, release):
    import uuid
    if not re.fullmatch(RELEASE_ID, release):
        raise ValueError('Invalid release ID')
    assert_plain_path(root / 'releases' / release)
    if not (root / 'releases' / release).is_dir():
        raise ValueError('Release missing')
    if (root / 'current').exists() or (root / 'current').is_symlink():
        current_release(root)
    temporary = root / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to('releases/' + release)
    os.replace(temporary, root / 'current')
    sync_dir(root)


def save_snapshot(backups, database, config, release):
    """Caller MUST stop both units before entry. SQLite backup includes any WAL."""
    import sqlite3
    from datetime import datetime, timezone
    import uuid
    if not re.fullmatch(RELEASE_ID, release):
        raise ValueError('Invalid snapshot release')
    assert_plain_path(backups)
    assert_plain_path(database)
    assert_plain_path(config)
    regular_bytes(database)  # Reject special files and symlinks before SQLite opens it.
    snapshot = backups / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12])
    snapshot.mkdir(mode=0o700)
    try:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot / 'reports.sqlite3') as target:
                source.backup(target)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed; no restoration attempted')
        os.chmod(snapshot / 'reports.sqlite3', 0o600)
        atomic_write(snapshot / 'config.json', regular_bytes(config))
        metadata = dict(format=1, release=release, files={name: digest(regular_bytes(snapshot / name))
                        for name in ('reports.sqlite3', 'config.json')})
        atomic_write(snapshot / 'snapshot.json', (json.dumps(metadata, indent=2) + '\n').encode())
        sync_dir(backups)
    except Exception:
        # Preserve partial backup for investigation. Missing metadata makes it unrestorable.
        raise
    return snapshot


def read_snapshot(backups, snapshot_id):
    if not re.fullmatch(SNAPSHOT_ID, snapshot_id):
        raise ValueError('Invalid snapshot ID')
    snapshot = backups / snapshot_id
    assert_plain_path(snapshot)
    metadata = json.loads(regular_bytes(snapshot / 'snapshot.json'))
    if (set(metadata) != {'format', 'release', 'files'} or metadata['format'] != 1
            or not re.fullmatch(RELEASE_ID, metadata['release'])
            or metadata['files'] != {name: digest(regular_bytes(snapshot / name))
                                    for name in ('reports.sqlite3', 'config.json')}):
        raise ValueError('Snapshot is incomplete or changed')
    return metadata


UNITS = ('exterminator@admin.service', 'exterminator@submission.service')


class Controller:
    def __init__(self, root=Path('/')):
        self.root = root
        self.base = root / 'opt/exterminator'
        self.releases = self.base / 'releases'
        self.config = root / 'etc/exterminator/config.json'
        self.database = root / 'var/lib/exterminator/reports.sqlite3'
        self.backups = root / 'var/backups/exterminator'
        self.unit = root / 'etc/systemd/system/exterminator@.service'
        self.state = self.base / 'managed.json'
        self.transaction = self.base / 'transaction.json'
        self.owner = None

    def run(self, args, **kwargs):
        import subprocess
        return subprocess.run(args, check=True, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                              'LANG': 'C.UTF-8', 'HOME': '/root', 'PIP_CONFIG_FILE': '/dev/null'}, **kwargs)

    def systemctl(self, *args):
        if args and args[0] == 'reset-failed':
            # Fresh inactive instances may be unloaded; resetting them fails on Debian.
            failed = tuple(unit for unit in args[1:]
                           if self.unit_property(unit, 'ActiveState') == 'failed')
            if not failed:
                return
            args = ('reset-failed', *failed)
        return self.run(['/usr/bin/systemctl', *args])

    def unit_property(self, unit, prop):
        return self.run(['/usr/bin/systemctl', 'show', unit, '--property=' + prop, '--value'],
                        capture_output=True, text=True).stdout.strip()

    def preflight_install(self):
        import pwd
        import grp
        for path in (self.base, self.config.parent, self.database.parent, self.backups, self.unit):
            assert_absent(path)
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup('exterminator')
            except KeyError:
                continue
            raise ValueError('Existing exterminator account/group refused')
        self.check_unit_conflicts(install=True)

    def check_unit_conflicts(self, install=False):
        names = (*UNITS, 'exterminator@.service')
        for name in names:
            # systemctl show only accepts instances, never an uninstantiated template.
            # Instance lookup also discovers inherited templates in systemd's load path.
            if name in UNITS:
                if install and self.unit_property(name, 'LoadState') != 'not-found':
                    raise ValueError('Existing systemd unit refused')
                if self.unit_property(name, 'DropInPaths'):
                    raise ValueError('Systemd unit overrides refused')
                if not install:
                    fragment = self.unit_property(name, 'FragmentPath')
                    if fragment and fragment != str(self.unit):
                        raise ValueError('Unrelated systemd unit refused')
            for directory in ('etc/systemd/system', 'run/systemd/system', 'usr/lib/systemd/system'):
                path = self.root / directory / name
                dropin = path.with_name(name + '.d')
                if dropin.exists() or dropin.is_symlink():
                    raise ValueError('Existing unit drop-in path refused')
                if path == self.unit and not install:
                    continue
                if path.exists() or path.is_symlink():
                    raise ValueError('Existing unit or drop-in path refused')

    def validate_data_paths(self):
        assert_plain_path(self.database.parent)
        for path in self.database.parent.iterdir():
            if path.name not in ('reports.sqlite3', 'reports.sqlite3-wal', 'reports.sqlite3-shm', 'reports.sqlite3-journal'):
                raise ValueError('Unrelated data file refused')
            assert_plain_path(path)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Unsafe data file')

    def stage(self, manifest, files, package_hash):
        release = manifest['version'] + '-' + package_hash[:12]
        path = self.releases / release
        assert_plain_path(path)
        if path.exists():
            if self.verify_release(release) != manifest:
                raise ValueError('Existing release does not match package')
            return release
        assert_absent(path)
        path.mkdir(mode=0o755)
        for name, data in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            atomic_write(target, data, mode=0o644)
        atomic_write(path / 'manifest.json', json.dumps(manifest, sort_keys=True).encode(), mode=0o644)
        self.run(['/usr/bin/python3', '-m', 'venv', str(path / 'venv')])
        command = [str(path / 'venv/bin/python'), '-m', 'pip', '--isolated', 'install',
                   '--disable-pip-version-check', '--no-cache-dir', '--require-hashes', '--only-binary=:all:']
        if (path / 'wheels').is_dir():
            command += ['--no-index', '--find-links', str(path / 'wheels')]
        else:
            command += ['--index-url', 'https://pypi.org/simple']
        self.run(command + ['-r', str(path / 'requirements-production.txt')])
        # An incomplete stage is never activated or silently reused.
        atomic_write(path / '.ready', b'1\n', mode=0o644)
        self.verify_release(release)
        return release

    def verify_release(self, release):
        if not re.fullmatch(RELEASE_ID, release):
            raise ValueError('Invalid release ID')
        path = self.releases / release
        assert_owned(path)
        assert_owned(path / 'manifest.json')
        assert_plain_path(path / '.ready')
        manifest = json.loads(regular_bytes(path / 'manifest.json'))
        if not (path / '.ready').is_file() or not PAYLOAD_FILES <= manifest['files'].keys():
            raise ValueError('Incomplete release')
        # Check the entire tree, including generated venv code. Only venv's known
        # interpreter/lib aliases may be symlinks, never application/manifest files.
        aliases = {'venv/bin/python': 'python3', 'venv/bin/python3': '/usr/bin/python3',
                   'venv/bin/python3.13': 'python3', 'venv/lib64': 'lib'}
        for directory, dirs, names in os.walk(path, followlinks=False):
            for name in dirs + names:
                child = Path(directory) / name
                relative = child.relative_to(path).as_posix()
                if child.is_symlink():
                    if aliases.get(relative) != str(child.readlink()) or child.lstat().st_uid != os.geteuid():
                        raise ValueError('Symlink in release refused')
                else:
                    assert_owned(child)
        for name, sha in manifest['files'].items():
            if name not in PAYLOAD_FILES and not re.fullmatch(r'wheels/[A-Za-z0-9_.+-]+\.whl', name):
                raise ValueError('Unmanaged release path')
            assert_plain_path(path / name)
            if digest(regular_bytes(path / name)) != sha:
                raise ValueError('Release file changed')
        return manifest

    def write_state(self):
        atomic_write(self.state, json.dumps({'format': 1, 'application': 'we-exterminator',
                     'uid': self.owner[0], 'gid': self.owner[1],
                     'unit_sha256': digest(regular_bytes(self.unit))}).encode())

    def preflight_managed(self):
        import pwd
        import grp
        for path in (self.base, self.releases, self.config.parent, self.backups, self.state, self.unit, self.config):
            assert_owned(path)
        state = json.loads(regular_bytes(self.state))
        if state.get('application') != 'we-exterminator' or state.get('format') != 1:
            raise ValueError('Unmanaged installation')
        user, group = pwd.getpwnam('exterminator'), grp.getgrnam('exterminator')
        if (user.pw_uid != state['uid'] or user.pw_gid != state['gid'] or group.gr_gid != state['gid']
                or user.pw_uid == 0 or user.pw_shell != '/usr/sbin/nologin' or user.pw_dir != '/nonexistent'
                or set(group.gr_mem) - {'exterminator'}):
            raise ValueError('Service identity changed')
        if any(g.gr_gid != group.gr_gid and 'exterminator' in g.gr_mem for g in grp.getgrall()):
            raise ValueError('Service identity has supplementary groups')
        self.owner = (user.pw_uid, user.pw_gid)
        assert_owned(self.database.parent, uid=user.pw_uid)
        if digest(regular_bytes(self.unit)) != state['unit_sha256']:
            raise ValueError('Managed unit changed; refusing overwrite')
        self.check_unit_conflicts()
        self.validate_data_paths()
        self.verify_release(current_release(self.base))

    def validate_config(self, release, data):
        import uuid
        candidate = self.config.parent / ('.candidate-' + uuid.uuid4().hex)
        atomic_write(candidate, data, mode=0o640, owner=(os.geteuid(), self.owner[1]))
        try:
            result = self.run(['/usr/sbin/runuser', '-u', 'exterminator', '--',
                              str(self.releases / release / 'venv/bin/python'), '-B', '-c',
                              'import json,sys; from dataclasses import asdict; from production import load_config; '
                              'print(json.dumps({k: asdict(v) for k,v in load_config(sys.argv[1]).items()}))',
                              str(candidate)], cwd=self.releases / release, capture_output=True, text=True)
            return json.loads(result.stdout)
        finally:
            candidate.unlink()

    def bootstrap(self):
        import pwd
        self.run(['/usr/sbin/groupadd', '--system', 'exterminator'])
        self.run(['/usr/sbin/useradd', '--system', '--gid', 'exterminator', '--home-dir', '/nonexistent',
                  '--no-create-home', '--shell', '/usr/sbin/nologin', 'exterminator'])
        user = pwd.getpwnam('exterminator')
        self.owner = (user.pw_uid, user.pw_gid)
        self.base.mkdir(mode=0o755)
        self.releases.mkdir(mode=0o755)
        self.config.parent.mkdir(mode=0o750)
        os.chown(self.config.parent, 0, user.pw_gid)
        self.database.parent.mkdir(mode=0o700)
        os.chown(self.database.parent, *self.owner)
        self.backups.mkdir(mode=0o700)

    def write_units(self, release):
        atomic_write(self.unit, regular_bytes(self.releases / release / 'deploy/exterminator@.service'), mode=0o644)

    def initialize_database(self, release):
        self.run(['/usr/sbin/runuser', '-u', 'exterminator', '--',
                  str(self.releases / release / 'venv/bin/python'), '-B', '-c',
                  'import os,sys; os.umask(0o077); from store import Store; Store(sys.argv[1]).health()',
                  str(self.database)], cwd=self.releases / release, capture_output=True)

    def check_ports(self, settings):
        import socket
        for surface in settings.values():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((surface['bind'], surface['port']))

    def health(self, release, settings):
        import http.client
        import time
        version = self.verify_release(release)['version']
        for name in ('admin', 'submission'):
            surface = settings[name]
            deadline = time.monotonic() + 40
            while True:
                connection = http.client.HTTPConnection(surface['bind'], surface['port'], timeout=2,
                                                        source_address=(surface['bind'], 0))
                try:
                    connection.request('GET', '/healthz')
                    response = connection.getresponse()
                    body = json.loads(response.read(4096))
                    if (response.status == 200 and body == {'ok': True, 'surface': name, 'version': version}
                            and self.unit_property(f'exterminator@{name}.service', 'ActiveState') == 'active'):
                        break
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
                if time.monotonic() >= deadline:
                    raise ValueError(f'{name} health check failed')
                time.sleep(0.25)

    def deploy(self, action, package, package_hash, config_path=None):
        manifest, files = read_package(package, package_hash)
        config_data = regular_bytes(config_path, limit=65536) if config_path else None
        if action == 'install':
            self.preflight_install()
            self.bootstrap()
            config_data = config_data if config_data is not None else files['deploy/config.example.json']
        elif action == 'update':
            self.preflight_managed()
            if self.transaction.exists():
                raise ValueError('Incomplete transaction: explicitly rollback or use manual recovery first')
            config_data = config_data if config_data is not None else regular_bytes(self.config)
        else:
            raise ValueError('Unknown deployment action')
        release = self.stage(manifest, files, package_hash)
        settings = self.validate_config(release, config_data)
        if action == 'install':
            self.write_units(release)
            self.systemctl('daemon-reload')
        self.activate(release, config_data, settings)

    def activate(self, release, config_data, settings, restore=None):
        record = {'target': release, 'phase': 'stopping', 'snapshot': None}
        atomic_write(self.transaction, json.dumps(record).encode())
        try:
            self.systemctl('stop', *UNITS)
            self.validate_data_paths()
            if (self.base / 'current').is_symlink():
                saved = save_snapshot(self.backups, self.database, self.config, current_release(self.base))
                record['snapshot'] = saved.name
                print(f'Preserved database/config: {saved}')
            record['phase'] = 'saved'
            atomic_write(self.transaction, json.dumps(record).encode())
            self.check_ports(settings)
            if restore:
                # Both surfaces are stopped and current data is already safely preserved.
                atomic_write(self.database, regular_bytes(restore / 'reports.sqlite3'), owner=self.owner)
                for suffix in ('-wal', '-shm', '-journal'):
                    self.database.with_name(self.database.name + suffix).unlink(missing_ok=True)
            atomic_write(self.config, config_data, mode=0o640, owner=(os.geteuid(), self.owner[1]))
            self.initialize_database(release)
            self.write_units(release)
            switch_current(self.base, release)
            self.write_state()
            record['phase'] = 'switched'
            atomic_write(self.transaction, json.dumps(record).encode())
            self.systemctl('daemon-reload')
            self.systemctl('reset-failed', *UNITS)
            self.systemctl('enable', *UNITS)
            self.systemctl('start', *UNITS)
            self.health(release, settings)
        except BaseException:
            self.systemctl('stop', *UNITS)
            print(f'Transition failed. Both surfaces left stopped; inspect {self.transaction}. No automatic data rollback.')
            raise
        self.transaction.unlink()
        sync_dir(self.base)
        print(f'Healthy release: {release}')

    def rollback(self, snapshot_id, restore_data):
        if not restore_data:
            raise ValueError('Rollback requires --restore-data; current data will be preserved first')
        metadata = read_snapshot(self.backups, snapshot_id)
        self.preflight_managed()
        release = metadata['release']
        self.verify_release(release)
        snapshot = self.backups / snapshot_id
        config_data = regular_bytes(snapshot / 'config.json')
        settings = self.validate_config(release, config_data)
        self.activate(release, config_data, settings, restore=snapshot)


def require_platform():
    import platform
    import sys
    release = Path('/etc/os-release')
    values = dict(line.split('=', 1) for line in release.read_text().splitlines() if '=' in line) if release.exists() else {}
    if (os.geteuid() != 0 or platform.system() != 'Linux' or platform.machine() != 'x86_64'
            or values.get('ID', '').strip('"') != 'debian' or values.get('VERSION_ID', '').strip('"') != '13'
            or sys.version_info[:2] != (3, 13)):
        raise ValueError('Controller requires sudo/root on Debian 13 amd64 with system Python 3.13')


def main():
    import argparse
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('install', 'update', 'verify-package'):
        sub = commands.add_parser(name)
        sub.add_argument('--package', type=Path, required=True)
        sub.add_argument('--sha256', required=True)
        if name != 'verify-package':
            sub.add_argument('--config', type=Path)
    sub = commands.add_parser('rollback')
    sub.add_argument('--snapshot', required=True)
    sub.add_argument('--restore-data', action='store_true')
    commands.add_parser('status')
    args = parser.parse_args()
    if args.command == 'verify-package':
        manifest, files = read_package(args.package, args.sha256)
        print(json.dumps({'version': manifest['version'], 'target': manifest['target'], 'files': len(files)}))
        return
    require_platform()
    os.umask(0o022)
    ctl = Controller()
    # Lock a trusted, pre-existing parent directory; never create a lock in /tmp.
    # All controller invocations share this lock, including first installation.
    assert_owned(Path('/opt'))
    lock = os.open('/opt', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in ('install', 'update'):
            ctl.deploy(args.command, args.package, args.sha256, args.config)
        elif args.command == 'rollback':
            ctl.rollback(args.snapshot, args.restore_data)
        else:
            ctl.preflight_managed()
            release = current_release(ctl.base)
            print(f'Current: {release}')
            if ctl.transaction.exists():
                print('Incomplete transaction: ' + regular_bytes(ctl.transaction).decode())
            for path in sorted(ctl.backups.iterdir()):
                if re.fullmatch(SNAPSHOT_ID, path.name):
                    print(f'Snapshot: {path.name}')
            ctl.health(release, ctl.validate_config(release, regular_bytes(ctl.config)))
    finally:
        os.close(lock)


if __name__ == '__main__':
    import sys
    import subprocess
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        # Never print captured subprocess output, SQL, config contents or report data.
        print(f'Exterminator controller refused/failed: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
