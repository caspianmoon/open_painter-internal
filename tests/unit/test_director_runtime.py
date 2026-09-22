"""Automatic native setup, integrity, safe extraction, reuse and repair."""
import base64
import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.ai import runtime


def archive(name='package/vendor/aarch64-apple-darwin/bin/codex', kind=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as package:
        member = tarfile.TarInfo(name)
        member.mode = 0o755
        if kind:
            member.type, member.linkname = kind, '/tmp/escaped'
            package.addfile(member)
        else:
            data = b'native runtime test fixture'
            member.size = len(data)
            package.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class RuntimeTests(unittest.TestCase):
    def install(self, root, payload, expected=None):
        manifest = dict(version='0.154.0', artifacts={'darwin-arm64': dict(
            tarball='https://registry.npmjs.org/test.tgz', integrity='sha512-' +
            base64.b64encode(hashlib.sha512(expected or payload).digest()).decode())})
        with patch.object(runtime, 'platform_key', return_value='darwin-arm64'), \
             patch.object(runtime, 'specification', return_value=manifest), \
             patch.object(runtime.urllib.request, 'urlopen', return_value=io.BytesIO(payload)) as download:
            path = runtime.ensure(root)
            return Path(path), download.call_count

    def test_setup_without_path_reuses_cache_and_repairs_corruption(self):
        payload = archive()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'PATH': ''}):
            binary, calls = self.install(root, payload)
            self.assertEqual(calls, 1)
            self.assertTrue(os.access(binary, os.X_OK))
            same, calls = self.install(root, payload)
            self.assertEqual(same, binary)
            self.assertEqual(calls, 0)
            binary.write_bytes(b'corrupt')
            repaired, calls = self.install(root, payload)
            self.assertEqual(calls, 1)
            self.assertEqual(repaired.read_bytes(), b'native runtime test fixture')

    def test_bad_integrity_never_installs_or_executes(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'integrity'):
                self.install(root, archive(), expected=b'other release')
            self.assertEqual(list((Path(root) / 'runtime').iterdir()), [])

    def test_archive_escape_and_links_rejected(self):
        for name, kind in (('package/../../escaped', None), ('/absolute', None),
                           ('package/vendor/link', tarfile.SYMTYPE),
                           ('package/vendor/hardlink', tarfile.LNKTYPE),
                           ('package/vendor/drive:C', None)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ValueError):
                    self.install(root, archive(name, kind))
                self.assertEqual(list((Path(root) / 'runtime').iterdir()), [])

    def test_platform_selection(self):
        for system, machine, expected in (('Darwin', 'arm64', 'darwin-arm64'),
                ('Windows', 'AMD64', 'win32-x64'), ('Linux', 'aarch64', 'linux-arm64')):
            with patch.object(runtime.platform, 'system', return_value=system), \
                 patch.object(runtime.platform, 'machine', return_value=machine):
                self.assertEqual(runtime.platform_key(), expected)
        with patch.object(runtime.platform, 'machine', return_value='unsupported'):
            with self.assertRaisesRegex(ValueError, 'processor'):
                runtime.platform_key()


if __name__ == '__main__':
    unittest.main()
