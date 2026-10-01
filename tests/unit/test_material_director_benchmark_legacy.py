"""Protocol/scoring tests only: no claim of live artist-asset quality."""

from copy import deepcopy
from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from material_director.legacy import manifest, report, scoring
from material_director import runner


def configuration():
    value = manifest.configuration_template()
    value.update(comparison_id="test-comparison")
    value["build"].update(commit="a" * 40, source_sha256="b" * 64)
    value["provider"].update(runtime_version="runtime-test", model_id="model-pinned",
                             returned_model_id="model-pinned", image_generation_available=True)
    value["environment"].update(hardware="test machine", blender_version="test version",
                                render_backend="test render", evaluator_backend="test evaluator",
                                material_library_sha256="c" * 64)
    value["pilot"] = {"status": "PASS", "evidence": "pilot/report.json"}
    return value


def fixtures():
    return {name: {"sha256": "d" * 64} for name in
            {case["fixture"] for case in manifest.load_cases()["cases"]} | {"R01_fabric_reference.png"}}


def sample_manifest():
    return {"configuration": configuration(), "fixtures": fixtures(),
            "specification": manifest.load_cases(), "schedule": manifest.build_schedule()}


def completed(entry, score=4):
    result = manifest.result_template(entry)
    result.update(status="COMPLETED", attempted=True)
    for stage in result["stages"].values():
        stage.update(status="COMPLETED", applied=True, visible_complete=True, evidence=["evidence.json"])
    result["checks"] = {key: {"passed": True, "evidence": ["evidence.json"]} for key in scoring.WORKFLOW_CHECKS}
    result["metrics"]["active_operator_seconds"] = 20
    case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
    review = manifest.review_template(entry, case)
    review["reviewers"] = [{"id": f"human-{i}", "independent": True, "is_generating_ai": False,
                            "role": "material_artist" if i == 1 else "game_asset_usability"}
                           for i in range(1, 4)]
    for stage in review["stages"].values():
        stage["ratings"] = [{"reviewer_id": f"human-{i}",
                             "dimensions": {key: score for key in scoring.DIMENSIONS},
                             "evidence": ["anonymous-views/"]} for i in range(1, 4)]
    for check in review["acceptance_checks"].values():
        check.update(passed=True, evidence=["evidence.json"])
    return result, review


