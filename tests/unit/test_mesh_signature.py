"""``dependencies.mesh_signature`` equals the digest of its RNA collections' own reads.

The signature reads positions, UVs, smooth shading and material indices from the mesh's
attributes and derives each face's corner count; it hashes with the fastest SHA-256 at hand.
These tests hash the same meshes the way it used to (each RNA collection's ``foreach_get``,
hashlib) and require the same digest: flat and smooth shading, material indices, several UV
maps (the derived paint UV skipped), custom normals, n-gons, loose and empty geometry. Runs
in Blender (``run_tests.py`` discovers it there); skipped without bpy.
"""
import hashlib
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


def reference(mesh):
    """The signature as RNA reads it, element by element, hashed with hashlib."""
    h = hashlib.sha256()
    for collection, prop, width, dtype in (
        (mesh.vertices, 'co', 3, np.float32), (mesh.vertices, 'normal', 3, np.float32),
        (mesh.loops, 'vertex_index', 1, np.int32),
        (mesh.polygons, 'loop_start', 1, np.int32), (mesh.polygons, 'loop_total', 1, np.int32),
        (mesh.polygons, 'material_index', 1, np.int32), (mesh.polygons, 'use_smooth', 1, np.bool_),
        (mesh.corner_normals, 'vector', 3, np.float32),
    ):
        values = np.empty(len(collection) * width, dtype=dtype)
        collection.foreach_get(prop, values)
        h.update(str((prop, len(collection))).encode()); h.update(values.tobytes())
    for uv in mesh.uv_layers:
        if uv.name == mesh.get('open_painter_udim_paint_uv', ''):
            continue
        original_uv = mesh.get('open_painter_udim_original_uv')
        active = uv.name == original_uv if original_uv is not None else uv == mesh.uv_layers.active
        h.update(str((uv.name, uv.active_render, active)).encode())
        values = np.empty(len(uv.data) * 2, np.float32)
        uv.data.foreach_get('uv', values); h.update(values.tobytes())
    return h.hexdigest()


