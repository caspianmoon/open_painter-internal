"""One fresh ordinary Director conversation, with immutable weapon-test evidence."""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "src")]
import open_painter
from open_painter.ai import session
from capture_evidence import material_state, render_primary_views
from prepare_fixtures import object_snapshot
from run_session import EvidenceCaptureError, LiveRun
from runtime import redact, sha256, write_json
from verify_final import _pack_sources
from weapon_protocol import MODES, PROMPTS, STAGES, SURFACE_PROMPTS


class WeaponRun(LiveRun):
    def __init__(self, job):
        super().__init__(job)
        prompts = SURFACE_PROMPTS if job.get("surface_sources") else PROMPTS
        self.instructions = [(name, mode, prompt) for name, mode, prompt in
                             zip(STAGES, MODES, prompts)]
        self.frozen_objects = [object_snapshot(o) for o in bpy.data.collections["TEST_MODEL"].objects]

    def capture(self, label):
        errors = _pack_sources(bpy.context.scene)
        path = self.out / (label + ".blend")
        # Preserve the actual state even when rendering fails.
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        value = dict(label=label, saved_state=dict(path=path.name, sha256=sha256(path)),
                     state=material_state(bpy.context.scene, self.fixture_config), packing_errors=errors)
        write_json(self.out / (label + "_state.json"), value)
        value["views"] = render_primary_views(bpy.context, self.out, label, self.fixture_config)
        for item in value["views"]["views"]:
            item["path"] = Path(item["path"]).name
            item["sanity"] = self.visual_input_sanity(self.out / item["path"])
            if not item["sanity"]["passed"]:
                errors.append("Empty/non-finite render: " + item["path"])
        now = [object_snapshot(o) for o in bpy.data.collections["TEST_MODEL"].objects]
        value["frozen_geometry_preserved"] = now == self.frozen_objects
        if not value["frozen_geometry_preserved"]:
            errors.append("Frozen geometry, UVs or material partitions changed")
        write_json(self.out / (label + "_state.json"), value)
        self.checkpoints[label] = value
        self.result.setdefault("checkpoints", {})[label] = label + "_state.json"
        if self.turn:
            self.turn["checkpoint"] = label + "_state.json"
        self.result["last_valid_checkpoint"] = dict(label=label, **value["saved_state"])
        self.save()
        if errors or value["views"]["status"] != "CAPTURED":
            raise EvidenceCaptureError(repr(errors + value["views"].get("errors", [])))
        self.fix_camera()
        return value

    def completed_turn(self):
        super().completed_turn()
        if self.phase != "finished" and not self.turn.get("applied"):
            self.finish("FAILED", "delivery", "Refinement applied no changes")

    def step(self):
        if self.phase != "preview":
            return super().step()
        if time.monotonic() > self.deadline:
            raise TimeoutError("Declared turn deadline reached during preview")
        if not self.settle():
            return
        self.turn["visible_material_ready"] = True
        self.capture("after_" + self.turn["name"])
        self.turn["visible_complete"] = True
        self.turn_index += 1
        if self.turn_index < len(self.instructions):
            self.begin_turn()
        else:
            self.finish("COMPLETED")

    def _finish_records(self, status, category=None, message=None):
        self.phase = "finished"
        self.result["status"] = status
        if category:
            self.result["failures"].append(dict(category=category, message=message))
        if self.turn:
            self.turn["response"] = session.state.get("response", "")
            self.turn["status_message"] = session.state.get("status", "")
            if not self.turn.get("applied"):
                self.turn["status"] = "FAILED"
        self.save()
        if status != "COMPLETED" and self.fixture_config:
            label = "failure_" + (self.turn["name"] if self.turn else "create")
            if label not in self.checkpoints:
                try:
                    self.capture(label)
                except Exception as exc:
                    self.result.setdefault("evidence_failures", []).append(dict(label=label, error=str(exc), status="CAPTURE_FAILED"))
        present = {stage["name"] for stage in self.result["stages"]}
        for name, mode, prompt in self.instructions:
            if name not in present:
                self.result["stages"].append(dict(name=name, mode=mode, prompt=prompt,
                    status="NOT_RUN", reason="Skipped after earlier failure", submitted=False, applied=False))
        self.result["review"] = dict(status="NOT_REVIEWED", independent_human_validation=False)
        self.save()
        write_json(self.out / "terminal_attempt.json", redact(self.result))
        print("WEAPON_SESSION", self.job["run_id"], status, flush=True)


def main():
    job = json.loads(Path(os.environ["OP_MD_BENCHMARK_JOB"]).read_text(encoding="utf-8"))
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    bpy.ops.wm.open_mainfile(filepath=job["fixture"], use_scripts=False)
    run = WeaponRun(job)

    def tick():
        try:
            if run.phase == "setup":
                run.setup()
            elif run.phase != "finished":
                with bpy.context.temp_override(window=bpy.context.window, area=run.area, region=run.region):
                    run.step()
        except Exception as exc:
            run.log("exception", dict(traceback=traceback.format_exc()))
            run.finish("FAILED", "evidence" if isinstance(exc, EvidenceCaptureError) else "runtime", str(exc))
        return None if run.phase == "finished" else .1

    bpy.app.timers.register(tick, first_interval=.5)


if __name__ == "__main__":
    main()