class ManifestTests(unittest.TestCase):
    def test_exact_prompts_and_balanced_alternating_schedule(self):
        specification = manifest.load_cases()
        order = manifest.build_schedule(91)
        self.assertEqual(order, manifest.build_schedule(91))
        self.assertNotEqual(order, manifest.build_schedule(92))
        self.assertEqual(len(order), 64)
        self.assertEqual(len({entry["run_id"] for entry in order}), 64)
        core = [entry for entry in order if entry["kind"] == "core"]
        self.assertEqual(len(core), 48)
        self.assertEqual(len({entry["review_id"] for entry in core}), 48)
        self.assertEqual(sum(entry["resolution_check_4k"] for entry in core), 16)
        for repeat, expected_modes in ((1, ["P", "H"]), (2, ["H", "P"]), (3, ["P", "H"])):
            group = [entry for entry in core if entry["repeat"] == repeat]
            self.assertEqual([group[0]["mode"], group[8]["mode"]], expected_modes)
            for mode in manifest.MODES:
                self.assertEqual({entry["case_id"] for entry in group if entry["mode"] == mode},
                                 {case["id"] for case in specification["cases"]})
        for case in specification["cases"]:
            prompts = manifest.prompts(case)
            self.assertEqual(prompts["create"], case["create"] + "\n\n" + specification["common_instruction"])
            self.assertEqual(prompts["refine_1"], case["refinements"][0])
            self.assertEqual(prompts["refine_2"], case["refinements"][1])

    def test_freeze_rejects_missing_pilot_or_unresolved_environment(self):
        with self.assertRaisesRegex(ValueError, "Resolve comparison pins"):
            manifest.validate_configuration(manifest.configuration_template())
        config = configuration()
        config["pilot"]["status"] = "NOT_RUN"
        with self.assertRaisesRegex(ValueError, "pilot"):
            manifest.validate_configuration(config)
        config = configuration()
        config["provider"]["returned_model_id"] = "different-model"
        with self.assertRaisesRegex(ValueError, "exact model"):
            manifest.validate_configuration(config)

    def test_frozen_schedule_and_completed_attempt_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as path:
            frozen = manifest.freeze_manifest(path, configuration(), fixtures())
            self.assertEqual(manifest.load_manifest(path), frozen)
            with self.assertRaises(FileExistsError):
                manifest.freeze_manifest(path, configuration(), fixtures())
            entry = frozen["schedule"][0]
            result, review = completed(entry)
            manifest.record_result(path, entry["run_id"], result)
            result["status"] = "FAILED"
            with self.assertRaisesRegex(ValueError, "finished attempt"):
                manifest.record_result(path, entry["run_id"], result)
            manifest_path = Path(path) / "manifest.json"
            frozen["schedule"].reverse()
            manifest_path.write_text(json.dumps(frozen), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Frozen manifest changed"):
                manifest.load_manifest(path)

    def test_environment_drift_is_rejected_before_another_attempt(self):
        frozen = sample_manifest()
        observed = deepcopy(frozen["configuration"])
        self.assertTrue(manifest.verify_environment(frozen, observed))
        observed["environment"]["material_library_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "material_library_sha256"):
            manifest.verify_environment(frozen, observed)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.frozen = sample_manifest()
        self.entry = self.frozen["schedule"][0]
        self.case = next(case for case in self.frozen["specification"]["cases"]
                         if case["id"] == self.entry["case_id"])

    def test_median_ratings_and_every_gate_required(self):
        result, review = completed(self.entry)
        review["stages"]["refine_2"]["ratings"][0]["dimensions"] = {key: 0 for key in scoring.DIMENSIONS}
        scored = scoring.score_session(result, self.case, review)
        self.assertEqual(scored["final_score"], 80)
        self.assertEqual(scored["verdict"], "PASS")
        for reviewer in review["stages"]["refine_2"]["ratings"]:
            reviewer["dimensions"] = {key: 5 for key in scoring.DIMENSIONS}
            reviewer["dimensions"]["handoff_integrity"] = 2
        scored = scoring.score_session(result, self.case, review)
        self.assertEqual(scored["final_score"], 94)
        self.assertEqual(scored["verdict"], "FAIL")
        result, review = completed(self.entry, 5)
        review["acceptance_checks"]["AC-03"]["passed"] = False
        self.assertEqual(scoring.score_session(result, self.case, review)["verdict"], "FAIL")

    def test_missing_reviews_or_handoff_are_unscored_not_zero_or_pass(self):
        result, review = completed(self.entry)
        absent = scoring.score_session(result, self.case)
        self.assertEqual(absent["verdict"], "NOT_SCORED")
        self.assertIsNone(absent["final_score"])
        result["checks"]["export_reimport"] = {"passed": True, "evidence": []}
        scored = scoring.score_session(result, self.case, review)
        self.assertEqual(scored["verdict"], "NOT_SCORED")
        self.assertIsNone(scored["workflow_checks"]["export_reimport"])

    def test_creator_ai_cannot_grade_and_single_human_is_disclosed(self):
        result, review = completed(self.entry)
        review["reviewers"][0]["is_generating_ai"] = True
        self.assertIsNone(scoring.score_session(result, self.case, review)["final_score"])
        review["reviewers"] = review["reviewers"][1:2]
        for stage in review["stages"].values():
            stage["ratings"] = stage["ratings"][1:2]
        scored = scoring.score_session(result, self.case, review)
        self.assertEqual(scored["verdict"], "PASS")
        self.assertTrue(scored["stages"]["refine_2"]["review"]["limitations"])

    def test_legitimate_no_op_needs_confirmation_and_accepts_ordered_stage_records(self):
        result, review = completed(self.entry)
        result["stages"]["refine_2"].update(applied=False, status="RESPONSE_ONLY")
        result["stages"] = [dict(stage, name=name) for name, stage in result["stages"].items()]
        self.assertEqual(scoring.score_session(result, self.case, review)["verdict"], "FAIL")
        review["stages"]["refine_2"]["no_op_acceptance"] = {
            "passed": True, "evidence": ["operator-confirmed-no-rust.json"]}
        scored = scoring.score_session(result, self.case, review)
        self.assertEqual(scored["verdict"], "PASS")
        self.assertTrue(scored["stages"]["refine_2"]["confirmed_no_op"])

    def test_missing_and_blocked_sessions_keep_all_denominators(self):
        result = manifest.result_template(self.entry)
        result.update(status="UNAVAILABLE", reason="Provider unavailable")
        result["generated_inputs"]["availability"] = "BLOCKED"
        summary = scoring.summarize(self.frozen, {self.entry["run_id"]: result})
        self.assertEqual((summary["core_scheduled"], summary["robustness_scheduled"]), (48, 16))
        mode = summary["modes"][self.entry["mode"]]
        self.assertEqual(mode["scheduled"], 24)
        self.assertEqual(mode["statuses"]["NOT_RUN"], 23)
        self.assertEqual(mode["generation_blocked"], 1)
        self.assertEqual(mode["conditional_final_quality"]["count"], 0)
        self.assertEqual(len(summary["failures"]), 1)

    def test_hard_failure_and_weak_material_class_cannot_hide_in_average(self):
        results, reviews = {}, {}
        for entry in self.frozen["schedule"]:
            if entry["kind"] == "core":
                result, review = completed(entry, 5)
                if entry["case_id"] == "MD-04":
                    result["status"] = "UNAVAILABLE"
                    for stage in result["stages"].values():
                        stage.update(applied=False, visible_complete=False)
                results[entry["run_id"]] = result
                reviews[entry["review_id"]] = review
        summary = scoring.summarize(self.frozen, results, reviews=reviews)
        for mode in summary["modes"].values():
            self.assertEqual(mode["passed"], 21)
            self.assertEqual(mode["gates"]["delivery"], "FAIL")
            self.assertEqual(mode["cases"]["MD-04"]["conditional_final_quality"]["count"], 0)
        results[self.entry["run_id"]]["hard_failures"] = [{"kind": "unauthorized_edit", "evidence": ["diff.json"]}]
        summary = scoring.summarize(self.frozen, results, reviews=reviews)
        self.assertEqual(summary["modes"][self.entry["mode"]]["gates"]["dependable_workflow"], "FAIL")

    def test_declared_value_claim_needs_matching_control_and_all_three_scores(self):
        results, reviews, controls = {}, {}, {}
        for entry in self.frozen["schedule"]:
            if entry["kind"] != "core":
                continue
            result, review = completed(entry, 4)
            results[entry["run_id"]] = result
            reviews[entry["review_id"]] = review
            case = next(case for case in self.frozen["specification"]["cases"] if case["id"] == entry["case_id"])
            control = manifest.control_template(case, "preset", self.frozen["configuration"])
            control.update(status="COMPLETED", fixture_sha256="d" * 64,
                           evidence=["control.blend"], active_operator_seconds=100,
                           reviewers=review["reviewers"], ratings=deepcopy(review["stages"]["refine_2"]["ratings"]))
            controls[f"{case['id']}-preset"] = control
        without = scoring.summarize(self.frozen, results, reviews=reviews)
        self.assertEqual(without["modes"]["P"]["gates"]["predeclared_value"], "PENDING")
        quality = scoring.summarize(self.frozen, results, controls, reviews)
        self.assertEqual(quality["modes"]["P"]["gates"]["predeclared_value"], "FAIL")
        self.frozen["configuration"]["value_claim"] = "efficiency"
        for control in controls.values():
            control["configuration_sha256"] = manifest.digest(self.frozen["configuration"])
        efficiency = scoring.summarize(self.frozen, results, controls, reviews)
        self.assertEqual(efficiency["modes"]["P"]["gates"]["predeclared_value"], "PASS")
        # Missing active time cannot be replaced with elapsed provider time.
        for result in results.values():
            result["metrics"]["active_operator_seconds"] = None
        unknown_time = scoring.summarize(self.frozen, results, controls, reviews)
        self.assertEqual(unknown_time["modes"]["P"]["gates"]["predeclared_value"], "PENDING")

    def test_report_has_every_scheduled_record_and_no_empty_template_success(self):
        with tempfile.TemporaryDirectory() as path:
            manifest.freeze_manifest(path, configuration(), fixtures())
            summary = report.write_report(path)
            self.assertEqual(len(summary["core_results"]), 48)
            self.assertEqual(len(summary["robustness_results"]), 16)
            text = Path(path, "report.md").read_text(encoding="utf-8")
            self.assertIn("0 / 24", text)
            self.assertIn("NOT SCORED", text)
            self.assertIn("PENDING", text)
            self.assertNotIn("| PASS |", text)

    def test_review_packets_remove_mode_metadata_and_verify_captured_image_hash(self):
        with tempfile.TemporaryDirectory() as path:
            frozen = manifest.freeze_manifest(path, configuration(), fixtures())
            entry = frozen["schedule"][0]
            result, review = completed(entry)
            run_directory = Path(path) / entry["relative_directory"]
            image_path = run_directory / "views" / "create" / "front.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"captured render bytes")
            checkpoint = {"views": {"views": [{"path": str(image_path), "sha256": manifest.file_sha256(image_path),
                                                  "camera": "BENCH_CAM_front", "distance": "normal"}]}}
            (run_directory / "create_state.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            result["stages"]["create"]["checkpoint"] = "create_state.json"
            manifest.record_result(path, entry["run_id"], result)
            exported = report.export_review_packets(path)
            self.assertEqual(exported["packets"], 1)
            self.assertEqual(exported["missing_packets"], 47)
            index = Path(exported["directory"], "index.json").read_text(encoding="utf-8")
            self.assertNotIn(entry["run_id"], index)
            self.assertNotIn('"mode"', index)
            anonymous = Path(exported["directory"], entry["review_id"], "create", "view-01.png")
            self.assertEqual(anonymous.read_bytes(), image_path.read_bytes())
            image_path.write_bytes(b"changed render bytes")
            with self.assertRaisesRegex(ValueError, "changed"):
                report.export_review_packets(path)


if __name__ == "__main__":
    unittest.main()
