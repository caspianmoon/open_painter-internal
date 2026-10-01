"""Build and test the portable C17 library; optionally create a platform extension ZIP.

Usage: python3 scripts/build_native.py --package dist/open_painter-macos-arm64.zip
This is a developer build command. Installing the resulting ZIP needs no compiler.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=root / '.blender-test/native/build')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--python-include', type=Path, help='CPython 3.11+ headers for the limited ABI binding')
    parser.add_argument('--test-timeout', type=int,
                        help='Seconds each C test may take (default 20; slower machines need more)')
    args = parser.parse_args()
    build = args.build_dir.resolve()
    headers = args.python_include
    if args.package and headers is None:
        import sysconfig
        candidates = [root / 'native/vendor/blender/lib/macos_arm64/python/include/python3.13', Path(sysconfig.get_path('include'))]
        headers = next((p for p in candidates if (p / 'Python.h').is_file()), None)
        if headers is None:
            raise RuntimeError('Pass --python-include with CPython 3.11+ headers to build the release binding')
    options = ['-DOP_PYTHON_INCLUDE=' + str(headers.resolve())] if headers else []
    if args.test_timeout:
        options.append(f'-DOP_TEST_TIMEOUT={args.test_timeout}')
    subprocess.run(['cmake', '-S', str(root / 'libopenpainter'), '-B', str(build),
                    '-DCMAKE_BUILD_TYPE=Release', *options], check=True)
    subprocess.run(['cmake', '--build', str(build), '--config', 'Release', '--parallel'], check=True)
    subprocess.run(['ctest', '--test-dir', str(build), '-C', 'Release', '--output-on-failure'], check=True)
    names = ('libopenpainter.dylib', 'libopenpainter.so', 'openpainter.dll')
    libraries = [directory / name for directory in (build, build / 'Release') for name in names
                 if (directory / name).is_file()]
    if len(libraries) != 1:
        raise RuntimeError(f'Expected one platform library, got {libraries}')
    if args.package:
        subprocess.run([sys.executable, str(root / 'scripts/build_extension.py'),
                        '--native-library', str(libraries[0]), '--output', str(args.package)], check=True)
    print(libraries[0])


if __name__ == '__main__':
    main()
