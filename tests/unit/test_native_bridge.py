"""Library selection, artifact identity and real ctypes ownership regression tests."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from open_painter.native_bridge import NativeCore, NativeError, Status, load_library
from open_painter.native_bridge.capabilities import select_backend
from open_painter.native_bridge.loader import find_library
from collect_gate0_baseline import artifact_digests


class NativeBridgeTests(unittest.TestCase):
    def test_explicit_path_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / 'other.dylib'
            other.write_bytes(b'not a library')
            with patch.dict(os.environ, {'OP_LIBOPENPAINTER_PATH': str(other)}):
                self.assertIsNone(find_library(Path(tmp) / 'missing.dylib'))
                self.assertEqual(find_library(), other.resolve())
                with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                    load_library(other, expected_sha256='0' * 64)

    def test_identity_covers_entire_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'libopenpainter').mkdir()
            adapter = root / 'src/open_painter/native_bridge'
            adapter.mkdir(parents=True)
            file = adapter / 'core.py'
            file.write_text('first', encoding="utf-8")
            before = artifact_digests(root)
            file.write_text('second', encoding="utf-8")
            after = artifact_digests(root)
            self.assertNotEqual(before['adapter_sha256'], after['adapter_sha256'])
            self.assertIsNone(after['native_binary'])
            binary = root / 'test.so'
            binary.write_bytes(b'identity only')
            report = artifact_digests(root, binary)
            self.assertEqual(report['native_binary']['sha256'],
                             hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_unqualified_backend_never_selected(self):
        for backend in ('VULKAN', 'METAL', 'OPENGL', None):
            self.assertEqual(select_backend(native_loaded=True, has_gpu=True,
                                             backend=backend)['selected'], 'legacy')

    @unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
    def test_real_abi_lifetime_and_validation(self):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        core = NativeCore(lib)
        doc = core.document_create(1)
        for _ in range(10):
            tx = core.begin(doc)
            core.layer_add(doc, tx, 1)
            core.cancel(doc, tx)
        self.assertEqual(len(core.snapshot(doc)), 44)
        tx = core.begin(doc)
        core.layer_add(doc, tx, 1)
        with self.assertRaises(NativeError) as error:
            core.set_scalar(doc, tx, 1, 1, float('nan'))
        self.assertEqual(error.exception.status, Status.INVALID_ARGUMENT)
        core.set_scalar(doc, tx, 1, 1, .25)
        core.commit(doc, tx)
        self.assertEqual(core.composite_read(doc, 0, 1, 1)[0], .5)
        core.evaluate(doc)
        lease = core.acquire(doc)
        core.release(doc, lease)
        with self.assertRaises(NativeError):
            core.release(doc, lease)
        core.document_destroy(doc)
        with self.assertRaises(NativeError):
            core.begin(doc)
        with self.assertRaises(ValueError):
            core.composite_read(doc, 0, 0, -1)
        core.close()
        core.close()
        with self.assertRaises(NativeError):
            core.begin(doc)


if __name__ == '__main__':
    unittest.main()
