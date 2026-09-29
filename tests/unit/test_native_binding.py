"""Compiled submission validation and native generation ownership."""
import ctypes as C
import os
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.compiler import Node, Target, CONSTANT, CONVERT
from open_painter.native_bridge.graph import Patch
from open_painter.native_bridge.revisions import Publication


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        cls.binding = getattr(lib, '_op_binding', None)
        if cls.binding is None:
            raise unittest.SkipTest('compiled binding not supplied')

    def test_rejects_truncated_and_untyped_ir(self):
        for bad in (b'', b'x', b'x' * (C.sizeof(Node) - 1)):
            with self.assertRaises((ValueError, RuntimeError)):
                self.binding.compile_ir(bad, bytes(Target(0, 0, 0)))
        nodes = (Node * 2)()
        nodes[0].opcode, nodes[0].type, nodes[0].value[0] = CONSTANT, 1, .25
        nodes[1].opcode, nodes[1].type = CONVERT, 2
        target = bytes(Target(1, 0, 0))
        self.assertIn('op_out0', self.binding.compile_ir(bytes(nodes), target))
        randomizer = random.Random(829)
        for _ in range(400):
            candidate = bytearray(bytes(nodes))
            for _ in range(3):
                candidate[randomizer.randrange(len(candidate))] ^= randomizer.randrange(1, 256)
            try:
                result = self.binding.compile_ir(candidate, target)
            except (ValueError, RuntimeError):
                continue
            self.assertIn('void main()', result)

    def test_graph_patch_atomicity_and_closed_generation(self):
        binding = self.binding
        graph = binding.graph_create()
        offsets, edges = (C.c_uint32 * 4)(0, 0, 1, 2), (C.c_uint32 * 2)(0, 1)
        binding.graph_replace(graph, bytes(offsets), bytes(edges))
        patch = (Patch * 3)(Patch(0, 0, 10), Patch(1, 0, 20), Patch(2, 0, 30))
        residents = bytes((C.c_uint64 * 3)())
        stamps, plan = binding.graph_evaluate(graph, bytes(patch), residents)
        self.assertEqual(len(plan), 12)
        self.assertEqual(binding.graph_plan(graph, 2, stamps), b'')
        patch[0].value = 99
        patch[1].node = 100
        with self.assertRaises(RuntimeError):
            binding.graph_evaluate(graph, bytes(patch), stamps)
        self.assertEqual(binding.graph_evaluate(graph, b'', stamps), (stamps, b''))
        cycle = (C.c_uint32 * 2)(1, 0)
        with self.assertRaises(RuntimeError):
            binding.graph_replace(graph, bytes(offsets), bytes(cycle))
        self.assertEqual(binding.graph_evaluate(graph, b'', stamps), (stamps, b''))
        binding.graph_close(graph)
        with self.assertRaisesRegex(RuntimeError, 'Closed'):
            binding.graph_evaluate(graph, b'', stamps)
        binding.graph_close(graph)

    def test_graph_execution_failure_cleanup_and_reentry(self):
        binding = self.binding
        graph = binding.graph_create()
        offsets, edges = bytes((C.c_uint32 * 4)(0, 0, 1, 2)), bytes((C.c_uint32 * 2)(0, 1))
        binding.graph_replace(graph, offsets, edges)
        resident = bytes((C.c_uint64 * 3)())
        with self.assertRaises(RuntimeError):
            binding.graph_execute(graph, 2, resident, lambda *_: None)
        stamps, _ = binding.graph_evaluate(graph, b'', resident)
        events, holds = [], set()
        def submit(action, node, stamp):
            events.append((action, node))
            self.assertNotEqual(stamp, 0)
            if action == 0:
                holds.add(node)
                for operation in (lambda: binding.graph_close(graph),
                                  lambda: binding.graph_replace(graph, offsets, edges),
                                  lambda: binding.graph_evaluate(graph, b'', resident),
                                  lambda: binding.graph_execute(graph, 2, resident, submit)):
                    with self.assertRaises(RuntimeError):
                        operation()
            elif action == 1 and node == 1:
                raise MemoryError('graph draw failed')
            elif action == 2:
                holds.remove(node)
        with self.assertRaisesRegex(MemoryError, 'graph draw failed'):
            binding.graph_execute(graph, 2, resident, submit)
        self.assertFalse(holds)
        self.assertEqual([n for a, n in events if a == 1], [0, 1])
        self.assertEqual(binding.graph_execute(graph, 2, resident, lambda *_: None), 3)
        self.assertEqual(binding.graph_execute(graph, 2, stamps, lambda *_: None), 0)
        with self.assertRaises(ValueError):
            binding.graph_execute(graph, 2, b'', submit)
        binding.graph_close(graph)
        with self.assertRaises(RuntimeError):
            binding.graph_execute(graph, 2, resident, submit)

    def test_graph_rejects_previous_abi_capsule(self):
        # The old graph owner has a different layout. Reject its tag before
        # dereferencing its payload, including when both DLLs remain mapped.
        storage = C.create_string_buffer(64)
        factory = C.pythonapi.PyCapsule_New
        factory.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p]
        factory.restype = C.py_object
        capsule = factory(C.addressof(storage), b'openpainter.graph', None)
        with self.assertRaises(ValueError):
            self.binding.graph_close(capsule)
        with self.assertRaises(ValueError):
            self.binding.graph_plan(capsule, 0, b'')

    def test_buffer_shapes_rejected_before_native_access(self):
        for fn, args in ((self.binding.rasterize, (b'x', 16, True)),
                         (self.binding.distances, (bytes(24), b'')),
                         (self.binding.brush_uniforms, (bytes(52), b''))):
            with self.assertRaises(ValueError):
                fn(*args)
        self.assertEqual(self.binding.rasterize(b'', 16, True), b'')
        with self.assertRaises(ValueError):
            self.binding.shader_source('unknown shader')

    def test_publication_exception_rollback_reentry_and_closed_owner(self):
        binding = self.binding
        owner = binding.revisions_create()
        records = bytes((Publication * 2)(Publication(10, 0, 4), Publication(11, 1, 4)))
        events = []
        def submit(action, index, level):
            events.append((action, index, level))
            if action == 0:
                with self.assertRaises(RuntimeError):
                    binding.revisions_close(owner)
                with self.assertRaises(RuntimeError):
                    binding.revisions_publish(owner, records, submit)
            if action == 1 and index == 1 and level == 1:
                raise MemoryError('injected submit failure')
        with self.assertRaisesRegex(MemoryError, 'injected submit failure'):
            binding.revisions_publish(owner, records, submit)
        self.assertEqual(events[:2], [(0, 0, 0), (0, 1, 0)])
        self.assertEqual(events[-6:], [(2, i, mip) for i in range(2) for mip in range(3)])
        self.assertEqual(binding.revisions_references(owner, 10), 0)
        self.assertEqual(binding.revisions_publish(owner, records, lambda *_: None), 1)
        self.assertGreater(binding.revisions_references(owner, 10), 0)
        def lost(action, index, level):
            if action in (1, 2):
                raise OSError('lost graphics context')
        with self.assertRaisesRegex(RuntimeError, 'recovery failed') as caught:
            binding.revisions_publish(owner, records, lost)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        with self.assertRaises(ValueError):
            binding.revisions_publish(owner, records[:-1], submit)
        binding.revisions_close(owner)
        binding.revisions_close(owner)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            binding.revisions_publish(owner, records, submit)
