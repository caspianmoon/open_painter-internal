"""Build libopenpainter and run its C tests for other platforms in containers.

    python3 scripts/cross_test_native.py [linux-arm64] [linux-amd64] [windows-amd64]

Uses the ``docker`` the environment points at (DOCKER_HOST, DOCKER_CONFIG). Linux builds
use GCC; the Windows build cross-compiles with MinGW-w64 and runs the tests under Wine,
so it checks a Windows PE build and the Windows ABI, though not MSVC. amd64 containers on
Apple silicon need Rosetta or QEMU, which makes the Windows run slow (the exhaustive sRGB
test takes about five minutes there). Prints each platform's ctest summary; exits nonzero
when one fails.
"""
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'debian:bookworm-slim'
PLATFORMS = {'linux-arm64': 'linux/arm64', 'linux-amd64': 'linux/amd64', 'windows-amd64': 'linux/amd64'}
LINUX = '''set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null && apt-get install -y -qq cmake gcc make >/dev/null
cd /work && tar -xzf source.tgz
cmake -S libopenpainter -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build -j2 2>&1 | grep -E "warning:|error:" | sort -u || true
cd build && ctest --output-on-failure
'''
WINDOWS = '''set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null && apt-get install -y -qq cmake make mingw-w64 wine64 >/dev/null 2>&1
cd /work && tar -xzf source.tgz
cat > mingw.cmake <<'T'
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER x86_64-w64-mingw32-gcc)
set(CMAKE_FIND_ROOT_PATH /usr/x86_64-w64-mingw32)
set(CMAKE_CROSSCOMPILING_EMULATOR wine)
T
cmake -S libopenpainter -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=/work/mingw.cmake \\
      -DOP_TEST_TIMEOUT=1200 >/dev/null
cmake --build build -j2 2>&1 | grep -E "warning:|error:" | sort -u || true
export WINEDEBUG=-all WINEPREFIX=/tmp/wine
wineboot -i >/dev/null 2>&1 || true
cd build && ctest --output-on-failure
'''


def run(*command, **kwargs):
    return subprocess.run(['docker', *command], check=True, text=True, **kwargs)


def main():
    targets = sys.argv[1:] or list(PLATFORMS)
    unknown = [t for t in targets if t not in PLATFORMS]
    if unknown:
        raise SystemExit(f'Unknown platforms {unknown}; choose from {list(PLATFORMS)}')
    failed = []
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / 'source.tgz'
        with tarfile.open(source, 'w:gz') as archive:
            archive.add(ROOT / 'libopenpainter', 'libopenpainter',
                        filter=lambda info: None if '/build' in info.name or info.name.endswith('.DS_Store') else info)
        for target in targets:
            script = Path(scratch) / 'build.sh'
            script.write_text(WINDOWS if target.startswith('windows') else LINUX, encoding="utf-8")
            container = run('run', '-d', '--platform', PLATFORMS[target], IMAGE, 'sleep', '7200',
                            capture_output=True).stdout.strip().splitlines()[-1]
            try:
                run('exec', container, 'mkdir', '-p', '/work')
                run('cp', str(source), f'{container}:/work/source.tgz', capture_output=True)
                run('cp', str(script), f'{container}:/work/build.sh', capture_output=True)
                result = subprocess.run(['docker', 'exec', container, 'bash', '/work/build.sh'],
                                        text=True, capture_output=True)
                lines = [line for line in result.stdout.splitlines()
                         if 'warning:' in line or 'error:' in line or 'tests passed' in line or 'Failed' in line
                         or '***' in line]
                print(f'{target}:', *lines, sep='\n  ', flush=True)
                if result.returncode:
                    failed.append(target)
                    print(result.stderr[-2000:], flush=True)
            finally:
                subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
    print('CROSS_TEST', 'FAIL ' + ' '.join(failed) if failed else 'PASS')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
