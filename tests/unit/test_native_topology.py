"""Native UV topology against an independent insertion-ordered edge oracle."""
from collections import defaultdict
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.topology import build, from_target, island_bounds


def reference(faces, vertices, uv, size):
    parents = list(range(len(faces)))
    edges, records, centroids = defaultdict(list), [], {}
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for face, (start, count, flags) in enumerate(faces):
        if not flags & 1:
            continue
        centroids[face] = uv[start:start+count].astype(np.float64).mean(axis=0) if count else np.zeros(2)
        for j in range(count):
            a, b = start+j, start+(j+1) % count
            if vertices[a] > vertices[b]:
                a, b = b, a
            item = (face, a, b, len(records))
            records.append(item)
            edges[int(vertices[a]), int(vertices[b])].append(item)
    def joined(a, b):
        return np.all(np.abs(uv[a[1:3], :] - uv[b[1:3], :]) <= np.float32(1e-6))
    border = np.ones(len(records), bool)
    for group in edges.values():
        if len(group) == 2 and joined(*group):
            a, b = group
            parents[root(b[0])] = root(a[0])
            border[[a[3], b[3]]] = False
    labels = np.array([root(i) if f[2] & 1 else -1 for i, f in enumerate(faces)], np.int64)
    roots = sorted(set(labels) - {-1})
    compact = {r: i for i, r in enumerate(roots)}
    islands = np.array([compact.get(i, -1) for i in labels], np.int64)
    borders = defaultdict(list)
    for keep, (face, a, b, _) in zip(border, records):
        if keep:
            borders[int(labels[face])].append(uv[[a, b]])
    sides, owners = [], []
    for key in sorted(edges):
        group = [item for item in edges[key] if faces[item[0], 2] & 2]
        if len(group) != 2 or joined(*group):
            continue
        for a, b in (group, group[::-1]):
            sides.append(np.concatenate((uv[a[1]]*size, uv[a[2]]*size,
                 (centroids[a[0]]*size).astype(np.float32), uv[b[1]]*size, uv[b[2]]*size,
                 (centroids[b[0]]*size).astype(np.float32))))
            owners.append(islands[a[0]])
    return labels, islands, {i: np.asarray(v) for i, v in borders.items()}, np.asarray(sides, np.float32).reshape(-1, 12), np.asarray(owners, np.int64)


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class TopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def compare(self, faces, vertices, uv, size=128):
        expected = reference(faces, vertices, uv, size)
        actual = build(self.lib, faces, vertices, uv, size)
        np.testing.assert_array_equal(actual.labels, expected[0])
        np.testing.assert_array_equal(actual.islands, expected[1])
        self.assertEqual(actual.island_count, len(set(expected[1]) - {-1}))
        borders = actual.grouped_borders()
        self.assertEqual(set(borders), set(expected[2]))
        for island in borders:
            np.testing.assert_array_equal(borders[island], expected[2][island])
        np.testing.assert_array_equal(actual.sides['island'], expected[4])
        np.testing.assert_array_equal(actual.sides['vectors'], expected[3])
        return actual

    def test_random_material_subsets_ngons_nonmanifold_and_representatives(self):
        rng = np.random.default_rng(50271)
        for iteration in range(75):
            counts = rng.integers(0, 8, 70)
            faces = np.column_stack((np.r_[0, counts.cumsum()[:-1]], counts, rng.choice((0, 1, 3), len(counts))))
            vertices = rng.integers(0, 25, counts.sum(), dtype=np.int64)
            coordinates = rng.uniform(-1, 2, (25, 2)).astype(np.float32)
            uv = coordinates[vertices].copy()
            for start, count, _ in faces[::3]:
                uv[start:start+count] += np.float32(.2)
            self.compare(faces, vertices, uv, (1, 512, 8192)[iteration % 3])

    def test_tolerance_orientation_degenerate_and_int64_vertex_keys(self):
        faces = np.array([[0, 4, 3], [4, 4, 3], [8, 3, 1], [11, 0, 1]], np.int64)
        vertices = np.array([0, 1, 2, 3, 1, 0, 4, 5, 0, 1, 6], np.int64)
        uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [1, 0], [0, 0], [0, -1], [1, -1], [0, 0], [1, 0], [.5, .5]], np.float32)
        for shift in (0, np.nextafter(np.float32(1e-6), np.float32(0)), np.nextafter(np.float32(1e-6), np.float32(1)), .5):
            sample = uv.copy()
            sample[4:8, 1] += shift
            for flags in (0, 1, 3):
                faces[2, 2] = flags
                a = self.compare(faces, vertices, sample)
                # Lexicographic ordering must also work near INT64_MAX.
                b = self.compare(faces, vertices + (np.iinfo(np.int64).max-10), sample)
                np.testing.assert_array_equal(a.labels, b.labels)
                np.testing.assert_array_equal(a.sides, b.sides)
        vertices[:] = 2
        self.compare(faces, vertices, uv)

    def test_dense_grid_and_independent_owned_outputs(self):
        side = 72
        y, x = np.mgrid[:side, :side]
        v = (y*(side+1)+x).ravel()
        vertices = np.column_stack((v, v+1, v+side+2, v+side+1)).ravel().astype(np.int64)
        faces = np.column_stack((np.arange(len(v))*4, np.full(len(v), 4), np.full(len(v), 3)))
        uv = np.column_stack((vertices % (side+1), vertices // (side+1))).astype(np.float32) / side
        result = self.compare(faces, vertices, uv)
        self.assertEqual(result.island_count, 1)
        self.assertEqual(len(result.borders), side*4)
        self.assertEqual(len(result.sides), 0)
        old = result.borders.copy()
        faces[:] = 0
        vertices[:] = 0
        uv[:] = np.nan
        np.testing.assert_array_equal(result.borders, old)

    def test_empty_and_invalid_snapshots(self):
        empty = build(self.lib, np.empty((0, 3), np.int64), np.empty(0, np.int64), np.empty((0, 2)), 1)
        self.assertEqual(empty.island_count, 0)
        self.assertFalse(len(empty.labels) or len(empty.borders) or len(empty.sides))
        faces = np.array([[0, 3, 3], [3, 3, 0]], np.int64)
        vertices, uv = np.arange(6, dtype=np.int64), np.zeros((6, 2), np.float32)
        for field, value in ((0, -1), (0, 7), (1, 7), (2, 2), (2, 4)):
            bad = faces.copy(); bad[0, field] = value
            with self.assertRaises((ValueError, RuntimeError)):
                build(self.lib, bad, vertices, uv)
        bad = faces.copy(); bad[1, 0] = 2
        with self.assertRaises(RuntimeError):
            build(self.lib, bad, vertices, uv)
        for value in (np.nan, np.inf, 1e9):
            bad = uv.copy(); bad[2, 1] = value
            with self.assertRaises(RuntimeError):
                build(self.lib, faces, vertices, bad)
        with self.assertRaises(ValueError):
            build(self.lib, faces, vertices.astype(float), uv)
        with self.assertRaises(ValueError):
            build(self.lib, faces, vertices, uv[:-1])
        binding = getattr(self.lib, '_op_binding', None)
        if binding is None:
            self.skipTest('compiled binding not supplied')
        for args in ((b'x', b'', b'', 1), (b'', b'x', b'', 1), (b'', b'', b'x', 1), (b'', bytes(8), b'', 1)):
            with self.assertRaises(ValueError):
                binding.uv_topology(*args)

    def test_island_bounds_sparse_empty_and_malformed_rasters(self):
        import ctypes
        rng = np.random.default_rng(1173)
        raw = ctypes.CDLL(os.environ['OP_TEST_NATIVE_LIBRARY'])
        for n in (1, 16, 127):
            raster = rng.integers(-1, 30, (n, n), dtype=np.int32)
            expected = {}
            for island in range(35):
                rows, columns = np.nonzero(raster == island)
                if len(rows):
                    expected[island] = (int(rows.min()), int(rows.max()), int(columns.min()), int(columns.max()))
            self.assertEqual(island_bounds(self.lib, raster, 35), expected)
            self.assertEqual(island_bounds(raw, raster, 35), expected)
        self.assertEqual(island_bounds(self.lib, np.full((16, 16), -1, np.int32), 0), {})
        for bad, count in ((np.zeros((2, 3), np.int32), 1), (np.full((2, 2), -2, np.int32), 1),
                           (np.full((2, 2), 1, np.int32), 1), (np.zeros((2, 2), float), 1)):
            with self.assertRaises(ValueError):
                island_bounds(self.lib, bad, count)
        binding = getattr(self.lib, '_op_binding', None)
        if binding is None:
            self.skipTest('compiled binding not supplied')
        with self.assertRaises(ValueError):
            binding.uv_bounds(b'x', 1, 1)
        with self.assertRaises(RuntimeError):
            binding.uv_bounds(np.full((2, 2), 7, np.int32), 2, 1)

    def test_host_extraction_and_ctypes_fallback(self):
        class Collection:
            def __init__(self, **fields):
                self.fields = fields
            def __len__(self):
                return len(next(iter(self.fields.values())))
            def foreach_get(self, key, output):
                output[:] = self.fields[key]
        mesh = SimpleNamespace(polygons=Collection(loop_start=[0, 3], loop_total=[3, 3], material_index=[0, 1]),
                               loops=Collection(vertex_index=[0, 1, 2, 1, 0, 3]))
        uv = np.array([[0, 0], [1, 0], [0, 1], [.8, .5], [.1, .5], [.8, 1]], np.float32)
        target = dict(mesh=mesh, uv=uv, slots={0, 1}, tri_polygons=np.array([0, 1], np.int32))
        actual = from_target(self.lib, target, 128, seams=True)
        self.assertEqual(len(actual.sides), 2)
        # Exercise the development C ABI adapter independently of the binding.
        import ctypes
        raw = ctypes.CDLL(os.environ['OP_TEST_NATIVE_LIBRARY'])
        other = from_target(raw, target, 128, seams=True)
        for field in ('labels', 'islands', 'borders', 'sides'):
            np.testing.assert_array_equal(getattr(actual, field), getattr(other, field))
        target['slots'] = {1}
        target['tri_polygons'] = np.array([1])
        other = from_target(self.lib, target, 128, seams=True)
        np.testing.assert_array_equal(other.labels, [-1, 1])
        self.assertEqual(len(other.sides), 0)
