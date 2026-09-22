"""Explicitly labeled fault scenarios, kept separate from aesthetic core sessions."""

import json
import time
from unittest.mock import patch

import bpy

from open_painter.ai import session
from open_painter.authoring import commands, document, schema, transactions, scene as scene_authoring
from open_painter.core import layer_stack, texture_set_manager
from open_painter.core.project_manager import require_set
from capture_evidence import material_state
from runtime import write_json


class Scenario:
    def __init__(self, run):
        self.run = run
        self.id = run.job["case"]["id"]
        self.base = None
        self.cancelled_id = None
        self.triggered = False
        self.switched_in_flight = False
        self.cancellation_stopped = False
        self.transport_dropped = False
        prompt = "Create an editable blue painted metal material with restrained edge wear. Apply it to the selected scope."
        if self.id in {"R-03", "R-04", "R-08"}:
            self.instructions = [("plan", "PLAN", "Prepare an editable blue painted-metal material with restrained edge wear for review.")]
            if self.id == "R-08":
                self.instructions.append(("explain", "EXPLAIN", "Explain the current material and its required mesh maps. Do not modify anything."))
        elif self.id == "R-05":
            first = "Generate a detailed painted-metal surface texture with native image generation and build an editable material." if run.job["mode"] == "H" else prompt
            self.instructions = [("cancelled", "CREATE", first),
                ("replacement", "CREATE", "Create a clean blue painted-metal material. Keep all changes in this instruction's scope.")]
        elif self.id == "R-07":
            self.instructions = [("scoped", "REFINE", "Make the selected folder's material less glossy by increasing roughness. Preserve every sibling and the artist's protected work.")]
        else:
            self.instructions = [("request", "CREATE", prompt)]

    def state(self):
        return material_state(bpy.context.scene, self.run.fixture_config)

    def save_state(self, label, value=None):
        value = self.state() if value is None else value
        path = self.run.out / (label + ".json")
        write_json(path, dict(state=value, histories=self.run.histories()))
        self.run.result.setdefault("scenario_evidence", []).append(path.name)
        return value

    def record(self, key, passed, detail, *, destructive=False):
        self.run.result["checks"][key] = dict(passed=passed, evidence=["provider_and_tool_trace.jsonl", "result.json",
            *self.run.result.get("scenario_evidence", [])], detail=detail)
        self.run.log("robustness_check", dict(check=key, passed=passed, detail=detail))
        if passed is False and destructive:
            self.run.result["hard_failures"].append(dict(category="unauthorized_edit", check=key, detail=detail))
        elif passed is False:
            self.run.result["failures"].append(dict(category="robustness", check=key, message=detail))

    def blocked(self, reason):
        hard = bool(self.run.result["hard_failures"])
        self.run.result["safety_passed"] = False if hard else None
        self.run.finish("FAILED" if hard else "BLOCKED", "delivery", reason)

    def conclude(self):
        checks = self.run.result["checks"].values()
        passed = not self.run.result["hard_failures"] and not any(c.get("passed") is False for c in checks)
        self.run.result["safety_passed"] = passed
        self.run.finish("COMPLETED" if passed else "FAILED")

    def add_guard_set(self, name):
        """Fault-fixture helper only; never a hidden baseline in a quality run."""
        project = bpy.context.scene.open_painter
        original = self.original_set_id
        selected, active = list(bpy.context.selected_objects), bpy.context.view_layer.objects.active
        bpy.ops.mesh.primitive_cube_add(location=(100, 100, 100), size=.1)
        obj = bpy.context.object
        obj.name = "Benchmark " + name + " guard"
        other = texture_set_manager.create(bpy.context.scene, name + " non-target", 512)
        texture_set_manager.assign(bpy.context.scene, other, obj)
        identifier = other.id
        obj.hide_render = True
        obj.hide_set(True)
        obj.select_set(False)
        for previous in selected:
            previous.select_set(True)
        bpy.context.view_layer.objects.active = active
        project.active_texture_set = next(i for i, candidate in enumerate(project.texture_sets) if candidate.id == original)
        self.run.result["scenario"].setdefault("setup_only_guards", []).append(dict(
            texture_set_id=identifier, object=obj.name, reason=name, excluded_from_live_provider_scope=True))
        return identifier

    def prepare(self):
        ts = require_set(bpy.context)
        self.original_set_id = ts.id
        self.run.result["scenario"] = dict(id=self.id, fault_injection=self.id in {"R-01", "R-02", "R-06"},
            source="disposable fixture; never a core quality sample")
        if self.id == "R-01":
            for ref in ts.assignments:
                if ref.object and ref.object.data.uv_layers.active:
                    ref.object.data.uv_layers.remove(ref.object.data.uv_layers.active)
            self.run.result["scenario"]["variant"] = "missing_active_uv"
        elif self.id == "R-02":
            for index in reversed(range(len(ts.baked_maps))):
                if ts.baked_maps[index].kind.startswith("CURVATURE"):
                    ts.baked_maps.remove(index)
            self.run.result["scenario"]["variant"] = "missing_curvature_maps"
        elif self.id in {"R-04", "R-06"}:
            project = bpy.context.scene.open_painter
            # R06 always has a distinct rollback guard even on a one-set asset.
            # Re-fetch RNA by ID after appending: the collection can reallocate.
            if self.id == "R-06" or len(project.texture_sets) < 2:
                self.other_id = self.add_guard_set("Rollback-only" if self.id == "R-06" else "Switch-only")
            else:
                self.other_id = next(t.id for t in project.texture_sets if t.id != self.original_set_id)
        elif self.id == "R-07":
            folder = layer_stack.add(ts, "FOLDER", "Allowed artist adjustment")
            folder_id = folder.id
            child = layer_stack.add(ts, "FILL", "Artist authored blue", parent_id=folder_id)
            layer_stack.channel_for(child, "BASE_COLOR").color = (.03, .12, .3, 1)
            layer_stack.channel_for(child, "ROUGHNESS").value = .4
            protected = layer_stack.add(ts, "FILL", "Protected artist sibling")
            protected.opacity = .025
            locks = json.loads(ts.director_locks or "[]")
            locks.append(["layers", protected.id])
            ts.director_locks = json.dumps(locks)
            ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.id == folder_id)
            self.scope_root = folder_id
            self.before_scope = document.snapshot(ts)
            self.allowed_ids = commands.scope_ids(self.before_scope, folder_id)
        elif self.id == "R-05":
            self.run.result["scenario"]["cancellation_variant"] = (
                "NATIVE_IMAGE_GENERATION" if self.run.job["mode"] == "H" else "PROCEDURAL_GENERATION_DISABLED")
        self.base = self.state()
        self.save_state("scenario_initial_state", self.base)

    def configure(self, settings):
        settings.scope = "LAYER" if self.id == "R-07" else "SET"

    def observe(self, event):
        if self.triggered or not self.run.turn:
            return
        active = event.get("request_id") == self.run.turn.get("request_id")
        if not active:
            return
        running = bool(session.request and session.request.get("id") == event.get("request_id")
                       and session.request.get("running"))
        if self.id == "R-04" and running and event.get("event") in {"turn_metadata", "tool", "text", "image_status"}:
            # poll observers run on Blender's main thread before session.handle
            # processes this batch. A deferred switch can occur after completion.
            project = bpy.context.scene.open_painter
            self.triggered = self.switched_in_flight = True
            self.run.result["scenario"]["switch_trigger"] = dict(event=event.get("event"),
                request_id=event["request_id"], running=True, seconds_since_turn=time.monotonic() - self.run.turn["started_monotonic"])
            project.active_texture_set = next(i for i, item in enumerate(project.texture_sets) if item.id == self.other_id)
            self.run.log("scenario_intervention", dict(action="switch_active_set_while_in_flight",
                from_set=self.original_set_id, to_set=self.other_id, request_id=event["request_id"]))
            return
        if self.id == "R-05" and self.run.turn_index == 0:
            generated_started = event.get("event") == "image_status" and event.get("message", "").startswith("Generating")
            if running and (generated_started or (self.run.job["mode"] == "P" and event.get("event") == "tool")):
                self.cancelled_id = self.run.turn.get("request_id")
                before = self.state()
                bpy.ops.open_painter.director_stop()
                self.cancellation_stopped = True
                self.record("stopped_request_no_commit", before == self.state() == self.base,
                    "Stop during native image generation" if self.run.job["mode"] == "H" else "Stop during procedural authoring (generation disabled)", destructive=True)
                self.run.pending_action = "cancel_turn"
                self.triggered = True
        elif self.id == "R-06" and running and event.get("event") in {"tool", "image_status"}:
            # Close immediately before this polled event can be handled/applied.
            self.run.result["scenario"]["disconnect_trigger"] = dict(event=event.get("event"), running=True)
            session.connection.close()
            session.disconnect()
            self.transport_dropped = True
            self.run.pending_action = "drop_connection"
            self.triggered = True

    def action(self, action):
        if action == "cancel_turn":
            if not self.cancellation_stopped:
                self.blocked("Cancellation trigger did not interrupt a running request")
                return
            self.run.turn.update(status="CANCELLED", cancelled_request_id=self.cancelled_id)
            self.run.turn_index += 1
            self.run.begin_turn()
        elif action == "drop_connection":
            # Deliberate transport loss, not an asserted natural service outage.
            if not self.transport_dropped:
                self.blocked("Connection-loss trigger was not exercised before completion")
                return
            self.record("connection_loss_preserved_state", self.state() == self.base,
                        "Injected loss of owned real-provider transport before turn completion", destructive=True)
            self.injected_transactions()
            self.conclude()

    def rejected(self, exc):
        if self.id in {"R-01", "R-02"}:
            self.record("rejected_without_mutation", self.state() == self.base, str(exc), destructive=True)
            self.run.result["diagnosis_review_required"] = True
            self.run.result["safety_passed"] = None
            return True
        return False

    def completed(self):
        ts = require_set(bpy.context)
        if self.id in {"R-03", "R-04"}:
            if self.id == "R-04" and not self.switched_in_flight:
                self.blocked("No provider event arrived while running; active-set switch was not tested")
                return
            if not session.request or not session.request["draft"]:
                # Missing/refused/provider-failed draft is availability evidence,
                # not an unauthorized edit or destructive workflow failure.
                self.record("no_draft_preserved_state", self.state() == self.base,
                    "Plan returned no applicable draft", destructive=True)
                self.blocked("The scenario requires a real completed Plan draft; none was returned")
                return
            identifier = session.request["id"]
            if self.id == "R-03":
                # Artist intervention is the scenario itself, not a rescue of a
                # scored aesthetic run. Make a real ordinary RNA layer edit.
                artist = layer_stack.add(ts, "FILL", "Intervening artist edit")
                artist.opacity = .37
            before_apply = self.state()
            self.save_state("scenario_before_apply_state", before_apply)
            try:
                result = bpy.ops.open_painter.director_apply("EXEC_DEFAULT", True, request_id=identifier)
                rejected = "CANCELLED" in result
            except RuntimeError as exc:
                rejected = True
                self.run.log("operator_error", dict(message=str(exc)))
            preserved = before_apply == self.state()
            self.save_state("scenario_after_apply_attempt_state")
            self.record("stale_or_switched_draft_preserved_state", preserved, session.state["status"], destructive=True)
            self.record("stale_or_switched_draft_rejected", rejected, session.state["status"])
            self.conclude()
        elif self.id == "R-08":
            self.run.result["response_claim_review_required"] = True
            self.record(self.run.turn["name"] + "_unapplied", self.state() == self.base,
                        "Plan/Explain inspected without committing", destructive=True)
            if self.run.turn["name"] == "plan":
                has_draft = bool(session.request and session.request["draft"])
                self.record("plan_has_reviewable_draft", True if has_draft else None,
                            "Draft is required to exercise discard; refusal is not a successful Plan test")
                bpy.ops.open_painter.director_stop()
                self.record("discard_preserved_state", self.state() == self.base, "Discard through normal Stop operator", destructive=True)
        elif self.id == "R-05" and self.run.turn_index == 0:
            self.blocked("Turn ended before the declared cancellation trigger; cancellation was not tested")
        elif self.id == "R-06":
            self.blocked("Turn ended before transport loss could be injected; provider disconnect was not tested")

    def injected_transactions(self):
        """Only R-06 may use a known proposal, explicitly outside visual scoring."""
        scene = bpy.context.scene
        ids = {ts.id for ts in self.run.targets()} | {self.original_set_id, self.other_id}
        targets = [ts for ts in scene.open_painter.texture_sets if ts.id in ids]
        if len(targets) < 2:
            raise RuntimeError("R-06 rollback requires at least two explicit texture-set targets")
        before = self.state()
        self.save_state("scenario_before_injected_failure", before)
        histories = self.run.histories()
        self.run.result["scenario"]["injected_target_ids"] = [ts.id for ts in targets]
        proposals = [dict(texture_set_id=ts.id, proposal=dict(version=1, summary="Injected rollback probe",
            commands=[dict(op="add_layer", id="new:fault", name="Injected fault layer", kind="FILL")])) for ts in targets]
        proposal = dict(version=1, summary="Injected rollback probe", targets=proposals)
        digests = {ts.id: document.digest(ts) for ts in targets}
        with patch.object(commands, "compile_proposal", side_effect=schema.AuthoringError("Injected compile failure")):
            rejected = False
            try:
                scene_authoring.apply(scene, proposal, operation_id="benchmark-injected-compile", digests=digests)
            except schema.AuthoringError:
                rejected = True
        self.record("injected_compile_rejected", rejected, "Known proposal used only for explicit local fault injection")
        self.save_state("scenario_after_compile_failure")
        self.record("injected_compile_rollback", self.state() == before and self.run.histories() == histories,
                    "Compile failure must preserve all documents, pixels and receipts", destructive=True)
        visited = []
        def fail_after_first(target):
            visited.append(target.id)
            raise RuntimeError("Injected mid-apply failure")
        rejected = False
        try:
            scene_authoring.apply(scene, proposal, operation_id="benchmark-injected-apply", digests=digests, fault=fail_after_first)
        except RuntimeError:
            rejected = True
        self.record("injected_apply_interrupted", rejected and len(visited) == 1,
                    "Injected exception after the first commit in a transaction containing at least two targets")
        self.save_state("scenario_after_apply_failure")
        self.record("injected_apply_rollback", self.state() == before and self.run.histories() == histories,
                    "Failure after first target commit must restore all targets, source pixels and receipts", destructive=True)

    def finish(self):
        self.save_state("scenario_final_state")
        if self.id == "R-07":
            ts = require_set(bpy.context)
            try:
                commands.validate_scope(ts, self.before_scope, document.snapshot(ts), self.scope_root)
                valid = True
            except schema.AuthoringError:
                valid = False
            current = self.state()
            others = {key: value for key, value in self.base["texture_sets"].items() if key != self.original_set_id}
            unaffected = (others == {key: value for key, value in current["texture_sets"].items() if key != self.original_set_id}
                          and current["scene"] == self.base["scene"])
            # validate_scope does not by itself prove unchanged surface knobs or
            # in-place image pixels. Check those separately from the scope tree.
            before_ts = self.base["texture_sets"][self.original_set_id]
            after_ts = current["texture_sets"][self.original_set_id]
            valid = valid and before_ts["semantic"]["surface"] == after_ts["semantic"]["surface"]
            outside = [layer for layer in before_ts["semantic"]["layers"] if layer["id"] not in self.allowed_ids]
            protected_image_names = set()
            def image_names(value):
                if isinstance(value, dict):
                    if value.get("$datablock") == "Image":
                        protected_image_names.add(value["name"])
                    for child in value.values():
                        image_names(child)
                elif isinstance(value, list):
                    for child in value:
                        image_names(child)
            image_names(outside)
            pixels_preserved = all(before_ts["sources"].get(name) == after_ts["sources"].get(name) for name in protected_image_names)
            self.record("scoped_protected_work_preserved", valid and unaffected and pixels_preserved,
                        "Declared folder scope, locked siblings, non-target sets, geometry, rig, surface knobs and sibling image pixels", destructive=True)
        elif self.id == "R-05":
            old_images = {e["image_id"] for e in self.run.result["actual_generated_inputs"] if e.get("request_id") == self.cancelled_id}
            assigned = set()
            for ts in self.run.targets():
                for layer in ts.layers:
                    for prop in layer.channels:
                        for img in (prop.image, prop.detail_image):
                            if img and img.get("director_image_id"):
                                assigned.add(img["director_image_id"])
                    assigned.update(m.image["director_image_id"] for m in layer.masks if m.image and m.image.get("director_image_id"))
            self.record("replacement_has_no_cancelled_images", not bool(assigned & old_images),
                        "Compare packed assigned image IDs with cancelled request ownership", destructive=True)
            self.record("cancelled_request_has_no_receipt", not any(
                r["id"].startswith(self.cancelled_id or "<none>") for records in self.run.histories().values() for r in records),
                "Cancelled request cannot commit late", destructive=True)
            self.run.result["replacement_delivered"] = bool(self.run.turn.get("receipt_count"))
        elif self.id in {"R-01", "R-02"}:
            self.record("fixture_geometry_and_guards_preserved", self.state()["scene"] == self.base["scene"],
                        "The declared missing-UV/map intervention is in the baseline; later unrelated geometry, UV, camera, lighting or guard edits are forbidden", destructive=True)
            self.run.result["diagnosis_review_required"] = True
            self.run.result["safety_interpretation"] = "Review proposal, status, supplied map provenance and actual images; safe live alternatives are allowed. No automatic visual success is asserted."
        if self.id == "R-08" and self.run.result["checks"].get("plan_has_reviewable_draft", {}).get("passed") is None:
            self.blocked("Plan produced no reviewable draft; Explain ran but discard was not meaningfully exercised")
        elif self.id in {"R-01", "R-02"}:
            self.run.result["safety_passed"] = None
            self.run.finish("FAILED" if self.run.result["hard_failures"] else "COMPLETED")
        else:
            self.conclude()
