"""Bitwise-compatible CPU optimizations against the frozen pre-change sources.

These tests run with NumPy Python. Only Blender's image lookup is replaced by a
synthetic map lookup when loading the otherwise unchanged generator modules.
"""

import ast
import gc
import hashlib
import importlib.util
import itertools
import json
import sys
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from open_painter.texture_engine import procedurals


def load_reference():
    name = "open_painter.texture_engine._frozen_procedurals"
    spec = importlib.util.spec_from_file_location(name, HERE / "reference_procedurals.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_generator(path, name, procedural):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "evaluation.mask_eval"]
    if len(imports) != 1 or [alias.name for alias in imports[0].names] != ["map_pixels"]:
        raise AssertionError("Update the pure-test image lookup adapter")
    tree.body.remove(imports[0])
    module.map_pixels = lambda texture_set, kind, cache: cache[kind]
    exec(compile(tree, str(path), "exec"), module.__dict__)
    module.procedural = procedural
    return module


reference = load_reference()
generators = load_generator(ROOT / "src/open_painter/generators/base.py",
                            "open_painter.generators._optimized_base", procedurals.evaluate)
reference_generators = load_generator(HERE / "reference_generators.py",
                                      "open_painter.generators._frozen_base", reference.evaluate)


def generator_fixture():
    rng = np.random.default_rng(42)
    maps = {name: rng.random((11, 13, 4), dtype=np.float32)
            for name in set().union(*reference_generators.REQUIRED_MAPS.values())}
    ts = SimpleNamespace(baked_maps=[SimpleNamespace(kind=name, image=True) for name in maps])
    v, u = np.indices((9, 13), dtype=np.float32)
    return ts, maps, u / 12 - .27, v / 8 - .31


def generator_parameters(**kwargs):
    values = dict(source="GENERATOR", amount=.41, contrast=3.7, noise_strength=.65,
                  noise_scale=17.23, seed=17, id_color=(.1, .6, .8))
    values.update(kwargs)
    return SimpleNamespace(**values)


class ProceduralOptimizationTests(unittest.TestCase):
    def test_frozen_sources_match_recorded_commit_hashes(self):
        metadata = json.loads((HERE / "reference_noise_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["commit"], "df32483a158fb3852564654c6d10f5f2ff7673d4")
        for entry in metadata["files"].values():
            # The recorded hashes describe Git's LF source blobs. Windows Git
            # may check text out as CRLF; Python loads either representation as
            # the same source. Preserve every byte except that newline mapping.
            content = (ROOT / entry["reference"]).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(content).hexdigest(),
                             entry["sha256"])

    def test_all_sources_and_aliases_are_exact_at_wrapped_uvs_and_angles(self):
        self.assertEqual(set(procedurals.CATALOG) - set(reference.CATALOG),
                         {"TAPERED_SCRATCHES", "FINE_FISSURES", "FRACTURED_PAINT",
                          "WEATHERING_FRONT", "CORROSION_GRAIN", "RUNOFF_TRAILS"})
        self.assertTrue(set(reference.CATALOG).issubset(procedurals.CATALOG))
        self.assertEqual(procedurals.ALIASES, reference.ALIASES)
        v, u = np.indices((9, 13), dtype=np.float32)
        u, v = u / 3 - 2.3, v / 2 - 1.7
        for name in list(reference.CATALOG) + list(reference.ALIASES):
            for seed, angle in ((17, 0), (-1, -37.2), (2 ** 40 + 19, 90), (0, 360)):
                with self.subTest(name=name, seed=seed, angle=angle):
                    kwargs = dict(seed=seed, scale=9.37, angle=angle, width=.13,
                                  density=.71, octaves=3, persistence=.57, warp=.37)
                    expected = reference.evaluate(name, u, v, **kwargs)
                    actual = procedurals.evaluate(name, u, v, **kwargs)
                    self.assertEqual(actual.dtype, expected.dtype)
                    np.testing.assert_array_equal(actual, expected)

    def test_all_sources_match_frozen_math_across_strip_boundaries(self):
        v, u = np.indices((13, 17), dtype=np.float32)
        u, v = u / 17 - .65, v / 13 - .23
        for name in reference.CATALOG:
            for angle in (0, -76):
                with self.subTest(name=name, angle=angle):
                    kwargs = dict(seed=-2 ** 35 + 3, scale=11.93, angle=angle)
                    expected = reference.evaluate(name, u, v, **kwargs)
                    actual = np.concatenate([procedurals.evaluate(name, u[a:b], v[a:b], **kwargs)
                                             for a, b in ((0, 1), (1, 5), (5, 12), (12, 13))])
                    np.testing.assert_array_equal(actual, expected)

    def test_scalar_broadcast_and_zero_angle_coordinate_extremes(self):
        coordinates = ((np.float32(-.0), np.float32(-.0)),
                       (np.array([-.0, 0, -.13, 1.7], np.float32), np.float32(.31)),
                       (np.arange(-3, 4, dtype=np.float32)[None, :],
                        np.arange(-2, 3, dtype=np.float32)[:, None]),
                       (np.array([-2 ** 31, -1024.5, 2 ** 31], np.float32),
                        np.array([2 ** 31, .75, -2 ** 31], np.float32)))
        for name in reference.CATALOG:
            for u, v in coordinates:
                for angle in (0., -0.):
                    with self.subTest(name=name, shape=np.shape(u), angle=angle):
                        np.testing.assert_array_equal(
                            procedurals.evaluate(name, u, v, angle=angle, scale=1.001),
                            reference.evaluate(name, u, v, angle=angle, scale=1.001))

    def test_cell_output_selection_preserves_existing_values_and_dtypes(self):
        rng = np.random.default_rng(73)
        cases = [(rng.uniform(-7, 6, (5, 7)).astype(dtype),
                  rng.uniform(-6, 9, (5, 7)).astype(dtype)) for dtype in (np.float32, np.float64)]
        cases += [(np.arange(-3, 4, dtype=np.float32)[None, :],
                   np.arange(-2, 3, dtype=np.float64)[:, None]),
                  (np.float32(-.3), np.float32(.71)),
                  (np.array([np.nan, np.inf, -np.inf, 2 ** 35], np.float32),
                   np.array([.1, .2, .3, -2 ** 35], np.float32))]
        for x, y in cases:
            with np.errstate(all="ignore"):
                expected = reference._cells(x, y, -2 ** 40 + 17)
                for flags in itertools.product((False, True), repeat=3):
                    kwargs = dict(zip(("need_first", "need_second", "need_identity"), flags))
                    actual = procedurals._cells(x, y, -2 ** 40 + 17, **kwargs)
                    for enabled, before, after in zip(flags, expected, actual):
                        if enabled:
                            self.assertEqual(before.dtype, after.dtype)
                            np.testing.assert_array_equal(after, before)
                        else:
                            self.assertIsNone(after)

    def test_cell_distance_ties_keep_first_winner(self):
        seed = 13

        def tied_hash(x, y, value):
            if value in {seed, seed + 79}:
                return np.full(np.broadcast_shapes(np.shape(x), np.shape(y)), .5, np.float32)
            return (np.asarray(x, np.float32) * .031 + np.asarray(y, np.float32) * .071) % 1

        x = np.array([-2., -1., 0., 1., 2.], np.float32)
        y = x[::-1].copy()
        with patch.object(reference, "_hash", side_effect=tied_hash), patch.object(procedurals, "_hash", side_effect=tied_hash):
            expected = reference._cells(x, y, seed)
            for flags in itertools.product((False, True), repeat=3):
                actual = procedurals._cells(x, y, seed, **dict(zip(
                    ("need_first", "need_second", "need_identity"), flags)))
                for enabled, before, after in zip(flags, expected, actual):
                    if enabled:
                        np.testing.assert_array_equal(after, before)

    def test_lattice_memo_does_not_outlive_the_evaluation(self):
        # The memo pins full-raster floor arrays; it must be released when
        # evaluate() returns (or raises), not held until later work evicts it.
        u, v = np.meshgrid(np.linspace(0, 1, 64, dtype=np.float32),
                           np.linspace(0, 1, 48, dtype=np.float32))
        seen = []
        original = procedurals._to_lattice

        def observed(values):
            if isinstance(values, np.ndarray) and values.ndim:
                seen.append(weakref.ref(values))
            return original(values)

        for source in ("FBM", "WORLEY", "GRUNGE_MEDIUM", "SCRATCHES"):
            seen.clear()
            with patch.object(procedurals, "_to_lattice", side_effect=observed):
                procedurals.evaluate(source, u, v, seed=3, scale=6)
            self.assertTrue(seen, source)
            self.assertEqual(procedurals._recent_lattice, [], source)
            gc.collect()
            self.assertTrue(all(ref() is None for ref in seen), source)
        # GRUNGE_SPOTS hashes its octaves before shaping them with _smooth.
        with patch.object(procedurals, "_smooth", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                procedurals.evaluate("GRUNGE_SPOTS", u, v, seed=3, scale=6)
        self.assertEqual(procedurals._recent_lattice, [])

    def test_identity_is_hashed_only_for_the_winning_cell(self):
        x = np.array([-.37, .4, 1.8], np.float32)
        for need_identity, expected_calls in ((False, 50), (True, 51)):
            with patch.object(procedurals, "_hash", wraps=procedurals._hash) as hashed:
                procedurals._cells(x, x[::-1], 17, need_second=False, need_identity=need_identity)
            self.assertEqual(hashed.call_count, expected_calls)


class GeneratorOptimizationTests(unittest.TestCase):
    def test_every_generator_matches_frozen_math(self):
        ts, maps, u, v = generator_fixture()
        for name in reference_generators.GENERATORS:
            for source, strength, seed in (("GENERATOR", .65, 17), ("GENERATOR", 0, -1),
                                           ("GENERATOR", 1, 2 ** 40 + 19), ("NOISE", .35, 29)):
                with self.subTest(name=name, source=source, strength=strength, seed=seed):
                    parameters = generator_parameters(source=source, noise_strength=strength, seed=seed)
                    expected = reference_generators.GENERATORS[name].evaluate(ts, parameters, u, v, maps)
                    actual = generators.GENERATORS[name].evaluate(ts, parameters, u, v, maps)
                    self.assertEqual(actual.dtype, expected.dtype)
                    np.testing.assert_array_equal(actual, expected)
                    partitioned = np.concatenate([
                        generators.GENERATORS[name].evaluate(ts, parameters, u[a:b], v[a:b], maps)
                        for a, b in ((0, 1), (1, 5), (5, 9))])
                    np.testing.assert_array_equal(partitioned, expected)

    def test_breakup_noise_is_computed_only_when_consumed(self):
        ts, maps, u, v = generator_fixture()
        consumers = {"NOISE", "EDGE_WEAR", "CHIPS", "DIRT", "CAVITY_DIRT", "RUST",
                     "DUST", "WORLD_NORMAL", "OBJECT_NORMAL"}
        for name, generator in generators.GENERATORS.items():
            with self.subTest(name=name):
                with patch.object(generators, "procedural", wraps=procedurals.evaluate) as evaluated:
                    generator.evaluate(ts, generator_parameters(), u, v, maps)
                calls = [call.args[0] for call in evaluated.call_args_list]
                self.assertEqual(calls.count("FBM"), 1 if name in consumers else 0)
                if name == "SCRATCHES":
                    self.assertEqual(calls, ["SCRATCHES"])
                elif name == "DRIPS":
                    self.assertEqual(calls, ["STREAKS"])

    def test_legacy_noise_source_keeps_its_existing_math(self):
        ts, maps, u, v = generator_fixture()
        with patch.object(generators, "procedural", wraps=procedurals.evaluate) as evaluated:
            parameters = generator_parameters(source="NOISE")
            result = generators.GENERATORS["NOISE"].evaluate(ts, parameters, u, v, maps)
        self.assertEqual(evaluated.call_count, 0)
        np.testing.assert_array_equal(result, reference_generators.noise(
            u, v, parameters.noise_scale, parameters.seed))


if __name__ == "__main__":
    unittest.main()
