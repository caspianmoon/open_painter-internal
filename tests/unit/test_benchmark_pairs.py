"""Paired GPU timings must compare the same input transition, with exact checks."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


HELPER = Path(__file__).resolve().parents[2] / "scripts/benchmark_pairs.py"
SPEC = importlib.util.spec_from_file_location("benchmark_pairs", HELPER)
pairs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pairs)
BENCHMARK_SPEC = importlib.util.spec_from_file_location(
    "benchmark_windows_viewport", HELPER.parent / "benchmark_windows_viewport.py")
benchmark = importlib.util.module_from_spec(BENCHMARK_SPEC)
BENCHMARK_SPEC.loader.exec_module(benchmark)


class BenchmarkPairTests(unittest.TestCase):
    def test_matched_transitions_order_exact_validation_and_flag_restoration(self):
        runtime = SimpleNamespace(COMPACT_PIXEL_PUBLISH=False)
        state = {"value": 0}
        validations = []

        def change(index):
            state["value"] = index

        def measure(operation):
            before = state["value"]
            operation()
            row = {key: 8.0 if runtime.COMPACT_PIXEL_PUBLISH else 10.0 for key in pairs.TIMINGS}
            row.update(before=before, after=state["value"],
                       completion_pixels={"ROUGHNESS": state["value"]}, gpu_passes=2, units_rendered=3,
                       engine_counters={"engine_texture_readbacks": 1, "engine_texture_readback_bytes": 64,
                                        "engine_channel_publications": 1,
                                        "engine_state_signature_checks": 4 if runtime.COMPACT_PIXEL_PUBLISH else 18,
                                        "engine_state_cache_hits": 14 if runtime.COMPACT_PIXEL_PUBLISH else 0})
            return row

        def validate(label, pixels):
            validations.append((label, state["value"]))
            self.assertEqual(pixels["ROUGHNESS"], state["value"])
            return {"ROUGHNESS": {"sha256": str(state["value"])}}

        result = pairs.measure_pairs(runtime, "COMPACT_PIXEL_PUBLISH", measure, change,
                                     validate, "roughness", 4,
                                     require_same_gpu_work=True, require_fewer_signatures=True)
        self.assertFalse(runtime.COMPACT_PIXEL_PUBLISH)
        self.assertEqual(len(result["samples"]), 8)
        self.assertEqual([row["variant_enabled"] for row in result["samples"]],
                         [False, True, True, False, False, True, True, False])
        for index in range(0, 8, 2):
            a, b = result["samples"][index:index + 2]
            self.assertEqual((a["before"], a["after"]), (b["before"], b["after"]))
            self.assertNotEqual(a["before"], a["after"])
        self.assertEqual([value for _, value in validations], [0, 0])
        summary = pairs.summarize_pairs(result["samples"], lambda rows: {"samples": len(rows)})
        self.assertEqual(summary["paired_delta_median_ms"]["wall_ms"], -2.0)
        self.assertEqual(summary["enabled_faster_wall_pairs"], 4)
        self.assertTrue(result["same_gpu_work_verified"])
        self.assertTrue(result["fewer_signature_checks_verified"])

        def changed_work(operation):
            row = measure(operation)
            if runtime.COMPACT_PIXEL_PUBLISH:
                row["engine_counters"]["engine_texture_readback_bytes"] = 32
            return row

        with self.assertRaisesRegex(AssertionError, "engine_texture_readback_bytes differs"):
            pairs.measure_pairs(runtime, "COMPACT_PIXEL_PUBLISH", changed_work, change,
                                validate, "roughness", 2, require_same_gpu_work=True)
        self.assertFalse(runtime.COMPACT_PIXEL_PUBLISH)

        def unchanged_signatures(operation):
            row = measure(operation)
            row["engine_counters"]["engine_state_signature_checks"] = 18
            return row

        with self.assertRaisesRegex(AssertionError, "signature checks did not decrease"):
            pairs.measure_pairs(runtime, "COMPACT_PIXEL_PUBLISH", unchanged_signatures, change,
                                validate, "roughness", 2, require_fewer_signatures=True)
        self.assertFalse(runtime.COMPACT_PIXEL_PUBLISH)

        def mismatched_validation(label, pixels):
            return {"ROUGHNESS": {"sha256": str(runtime.COMPACT_PIXEL_PUBLISH)}}

        with self.assertRaisesRegex(AssertionError, "full published pixels differ"):
            pairs.measure_pairs(runtime, "COMPACT_PIXEL_PUBLISH", measure, change,
                                mismatched_validation, "roughness", 2)
        self.assertFalse(runtime.COMPACT_PIXEL_PUBLISH)
        with self.assertRaisesRegex(ValueError, "no boolean"):
            pairs.check_flag(runtime, "NONEXISTENT_FLAG")

    def test_different_layer_workloads_cannot_report_a_speedup(self):
        def report(fixture, elapsed):
            return {"results": [{"size": 512, "measurement_version": 2, "fixture": fixture,
                                 "summary": {"roughness": {"wall_ms": {"median": elapsed}}},
                                 "final_pixels": {"ROUGHNESS": {"sha256": "synthetic"}}}]}
        old = report("two_fills_cellular_mask_v1", 10)
        deep = report("root_fills_24_cellular_coat_mask_v1", 5)
        result = benchmark.comparison(old, deep)["512"]
        self.assertFalse(result["same_environment"])
        self.assertIsNone(result["stages"]["roughness"]["timings"]["wall_ms"]["speedup"])
        same = benchmark.comparison(old, report("two_fills_cellular_mask_v1", 5))["512"]
        self.assertTrue(same["same_environment"])
        self.assertEqual(same["stages"]["roughness"]["timings"]["wall_ms"]["speedup"], 2)


if __name__ == "__main__":
    unittest.main()
