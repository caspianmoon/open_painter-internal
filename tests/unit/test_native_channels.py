"""Native channel acceptance is atomic across render/commit exceptions."""
import ctypes as C
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.channels import NativeChannels, Request


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class ChannelTests(unittest.TestCase):
    def test_compiled_and_ctypes_failure_retry_selection_and_owned_requests(self):
        for compiled in (True, False):
            lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
            if not compiled:
                lib._op_binding = None
            owner = NativeChannels(lib)
            records = (Request * 8)(*(Request(revision=11, target=i+1, channel=i, kind=1, size=512)
                                      for i in range(8)))
            try:
                for fail_at in range(9):
                    events = []
                    def fail(action, channel):
                        with self.assertRaises(RuntimeError):
                            owner.close()
                        with self.assertRaises(RuntimeError):
                            owner.execute(records, lambda *_: None)
                        events.append((action, channel))
                        if len(events) == fail_at + 1:
                            raise MemoryError('channel failure')
                    with self.assertRaisesRegex(MemoryError, 'channel failure'):
                        owner.execute(records, fail)
                    self.assertEqual(len(events), fail_at + 1)
                original = bytes(records)
                events = []
                def copied(action, channel):
                    events.append((action, channel))
                    records[7].revision = 900
                self.assertEqual(owner.execute(records, copied), 255)
                self.assertEqual(events, [(0, i) for i in range(8)] + [(1, 255)])
                records = (Request * 8).from_buffer_copy(original)
                self.assertEqual(owner.execute(records, copied), 0)
                records[4].revision += 1
                self.assertEqual(owner.execute(records, lambda *_: None), 16)
                self.assertEqual(owner.execute(records, lambda *_: None), 0)
            finally:
                owner.close()

    def test_binding_validation_reentry_reset_and_closed_capsule(self):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        b = getattr(lib, '_op_binding', None)
        if b is None:
            self.skipTest('compiled binding not supplied')
        owner = b.channels_create()
        data = bytes(Request(target=1, channel=0, size=128))
        def submit(*_):
            for call in (lambda: b.channels_close(owner), lambda: b.channels_reset(owner),
                         lambda: b.channels_execute(owner, data, submit)):
                with self.assertRaises(RuntimeError):
                    call()
        self.assertEqual(b.channels_execute(owner, data, submit), 1)
        self.assertEqual(b.channels_execute(owner, data, submit), 0)
        b.channels_reset(owner)
        self.assertEqual(b.channels_execute(owner, data, submit), 1)
        for malformed in (b'x', data[:-1], data * 9):
            with self.assertRaises(ValueError):
                b.channels_execute(owner, malformed, submit)
        with self.assertRaises(RuntimeError):
            b.channels_execute(owner, data * 2, submit)
        with self.assertRaises(TypeError):
            b.channels_execute(owner, data, None)
        b.channels_close(owner)
        b.channels_close(owner)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            b.channels_execute(owner, data, submit)

    def test_host_registry_constants_and_configuration_changes(self):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        owner = NativeChannels(lib)
        image = SimpleNamespace(as_pointer=lambda: 123456789)
        def request(ref=('const', (.1, .2, .3, 1)), precise=False, pad=True, force=False):
            return owner.request(0, image, ref, 7, 512, precise, pad, force)
        try:
            item = request()
            self.assertNotEqual(item.target, image.as_pointer())
            self.assertEqual(owner.execute((Request * 1)(item), lambda *_: None), 1)
            self.assertEqual(owner.execute((Request * 1)(request()), lambda *_: None), 0)
            for item in (request(precise=True), request(pad=False), request(force=True),
                         request(ref=('const', (.1, .2, .4, 1)))):
                self.assertEqual(owner.execute((Request * 1)(item), lambda *_: None), 1)
            old = request().target
            image.session_uid = 99
            self.assertNotEqual(request().target, old)
            old = request().target
            image.as_pointer = lambda: 987654321
            self.assertNotEqual(request().target, old)
        finally:
            owner.close()


if __name__ == '__main__':
    unittest.main()
