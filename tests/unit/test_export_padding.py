"""Deterministic nearest-donor padding: the production transform against the brute-force
reference, channel semantics, coverage-aware resampling (quality remedy M2A, plan §6.3, 6.7)."""
import sys
import time
import unittest
import tracemalloc
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.export import padding


def rng(seed=0):
    return np.random.default_rng(seed)


def blobs(n, count, seed, radius=(2, 8)):
    """Random discs as islands (later discs overwrite earlier ones, like the rasteriser)."""
    covered = np.zeros((n, n), bool)
    island = np.full((n, n), -1, np.int32)
    r = rng(seed)
    yy, xx = np.mgrid[:n, :n]
    for identifier in range(count):
        cy, cx = r.integers(0, n, 2)
        rad = r.integers(radius[0], radius[1] + 1)
        inside = (yy - cy) ** 2 + (xx - cx) ** 2 <= rad * rad
        covered |= inside
        island[inside] = identifier
    return covered, island


def same(a, b):
    return np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


class PaddingTests(unittest.TestCase):

    def test_matches_reference_random_layouts(self):
        for seed in range(12):
            n = 48
            covered, island = blobs(n, 3 + seed % 4, seed)
            for limit in (1, 3, 7, 16):
                fast = padding.nearest_donors(covered, island, limit)
                slow = padding.reference_donors(covered, island, limit)
                assert same(fast, slow), (seed, limit)
            assert same(padding.nearest_donors(covered, island, "FILL"), padding.reference_donors(covered, island, "FILL")), seed


    def test_ties_diagonals_and_narrow_gaps(self):
        n = 32
        covered = np.zeros((n, n), bool)
        island = np.full((n, n), -1, np.int32)
        # Two islands facing each other across a 3-texel gap: the middle column is a tie.
        covered[8:24, 8:12] = True; island[8:24, 8:12] = 1
        covered[8:24, 15:19] = True; island[8:24, 15:19] = 0
        # A diagonal pixel far from both, and a lone texel of island 2 in a corner.
        covered[2, 29] = True; island[2, 29] = 2
        for limit in (1, 2, 4, 10, "FILL"):
            fast = padding.nearest_donors(covered, island, limit)
            slow = padding.reference_donors(covered, island, limit)
            assert same(fast, slow), limit
        donor, d2 = padding.nearest_donors(covered, island, 4)
        # The tie column (13) between islands 1 (cols 8-11) and 0 (cols 15-18): distance 2 to
        # both, so the lower island id (0) wins deterministically.
        middle = donor[10, 13]
        assert island.reshape(-1)[middle] == 0 and d2[10, 13] == 4
        assert island.reshape(-1)[donor[10, 12]] == 1 and island.reshape(-1)[donor[10, 14]] == 0


    def test_no_seeds_full_coverage_and_zero_limit(self):
        n = 16
        empty = np.zeros((n, n), bool)
        donor, d2 = padding.nearest_donors(empty, np.full((n, n), -1, np.int32), 4)
        assert (donor == -1).all() and (d2 == -1).all()
        full = np.ones((n, n), bool)
        donor, d2 = padding.nearest_donors(full, np.zeros((n, n), np.int32), 4)
        assert (donor == -1).all() and (d2 == 0).all()
        covered, island = blobs(n, 2, 3)
        donor, d2 = padding.nearest_donors(covered, island, 0)
        assert (donor == -1).all()


    def test_no_wrap_across_the_border(self):
        n = 24
        covered = np.zeros((n, n), bool)
        island = np.full((n, n), -1, np.int32)
        covered[:, 0:2] = True; island[:, 0:2] = 0            # a strip on the left border
        donor, d2 = padding.nearest_donors(covered, island, 6)
        assert (donor[:, -6:] == -1).all(), "the right border must not receive the left strip"
        assert (donor[:, 2:8] >= 0).all()


    def test_extend_copies_never_averages_or_touches_interior(self):
        n = 32
        covered = np.zeros((n, n), bool)
        island = np.full((n, n), -1, np.int32)
        covered[4:14, 4:14] = True; island[4:14, 4:14] = 0
        covered[18:28, 18:28] = True; island[18:28, 18:28] = 1
        values = np.zeros((n, n, 4), np.float32)
        values[4:14, 4:14] = (1, 0, 0, 1)
        values[18:28, 18:28] = (0, 0, 1, 1)
        values[~covered] = (.5, .5, .5, 0)      # garbage outside
        donor, d2 = padding.nearest_donors(covered, island, "FILL")
        out = padding.extend(values, donor)
        assert np.array_equal(out[covered], values[covered])
        outside = out[~covered]
        # Every gutter texel is a copy of an interior texel: exactly red or exactly blue.
        assert np.all((outside == (1, 0, 0, 1)).all(axis=1) | (outside == (0, 0, 1, 1)).all(axis=1))
        # Fronts meet halfway along the diagonal band; each side belongs to its nearer island.
        assert (out[15, 15] == (1, 0, 0, 1)).all() and (out[16, 16] == (0, 0, 1, 1)).all()


    def test_black_and_zero_values_are_valid(self):
        n = 16
        covered = np.zeros((n, n), bool)
        island = np.full((n, n), -1, np.int32)
        covered[4:12, 4:12] = True; island[4:12, 4:12] = 0
        values = np.ones((n, n, 4), np.float32)
        values[covered] = 0            # a fully black, zero-alpha, zero-scalar surface
        donor, _ = padding.nearest_donors(covered, island, 3)
        out = padding.extend(values, donor)
        assert (out[covered] == 0).all()
        assert (out[3, 3] == 0).all() and (out[12, 12] == 0).all()   # zeros were copied, not treated as empty


    def test_resample_down_excludes_empty_texels(self):
        n, n_out = 64, 16
        covered = np.zeros((n, n), bool)
        covered[8:40, 8:40] = True         # an island whose edges fall inside 4x4 blocks
        values = np.full((n, n, 4), .9, np.float32)
        values[~covered] = 0.0
        out, has_data = padding.resample(values, covered, n_out)
        inside = has_data
        assert np.allclose(out[inside], .9), "empty texels leaked into valid samples"
        assert not has_data[0, 0] and has_data[2, 2]
        plain = values.reshape(n_out, 4, n_out, 4, 4).mean(axis=(1, 3))
        assert plain[2, 2, 0] < .9, "the plain box filter would have darkened the edge"


    def test_resample_up_is_coverage_weighted(self):
        n, n_out = 8, 32
        covered = np.zeros((n, n), bool)
        covered[2:6, 2:6] = True
        values = np.zeros((n, n, 4), np.float32)
        values[covered] = (.25, .5, .75, 1)
        out, has_data = padding.resample(values, covered, n_out)
        assert np.allclose(out[has_data], (.25, .5, .75, 1)), "edges must not fade into empty texels"
        assert has_data.sum() > 16 * 16 and not has_data[0, 0]


    def test_resample_normal_renormalises(self):
        n, n_out = 32, 8
        covered = np.ones((n, n), bool)
        values = np.zeros((n, n, 4), np.float32)
        values[..., :3] = np.array((.5, .5, 1.0))      # flat
        values[:16, :, 0] = 1.0                        # tilted half
        values[..., 3] = 1
        out, _ = padding.resample(values, covered, n_out, vector=True)
        xyz = out[..., :3] * 2 - 1
        assert np.allclose(np.linalg.norm(xyz, axis=-1), 1, atol=1e-5)


    def test_fill_beyond_band_is_deterministic_and_complete(self):
        n = 256
        covered = np.zeros((n, n), bool)
        island = np.full((n, n), -1, np.int32)
        covered[10:20, 10:20] = True; island[10:20, 10:20] = 0
        covered[230:250, 230:250] = True; island[230:250, 230:250] = 1
        donor, d2 = padding.nearest_donors(covered, island, "FILL")
        assert (donor[~covered] >= 0).all(), "fill mode leaves no empty texel"
        again, _ = padding.nearest_donors(covered, island, "FILL")
        assert np.array_equal(donor, again)
        owner = island.reshape(-1)[donor[~covered]]
        assert set(np.unique(owner)) == {0, 1}
        # Within the exact band the reference agrees.
        reference, _ = padding.reference_donors(covered, island, padding.FILL_BAND)
        near = reference >= 0
        assert np.array_equal(donor[near], reference[near])


    # ---- fill completeness (hardening H3) --------------------------------------------------------
    def test_fill_sparse_corner_islands_resolve_every_texel(self):
        for n in (128, 256, 512, 1024):
            for corner in ((0, 0), (0, n - 8), (n - 8, 0), (n - 8, n - 8)):
                covered = np.zeros((n, n), bool)
                covered[corner[0]:corner[0] + 8, corner[1]:corner[1] + 8] = True
                island = np.where(covered, 0, -1).astype(np.int32)
                donor, d2 = padding.nearest_donors(covered, island, "FILL")
                self.assertEqual(padding.unresolved_texels(covered, donor), 0, (n, corner))
                self.assertTrue(padding.donors_valid(covered, donor), (n, corner))
                self.assertTrue((donor[covered] == -1).all())

    def test_fill_hierarchy_edge_cases(self):
        n = 512
        layouts = {}
        one = np.zeros((n, n), bool); one[300, 77] = True; layouts["one texel"] = one
        edge = np.zeros((n, n), bool); edge[0, 200:210] = True; layouts["isolated edge seed"] = edge
        centre = np.zeros((n, n), bool); centre[250:262, 250:262] = True; layouts["central island"] = centre
        diagonal = np.zeros((n, n), bool); idx = np.arange(0, n, 3); diagonal[idx, idx] = True; layouts["thin diagonal"] = diagonal
        separated = np.zeros((n, n), bool); separated[10:20, 10:20] = True; separated[480:500, 470:490] = True; layouts["separated islands"] = separated
        occupied_cell = np.zeros((n, n), bool); occupied_cell[100, 100] = True; occupied_cell[5:8, 5:8] = True
        layouts["occupied coarse cell, mostly empty children"] = occupied_cell
        for label, covered in layouts.items():
            island = np.zeros((n, n), np.int32)
            island[~covered] = -1
            if label == "separated islands":
                island[480:500, 470:490] = 1
            donor, d2 = padding.nearest_donors(covered, island, "FILL")
            self.assertEqual(padding.unresolved_texels(covered, donor), 0, label)
            self.assertTrue(padding.donors_valid(covered, donor), label)
            again, _ = padding.nearest_donors(covered, island, "FILL")
            self.assertTrue(np.array_equal(donor, again), label)

    def test_fill_all_valid_none_valid_and_zero_distance(self):
        n = 64
        full = np.ones((n, n), bool)
        donor, d2 = padding.nearest_donors(full, np.zeros((n, n), np.int32), "FILL")
        self.assertEqual(padding.unresolved_texels(full, donor), 0)
        self.assertTrue((donor == -1).all() and (d2 == 0).all())
        empty = np.zeros((n, n), bool)
        donor, d2 = padding.nearest_donors(empty, np.full((n, n), -1, np.int32), "FILL")
        self.assertEqual(padding.unresolved_texels(empty, donor), n * n, "no valid texel: nothing can be filled")
        covered = np.zeros((n, n), bool); covered[10:20, 10:20] = True
        donor, _ = padding.nearest_donors(covered, np.where(covered, 0, -1).astype(np.int32), 0)
        self.assertTrue((donor == -1).all(), "a zero finite distance pads nothing")

    def test_fill_distant_donors_within_the_declared_error_bound(self):
        """Beyond the exact band a donor is the representative of the nearest coarse cell: no
        farther than 2·√2·c from the true nearest valid texel, c the level's cell size."""
        n = 512
        covered = np.zeros((n, n), bool); covered[:8, :8] = True; covered[500:508, 300:306] = True
        island = np.where(covered, 0, -1).astype(np.int32)
        island[500:508, 300:306] = 1
        donor, _ = padding.nearest_donors(covered, island, "FILL")
        reference, reference_d2 = padding.reference_donors(covered, island, "FILL")
        ys, xs = np.nonzero(~covered)
        chosen = donor[ys, xs]
        dy, dx = ys - chosen // n, xs - chosen % n
        chosen_distance = np.sqrt((dy * dy + dx * dx).astype(np.float64))
        true_distance = np.sqrt(reference_d2[ys, xs].astype(np.float64))
        excess = chosen_distance - true_distance
        # Within the exact band the donors agree with the oracle exactly.
        near = true_distance <= padding.FILL_BAND
        self.assertTrue(np.array_equal(donor[ys[near], xs[near]], reference[ys[near], xs[near]]))
        # Beyond it the excess stays within the bound of the coarsest level that can have resolved
        # the texel (cells of 8, 64 or 512 texels): 2·√2·c.
        bound = 2 * np.sqrt(2.0) * 64      # the 512-square needs at most the 64-texel level
        self.assertLessEqual(float(excess[~near].max()), bound + 1e-6, float(excess[~near].max()))
        self.assertLess(float(np.median(excess[~near])), bound / 4)

    def test_fill_hierarchy_bounded_time_at_2k(self):
        n = 2048
        covered = np.zeros((n, n), bool); covered[:16, :16] = True
        island = np.where(covered, 0, -1).astype(np.int32)
        started = time.perf_counter()
        donor, _ = padding.nearest_donors(covered, island, "FILL")
        seconds = time.perf_counter() - started
        self.assertEqual(padding.unresolved_texels(covered, donor), 0)
        self.assertLess(seconds, 30, seconds)
        print(f"fill 2K sparse: {seconds:.1f}s")

    def test_scratch_memory_is_bounded_at_4k(self):
        n = 4096
        covered, island = blobs(n, 24, 5, radius=(64, 300))
        tracemalloc.start()
        started = time.perf_counter()
        donor, d2 = padding.nearest_donors(covered, island, 32)
        seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        scratch = peak - covered.nbytes - island.nbytes
        assert scratch <= 384 * 1024 * 1024, f"scratch {scratch / 2**20:.0f} MiB"
        assert (donor[covered] == -1).all()
        print(f"padding 4K/32px: {seconds:.1f}s, scratch {scratch / 2**20:.0f} MiB")




if __name__ == "__main__":
    unittest.main(verbosity=2)