@unittest.skipIf(bpy is None, 'needs Blender')
class MeshSignatureTests(unittest.TestCase):
    def setUp(self):
        self.meshes = []

    def tearDown(self):
        for mesh in self.meshes:
            bpy.data.meshes.remove(mesh)

    def mesh(self, vertices, faces, name='Signature'):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        self.meshes.append(mesh)
        return mesh

    def grid(self, n=9):
        vertices = [(x * .3, y * .25, ((x * 7 + y * 3) % 5) * .05) for y in range(n) for x in range(n)]
        faces = [(y * n + x, y * n + x + 1, (y + 1) * n + x + 1, (y + 1) * n + x)
                 for y in range(n - 1) for x in range(n - 1)]
        mesh = self.mesh(vertices, faces)
        uv = mesh.uv_layers.new(name='UVMap')
        values = np.random.default_rng(3).random(len(uv.data) * 2, dtype=np.float32)
        uv.data.foreach_set('uv', values)
        return mesh

    def check(self, mesh):
        from open_painter.evaluation import dependencies
        dependencies._mesh_fingerprints.pop(mesh.as_pointer(), None)
        self.assertEqual(dependencies.mesh_signature(mesh), reference(mesh))

    def test_flat_faces(self):
        mesh = self.grid()
        mesh.shade_flat()
        self.assertIn('sharp_face', mesh.attributes)
        self.check(mesh)

    def test_smooth_faces_without_the_attribute(self):
        mesh = self.grid()
        mesh.shade_smooth()
        self.assertNotIn('sharp_face', mesh.attributes)
        self.check(mesh)

    def test_some_faces_sharp(self):
        mesh = self.grid()
        mesh.shade_smooth()
        for index in range(0, len(mesh.polygons), 3):
            mesh.polygons[index].use_smooth = False
        self.check(mesh)

    def test_material_indices(self):
        mesh = self.grid()
        self.assertNotIn('material_index', mesh.attributes)
        self.check(mesh)
        for index, polygon in enumerate(mesh.polygons):
            polygon.material_index = index % 3
        self.assertIn('material_index', mesh.attributes)
        self.check(mesh)

    def test_uv_maps_and_the_paint_uv(self):
        mesh = self.grid()
        second = mesh.uv_layers.new(name='Second')
        second.data.foreach_set('uv', np.linspace(0, 1, len(second.data) * 2, dtype=np.float32))
        paint = mesh.uv_layers.new(name='Paint UV')
        mesh['open_painter_udim_paint_uv'] = 'Paint UV'
        mesh.uv_layers.active = second
        self.check(mesh)
        mesh['open_painter_udim_original_uv'] = 'UVMap'
        self.check(mesh)
        self.assertTrue(paint.name)

    def test_custom_normals(self):
        mesh = self.grid()
        normals = [(0.0, (i % 7) * .1, 1.0) for i in range(len(mesh.loops))]
        mesh.normals_split_custom_set(normals)
        self.assertTrue(mesh.has_custom_normals)
        self.check(mesh)

    def test_ngons_triangles_and_loose_vertices(self):
        vertices = [(0, 0, 0), (1, 0, 0), (1.5, .8, 0), (1, 1.6, 0), (0, 1.6, 0), (-.5, .8, 0), (3, 3, 3), (4, 4, 4)]
        mesh = self.mesh(vertices, [(0, 1, 2, 3, 4, 5), (1, 2, 6)])
        self.check(mesh)

    def test_no_faces(self):
        mesh = self.mesh([(0, 0, 0), (1, 1, 1)], [])
        self.check(mesh)
        empty = self.mesh([], [])
        self.check(empty)

    def test_the_digest_is_cached_until_the_mesh_changes(self):
        from open_painter.evaluation import dependencies
        mesh = self.grid()
        first = dependencies.mesh_signature(mesh)
        self.assertEqual(dependencies.mesh_signature(mesh), first)
        mesh.vertices[0].co.x += 1.0
        dependencies._mesh_fingerprints.pop(mesh.as_pointer(), None)
        self.assertNotEqual(dependencies.mesh_signature(mesh), first)
        self.assertEqual(dependencies.mesh_signature(mesh), reference(mesh))

    def test_an_undo_keeps_an_unchanged_mesh_digest_without_hashing(self):
        from unittest.mock import patch
        from open_painter.evaluation import dependencies
        from open_painter.utils import digest
        mesh = self.grid()
        first = dependencies.mesh_signature(mesh)
        dependencies.clear(undo=True)   # what undo_post runs
        with patch.object(digest, 'sha256', side_effect=AssertionError('hashed again')):
            self.assertEqual(dependencies.mesh_signature(mesh), first)
        # The same address with other content (a memfile undo restoring an edited mesh).
        mesh.vertices[3].co.z += .5
        dependencies.clear(undo=True)
        changed = dependencies.mesh_signature(mesh)
        self.assertNotEqual(changed, first)
        self.assertEqual(changed, reference(mesh))
        uv = mesh.uv_layers[0]
        uv.data[0].uv = (uv.data[0].uv[0] + .25, uv.data[0].uv[1])
        dependencies.clear(undo=True)
        self.assertEqual(dependencies.mesh_signature(mesh), reference(mesh))

    def test_integer_arrays_are_copied_from_blender_memory(self):
        from open_painter.evaluation import dependencies
        mesh = self.grid()
        for index, polygon in enumerate(mesh.polygons):
            polygon.material_index = index % 4
        for collection, count, read, rna in (
                (mesh.attributes['.corner_vert'].data, len(mesh.loops), lambda item: item.value,
                 lambda values: mesh.loops.foreach_get('vertex_index', values)),
                (mesh.polygons, len(mesh.polygons), lambda item: item.loop_start,
                 lambda values: mesh.polygons.foreach_get('loop_start', values)),
                (mesh.attributes['material_index'].data, len(mesh.polygons), lambda item: item.value,
                 lambda values: mesh.polygons.foreach_get('material_index', values))):
            direct = dependencies._direct(collection, count, np.int32, 1, read)
            self.assertIsNotNone(direct)
            expected = np.empty(count, np.int32)
            rna(expected)
            np.testing.assert_array_equal(direct, expected)
        # Elements that are not one array, or whose values RNA reads otherwise, are refused.
        self.assertIsNone(dependencies._direct(mesh.polygons, len(mesh.polygons), np.int32, 1,
                                               lambda item: item.loop_start + 1))
        self.assertIsNone(dependencies._direct(mesh.vertices, 0, np.float32, 3, lambda item: tuple(item.co)))

    def test_a_freed_mesh_address_does_not_answer_for_a_new_mesh(self):
        from open_painter.evaluation import dependencies
        mesh = self.grid()
        # What a mesh freed at this address left: its epoch matches, its session id does not.
        dependencies._mesh_fingerprints[mesh.as_pointer()] = (dependencies._mesh_epochs.get(mesh.as_pointer(), 0),
                                                              'stale', mesh.session_uid + 1)
        self.assertEqual(dependencies.mesh_signature(mesh), reference(mesh))

    def test_a_mesh_past_the_bound_is_hashed_again(self):
        from unittest.mock import patch
        from open_painter.evaluation import dependencies
        mesh = self.grid()
        with patch.object(dependencies, 'CONTENT_BYTES', 64):
            first = dependencies.mesh_signature(mesh)
            self.assertNotIn(mesh.session_uid, dependencies._mesh_contents)
            dependencies.clear(undo=True)
            self.assertEqual(dependencies.mesh_signature(mesh), first)
        self.assertEqual(first, reference(mesh))


if __name__ == '__main__':
    unittest.main()
