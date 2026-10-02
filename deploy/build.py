#!/usr/bin/env python3
"""Build an explicit, deterministic source payload; optional Debian CPython 3.13 wheels."""
import argparse
import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib

from controller import PAYLOAD_FILES, digest, read_package, regular_bytes

ROOT = Path(__file__).resolve().parents[1]


def build(output, wheels=False):
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    # Require the production lock to be the exact no-dev export of the canonical lock.
    with tempfile.TemporaryDirectory() as temporary:
        exported = Path(temporary) / 'requirements.txt'
        subprocess.run(['uv', 'export', '--locked', '--no-dev', '--no-emit-project',
                        '--no-header', '--format', 'requirements-txt', '--output-file', str(exported)],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        normalize = lambda text: '\n'.join(line for line in text.splitlines() if not line.startswith('#'))
        if normalize(exported.read_text()) != normalize((ROOT / 'requirements-production.txt').read_text()):
            raise ValueError('Production dependency lock out of sync; re-export with uv')
        files = {}
        for name in sorted(PAYLOAD_FILES):
            path = ROOT / name
            if any((ROOT / p).is_symlink() for p in (Path(name), *Path(name).parents)):
                raise ValueError('Symlink source refused')
            files[name] = regular_bytes(path)
        if wheels:
            wheel_dir = Path(temporary) / 'wheels'
            # pip runs in a disposable uv tool environment, never in the system Python.
            subprocess.run(['uv', 'tool', 'run', '--from', 'pip', 'pip', '--isolated', 'download',
                            '--index-url', 'https://pypi.org/simple', '--require-hashes', '--only-binary=:all:',
                            '--platform', 'manylinux2014_x86_64', '--python-version', '3.13',
                            '--implementation', 'cp', '--abi', 'cp313', '--dest', str(wheel_dir),
                            '-r', str(ROOT / 'requirements-production.txt')], check=True)
            for path in sorted(wheel_dir.iterdir()):
                files['wheels/' + path.name] = regular_bytes(path)
    manifest = dict(format=1, version=version, target='debian13-amd64',
                    files={name: digest(data) for name, data in files.items()})
    files['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f'exterminator-{version}-debian13-amd64.tar.gz'
    with package.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for name, data in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                item.mode = 0o644
                tar.addfile(item, io.BytesIO(data))
    sha = digest(package.read_bytes())
    read_package(package, sha)
    package.with_suffix('.gz.sha256').write_text(f'{sha}  {package.name}\n')
    (output / 'manifest.json').write_bytes(files['manifest.json'])
    (output / 'exterminatorctl.py').write_bytes(files['deploy/controller.py'])
    (output / 'exterminatorctl.py.sha256').write_text(f"{digest(files['deploy/controller.py'])}  exterminatorctl.py\n")
    print(f'{package.resolve()}\nSHA256 {sha}')
    return package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/release')
    parser.add_argument('--wheels', action='store_true', help='Bundle only CPython 3.13 Linux amd64/universal wheels')
    args = parser.parse_args()
    build(args.output, args.wheels)
