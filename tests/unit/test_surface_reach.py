"""Surface-local reach (0.13 hardening, H2 §6.4) on hand-built meshes: which triangles a
stamp may paint. Each case states the geometry, the anchor and the accepted/excluded pairs."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.painting import surface_reach as reach  # noqa: E402
from open_painter.painting.stroke_raster import kernel  # noqa: E402
from open_painter.geometry.triangle_bounds import TriangleBounds  # noqa: E402


def grid(nx, ny, size=1.0, z=0.0, origin=(0.0, 0.0), flip=False, vertex_offset=0, polygon_offset=0):
    """A flat quad grid on the plane z (two triangles per quad, upward winding unless ``flip``)."""
    xs = np.linspace(origin[0], origin[0] + size, nx + 1)
    ys = np.linspace(origin[1], origin[1] + size, ny + 1)
    vertices = np.array([(x, y, z) for y in ys for x in xs], np.float64)
    triangles, polygons = [], []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i + vertex_offset
            b, c, d = a + 1, a + nx + 2, a + nx + 1
            quad = [(a, b, c), (a, c, d)] if not flip else [(a, c, b), (a, d, c)]
            triangles.extend(quad)
            polygons.extend([polygon_offset + j * nx + i] * 2)
    return vertices, np.array(triangles, np.int64), np.array(polygons, np.int64)


def target_of(parts, mirrored=False):
    """A snapshot-like target from (vertices, triangles, polygons) parts, concatenated."""
    positions, tri, poly, v_off = [], [], [], 0
    for vertices, triangles, polygons in parts:
        positions.append(vertices)
        tri.append(triangles + v_off)
        poly.append(polygons)
        v_off += len(vertices)
    positions = np.concatenate(positions)
    tri, poly = np.concatenate(tri), np.concatenate(poly)
    return {"positions": positions, "all_tri_vertices": tri, "all_tri_polygons": poly, "mirrored": mirrored}


def sample(centre, normal=(0, 0, 1), radius=.3):
    row = np.zeros(13, np.float32)
    row[:3], row[3:6], row[6], row[7] = centre, normal, 1.0, radius
    return row


def anchor(point, polygon):
    return np.array([*point, polygon], np.float32)


def accepted(target, samples, anchors, planar=None, depth_limit=1.0):
    sheets = reach.Sheets(target, np.asarray(samples, np.float32),
                          np.asarray(anchors, np.float32) if anchors is not None else None, planar, depth_limit)
    count = len(target["all_tri_vertices"])
    return sheets.mask(np.arange(count), 0, len(samples))


class IndexedCandidates(unittest.TestCase):
    def test_sorted_material_subsets_keep_chunk_boundaries_and_widened_anchor(self):
        target = target_of([grid(48, 48)])
        samples = np.repeat(sample((.5, .5, 0), radius=.04)[None], 259, axis=0)
        anchors = np.repeat(anchor((.5, .5, 0), 24 * 48 + 24)[None], len(samples), axis=0)
        sheet = reach.Sheets(target, samples, anchors)
        for triangles in (np.arange(4608), np.arange(4608)[::3],
                          np.array([-8, -1, 4608, 100000]), np.zeros(0, np.int64)):
            for start, stop in ((0, 1), (3, 7), (254, 259), (0, 0)):
                np.testing.assert_array_equal(sheet.mask_sorted(triangles, start, stop),
                                              sheet.mask(triangles, start, stop))
        # The retained hit can widen the walk beyond the current brush box.
        # Sparse lookup must include every candidate, not just the UV window.
        widened = reach.Sheets(target, sample((.1, .1, 0), radius=.04)[None],
                               anchor((.9, .9, 0), 43 * 48 + 43)[None])
        full = np.arange(4608)
        actual = widened.mask_sorted(full, 0, 1)
        np.testing.assert_array_equal(actual, widened.mask(full, 0, 1))
        self.assertTrue(actual[43 * 48 * 2 + 43 * 2].any())
        broad = reach.Sheets(target, sample((.5, .5, 0), radius=2.)[None], anchors[:1])
        subset = [17, 60]
        expected = broad.mask(subset, 0, 1)
        with patch.object(broad, 'mask', wraps=broad.mask) as generic:
            np.testing.assert_array_equal(broad.mask_sorted(subset, 0, 1), expected)
            self.assertEqual(generic.call_count, 1)

    def test_dense_disconnected_sheets_and_stale_anchors_match_full_scans(self):
        entries = [target_of([grid(48, 48, z=z)]) for z in (0., .02)]
        samples = np.asarray([sample((.25, .25, 0), radius=.025),
                              sample((.62, .57, 0), radius=.12),
                              sample((.91, .91, .02), radius=.4),
                              sample((.5, .5, .01), radius=.15),
                              sample((4, 4, 4), radius=.02)], np.float32)
        anchors = np.asarray([anchor(row[:3], int(min(row[1], .99) * 48) * 48 + int(min(row[0], .99) * 48))
                              for row in samples], np.float32)

        def direct(index, low, high):
            return np.flatnonzero(np.all(index.minimum <= high, axis=1) & np.all(index.maximum >= low, axis=1))

        for planar in (None, (0, .1, 1)):
            for hit in (anchors, None, anchors + np.array([0, 0, 0, 100000], np.float32)):
                current = reach.for_targets(entries, samples, hit, planar=planar)
                actual = [sheets.mask(np.arange(len(entry['all_tri_vertices'])), 0, len(samples))
                          for entry, sheets in zip(entries, current)]
                self.assertTrue(all(entry['_reach']['bounds_index'].extra_bytes for entry in entries))
                with patch.object(TriangleBounds, 'query', direct):
                    previous = reach.for_targets(entries, samples, hit, planar=planar)
                    expected = [sheets.mask(np.arange(len(entry['all_tri_vertices'])), 0, len(samples))
                                for entry, sheets in zip(entries, previous)]
                for a, b in zip(actual, expected):
                    np.testing.assert_array_equal(a, b)
                if hit is anchors:
                    self.assertFalse(actual[1][:, 0].any(), 'the rear object must remain excluded')
                    self.assertFalse(actual[0][:, 2].any(), 'the front object must remain excluded')


class Adjacency(unittest.TestCase):
    def test_edge_neighbours_of_a_grid(self):
        vertices, triangles, polygons = grid(2, 2)
        indptr, indices = reach.adjacency(triangles)
        self.assertEqual(len(indptr), len(triangles) + 1)
        # Triangle 0 = (a, b, c) of quad 0 shares edge (a, c) with triangle 1 and (b, c) with quad 1's second triangle.
        neighbours = set(indices[indptr[0]:indptr[1]].tolist())
        self.assertIn(1, neighbours)
        for tri in range(len(triangles)):
            for other in indices[indptr[tri]:indptr[tri + 1]]:
                self.assertEqual(len(set(triangles[tri]) & set(triangles[other])), 2, (tri, other))
        # Symmetric.
        for tri in range(len(triangles)):
            for other in indices[indptr[tri]:indptr[tri + 1]]:
                self.assertIn(tri, indices[indptr[other]:indptr[other + 1]])

    def test_disconnected_pieces_have_no_edges_between_them(self):
        a = grid(2, 2)
        b = grid(2, 2, z=.01, vertex_offset=0)
        target = target_of([a, b])
        indptr, indices = reach.adjacency(target["all_tri_vertices"])
        first = len(a[1])
        for tri in range(first):
            self.assertTrue(all(other < first for other in indices[indptr[tri]:indptr[tri + 1]]))


class PointTriangle(unittest.TestCase):
    def test_distances(self):
        corners = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]] * 5, np.float64)
        points = np.array([[.2, .2, .5], [2, 0, 0], [-1, -1, 0], [.5, .5, 0], [0, 2, 1]], np.float64)
        d = reach.point_triangle_distance(points, corners)
        np.testing.assert_allclose(d, [.5, 1.0, np.sqrt(2), 0.0, np.sqrt(2)], atol=1e-9)


class Reach(unittest.TestCase):
    def test_same_facing_sheet_behind_the_hit_sheet_is_excluded(self):
        # A front sheet at z=.008 over a larger rear sheet at z=0: an anchored stamp on the front
        # reaches only the front, whatever the depth band says.
        rear = grid(4, 4, size=2.0, origin=(-1, -1))
        front = grid(2, 2, size=1.0, origin=(-.5, -.5), z=.008, polygon_offset=16)
        target = target_of([rear, front])
        rear_count = len(rear[1])
        s = sample((0, 0, .008), radius=.3)
        a = anchor((0.0, 0.0, .008), polygon=16 + 0)   # quad (0,0) of the front grid holds the origin corner
        # The front quad containing (0, 0) is the last quad (origin -.5..0 is quad 0; (0,0) lies on the corner shared by all 4)
        got = accepted(target, [s], [anchor((-.25, -.25, .008), 16)])
        self.assertTrue(got[rear_count:].any(), "the front sheet must be reached")
        self.assertFalse(got[:rear_count].any(), "the rear sheet must not be reached")
        # A DEPTH kernel would have painted the rear sheet within the band: the eligibility is what excludes it.
        positions = np.array([[0, 0, 0], [0, 0, .008]], np.float32)
        normals = np.array([[0, 0, 1], [0, 0, 1]], np.float32)
        plain = kernel(positions, normals, np.asarray([s]), depth_limit=.5)
        self.assertTrue((plain > 0).all())

    def test_two_nearby_same_facing_objects(self):
        # Two grids side by side 2 mm apart (a gap, no shared vertices): the stamp on the left
        # never crosses onto the right one though its disc covers it.
        left = grid(2, 2, size=1.0, origin=(-1.0, 0))
        right = grid(2, 2, size=1.0, origin=(.002, 0), polygon_offset=4)
        target = target_of([left, right])
        s = sample((-.05, .5, 0), radius=.4)
        got = accepted(target, [s], [anchor((-.05, .5, 0), 1)])   # quad 1 of the left grid: x in [-.5, 0]
        self.assertTrue(got[:len(left[1])].any())
        self.assertFalse(got[len(left[1]):].any(), "the neighbouring object must stay clean")

    def test_recess_wall_stops_the_walk(self):
        # A step: an upper plateau connected through a vertical wall to a lower plateau facing
        # the same way (an occluded recess seen from above when the lower one lies under a lip,
        # the same topology). The wall turns away from the stamp axis: the walk stops there.
        upper = grid(2, 1, size=1.0, origin=(-1.0, 0), z=.2)
        # wall: x = 0, from z=.2 down to z=0, y in [0, 1]
        wall_vertices = np.array([(0, 0, .2), (0, 1, .2), (0, 1, 0), (0, 0, 0)], np.float64)
        wall_triangles = np.array([(0, 1, 2), (0, 2, 3)], np.int64)
        lower = grid(2, 1, size=1.0, origin=(0, 0), z=0.0)
        # share vertices by welding: build a combined target by hand
        parts = [upper, (wall_vertices, wall_triangles, np.array([100, 100])), lower]
        target = target_of(parts)
        positions = target["positions"]
        # weld coincident vertices so the pieces are edge-connected
        rounded = np.round(positions, 6)
        _, first_index, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
        tri = inverse[target["all_tri_vertices"]]
        target = {"positions": rounded[first_index], "all_tri_vertices": tri, "all_tri_polygons": target["all_tri_polygons"]}
        upper_tris = np.arange(len(upper[1]))
        lower_tris = np.arange(len(upper[1]) + 2, len(upper[1]) + 2 + len(lower[1]))
        s = sample((-.1, .5, .2), radius=.5)
        got = accepted(target, [s], [anchor((-.1, .5, .2), 1)])
        self.assertTrue(got[upper_tris].any())
        self.assertFalse(got[lower_tris].any(), "the plateau below the wall must not be reached")
        # A gentle ramp instead of a wall (a visible slope) is walkable: the lower plateau is reached.
        ramp_vertices = np.array([(-.3, 0, .2), (-.3, 1, .2), (0, 1, 0), (0, 0, 0)], np.float64)
        upper2 = grid(1, 1, size=.7, origin=(-1.0, 0), z=.2)
        upper2 = (np.array([(-1, 0, .2), (-.3, 0, .2), (-.3, 1, .2), (-1, 1, .2)], np.float64),
                  np.array([(0, 1, 2), (0, 2, 3)], np.int64), np.array([0, 0]))
        parts = [upper2, (ramp_vertices, np.array([(0, 2, 1), (0, 3, 2)], np.int64), np.array([100, 100])), lower]   # wound to face up
        target = target_of(parts)
        rounded = np.round(target["positions"], 6)
        _, first_index, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
        target = {"positions": rounded[first_index], "all_tri_vertices": inverse[target["all_tri_vertices"]],
                  "all_tri_polygons": target["all_tri_polygons"]}
        got = accepted(target, [sample((-.4, .5, .2), radius=.6)], [anchor((-.4, .5, .2), 0)])
        self.assertTrue(got[4:].any(), "a visible slope connects the plateaus")

    def test_back_sheet_of_a_thin_shell_is_not_reached(self):
        # A closed thin box: top faces up, bottom faces down, rims perpendicular. A stamp on the
        # top reaches the top only (the rim turns away, the bottom faces away).
        top = grid(2, 2, size=1.0, z=.01)
        bottom = grid(2, 2, size=1.0, z=0.0, flip=True, polygon_offset=4)
        target = target_of([top, bottom])
        got = accepted(target, [sample((.5, .5, .01), radius=.8)], [anchor((.5, .5, .01), 3)])
        self.assertTrue(got[:8].any() and not got[8:].any())

    def test_planar_axis_replaces_the_sample_normal(self):
        # Planar projection along -Z over a vertical wall: the wall does not face the axis, so a
        # stamp anchored on the floor never climbs it; along the wall's own normal it would.
        floor = grid(2, 1, size=1.0, origin=(0, 0))
        wall = (np.array([(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)], np.float64), np.array([(0, 1, 2), (0, 2, 3)], np.int64), np.array([9, 9]))
        target = target_of([floor, wall])
        rounded = np.round(target["positions"], 6)
        _, first_index, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
        target = {"positions": rounded[first_index], "all_tri_vertices": inverse[target["all_tri_vertices"]],
                  "all_tri_polygons": target["all_tri_polygons"]}
        s = sample((.9, .5, 0), radius=.5)
        planar = accepted(target, [s], [anchor((.9, .5, 0), 1)], planar=(0, 0, 1))
        self.assertTrue(planar[:4].any() and not planar[4:].any())
        surface = accepted(target, [sample((.9, .5, 0), normal=(1, 0, 0), radius=.5)], [anchor((.9, .5, 0), 1)])
        self.assertTrue(surface[4:].any(), "an axis along the wall's normal reaches it")

    def test_unresolved_anchor_falls_back_to_the_nearest_surface_within_the_band(self):
        # The anchor's polygon no longer holds it (renumbered): the nearest facing triangle
        # within the band seeds, not the sheet 8 mm behind.
        rear = grid(2, 2, size=1.0)
        front = grid(2, 2, size=1.0, z=.008, polygon_offset=4)
        target = target_of([rear, front])
        s = sample((.5, .5, .008), radius=.3)
        got = accepted(target, [s], [anchor((.5, .5, .0081), 0)], depth_limit=.5)   # polygon 0 is a rear quad far from the point? no: it holds (.5,.5,0) at 8 mm: outside tolerance
        self.assertTrue(got[8:].any(), "the front sheet (nearest) must seed")
        self.assertFalse(got[:8].any())
        # No anchors at all: each sample seeds from the surface nearest to its centre.
        got = accepted(target, [s], None)
        self.assertTrue(got[8:].any() and not got[:8].any())
        # Nothing within the band: nothing is reached.
        far = accepted(target, [sample((.5, .5, 5.0), radius=.3)], [anchor((.5, .5, 5.0), -1)], depth_limit=.5)
        self.assertFalse(far.any())

    def test_proximity_fallback_never_seeds_a_target_the_ray_did_not_hit(self):
        # Two objects (two targets) 2 mm apart: the anchor resolves on A, so B — within the band
        # of the same sample — gets no seed; an anchor no target holds falls back on the nearest.
        a = target_of([grid(2, 2, size=1.0, origin=(-1.0, 0))])
        b = target_of([grid(2, 2, size=1.0, origin=(.002, 0))])
        s = sample((-.05, .5, 0), radius=.4)
        sheets = reach.for_targets([a, b], np.asarray([s], np.float32), np.asarray([anchor((-.05, .5, 0), 1)]), None, 1.0)
        self.assertTrue(sheets[0].mask(np.arange(8), 0, 1).any())
        self.assertFalse(sheets[1].mask(np.arange(8), 0, 1).any(), "B was seeded by proximity although A holds the anchor")
        # The anchor's polygon renumbered (no target holds it): proximity seeds the nearest target
        # only — A, on which the anchor lies; B is 5 cm away, well within the band, and stays clean.
        sheets = reach.for_targets([a, b], np.asarray([s], np.float32), np.asarray([anchor((-.05, .5, 0), 0)]), None, 1.0)
        self.assertTrue(sheets[0].mask(np.arange(8), 0, 1).any())
        self.assertFalse(sheets[1].mask(np.arange(8), 0, 1).any(), "only the nearest target seeds a lost anchor")

    def test_chunks_and_mask_windows(self):
        vertices, triangles, polygons = grid(3, 3, size=3.0)
        target = target_of([(vertices, triangles, polygons)])
        count = 300   # more than one chunk
        samples = np.array([sample((1.5, 1.5, 0), radius=.2)] * count, np.float32)
        anchors = np.array([anchor((1.5, 1.5, 0), 4)] * count, np.float32)
        sheets = reach.Sheets(target, samples, anchors)
        window = sheets.mask(np.arange(len(triangles)), 250, 270)
        self.assertEqual(window.shape, (len(triangles), 20))
        self.assertTrue(window.any())
        # A triangle that is not a candidate answers False without error.
        outside = sheets.mask(np.array([0]), 0, 1)
        self.assertEqual(outside.shape, (1, 1))
        self.assertTrue(sheets.seeded_samples().all())

    def test_kernel_honours_the_eligibility(self):
        # The same disc on two coincident sheets: only the eligible one receives alpha.
        s = sample((0, 0, 0), radius=.5)
        positions = np.array([[.1, 0, 0], [.1, 0, .002]], np.float32)
        normals = np.array([[0, 0, 1], [0, 0, 1]], np.float32)
        plain = kernel(positions, normals, np.asarray([s]))
        self.assertTrue((plain > 0).all())
        gated = kernel(positions, normals, np.asarray([s]), eligible=lambda idx, a, b: np.array([[True], [False]])[idx])
        self.assertGreater(gated[0], 0)
        self.assertEqual(gated[1], 0)

    def test_pack_roundtrip(self):
        anchors = np.random.default_rng(1).normal(size=(7, 4)).astype(np.float32)
        anchors[:, 3] = np.arange(7)
        self.assertTrue(np.array_equal(reach.unpack_anchors(reach.pack_anchors(anchors)), anchors))
        self.assertEqual(reach.unpack_anchors("").shape, (0, 4))


if __name__ == "__main__":
    unittest.main()
