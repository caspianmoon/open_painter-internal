"""Visible Blender live benchmark session. No authored proposal or replacement image fixtures.

The launcher supplies OP_MD_BENCHMARK_JOB and an isolated access-only login.
Every core instruction goes through the normal Director Run operator.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src"), str(HERE)]
import open_painter
from open_painter.ai import session, generated_images
from open_painter.ai.transport import Connection
from open_painter.authoring import document, transactions
from open_painter.core.project_manager import require_set
from open_painter.evaluation import shader_preview
from runtime import append_trace, redact, save_attachment, sha256, write_json
from capture_evidence import capture_checkpoint, fixed_rig_check


class EvidenceCaptureError(RuntimeError):
    """Infrastructure evidence is incomplete; this is not an artistic verdict."""


class LiveRun:
    def __init__(self, job):
        self.job = job
        self.out = Path(job["output"])
        self.out.mkdir(parents=True, exist_ok=True)
        self.phase = "setup"
        self.started = time.monotonic()
        self.deadline = self.started + 120
        self.turn = None
        self.turn_index = 0
        self.ready_frames = 0
        self.last_status = None
        self.pending_action = None
        self.fixture_config = {}
        self.checkpoints = {}
        self.result = dict(run_id=job["run_id"], case_id=job["case"]["id"], mode=job["mode"],
            repeat=job.get("repeat", 1), status="RUNNING", stages=[], checks={}, hard_failures=[],
            failures=[], actual_generated_inputs=[], cost=None, cost_status="UNKNOWN_NOT_EXPOSED",
            manual_artistic_interventions=0, artist_intervention_count=0,
            artist_interventions=[], pilot=job.get("pilot", False),
            provider=dict(requested_model_id=job["model_id"], runtime_version=None, returned_model_id=None))
        case = job["case"]
        self.instructions = [("create", "CREATE", case["create"] + "\n\n" + job["common_instruction"]),
                             *( (f"refine_{i+1}", "REFINE", prompt) for i,prompt in enumerate(case["refinements"]) )]
        self.robust = None
        if job.get("kind") == "robustness":
            from robustness import Scenario
            self.robust = Scenario(self)
            self.instructions = self.robust.instructions
        self.save()

    def save(self):
        self.result["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        write_json(self.out / "result.json", redact(self.result))

    def log(self, direction, value):
        append_trace(self.out / "provider_and_tool_trace.jsonl", direction, value)

    def targets(self):
        ids = self.fixture_config.get("target_set_ids", [])
        return [ts for ts in bpy.context.scene.open_painter.texture_sets if not ids or ts.id in ids]

    def documents(self):
        return {ts.id: document.encode(document.snapshot(ts), digest=True) for ts in bpy.context.scene.open_painter.texture_sets}

    def histories(self):
        return {ts.id: transactions.history(ts) for ts in bpy.context.scene.open_painter.texture_sets}

    def observe_event(self, event):
        self.log("provider", event)
        kind = event.get("event")
        if kind == "connected":
            self.result["provider"]["runtime_version"] = event["runtime"]
        if kind == "turn_metadata":
            self.result["provider"]["returned_model_id"] = event.get("model")
            if self.turn:
                self.turn["provider_metadata"] = event
            if event.get("model") != self.job["model_id"]:
                self.pending_action = "model_mismatch"
                session.stop()
        if kind == "model_rerouted":
            self.result["provider"]["rerouted"] = event
            self.pending_action = "model_mismatch"
            session.stop()
        if kind == "usage" and self.turn:
            self.turn["token_usage"] = event.get("usage")
        if kind == "generated_image":
            try:
                data = generated_images.read(session.connection.state_directory, event)
                identifier = event["image_id"].split(":", 1)[1]
                relative = Path("generated") / (identifier + ".png")
                (self.out / relative).parent.mkdir(exist_ok=True)
                (self.out / relative).write_bytes(data)
                item = dict(event, path=relative.as_posix(), sha256=sha256(self.out / relative))
                self.result["actual_generated_inputs"].append(item)
            except Exception as exc:
                self.result["failures"].append(dict(category="evidence", message=str(exc)))
        if self.turn and event.get("request_id") == self.turn.get("request_id"):
            if kind == "image_status" and event.get("message", "").startswith("Generating"):
                self.turn["generation_call_count"] = self.turn.get("generation_call_count", 0) + 1
            if kind == "completed":
                self.turn["provider_wait_seconds"] = round(time.monotonic() - self.turn.get("sent_monotonic", self.turn["started_monotonic"]), 3)
                self.turn["provider_status"] = event.get("status")
                self.turn["provider_completed_monotonic"] = time.monotonic()
            if kind == "error":
                self.turn.setdefault("provider_errors", []).append(event)
            if kind == "tool":
                self.turn["tool_call_count"] = self.turn.get("tool_call_count", 0) + 1
                if event.get("tool") in {"propose_changes", "propose_scene_changes"}:
                    self.turn.setdefault("proposal_attempts", []).append(event.get("arguments", {}))
            if kind == "text":
                self.turn["response"] = (self.turn.get("response", "") + event.get("text", ""))[-32000:]
        if self.robust:
            self.robust.observe(event)
        self.save()

    def observe_send(self, message):
        record = {key: value for key, value in message.items() if key not in {"images", "token"}}
        prefix = self.turn["name"] if self.turn else "setup"
        attachments = [save_attachment(self.out, item, prefix=prefix) for item in message.get("images", [])]
        for attachment in attachments:
            if attachment["id"] in {"viewport", "target_view"}:
                attachment["sanity"] = self.visual_input_sanity(self.out / attachment["path"])
        if attachments:
            record["images"] = attachments
            with (self.out / "input_manifest.jsonl").open("a") as handle:
                handle.write(json.dumps(dict(turn=prefix, action=message.get("action"),
                    call_id=message.get("call_id"), attachments=attachments)) + "\n")
        if message.get("action") == "run" and self.turn:
            self.turn["sent_monotonic"] = time.monotonic()
            self.turn["input_capture_seconds"] = round(self.turn["sent_monotonic"] - self.turn["started_monotonic"], 3)
            self.turn["request_id"] = message["request_id"]
            self.turn["actual_settings"] = {k: message.get(k) for k in ("model", "generate_textures", "scene_scope")}
            payload = json.loads(message["input"])
            self.check_context_boundary(payload.get("current_context", {}), message.get("tools", []))
            write_json(self.out / (prefix + "_input.json"), redact(dict(payload, attachments=attachments)))
            self.turn["input_manifest"] = prefix + "_input.json"
            self.turn["old_receipt_ids"] = [r["id"] for records in self.histories().values() for r in records]
        if message.get("action") == "tool_result" and self.turn and not message.get("success", True):
            self.turn.setdefault("validation_errors", []).append(message.get("result"))
        elif message.get("action") == "tool_result" and isinstance(message.get("result"), dict):
            self.check_context_boundary(message["result"])
        self.log("host", record)
        self.save()
        if any(item.get("sanity", {}).get("passed") is False for item in attachments):
            raise RuntimeError("Viewport attachment is empty, black or non-finite; no instruction was sent")

    def check_context_boundary(self, context, tools=None):
        """Check the ordinary product context, without rewriting model inputs."""
        if not self.fixture_config.get("primary_evidence"):
            return
        allowed = {obj.name for obj in bpy.data.collections["TEST_MODEL"].all_objects if obj.type == "MESH"}
        for obj in context.get("objects", []):
            if obj.get("type") != "MESH" or obj.get("name") not in allowed:
                raise RuntimeError("Non-model object appeared in Material Director context")
        supported = {"get_context", "get_material_context", "search_library", "describe_asset",
                     "list_generated_images", "propose_changes", "propose_scene_changes"}
        if tools is not None and any(tool.get("name") not in supported for tool in tools):
            raise RuntimeError("Benchmark provider was offered a tool outside material authoring")

    @staticmethod
    def visual_input_sanity(path):
        """Catch a failed offscreen capture; these statistics never grade art."""
        import numpy as np
        preview = bpy.data.images.load(str(path), check_existing=False)
        try:
            values = np.empty(len(preview.pixels), dtype=np.float32)
            preview.pixels.foreach_get(values)
            rgba = values.reshape(-1, 4)
            rgb = rgba[:, :3]
            finite = bool(np.isfinite(rgb).all())
            nonblack = float(np.mean((np.max(rgb, axis=1) > 1e-6) & (rgba[:, 3] > 1e-6))) if len(rgb) else 0
            variation = float(np.max(np.var(rgb, axis=0))) if len(rgb) and finite else 0
            return dict(passed=finite and nonblack > .0001 and variation > 1e-10, finite=finite,
                nonblack_fraction=nonblack, maximum_channel_variance=variation, size=list(preview.size),
                purpose="Empty-capture detection only; semantic framing and appearance still require review")
        finally:
            bpy.data.images.remove(preview)

    def setup(self):
        self.area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        self.region = next(r for r in self.area.regions if r.type == "WINDOW")
        self.fixture_config = json.loads(bpy.context.scene.get("md_benchmark_config", "{}"))
        if not self.fixture_config.get("target_set_ids"):
            raise RuntimeError("Fixture has no frozen md_benchmark_config target sets")
        if self.job.get("evidence", {}).get("primary_evidence", True):
            if not self.fixture_config.get("primary_evidence"):
                raise RuntimeError("The revised benchmark requires a prepared fixed Eevee fixture")
            validation = fixed_rig_check(bpy.context.scene, self.fixture_config)
            if validation["status"] != "PASS":
                raise RuntimeError("Fixture preparation failed: " + "; ".join(validation["errors"]))
        self.result["fixture"] = dict(sha256=sha256(self.job["fixture"]), metadata=self.fixture_config)
        self.result["environment"] = dict(blender_version=bpy.app.version_string,
            blender_build_hash=bpy.app.build_hash.decode(), render_engine=bpy.context.scene.render.engine,
            color_management=dict(view_transform=bpy.context.scene.view_settings.view_transform,
                                  look=bpy.context.scene.view_settings.look),
            evaluation="Open Painter LayerEvaluator CPU",
            input_viewport=dict(shading="MATERIAL", scene_lights=True, scene_world=True),
            build=self.job.get("build", {}))
        expected = self.job.get("expected_environment", {})
        actual = {"blender_version": bpy.app.version_string, "render_backend": bpy.context.scene.render.engine,
                  "evaluator_backend": self.result["environment"]["evaluation"]}
        drift = {key: dict(expected=expected[key], actual=value) for key, value in actual.items()
                 if key in expected and expected[key] != value}
        if drift:
            self.result["environment_drift"] = drift
            raise RuntimeError("Blender/render/evaluator differs from the frozen environment")
        if self.result["fixture"]["sha256"] != self.job["fixture_sha256"]:
            raise RuntimeError("Fixture checksum changed since it was frozen")
        addon = bpy.context.preferences.addons.get("open_painter") or bpy.context.preferences.addons.new()
        addon.module = "open_painter"
        session.preferences().director_share_context = True
        if not bpy.app.online_access:
            raise RuntimeError("Benchmark needs Blender online mode for the real provider")
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.open_painter.open_editor()
            if any(area.type == "PROPERTIES" for area in bpy.context.screen.areas):
                bpy.ops.open_painter.open_inspector(placement="EXISTING")
            self.fix_camera()
            project = bpy.context.scene.open_painter
            project.active_texture_set = next(i for i,ts in enumerate(project.texture_sets) if ts.id == self.fixture_config["target_set_ids"][0])
            bpy.ops.object.select_all(action="DESELECT")
            for ts in self.targets():
                for ref in ts.assignments:
                    if ref.object:
                        ref.object.select_set(True)
                        bpy.context.view_layer.objects.active = ref.object
            if self.robust:
                self.robust.prepare()
            bpy.ops.ed.undo_push(message="Benchmark frozen input")
        session.clear_conversation()
        observer = self

        class ObservedConnection(Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                write_json(os.environ["OP_MD_BENCHMARK_CONTROL"], {"companion_pid": self.process.pid})

            def poll(self):
                events = super().poll()
                for event in events:
                    observer.observe_event(event)
                return events

            def send(self, message):
                observer.observe_send(message)
                result = super().send(message)
                if message.get("action") == "run" and observer.turn:
                    observer.turn["submitted"] = True
                    observer.save()
                return result

        # Authentication isolation is the only launcher substitution. Tool
        # requests, response validation and commit all use the production path.
        session.connection = ObservedConnection(os.environ["OP_MD_BENCHMARK_CODEX"],
            os.environ["OP_MD_BENCHMARK_AUTH"], credentials="file")
        bpy.app.timers.register(session.tick, first_interval=.05)
        self.phase, self.deadline = "connect", time.monotonic() + 120
        self.save()

    def fix_camera(self):
        name = self.fixture_config.get("input_camera_name")
        camera = bpy.data.objects.get(name) if name else None
        if camera is None:
            raise RuntimeError("Fixture is missing its fixed input camera")
        bpy.context.scene.camera = camera
        space = self.area.spaces.active
        # GPUOffScreen does not capture the progressive Cycles render. Use
        # Blender's ordinary material viewport with the frozen scene lighting.
        space.shading.type = "MATERIAL"
        space.shading.use_scene_world = True
        space.shading.use_scene_lights = True
        space.overlay.show_overlays = False
        space.region_3d.view_perspective = "CAMERA"
        space.region_3d.view_camera_zoom = 0
        space.region_3d.update()

    def settle(self):
        ready = True
        for ts in self.targets():
            shader_preview.tick(bpy.context.scene, ts)
            info = shader_preview.diagnostics(bpy.context.scene, ts)
            if info.get("error"):
                raise RuntimeError(ts.name + ": " + info["error"])
            ready = ready and shader_preview.current(bpy.context.scene, ts)
            for layer in ts.layers:
                for mask in layer.masks:
                    if mask.enabled and mask.source == "SCENE":
                        from open_painter.generators import scene_fields
                        field = mask.influence
                        if field.error and field.error != "Analysing…":
                            raise RuntimeError(field.error)
                        if field.mode == "LIVE" and (not field.image or field.provenance != repr(scene_fields.signature(ts, field))):
                            ready = False
        self.ready_frames = self.ready_frames + 1 if ready else 0
        return self.ready_frames >= 5

    def capture(self, label):
        started = time.monotonic()
        options = dict(self.job.get("evidence", {}), case_id=self.job["case"]["id"],
                       mode=self.job["mode"], repeat=self.job.get("repeat", 1), fixture_sha256=self.job["fixture_sha256"])
        if self.robust:
            options.update(render_views=False, capture_channels=False)
        value = capture_checkpoint(bpy.context, self.out, label, config=options)
        if options.get("render_views", True):
            value["ui_layer_mask_views"] = self.capture_ui(label)
            write_json(self.out / (label + "_state.json"), value)
        self.checkpoints[label] = value
        self.fix_camera()
        self.result.setdefault("evidence_timings", {})[label] = round(time.monotonic() - started, 3)
        if self.turn:
            self.turn["checkpoint"] = label + "_state.json"
        self.save()
        expected = []
        if options.get("capture_channels", True):
            expected.append("channels")
        if options.get("render_views", True):
            expected.extend(("views", "ui_layer_mask_views"))
        failed = {key: value.get(key, {}).get("status", "MISSING") for key in expected
                  if value.get(key, {}).get("status") != "CAPTURED"}
        if failed:
            self.result.setdefault("evidence_failures", []).append(dict(checkpoint=label, components=failed))
            raise EvidenceCaptureError("Required evidence capture did not complete: " + json.dumps(failed))
        return value

    def capture_ui(self, label):
        """Record the normal editor; UI selection is restored before the next turn."""
        from open_painter.ui.dock import state as dock_state
        from capture_evidence import safe_name
        from ui_evidence import capture_inspector_pages
        project = bpy.context.scene.open_painter
        previous_set = project.active_texture_set
        ui = dock_state.get(self.area)
        previous_ui = {key: getattr(ui, key) for key in ("selection_kind", "properties_scroll", "layer_scroll", "show_properties")}
        selected = {ts.id: ts.active_layer for ts in project.texture_sets}
        selected_masks = {layer.id: layer.active_mask for ts in project.texture_sets for layer in ts.layers}
        before = self.documents()
        records, selections_captured, errors = [], [], []
        try:
            for ts in self.targets():
                project.active_texture_set = next(i for i, item in enumerate(project.texture_sets) if item.id == ts.id)
                # Every actual layer gets its ordinary inspector; masked layers
                # also get a mask inspector. Full values remain in state.json.
                selections = [(i, None) for i in range(len(ts.layers))] or [(None, None)]
                selections += [(i, m) for i, layer in enumerate(ts.layers) for m in range(len(layer.masks))]
                for index, mask_index in selections:
                    if index is not None:
                        ts.active_layer = index
                    if mask_index is not None:
                        ts.layers[index].active_mask = mask_index
                    ui.mask_selected = mask_index is not None
                    ui.properties_scroll = ui.layer_scroll = 0
                    ui.show_properties = True
                    self.area.tag_redraw()
                    with bpy.context.temp_override(window=bpy.context.window, area=self.area, region=self.region):
                        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
                    stem = (f"{label}-{safe_name(ts.id)}-{index if index is not None else 'empty'}-" +
                            (f"mask-{mask_index}" if mask_index is not None else "layer"))
                    identity = dict(texture_set_id=ts.id,
                        layer_id=ts.layers[index].id if index is not None else None,
                        mask_id=ts.layers[index].masks[mask_index].id if mask_index is not None else None,
                        view="mask" if mask_index is not None else "layer")
                    with bpy.context.temp_override(window=bpy.context.window, area=self.area, region=self.region):
                        pages = capture_inspector_pages(bpy.context, self.area, self.out, stem)
                    records.extend(dict(page, **identity) for page in pages.pop("views", []))
                    selections_captured.append(dict(pages, **identity))
                    if pages.get("status") != "CAPTURED":
                        errors.extend(pages.get("errors") or [stem + ": incomplete inspector evidence"])
        except Exception as exc:
            errors.append(str(exc))
        finally:
            for ts in project.texture_sets:
                ts.active_layer = selected[ts.id]
                for layer in ts.layers:
                    layer.active_mask = selected_masks[layer.id]
            project.active_texture_set = previous_set
            for key, value in previous_ui.items():
                setattr(ui, key, value)
        if self.documents() != before:
            errors.append("UI evidence capture altered an authoring document")
        return dict(status="FAILED" if errors else "CAPTURED", views=records,
                    selections=selections_captured, errors=errors)

    def begin_turn(self):
        name, mode, prompt = self.instructions[self.turn_index]
        self.fix_camera()
        settings = bpy.context.window_manager.open_painter_director
        settings.mode = mode
        settings.scope = "SCENE" if len(self.targets()) > 1 else "SET"
        if self.job["case"].get("scope") in {"SET", "SCENE"}:
            settings.scope = self.job["case"]["scope"]
        if self.robust:
            self.robust.configure(settings)
        settings.prompt = prompt
        settings.model = self.job["model_id"]
        settings.send_view = settings.send_maps = True
        settings.generate_textures = self.job["mode"] == "H"
        settings.reference_path = self.job.get("reference", "") if self.job["case"].get("reference") else ""
        self.turn = dict(name=name, mode=mode, prompt=prompt, status="RUNNING", submitted=False, started_monotonic=time.monotonic(),
                         before_documents=self.documents(), provider_wait_seconds=None, validation_errors=[])
        self.result["stages"].append(self.turn)
        write_json(self.out / "prompts.json", [dict(name=n, mode=m, prompt=p) for n,m,p in self.instructions])
        self.log("operator", dict(action="director_run", name=name, mode=mode, scope=settings.scope, prompt=prompt))
        try:
            # Render/screenshot operators can clear Blender's temporary area
            # context. Enter a fresh override directly around the public action.
            with bpy.context.temp_override(window=bpy.context.window, area=self.area, region=self.region):
                result = bpy.ops.open_painter.director_run()
            if result != {"FINISHED"}:
                raise RuntimeError(session.state["status"])
        except Exception as exc:
            self.turn.update(status="REJECTED", error=str(exc), response=session.state["response"])
            if self.robust and self.robust.rejected(exc):
                self.finish("COMPLETED")
                return
            self.finish("BLOCKED" if not self.turn.get("submitted") else "FAILED", "input_capture", str(exc))
            return
        self.phase = "running"
        self.ready_frames = 0
        limits = self.job["limits"]
        self.deadline = self.turn["started_monotonic"] + limits["multi_set_turn_seconds" if settings.scope == "SCENE" else "single_asset_turn_seconds"]
        self.save()

    def completed_turn(self):
        now = time.monotonic()
        self.turn["response"] = session.state["response"]
        self.turn["status_message"] = session.state["status"]
        self.turn["turn_elapsed_seconds"] = round(now - self.turn["started_monotonic"], 3)
        self.turn["local_application_seconds"] = round(now - self.turn.get("provider_completed_monotonic", now), 3)
        receipts = [dict(receipt, texture_set_id=key) for key, records in self.histories().items() for receipt in records
                    if receipt["id"] not in self.turn.get("old_receipt_ids", [])]
        self.turn["receipt_count"] = len(receipts)
        self.turn["document_changed"] = self.turn["before_documents"] != self.documents()
        self.turn["status"] = "APPLIED" if receipts else "DRAFT" if session.request and session.request["draft"] else "RESPONSE_ONLY"
        self.turn["applied"] = bool(receipts)
        write_json(self.out / (self.turn["name"] + "_receipt.json"), receipts)
        write_json(self.out / (self.turn["name"] + "_proposal.json"), self.turn.get("proposal_attempts", []))
        if self.robust:
            self.robust.completed()
            if self.phase == "finished":
                return
        elif not receipts:
            if self.turn_index == 0:
                self.result["failures"].append(dict(category="delivery", stage=self.turn["name"],
                    message="Create produced no material commit; see provider reply and status", status=self.turn["status"]))
                self.finish("FAILED")
                return
            # E.g. MD-01 may correctly find no rust to reduce. The independent
            # reviewer must verify the requested component is already satisfied;
            # prose or an unchanged document does not establish a successful no-op.
            self.turn["no_op_review"] = dict(status="REVIEW_REQUIRED", evidence=[
                self.turn["name"] + "_input.json", self.turn["name"] + "_proposal.json",
                "provider_and_tool_trace.jsonl"])
        self.phase = "preview"
        self.preview_started = time.monotonic()
        self.ready_frames = 0
        # Preview settling belongs to the same turn's frozen wall-clock cap.
        self.save()

    def step(self):
        if self.phase == "finished":
            return
        if time.monotonic() - self.started > self.job["limits"]["session_seconds"]:
            raise TimeoutError("Predeclared total session cap reached")
        status = session.state["status"]
        if status != self.last_status:
            self.log("status", dict(message=status, phase=self.phase))
            self.last_status = status
        if self.pending_action == "model_mismatch":
            session.stop()
            self.finish("BLOCKED", "infrastructure", "Returned/rerouted model does not match the frozen model ID")
            return
        if self.robust and self.pending_action:
            self.robust.action(self.pending_action)
            self.pending_action = None
            if self.phase == "finished":
                return
        if self.phase == "connect":
            if session.connection is None:
                raise RuntimeError(status)
            if session.state["signed_in"] and session.state["models"] and self.settle():
                if self.job.get("runtime_version") and self.job["runtime_version"] != session.state["runtime"]:
                    raise RuntimeError("Codex runtime differs from the frozen runtime")
                if self.job["model_id"] not in {m["id"] for m in session.state["models"]}:
                    raise RuntimeError("Pinned model is not available to this account")
                self.capture("before")
                self.begin_turn()
        elif self.phase == "running":
            if session.request is None or not session.request["running"]:
                self.completed_turn()
        elif self.phase == "preview" and self.settle():
            self.turn["preview_settling_seconds"] = round(time.monotonic() - self.preview_started, 3)
            self.turn["visible_material_ready"] = True
            self.turn["prompt_to_visible_seconds"] = round(time.monotonic() - self.turn["started_monotonic"], 3)
            self.capture("after_" + self.turn["name"])
            self.turn["visible_complete"] = True
            if time.monotonic() > self.deadline:
                raise TimeoutError("Predeclared turn cap reached while collecting the settled result")
            self.turn_index += 1
            if self.turn_index < len(self.instructions):
                self.begin_turn()
            else:
                if self.robust:
                    self.robust.finish()
                else:
                    from verify_final import finalize_session
                    result = finalize_session(bpy.context, self.out, config=dict(self.job.get("evidence", {}),
                        case_id=self.job["case"]["id"], repeat=self.job.get("repeat", 1), mode=self.job["mode"],
                        fixture_sha256=self.job["fixture_sha256"]))
                    self.result["finalization"] = result
                    if result.get("packing_errors") or result.get("status") != "SAVED":
                        raise EvidenceCaptureError("Final material save/packing did not complete")
                    if self.fixture_config.get("primary_evidence") and result.get("primary_evidence", {}).get("status") != "CAPTURED":
                        raise EvidenceCaptureError("Required primary render inventory is incomplete")
                    self.finish("COMPLETED" if not self.result["failures"] else "FAILED")
        if self.phase not in {"finished"} and time.monotonic() > self.deadline:
            raise TimeoutError("Predeclared turn/setup cap reached in " + self.phase)

    def finish(self, status, category=None, message=None):
        self.phase = "finished"
        if category:
            self.result["failures"].append(dict(category=category, message=message))
        self.result["status"] = status
        if self.turn and self.turn.get("status") == "RUNNING":
            self.turn["status"] = status
        if status in {"FAILED", "BLOCKED"}:
            try:
                bpy.ops.wm.save_as_mainfile(filepath=str(self.out / "failure.blend"))
            except Exception as exc:
                self.result["failures"].append(dict(category="persistence", message=str(exc)))
        session.disconnect()
        self.save()
        print("BENCHMARK_SESSION", self.job["run_id"], status, flush=True)
        bpy.ops.wm.quit_blender()


def main():
    job = json.loads(Path(os.environ["OP_MD_BENCHMARK_JOB"]).read_text())
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    bpy.ops.wm.open_mainfile(filepath=job["fixture"], use_scripts=False)
    run = LiveRun(job)

    def tick():
        try:
            if run.phase == "setup":
                run.setup()
            elif run.phase != "finished":
                with bpy.context.temp_override(window=bpy.context.window, area=run.area, region=run.region):
                    run.step()
        except Exception as exc:
            run.log("exception", dict(traceback=traceback.format_exc()))
            submitted = any(stage.get("submitted") for stage in run.result["stages"])
            run.finish("FAILED" if submitted and not isinstance(exc, EvidenceCaptureError) else "BLOCKED",
                       "evidence" if isinstance(exc, EvidenceCaptureError) else
                       "timeout" if isinstance(exc, TimeoutError) else "infrastructure", str(exc))
        return None if run.phase == "finished" else .1

    bpy.app.timers.register(tick, first_interval=.5)


if __name__ == "__main__":
    main()
