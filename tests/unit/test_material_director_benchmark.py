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
from material_director import report
from material_director.v2 import manifest, scoring
from material_director import runner


def configuration():
    value = manifest.configuration_template()
    value.update(comparison_id="test-comparison")
    value["build"].update(commit="a" * 40, source_sha256="b" * 64)
    value["provider"].update(runtime_version="runtime-test", model_id="model-pinned",
                             returned_model_id="model-pinned", image_generation_available=True)
    value["environment"].update(hardware="test machine", blender_version="test version",
                                render_backend="BLENDER_EEVEE", evaluator_backend="test evaluator",
                                material_library_sha256="c" * 64)
    value["pilot"] = {"status": "PASS", "evidence": "pilot/report.json"}
    return value


def fixtures():
    return {name: {"sha256": "d" * 64} for name in
            {case["fixture"] for case in manifest.load_cases()["cases"]} | {"R01_fabric_reference.png"}}


def sample_manifest():
    return {"schema_version": 2, "protocol": manifest.PROTOCOL, "configuration": configuration(), "fixtures": fixtures(),
            "specification": manifest.load_cases(), "schedule": manifest.build_schedule()}


def completed(entry, score=4):
    result = manifest.result_template(entry)
    result.update(status="COMPLETED", attempted=True, artist_intervention_count=0)
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


