"""Synthetic source/trace fixtures for read-only deadline attribution.

These records do not represent provider runs, material results, or artist scores.
They exercise only evidence linkage, exclusion rules, and immutable reporting.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/diagnose_material_director_deadlines.py"

# Exact relevant statements from the ordinary product client. This is inert
# source text parsed by the diagnostic, never a provider or transport mock run.
SOURCE_EXCERPT = '''import time

class CodexClient:
    def run(self, message):
        self.request_id = message["request_id"]
        self.generate_textures = message.get("generate_textures", True)
        self.deadline = time.monotonic() + (1800 if message.get("scene_scope") else 900 if self.generate_textures else 180)

    def poll(self):
        now = time.monotonic()
        if self.request_id and now > self.deadline:
            request_id = self.request_id
            self.interrupt()
            self.emit(dict(event="error", request_id=request_id, message="The instruction timed out. Your material was not changed."))
        expired = [key for key, (_, _, started, _) in self.pending.items() if now - started > 30]
        if expired:
            raise ValueError("Codex connection timed out; reconnect to try again")
'''
LOCAL_DEADLINE_MESSAGE = "The instruction timed out. Your material was not changed."
PRODUCT_DEADLINES = dict(scene_scope_seconds=1800, set_hybrid_seconds=900, set_native_seconds=180)
LIMITS = dict(single_asset_turn_seconds=600, multi_set_turn_seconds=900, session_seconds=1800)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root):
    return {str(path.relative_to(root)): file_hash(path) for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


class SyntheticCampaign:
    def __init__(self, root, *, build_id="improved", mode="P", scope="SET"):
        self.root = root
        self.product_root = root / "product_snapshot"
        self.source = self.product_root / "src/open_painter/ai/codex_client.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(SOURCE_EXCERPT, encoding="utf-8")
        self.pin = dict(commit="a" * 40,
                        source_sha256=digest({str(self.source.relative_to(self.product_root)): file_hash(self.source)}))
        write_json(self.product_root / ".benchmark_product_pin.json", self.pin)
        schedule = []
        for kind, builds in (("core", ("baseline", "improved")),
                             ("correction_ablation", ("improved",))):
            for case in range(1, 6):
                for scheduled_mode in ("P", "H"):
                    for repeat in (1, 2, 3):
                        for build in builds:
                            case_id = f"T{case:02d}"
                            relative = f"{build}/{case_id}/{scheduled_mode}/{repeat}"
                            if kind != "core":
                                relative = f"ablation/{relative}"
                            schedule.append(dict(run_id=f"{kind}-{case_id}-{build}-{scheduled_mode}-{repeat}",
                                kind=kind, case_id=case_id, build_id=build, mode=scheduled_mode, repeat=repeat,
                                scope="SET", relative_directory=relative, order=len(schedule) + 1,
                                correction_enabled=build == "improved" and kind == "core"))
        self.manifest = dict(schema_version=3, protocol="five_asset_eevee_v3", status="FROZEN_NOT_RUN",
            configuration=dict(builds={key: deepcopy(self.pin) for key in ("baseline", "improved")},
                product_deadlines=deepcopy(PRODUCT_DEADLINES), limits=deepcopy(LIMITS),
                comparison_id="SYNTHETIC-NOT-A-PROVIDER-RUN"), schedule=schedule)
        self.entry = next(entry for entry in schedule if entry["kind"] == "core"
                          and entry["build_id"] == build_id and entry["mode"] == mode)
        self.entry["scope"] = scope
        self.manifest_path = root / "manifest.json"
        write_json(self.manifest_path, self.manifest)
        self.directory = root / self.entry["relative_directory"]
        self.job = dict(run_id=self.entry["run_id"], kind="core", mode=mode, repeat=1,
            case=dict(id="T01", scope=scope), build_id=build_id, build=deepcopy(self.pin),
            product_root=str(self.product_root), product_deadlines=deepcopy(PRODUCT_DEADLINES),
            limits=deepcopy(LIMITS), comparison_sha256=digest(self.manifest))
        event = dict(event="error", request_id="synthetic-request", message=LOCAL_DEADLINE_MESSAGE)
        budget = 1800 if scope == "SCENE" else 900 if mode == "H" else 180
        self.stage = dict(name="create", mode="CREATE", status="RESPONSE_ONLY", submitted=True,
            applied=False, request_id="synthetic-request", started_monotonic=99.0, sent_monotonic=100.0,
            actual_settings=dict(model="synthetic-model", generate_textures=mode == "H", scene_scope=scope == "SCENE"),
            provider_errors=[deepcopy(event)], status_message=LOCAL_DEADLINE_MESSAGE,
            turn_elapsed_seconds=float(budget + 6), receipt_count=0, document_changed=False,
            structured_outcome=dict(version=1, execution="PROVIDER_FAILED",
                cause_code="PROVIDER_TRANSPORT_ERROR", request_id="synthetic-request",
                reason=LOCAL_DEADLINE_MESSAGE),
            execution=dict(state="PROVIDER_FAILED", cause_code="PROVIDER_TRANSPORT_ERROR"))
        self.result = dict(run_id=self.entry["run_id"], case_id="T01", mode=mode, repeat=1,
            status="FAILED", attempted=True, execution_summary="PROVIDER_FAILED",
            manifest_sha256=digest(self.manifest), stages=[self.stage],
            completed_at="2026-09-22T00:00:00+00:00",
            budget_policy=dict(harness=deepcopy(LIMITS), product=deepcopy(PRODUCT_DEADLINES)))
        self.trace = [dict(monotonic=100.0, direction="host", data=dict(action="run",
                          request_id="synthetic-request", model="synthetic-model",
                          generate_textures=mode == "H", scene_scope=scope == "SCENE")),
                      dict(monotonic=float(budget + 105), direction="provider", data=deepcopy(event)),
                      dict(monotonic=budget + 105.1, direction="provider",
                           data=dict(event="completed", request_id=None, status="interrupted"))]
        self.write()

    def write(self):
        write_json(self.directory / "job.json", self.job)
        write_json(self.directory / "result.json", self.result)
        write_json(self.directory / "terminal_attempt.json",
                   {key: value for key, value in self.result.items() if key != "manifest_sha256"})
        (self.directory / "provider_and_tool_trace.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in self.trace), encoding="utf-8")

    def repin_source(self):
        self.pin["source_sha256"] = digest({str(self.source.relative_to(self.product_root)): file_hash(self.source)})
        write_json(self.product_root / ".benchmark_product_pin.json", self.pin)
        self.manifest["configuration"]["builds"] = {key: deepcopy(self.pin) for key in ("baseline", "improved")}
        write_json(self.manifest_path, self.manifest)
        self.job["build"] = deepcopy(self.pin)
        self.job["comparison_sha256"] = self.result["manifest_sha256"] = digest(self.manifest)
        self.write()


def load_diagnostic():
    spec = importlib.util.spec_from_file_location("material_director_deadline_diagnostic_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceLinkedDeadlineDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.diagnostic = load_diagnostic()

    def inspect(self, campaign):
        campaign.write()
        return self.diagnostic.inspect_campaign(campaign.root)

    def selected_run(self, report, campaign):
        return next(run for run in report["runs"] if run["run_id"] == campaign.entry["run_id"])

    def assert_not_attributed(self, report):
        self.assertEqual(report["counts"]["confirmed_local_deadline_stages"], 0, report)
        self.assertTrue(all(not run["diagnostics"] for run in report["runs"]))

    def test_source_linked_local_deadline_preserves_all_ninety_schedules_and_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            campaign = SyntheticCampaign(Path(folder))
            before = tree_hashes(campaign.root)
            report = self.diagnostic.inspect_campaign(campaign.root)
            self.assertEqual(tree_hashes(campaign.root), before, "Inspection must never mutate source evidence")
            self.assertEqual(report["counts"]["scheduled_sessions"], 90)
            self.assertEqual(report["counts"]["pending_sessions"], 89)
            self.assertEqual(report["counts"]["confirmed_local_deadline_stages"], 1)
            self.assertEqual(len(report["runs"]), 90)
            run = self.selected_run(report, campaign)
            finding = run["diagnostics"][0]
            self.assertEqual(finding["failure_origin"], "PRODUCT_LOCAL_DEADLINE")
            self.assertEqual(finding["diagnostic_cause"], "NATIVE_INSTRUCTION_DEADLINE_EXCEEDED")
            self.assertEqual(finding["configured_seconds"], 180)
            self.assertEqual(finding["observed_request_interval_seconds"], 185.0)
            self.assertEqual(finding["source_execution"]["state"], "PROVIDER_FAILED")
            self.assertEqual(finding["source_session_status"], "FAILED")
            self.assertEqual(finding["following_interrupted_completion"]["data"]["request_id"], None)
            self.assertEqual(report["implementation"]["sha256"], file_hash(SCRIPT))
            self.assertEqual(report["manifest"]["sha256"], file_hash(campaign.manifest_path))
            self.assertEqual(report["manifest"]["canonical_sha256"], digest(campaign.manifest))
            for item in run["evidence"].values():
                self.assertEqual(item["sha256"], file_hash(Path(item["path"])))
            proof = report["source_proofs"][campaign.pin["source_sha256"]]
            self.assertEqual(proof["source"]["sha256"], file_hash(campaign.source))
            self.assertEqual(proof["recognized_budget_seconds"], PRODUCT_DEADLINES)

    def test_model_prose_status_text_and_generic_transport_errors_do_not_count(self):
        for case in ("model_prose", "status_only", "generic_transport", "near_match"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                campaign.stage["provider_errors"] = []
                campaign.stage["response"] = LOCAL_DEADLINE_MESSAGE
                if case == "model_prose":
                    campaign.trace[1]["data"] = dict(event="text", request_id="synthetic-request",
                                                      text=LOCAL_DEADLINE_MESSAGE)
                elif case == "status_only":
                    campaign.trace[1]["direction"] = "status"
                else:
                    message = ("Codex connection timed out; reconnect to try again" if case == "generic_transport"
                               else LOCAL_DEADLINE_MESSAGE + " Please retry.")
                    campaign.trace[1]["data"]["message"] = message
                    campaign.stage["provider_errors"] = [deepcopy(campaign.trace[1]["data"])]
                self.assert_not_attributed(self.inspect(campaign))

    def test_all_product_budgets_are_derived_symmetrically_for_both_builds_and_modes(self):
        for build_id in ("baseline", "improved"):
            for mode in ("P", "H"):
                for scope in ("SET", "SCENE"):
                    with self.subTest(build_id=build_id, mode=mode, scope=scope), \
                            tempfile.TemporaryDirectory() as folder:
                        campaign = SyntheticCampaign(Path(folder), build_id=build_id, mode=mode, scope=scope)
                        report = self.diagnostic.inspect_campaign(campaign.root)
                        self.assertEqual(report["counts"]["confirmed_local_deadline_stages"], 1)
                        self.assertEqual(report["counts"]["pending_sessions"], 89)
                        run = self.selected_run(report, campaign)
                        expected = 1800 if scope == "SCENE" else 900 if mode == "H" else 180
                        self.assertEqual(run["build_id"], build_id)
                        self.assertEqual(run["mode"], mode)
                        self.assertEqual(run["diagnostics"][0]["configured_seconds"], expected)
                        self.assertEqual(run["diagnostics"][0]["observed_request_interval_seconds"], expected + 5)

    def test_mismatched_request_direction_and_actual_settings_do_not_count(self):
        for case in ("result_error_id", "trace_error_id", "host_id", "stage_id", "structured_id",
                     "host_direction", "error_direction", "actual_settings", "host_settings"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                if case == "result_error_id":
                    campaign.stage["provider_errors"][0]["request_id"] = "other-request"
                elif case == "trace_error_id":
                    campaign.trace[1]["data"]["request_id"] = "other-request"
                elif case == "host_id":
                    campaign.trace[0]["data"]["request_id"] = "other-request"
                elif case == "stage_id":
                    campaign.stage["request_id"] = "other-request"
                elif case == "structured_id":
                    campaign.stage["structured_outcome"]["request_id"] = "other-request"
                elif case == "host_direction":
                    campaign.trace[0]["direction"] = "provider"
                elif case == "error_direction":
                    campaign.trace[1]["direction"] = "host"
                elif case == "actual_settings":
                    campaign.stage["actual_settings"]["scene_scope"] = True
                else:
                    campaign.trace[0]["data"]["generate_textures"] = "false"
                self.assert_not_attributed(self.inspect(campaign))

    def test_early_nonfinite_ambiguous_and_out_of_order_errors_do_not_count(self):
        for case in ("early", "nan", "infinity", "bool", "out_of_order", "duplicate_run",
                     "overlapping_run", "duplicate_error"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                if case in ("early", "nan", "infinity", "bool"):
                    campaign.trace[1]["monotonic"] = {
                        "early": 101, "nan": float("nan"), "infinity": float("inf"), "bool": True}[case]
                elif case == "out_of_order":
                    campaign.trace[0], campaign.trace[1] = campaign.trace[1], campaign.trace[0]
                elif case in ("duplicate_run", "overlapping_run"):
                    duplicate = deepcopy(campaign.trace[0])
                    if case == "overlapping_run":
                        duplicate["data"]["request_id"] = "other-request"
                    campaign.trace.insert(1, duplicate)
                else:
                    campaign.trace.insert(2, deepcopy(campaign.trace[1]))
                self.assert_not_attributed(self.inspect(campaign))

    def test_applied_or_successful_outcomes_are_never_reclassified(self):
        for case in ("applied_flag", "receipt", "typed_applied", "structured_applied", "session_completed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                if case == "applied_flag":
                    campaign.stage["applied"] = True
                elif case == "receipt":
                    campaign.stage["receipt_count"] = 1
                elif case == "typed_applied":
                    campaign.stage["execution"]["state"] = "APPLIED"
                elif case == "structured_applied":
                    campaign.stage["structured_outcome"]["execution"] = "APPLIED"
                else:
                    campaign.result["status"] = "COMPLETED"
                self.assert_not_attributed(self.inspect(campaign))

    def test_source_or_build_pin_changes_invalidate_attribution(self):
        for case in ("source_changed", "pin_changed", "job_pin_changed", "extra_source_file"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                if case == "source_changed":
                    campaign.source.write_text(SOURCE_EXCERPT + "\n# Changed after producing evidence\n", encoding="utf-8")
                elif case == "pin_changed":
                    write_json(campaign.product_root / ".benchmark_product_pin.json",
                               dict(campaign.pin, source_sha256="0" * 64))
                elif case == "job_pin_changed":
                    campaign.job["build"]["source_sha256"] = "0" * 64
                else:
                    (campaign.source.parent / "extra.py").write_text("# New product bytes\n", encoding="utf-8")
                report = self.inspect(campaign)
                self.assert_not_attributed(report)
                self.assertEqual(self.selected_run(report, campaign)["status"], "UNVERIFIED")

    def test_source_text_alone_cannot_prove_an_executable_deadline_branch(self):
        variants = {
            "comment_only": "\n".join("# " + line for line in SOURCE_EXCERPT.splitlines()),
            "wrong_guard": SOURCE_EXCERPT.replace("now > self.deadline", "now < self.deadline"),
            "different_budget": SOURCE_EXCERPT.replace("else 180)", "else 181)"),
            "syntax_error": SOURCE_EXCERPT + "\nthis is not valid Python !!!\n",
            "duplicate_run": SOURCE_EXCERPT + "\n    def run(self, message):\n        pass\n",
            "duplicate_poll": SOURCE_EXCERPT + "\n    def poll(self):\n        pass\n",
            "overwritten_deadline": SOURCE_EXCERPT.replace("    def poll(self):",
                "        self.deadline = 0\n\n    def poll(self):"),
            "overwritten_generation": SOURCE_EXCERPT.replace("    def poll(self):",
                "        self.generate_textures = True\n\n    def poll(self):"),
        }
        for case, source in variants.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                campaign.source.write_text(source, encoding="utf-8")
                campaign.repin_source()
                report = self.inspect(campaign)
                self.assert_not_attributed(report)
                self.assertEqual(self.selected_run(report, campaign)["status"], "UNVERIFIED")

    def test_interrupted_completion_from_a_later_request_is_not_attached(self):
        with tempfile.TemporaryDirectory() as folder:
            campaign = SyntheticCampaign(Path(folder))
            campaign.trace.insert(2, dict(monotonic=285.05, direction="host",
                data=dict(action="run", request_id="next-request", scene_scope=False, generate_textures=False)))
            report = self.inspect(campaign)
            self.assertEqual(report["counts"]["confirmed_local_deadline_stages"], 1)
            finding = self.selected_run(report, campaign)["diagnostics"][0]
            self.assertIsNone(finding["following_interrupted_completion"])

    def test_manifest_job_and_terminal_records_must_describe_the_same_attempt(self):
        for case in ("manifest_hash", "job_manifest_hash", "terminal", "run_id", "scope", "budget"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                if case == "manifest_hash":
                    campaign.result["manifest_sha256"] = "0" * 64
                elif case == "job_manifest_hash":
                    campaign.job["comparison_sha256"] = "0" * 64
                elif case == "run_id":
                    campaign.job["run_id"] = "other-run"
                elif case == "scope":
                    campaign.job["case"]["scope"] = "SCENE"
                elif case == "budget":
                    campaign.job["product_deadlines"]["set_native_seconds"] = 181
                campaign.write()
                if case == "terminal":
                    terminal = campaign.directory / "terminal_attempt.json"
                    data = json.loads(terminal.read_text(encoding="utf-8"))
                    data["status"] = "COMPLETED"
                    write_json(terminal, data)
                report = self.diagnostic.inspect_campaign(campaign.root)
                self.assert_not_attributed(report)
                self.assertEqual(self.selected_run(report, campaign)["status"], "UNVERIFIED")

    def test_unpublished_or_missing_results_remain_pending(self):
        for case in ("unpublished", "missing"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                campaign = SyntheticCampaign(Path(folder))
                path = campaign.directory / "result.json"
                if case == "missing":
                    path.unlink()
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    del data["manifest_sha256"]
                    write_json(path, data)
                report = self.diagnostic.inspect_campaign(campaign.root)
                self.assert_not_attributed(report)
                self.assertEqual(report["counts"]["pending_sessions"], 90)

    def test_malformed_trace_is_unverified_instead_of_inventing_a_diagnosis(self):
        with tempfile.TemporaryDirectory() as folder:
            campaign = SyntheticCampaign(Path(folder))
            (campaign.directory / "provider_and_tool_trace.jsonl").write_text('{"broken":\n', encoding="utf-8")
            report = self.diagnostic.inspect_campaign(campaign.root)
            self.assert_not_attributed(report)
            self.assertEqual(self.selected_run(report, campaign)["status"], "UNVERIFIED")

    def test_cli_writes_only_separate_sidecar_and_refuses_original_artifact_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            campaign = SyntheticCampaign(Path(folder))
            before = tree_hashes(campaign.root)
            with patch("sys.stdout"):
                self.diagnostic.main(["--comparison", str(campaign.root)])
            sidecar = campaign.root / self.diagnostic.OUTPUT_NAME
            self.assertTrue(sidecar.is_file())
            after = tree_hashes(campaign.root)
            del after[self.diagnostic.OUTPUT_NAME]
            self.assertEqual(after, before)
            for target in (campaign.manifest_path, campaign.directory / "result.json", campaign.source):
                with self.subTest(target=target), patch("sys.stderr"), self.assertRaises(SystemExit):
                    self.diagnostic.main(["--comparison", str(campaign.root), "--output", str(target)])


if __name__ == "__main__":
    unittest.main()
