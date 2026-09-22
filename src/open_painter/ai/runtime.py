"""Provision the pinned native Codex runtime; no shell, npm, or user PATH required.

Runs in the companion, never on Blender's UI thread. Downloads contain only the
official platform package and must match the release-pinned SHA-512 digest.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
MAX_SECONDS = 240


def specification():
    return json.loads(Path(__file__).with_name('runtime_manifest.json').read_text())


def platform_key():
    systems = {'Darwin': 'darwin', 'Linux': 'linux', 'Windows': 'win32'}
    arches = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}
    system, arch = systems.get(platform.system()), arches.get(platform.machine().lower())
    if not system or not arch:
        raise ValueError('Material Director does not yet support this operating system/processor')
    return system + '-' + arch


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def cached(directory, integrity):
    try:
        if directory.is_symlink():
            return None
        receipt = json.loads((directory / 'runtime.json').read_text())
        relative = PurePosixPath(receipt['executable'])
        if relative.is_absolute() or '..' in relative.parts or receipt['integrity'] != integrity:
            return None
        binary = directory.joinpath(*relative.parts)
        if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
            return None
        if digest_file(binary) != receipt['sha256']:
            return None
        return binary
    except (OSError, ValueError, KeyError, TypeError):
        return None


def unpack(archive, destination):
    """Extract regular package files only; never follow archive links or run scripts."""
    total = 0
    with tarfile.open(archive, 'r:gz') as package:
        for count, member in enumerate(package, 1):
            path = PurePosixPath(member.name)
            if (count > 128 or path.is_absolute() or '..' in path.parts or
                    '\\' in member.name or ':' in member.name or not path.parts or path.parts[0] != 'package'):
                raise ValueError('Invalid Material Director runtime package paths')
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Unsupported link in Material Director runtime package')
            total += member.size
            if total > MAX_UNPACKED:
                raise ValueError('Material Director runtime package is too large')
            target = destination.joinpath(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    binaries = [p for p in destination.glob('vendor/*/bin/codex*')
                if p.name in {'codex', 'codex.exe'} and p.is_file()]
    if len(binaries) != 1:
        raise ValueError('Material Director runtime package has no unique native executable')
    binaries[0].chmod(0o755)
    return binaries[0]


def ensure(state_directory, emit=lambda message: None):
    manifest = specification()
    key = platform_key()
    artifact = manifest['artifacts'][key]
    base = Path(state_directory).resolve() / 'runtime'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = base / (manifest['version'] + '-' + key)
    binary = cached(target, artifact['integrity'])
    if binary:
        return str(binary)
    emit('Preparing Material Director for first use…')
    try:
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=base) as temporary:
            temporary = Path(temporary)
            archive = temporary / 'runtime.tgz'
            digest = hashlib.sha512()
            started, received, reported = time.monotonic(), 0, -1
            request = urllib.request.Request(artifact['tarball'], headers={'User-Agent': 'OpenPainter-RuntimeSetup/1'})
            with urllib.request.urlopen(request, timeout=20) as response, archive.open('wb') as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    received += len(chunk)
                    if received > MAX_DOWNLOAD or time.monotonic() - started > MAX_SECONDS:
                        raise ValueError('Material Director setup exceeded its download limit; reconnect to retry')
                    digest.update(chunk)
                    output.write(chunk)
                    mb = received // (1024 * 1024)
                    if mb // 5 > reported:
                        reported = mb // 5
                        emit(f'Preparing Material Director: downloaded {mb} MB…')
            expected = base64.b64decode(artifact['integrity'].removeprefix('sha512-'), validate=True)
            if digest.digest() != expected:
                raise ValueError('Material Director download failed its integrity check; reconnect to retry')
            emit('Verifying and installing Material Director…')
            stage = temporary / 'package'
            stage.mkdir()
            binary = unpack(archive, stage)
            relative = binary.relative_to(stage).as_posix()
            (stage / 'runtime.json').write_text(json.dumps(dict(integrity=artifact['integrity'],
                executable=relative, sha256=digest_file(binary))))
            if target.exists() or target.is_symlink():
                existing = cached(target, artifact['integrity'])
                if existing:
                    return str(existing)  # Another Blender finished the same installation.
                target.rename(temporary / 'previous-incomplete-runtime')
            try:
                stage.rename(target)
            except OSError:
                existing = cached(target, artifact['integrity'])
                if existing:
                    return str(existing)
                raise
            return str(target / relative)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError('Could not prepare Material Director. Check your network connection and reconnect to retry.') from exc