class RevisedProtocolTests(unittest.TestCase):
    def test_exact_user_prompts_and_five_case_alternating_schedule(self):
        spec = manifest.load_cases()
        self.assertEqual([case["id"] for case in spec["cases"]], ["T01", "T02", "T03", "T04", "T05"])
        source = Path(__file__).resolve().parents[2].joinpath("Open_Painter_Material_Director_Eevee_Benchmark.md")
        if not source.is_file():
            self.skipTest("the benchmark brief is not part of this checkout")
        raw = source.read_text(encoding="utf-8")
        for case in spec["cases"]:
            self.assertIn(case["create"], raw)
            for prompt in case["refinements"]:
                self.assertIn(prompt, raw)
            self.assertEqual(manifest.prompts(case)["create"], case["create"] + "\n\n" + spec["common_instruction"])
        order = manifest.build_schedule(91)
        self.assertEqual(order, manifest.build_schedule(91))
        self.assertNotEqual(order, manifest.build_schedule(92))
        self.assertEqual(len(order), 5)
        self.assertEqual(len({entry["review_id"] for entry in order}), 5)
        self.assertFalse(any(entry["resolution_check_4k"] for entry in order))
        controlled = manifest.build_schedule(91, modes=["P", "H"], repeat_count=3)
        self.assertEqual(len(controlled), 30)
        for repeat, modes in ((1, ["P", "H"]), (2, ["H", "P"]), (3, ["P", "H"])):
            group = [entry for entry in controlled if entry["repeat"] == repeat]
            self.assertEqual([group[0]["mode"], group[5]["mode"]], modes)
            self.assertEqual(len(group), 10)
        extended = manifest.build_schedule(91, include_robustness=True, extended_checks=True, modes=["P", "H"], repeat_count=3)
        self.assertEqual(len(extended), 46)
        self.assertEqual(sum(entry.get("resolution_check_4k", False) for entry in extended), 10)

    def test_frozen_primary_eevee_and_run_order_cannot_change(self):
        config = configuration()
        manifest.validate_configuration(config)
        config["environment"]["render_backend"] = "CYCLES"
        with self.assertRaisesRegex(ValueError, "BLENDER_EEVEE"):
            manifest.validate_configuration(config)
        with tempfile.TemporaryDirectory() as path:
            frozen = manifest.freeze_manifest(path, configuration(), fixtures())
            self.assertEqual(frozen["schema_version"], 2)
            self.assertEqual(manifest.load_manifest(path), frozen)
            self.assertFalse((Path(path) / "controls").exists())
            with self.assertRaises(FileExistsError):
                manifest.freeze_manifest(path, configuration(), fixtures())
            frozen["schedule"].reverse()
            Path(path, "manifest.json").write_text(json.dumps(frozen), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Frozen manifest changed"):
                manifest.load_manifest(path)

    def test_five_score_weights_and_no_invented_numeric_threshold(self):
        entry = manifest.build_schedule()[0]
        case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
        self.assertEqual(scoring.DIMENSIONS, {"material_appearance": 25, "spatial_placement": 25,
            "prompt_compliance": 20, "editability": 15, "revision_fidelity": 15})
        result, review = completed(entry)
        review["stages"]["refine_2"]["ratings"][0]["dimensions"] = {key: 0 for key in scoring.DIMENSIONS}
        scored = scoring.score_session(result, case, review)
        self.assertEqual(scored["final_score"], 80)
        self.assertEqual(scored["verdict"], "PASS")
        self.assertNotIn("handoff_integrity", scored["final_dimensions"])
        self.assertNotIn("undo", scored["workflow_checks"])
        review["acceptance_checks"]["AC-02"]["passed"] = False
        self.assertEqual(scoring.score_session(result, case, review)["verdict"], "FAIL")

    def test_interventions_are_not_ignored_or_invented(self):
        entry = manifest.build_schedule()[0]
        case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
        for count, verdict in ((0, "PASS"), (1, "MINOR_ASSISTANCE"), (2, "SIGNIFICANT_ASSISTANCE"), (3, "FAIL")):
            result, review = completed(entry)
            result["artist_intervention_count"] = count
            scored = scoring.score_session(result, case, review)
            self.assertEqual(scored["verdict"], verdict)
            self.assertEqual(scored["artist_intervention_count"], count)
        result["artist_intervention_count"] = None
        self.assertEqual(scoring.score_session(result, case, review)["verdict"], "NOT_SCORED")
        review["artist_interventions"] = {"count": 1, "evidence": ["artist-correction-log.json"]}
        self.assertEqual(scoring.score_session(result, case, review)["verdict"], "MINOR_ASSISTANCE")

    def test_no_artist_review_no_aesthetic_score_and_full_denominators(self):
        frozen = sample_manifest()
        entry = frozen["schedule"][0]
        result, review = completed(entry)
        summary = scoring.summarize(frozen, {entry["run_id"]: result})
        self.assertEqual(summary["core_scheduled"], 5)
        self.assertEqual(summary["robustness_scheduled"], 0)
        self.assertEqual(summary["modes"][entry["mode"]]["scheduled"], 5)
        self.assertEqual(summary["modes"][entry["mode"]]["autonomous_successes"], 0)
        self.assertEqual(summary["modes"][entry["mode"]]["conditional_final_quality"]["count"], 0)
        result["status"] = "UNAVAILABLE"
        result["stages"] = []
        blocked = scoring.summarize(frozen, {entry["run_id"]: result})
        self.assertEqual(blocked["modes"][entry["mode"]]["scheduled"], 5)
        self.assertEqual(len(blocked["failures"]), 1)

    def test_invalid_fixture_is_not_scored_as_a_bad_ai_material(self):
        entry = manifest.build_schedule()[0]
        case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
        result, review = completed(entry)
        result["checks"]["input_integrity"] = {"passed": False, "evidence": ["blank-input.json"]}
        scored = scoring.score_session(result, case, review)
        self.assertEqual(scored["verdict"], "BLOCKED")
        self.assertIsNone(scored["final_score"])
        self.assertFalse(scored["conditional_quality_eligible"])

    def test_primary_report_and_packets_preserve_all_scheduled_outcomes(self):
        with tempfile.TemporaryDirectory() as path:
            frozen = manifest.freeze_manifest(path, configuration(), fixtures())
            summary = report.write_report(path)
            self.assertEqual(len(summary["core_results"]), 5)
            text = Path(path, "report.md").read_text(encoding="utf-8")
            self.assertIn("0 / 5", text)
            self.assertIn("NOT SCORED", text)
            self.assertNotIn("20/24", text)
            self.assertEqual(report.export_review_packets(path)["missing_packets"], 5)



class RunnerTests(unittest.TestCase):
    def test_primary_evidence_guard_requires_actual_files_and_save_receipts(self):
        with tempfile.TemporaryDirectory() as path:
            out = Path(path)
            job = {"case": {"id": "T04"}, "kind": "core"}
            entry = next(item for item in manifest.build_schedule() if item["case_id"] == "T04")
            result, review = completed(entry)

            def artifact(name):
                target = out / name
                target.write_bytes(("synthetic evidence boundary: " + name).encode())
                return {"path": name, "sha256": manifest.file_sha256(target)}

            views = [artifact(name) for name in manifest.load_cases()["evidence_files"]]
            finalization = {"status": "SAVED", "packing_errors": [], "final_blend": artifact("final.blend"),
                "primary_evidence": {"status": "CAPTURED", "files": {item["path"]: item["sha256"] for item in views}}}
            runner.write_json(out / "finalization.json", finalization)
            channels = {key: dict(artifact(key + ".npy"), preview=artifact(key + ".png")["path"], finite=True)
                        for key in ("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY")}
            ui = dict(artifact("native-ui.png"), texture_set_id="set-id")
            checkpoints = ("before", "after_create", "after_refine_1", "after_refine_2")
            for index, label in enumerate(checkpoints):
                checkpoint = {"state": {"texture_sets": {"set-id": {"target": True}}},
                    "channels": {"status": "CAPTURED", "sets": {"set-id": {"status": "CAPTURED", "channels": channels}}},
                    "views": {"status": "CAPTURED", "engine": "BLENDER_EEVEE", "resolution": [1024, 1024],
                              "views": views[:1] if index == 0 else views[1 + (index - 1) * 3:1 + index * 3]},
                    "ui_layer_mask_views": {"status": "CAPTURED", "views": [ui]}}
                runner.write_json(out / (label + "_state.json"), checkpoint)
                if index:
                    runner.write_json(out / (label.removeprefix("after_") + "_receipt.json"), [{"id": "applied-change"}])
            self.assertTrue(runner._evidence_delivery(deepcopy(result), job, out)["passed"])

            case = next(item for item in manifest.load_cases()["cases"] if item["id"] == "T04")
            for defect in ("tampered_image", "missing_ui", "failed_channel", "packing_error", "missing_receipt"):
                with self.subTest(defect=defect):
                    changed = deepcopy(result)
                    restore = {}
                    if defect == "tampered_image":
                        target = out / "09_refine2_close.png"
                        restore[target] = target.read_bytes()
                        target.write_bytes(b"changed after capture")
                    elif defect == "missing_ui":
                        target = out / "native-ui.png"
                        restore[target] = target.read_bytes()
                        target.unlink()
                    elif defect == "failed_channel":
                        target = out / "after_refine_2_state.json"
                        restore[target] = target.read_bytes()
                        data = json.loads(target.read_text(encoding="utf-8"))
                        data["channels"]["status"] = "FAILED"
                        runner.write_json(target, data)
                    elif defect == "packing_error":
                        target = out / "finalization.json"
                        restore[target] = target.read_bytes()
                        runner.write_json(target, dict(finalization, packing_errors=["image could not be packed"]))
                    else:
                        target = out / "refine_2_receipt.json"
                        restore[target] = target.read_bytes()
                        target.unlink()
                    changed["checks"]["evidence_delivery"] = runner._evidence_delivery(changed, job, out)
                    self.assertFalse(changed["checks"]["evidence_delivery"]["passed"])
                    self.assertEqual(changed["status"], "BLOCKED")
                    scored = scoring.score_session(changed, case, review)
                    self.assertEqual(scored["verdict"], "BLOCKED")
                    self.assertIsNone(scored["final_score"])
                    self.assertFalse(scored["conditional_quality_eligible"])
                    for target, original in restore.items():
                        target.write_bytes(original)

    def test_unfinished_provider_turn_does_not_invent_missing_evidence_failure(self):
        with tempfile.TemporaryDirectory() as path:
            result = {"status": "FAILED", "failures": [{"category": "provider"}]}
            check = runner._evidence_delivery(result, {"case": {"id": "T04"}}, Path(path))
            self.assertIsNone(check["passed"])
            self.assertEqual(result["status"], "FAILED")

    def test_actual_input_images_are_required_even_when_sharing_flags_claim_success(self):
        with tempfile.TemporaryDirectory() as path:
            out = Path(path)
            families = ("AO", "CURVATURE", "WORLD_NORMAL", "POSITION", "THICKNESS")
            image = out / "payload.png"
            image.write_bytes(b"captured image boundary bytes")
            ids = ["viewport", *("map:" + kind for kind in families)]
            attachments = [{"id": identifier, "path": image.name, "sha256": manifest.file_sha256(image),
                            "sanity": {"passed": True}} for identifier in ids]
            context = {"visual_context": {"view_sent": True, "maps_sent": True, "reference_sent": False},
                       "target": {"id": "texture-set"},
                       "visual_inputs": [{"id": "map:" + kind, "provenance_status": "Ready"} for kind in families]}
            (out / "create_input.json").write_text(json.dumps({"current_context": context}), encoding="utf-8")
            log = out / "input_manifest.jsonl"
            log.write_text(json.dumps({"turn": "create", "action": "run", "attachments": attachments}) + "\n", encoding="utf-8")
            result = {"stages": [{"name": "create", "status": "APPLIED",
                                   "actual_settings": {"generate_textures": False, "model": "pinned-model"}}]}
            job = {"case": {"id": "T04"}, "mode": "P", "model_id": "pinned-model"}
            self.assertTrue(runner._input_checks(result, job, out)["passed"])
            result['status']='BLOCKED'
            result['stages'].append(dict(name='refine_1',status='REJECTED',submitted=False))
            shared=runner._input_checks(result,job,out)
            self.assertTrue(shared['passed'])
            self.assertEqual(shared['stages'][1]['status'],'NOT_APPLICABLE')
            self.assertEqual(shared['stages'][1]['cause_code'],'REQUEST_NOT_SUBMITTED')
            self.assertEqual(shared['stages'][1]['errors'],[])
            self.assertEqual(result['status'],'BLOCKED')
            unsubmitted=runner._input_checks({'stages':[result['stages'][1]]},job,out)
            self.assertIsNone(unsubmitted['passed'])
            self.assertEqual(unsubmitted['status'],'NOT_APPLICABLE')
            contradiction=json.dumps({'turn':'refine_1','action':'run','attachments':attachments})+'\n'
            with log.open('a') as stream:stream.write(contradiction)
            self.assertFalse(runner._input_checks(result,job,out)['passed'])
            log.write_text(json.dumps({'turn':'create','action':'run','attachments':attachments})+'\n', encoding="utf-8")
            attachments[0]["sanity"]["passed"] = False
            log.write_text(json.dumps({"turn": "create", "action": "run", "attachments": attachments}) + "\n", encoding="utf-8")
            self.assertFalse(runner._input_checks(result, job, out)["passed"])
            attachments[0]["sanity"]["passed"] = True
            attachments.pop()
            log.write_text(json.dumps({"turn": "create", "action": "run", "attachments": attachments}) + "\n", encoding="utf-8")
            self.assertFalse(runner._input_checks(result, job, out)["passed"])

    def test_metrics_do_not_invent_operator_time_or_image_assignments(self):
        result = {"mode": "H", "stages": [{"name": "create", "status": "APPLIED",
                    "provider_wait_seconds": 7, "tool_call_count": 2}],
                  "actual_generated_inputs": [{"image_id": "generated:example"}]}
        runner._metrics(result)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["metrics"]["provider_wait_seconds"], 7)
        self.assertIsNone(result["metrics"]["active_operator_seconds"])
        self.assertIsNone(result["metrics"]["local_apply_seconds"])
        self.assertEqual(result["generated_inputs"]["assigned"], 0)
        self.assertFalse(result["generated_inputs"]["assignment_verified"])
        result["offline_verification"] = {"generated_inputs": {"count": 2, "status": "PASS"}}
        runner._metrics(result)
        self.assertEqual(result["generated_inputs"]["assigned"], 2)
        self.assertTrue(result["generated_inputs"]["assignment_verified"])

    def test_machine_evidence_does_not_pass_manual_export_or_editability_judgment(self):
        with tempfile.TemporaryDirectory() as path:
            out = Path(path)
            evidence = {"provider_isolation": {"status": "PASS"},
                        "persistence": {"status": "PASS"}, "generated_inputs": {"status": "NOT_PRESENT"},
                        "undo": {"status": "PASS"}, "editability": {"status": "CAPTURED"},
                        "export": {"status": "CAPTURED"}, "reconstruction": {"status": "CAPTURED"},
                        "resolution_4k": {"status": "CAPTURED"}}
            (out / "offline_verification.json").write_text(json.dumps(evidence), encoding="utf-8")
            result = {"offline_verification": evidence, "checks": {}}
            runner._checks(result, {"fixture": str(out / "fixture.blend"), "kind": "core"}, out)
            self.assertTrue(result["checks"]["undo"]["passed"])
            self.assertTrue(result["checks"]["save_reopen_offline"]["passed"])
            self.assertIsNone(result["checks"]["export_reimport"]["passed"])
            self.assertIsNone(result["checks"]["editability_probe"]["passed"])
            self.assertEqual(result["resolution_4k"]["status"], "CAPTURED")

    def test_finished_live_result_uses_frozen_directory_and_is_not_rerolled(self):
        with tempfile.TemporaryDirectory() as path:
            base = Path(path)
            fixture_dir, comparison = base / "fixtures", base / "comparison"
            fixture_dir.mkdir()
            fixture_pins = fixtures()
            for name in fixture_pins:
                fixture = fixture_dir / name
                fixture.write_bytes(b"neutral fixture bytes")
                fixture_pins[name]["sha256"] = manifest.file_sha256(fixture)
            frozen = manifest.freeze_manifest(comparison, configuration(), fixture_pins)
            calls = []

            def fake_execute(job, **kwargs):
                calls.append(job)
                entry = next(item for item in frozen["schedule"] if item["run_id"] == job["run_id"])
                self.assertEqual(Path(job["output"]), (comparison / entry["relative_directory"]).resolve())
                existing = json.loads((Path(job["output"]) / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(existing["status"], "RUNNING")
                result = manifest.result_template(entry)
                result.update(status="UNAVAILABLE", reason="Synthetic provider unavailable boundary")
                runner.write_json(Path(job["output"]) / "result.json", result)
                return result

            args = ["run", "--comparison", str(comparison), "--fixtures", str(fixture_dir),
                    "--use-codex-login", "--max-sessions", "1"]
            with patch.object(runner, "_verify_run_pins", return_value="fake-codex"), \
                    patch.object(runner, "preflight_login", return_value={"status":"READY"}), \
                    patch.object(runner, "execute", side_effect=fake_execute), redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(args), 0)
                self.assertEqual(runner.main(args), 0)
            self.assertEqual([job["run_id"] for job in calls], [entry["run_id"] for entry in frozen["schedule"][:2]])
            self.assertFalse((comparison / "results").exists())
            summary = json.loads((comparison / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(summary["failures"]), 2)
            self.assertEqual(summary["core_scheduled"], 5)

    def test_auth_unavailable_preserves_pristine_template_without_an_attempt(self):
        @contextmanager
        def unavailable(*args, **kwargs):
            raise runner.AuthRefreshRequired("Synthetic expired access-only login")
            yield

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            fixture = root / "neutral.blend"
            fixture.write_bytes(b"geometry")
            out = root / "session"
            out.mkdir()
            codex = root / "codex"
            codex.write_text("synthetic executable boundary", encoding="utf-8")
            entry = manifest.build_schedule()[0]
            (out / "result.json").write_text(json.dumps(manifest.result_template(entry)), encoding="utf-8")
            pristine = (out / "result.json").read_bytes()
            case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
            job = runner.make_job(case, mode=entry["mode"], repeat=1, fixture=fixture, output=out, model="model-pinned")
            with patch.object(runner, "runtime_version", return_value="runtime-test"), \
                    patch.object(runner, "source_fingerprint", return_value=configuration()["build"]), \
                    patch.object(runner, "library_fingerprint", return_value="c" * 64), \
                    patch.object(runner, "isolated_login", side_effect=unavailable):
                for _ in range(2):
                    with self.assertRaises(runner.AuthRefreshRequired):
                        runner.execute(job, codex=str(codex), use_login=True)
                    self.assertEqual((out / "result.json").read_bytes(),pristine)
                    self.assertFalse((out / "attempt.json").exists())

    def test_manual_review_can_complete_pending_workflow_but_not_override_failure(self):
        with tempfile.TemporaryDirectory() as path:
            frozen = manifest.freeze_manifest(path, configuration(), fixtures())
            entry = frozen["schedule"][0]
            result, review = completed(entry)
            result["checks"]["editability_probe"] = {"passed": None, "evidence": ["probes.json"]}
            review["workflow_checks"]["editability_probe"] = {"passed": True, "evidence": ["reviewed-probes.json"]}
            source = Path(path) / "returned-review.json"
            source.write_text(json.dumps(review), encoding="utf-8")
            runner._import_review(path, source)
            stored = json.loads((Path(path) / "reviews" / f"{entry['review_id']}.json").read_text(encoding="utf-8"))
            case = next(case for case in manifest.load_cases()["cases"] if case["id"] == entry["case_id"])
            self.assertEqual(scoring.score_session(result, case, stored)["verdict"], "PASS")
            result["checks"]["editability_probe"]["passed"] = False
            self.assertEqual(scoring.score_session(result, case, stored)["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
