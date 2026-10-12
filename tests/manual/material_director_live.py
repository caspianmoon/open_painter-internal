"""Real Codex replies, real Painter operators, and real view captures. No model fixtures."""

import base64
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import struct
import zlib

import bpy
from mathutils import Vector

bpy.context.preferences.view.show_splash = False

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.ai import session, viewport
from open_painter.ai.transport import Connection
from open_painter.authoring import context as authoring, document, transactions
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.core.project_manager import require_set
from open_painter.evaluation import shader_preview
from open_painter.generators import scene_fields

OUT = Path(os.environ["OP_DIRECTOR_LIVE_OUTPUT"])
SCENE_SCOPE = os.environ.get("OP_DIRECTOR_LIVE_SCOPE") == "SCENE"
CASES = {
    "scene_robot": ("CREATE", True, "Texture this like a battle-worn sci-fi robot."),
    "scene_robot_explain": ("EXPLAIN", True, "Look at the full model. Does it look like a battle-worn sci-fi robot? What would you improve?"),
    "scene_create": ("CREATE", True, "Texture this entire droid model from scratch, across ALL ten material regions in the scene. Inspect every part using its isolated view and newly baked maps. Use the supplied olive industrial paint reference as the art direction and use native image generation for actual reusable surface texture sources. Create a coherent aged olive industrial droid: predominantly painted steel shell and inset panels, matching head coating, restrained irregular exposed-metal chips, localized mild cavity rust and dirt; give optical parts and antennas appropriate distinct physical finishes. Keep most paint intact, avoid continuous bright edge outlines, uniform rust washes or confetti. Reuse generated sources coherently across parts and keep all layers, masks and physical channels editable. Build original stacks, without inserting a finished library material. Submit one coordinated proposal covering the full model, including every back panel and small part. Explain any limitation caused by the mesh segmentation."),
    "scene_explain": ("EXPLAIN", True, "Inspect the actual full-model result and all material regions. Does the scene read as a coherent aged olive industrial droid? Identify two concrete visual problems with named material controls, and note any physical-material approximation caused by the mesh segmentation. Confirm whether each part received its own fresh maps. Do not generate or modify anything."),
    "scene_plan": ("PLAN", True, "Prepare one coordinated scene refinement: halve existing rust layer opacity on all painted head, body and panel materials, and slightly increase their paint roughness to reduce the shiny highlights. Keep the optical parts and antenna finishes unchanged. Reuse existing layers and generated images. Preserve the chip pattern and geometry. Prepare a complete grouped draft for review."),
    "scene_fix": ("REFINE", True, "Improve the actual full-model result. Your visual diagnosis found two problems: wear reads as thin traced panel lines rather than irregular abrasion, and the optical assembly is excessively mirror-polished. Inspect the relevant parts and fix both across the scene. Break up edge chipping at a broader irregular scale and avoid continuous outlines, while retaining quiet painted areas; soften the optical reflections. Preserve all generated texture pixels, the olive identity, the restrained existing rust, the antenna finish and all geometry. Edit existing layers and add only the masks necessary. Apply one coordinated editable refinement, and accurately explain the remaining segmentation limitation."),
    "user_reference": ("CREATE", True, "Create a new realistic material from scratch on the editable parts shown in target_view. First inspect the whole model, isolated target, reference image and fresh mesh maps. Briefly identify the target part and reference appearance. Use native image generation to make a tileable surface texture guided by the supplied olive industrial paint reference. Build separate editable steel substrate, aged olive enamel, sparse irregular chipped paint, restrained localized cavity rust and dirt. Use the fresh baked maps for geometry-aware placement. Keep most of the surface painted, avoid uniform edge outlines and confetti chips, and preserve all surrounding parts. Do not insert a finished library material or reuse old authored layers."),
    "user_explain": ("EXPLAIN", True, "Inspect the actual rendered model and the reference. Identify the editable part using target_view, confirm which mesh maps were supplied and whether they are current, then name the two most visible material problems and the exact controls to improve. Do not generate or modify anything."),
    "user_refine": ("REFINE", True, "Fix those two visible problems while preserving the generated paint texture and material identity. Keep the rust restrained, most paint intact, and surrounding parts unchanged. Use the existing layers, masks and fresh mesh maps."),
    "user_plan": ("PLAN", True, "Prepare a small refinement: reduce the existing rust opacity by half and make paint roughness slightly lower. Preserve generated texture pixels, chip pattern and all geometry. Keep the changes editable and ready for review."),
    "image_material": ("CREATE", False, "Texture this industrial steel housing realistically using your native image generation feature. Generate a seamless dark olive enamel surface color texture and a grayscale irregular worn-paint breakup texture for an editable chip mask. Use separate editable exposed steel, mild cavity rust, and bottom dirt layers; keep metallic and roughness physically sensible. Chipping should respond to real edges as well as irregular breakup. Keep the result restrained, not camouflage. Import the actual generated images and use them on the material, not just in a reply."),
    "image_roughness": ("PLAN", False, "Generate a seamless grayscale subtle orange-peel enamel roughness-variation texture with native image generation. Propose it as a DETAIL image on the existing Dark Olive Enamel ROUGHNESS channel, with restrained variation around its current value. Keep the base color and every other layer and mask unchanged. Prepare a draft; I want to review it before applying."),
    "image_wear_plan": ("PLAN", True, "The flat recessed front still looks too brown. Keep it mostly olive enamel: constrain the existing cavity rust to the existing exposed-steel paint-loss mask using a shared anchor, and lower rust opacity to 0.12. Also shorten the bottom dirt upper height bound to 0.18. Preserve all generated images, protected paint color, roughness detail and screw exclusion. Prepare editable changes for review."),
    "create": ("CREATE", False, "Old Soviet industrial steel. Dark green painted metal, chipped heavily around exposed edges, irregular scratches, mild rust in cavities, dirt accumulated near the bottom, and oily fingerprints around the handle. Keep every layer, mask and parameter editable."),
    "less_rust": ("REFINE", False, "Too much rust. Reduce the existing rust by about half, without changing the paint color or the scratch pattern."),
    "bottom": ("REFINE", False, "Make the bottom dirtier, but keep the recessed screws clean. Keep the rest of the material as it is."),
    "explain": ("EXPLAIN", True, "Why does this material still look procedural? Look at the image and identify the two most visible problems and concrete adjustments that would improve it."),
    "fix": ("REFINE", True, "Fix those two problems. Keep the material identity and the protected paint color."),
    "plan": ("PLAN", False, "Make this suitable for a stylized PS2 game: broad color shapes, fewer fine scratches and simpler roughness. Prepare editable changes for me to review first."),
    "handling": ("REFINE", False, "Make the existing oily fingerprints affect roughness only, only where the object would be held. Preserve every other layer."),
    "clean_screws": ("REFINE", True, "There is still dirt leaking onto the screw edges. Clean it off the screws without reducing the dirt on the housing."),
}
names = os.environ["OP_DIRECTOR_LIVE_CASES"].split(",")
report = dict(status="running", account="current Codex ChatGPT login", model="account default",
              cases=[], started=time.time(), note="Live model responses; no generated proposal fixtures")
area = None
phase = "setup"
deadline = time.monotonic() + 40
current = None
case_index = 0
ready_frames = 0


def save_report():
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))


class ObservedConnection(Connection):
    def poll(self):
        events = super().poll()
        for event in events:
            if current is not None and event.get("event") in {"tool", "completed", "error", "generated_image", "image_status"}:
                current.setdefault("events", []).append(event)
                print("LIVE_EVENT", current["name"], event.get("event"), event.get("tool", event.get("status", event.get("message", ""))), flush=True)
                save_report()
            elif event.get("event") == "account":
                report["authenticated"] = event["connected"]
                print("LIVE_ACCOUNT", event["connected"], flush=True)
            elif event.get("event") == "connected":
                report["runtime"] = event["runtime"]
            elif event.get("event") == "models":
                report["default_model"] = next((m["id"] for m in event["models"] if m["default"]), "")
        return events

    def send(self, message):
        if current is not None and message.get("action") == "run":
            payload = json.loads(message["input"])
            current["conversation_sent"] = payload.get("recent_conversation", [])
            current["limits_sent"] = payload["current_context"]["limits"]
            current["visual_inputs_sent"] = payload["current_context"].get("visual_inputs", [])
            current["visual_context_sent"] = payload["current_context"].get("visual_context", {})
            current["initial_layer_count_sent"] = len(payload["current_context"]["layers"])
            current["scene_materials_sent"] = payload["current_context"].get("scene_materials", [])
            for attachment in message.get("images", []):
                name = attachment["id"].replace(":", "-")
                (OUT / (current["name"] + "-input-" + name + ".png")).write_bytes(base64.b64decode(attachment["url"].split(",", 1)[1]))
        if current is not None and message.get("action") == "tool_result":
            current.setdefault("tool_results", []).append({k: v for k, v in message.items() if k not in {"token", "images"}})
            for attachment in message.get("images", []):
                name = attachment["id"].replace(":", "-")
                part = message.get("result", {}).get("target", {}).get("id", "part")
                (OUT / f"{current['name']}-{part}-input-{name}.png").write_bytes(base64.b64decode(attachment["url"].split(",", 1)[1]))
            if not message.get("success", True):
                print("LIVE_VALIDATION", current["name"], message.get("result"), flush=True)
            save_report()
        return super().send(message)


def build_object():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, .43))
    body = bpy.context.object
    body.name = "Industrial Steel Housing"
    body.scale = (.3, .2, .4)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = body.modifiers.new("Machined corners", "BEVEL")
    bevel.width = .018; bevel.segments = 3
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    # A real inset has concave edges; naming disconnected convex screw heads
    # 'recessed' does not create a cavity for live curvature to detect.
    bpy.ops.mesh.primitive_cube_add(location=(0, -.205, .43))
    cutter = bpy.context.object
    cutter.scale = (.255, .045, .34)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = cutter.modifiers.new("Rounded panel inset", "BEVEL")
    bevel.width = .012; bevel.segments = 3
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    bpy.context.view_layer.objects.active = body
    inset = body.modifiers.new("Recessed face", "BOOLEAN")
    inset.operation = "DIFFERENCE"; inset.object = cutter
    bpy.ops.object.modifier_apply(modifier=inset.name)
    bpy.data.objects.remove(cutter, do_unlink=True)
    parts = [body]
    for x in (-.22, .22):
        for z in (.17, .67):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=.018, location=(x, -.162, z))
            screw = bpy.context.object
            screw.scale.y = .6
            group = screw.vertex_groups.new(name="Recessed Screws")
            group.add(list(range(len(screw.data.vertices))), 1, "REPLACE")
            parts.append(screw)
    bpy.ops.mesh.primitive_torus_add(major_segments=32, minor_segments=8, location=(0, -.235, .66),
                                   rotation=(math.pi / 2, 0, 0), major_radius=.072, minor_radius=.012)
    handle = bpy.context.object
    group = handle.vertex_groups.new(name="Handling")
    group.add(list(range(len(handle.data.vertices))), 1, "REPLACE")
    parts.append(handle)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts: obj.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=.025)
    bpy.ops.object.mode_set(mode="OBJECT")
    return body


def setup():
    global area, phase, deadline
    addon = bpy.context.preferences.addons.get("open_painter") or bpy.context.preferences.addons.new()
    addon.module = "open_painter"
    prefs = session.preferences()
    if prefs is None: raise ValueError("Real add-on preferences did not register")
    prefs.director_share_context = True
    assert bpy.app.online_access
    bpy.context.preferences.view.show_splash = False
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.open_editor()
        if not os.environ.get("OP_DIRECTOR_LIVE_BLEND"):
            obj = build_object()
            ts = sets.create(bpy.context.scene, "Industrial Steel", 1024)
            sets.assign(bpy.context.scene, ts, obj)
        if os.environ.get("OP_DIRECTOR_LIVE_SET"):
            project = bpy.context.scene.open_painter
            project.active_texture_set = next(i for i,t in enumerate(project.texture_sets) if t.name == os.environ["OP_DIRECTOR_LIVE_SET"])
            bpy.ops.object.select_all(action="DESELECT")
            for ref in require_set(bpy.context).assignments:
                ref.object.select_set(True)
                bpy.context.view_layer.objects.active = ref.object
        if os.environ.get("OP_DIRECTOR_LIVE_REFERENCE"):
            bpy.context.window_manager.open_painter_director.reference_path = os.environ["OP_DIRECTOR_LIVE_REFERENCE"]
        area.spaces.active.region_3d.view_distance = 1.5
        area.spaces.active.region_3d.view_location = (0, 0, .43)
        area.spaces.active.region_3d.view_rotation = Vector((1.15, -2, .9)).to_track_quat("Z", "Y")
        area.spaces.active.region_3d.view_perspective = "ORTHO"
        area.spaces.active.shading.type = "MATERIAL"
        if os.environ.get("OP_DIRECTOR_LIVE_FRAME_MODEL"):
            bpy.ops.view3d.view_all(center=False)
            area.spaces.active.region_3d.view_distance *= .8
        bpy.context.view_layer.update()
        shader_preview.tick(bpy.context.scene, require_set(bpy.context))
        bpy.ops.ed.undo_push(message="Live Material Director baseline")
    session.connection = ObservedConnection(os.environ["OP_DIRECTOR_LIVE_BINARY"],
                                             os.environ["OP_DIRECTOR_LIVE_AUTH"], credentials="file")
    session.state.update(status="Connecting actual Codex account…")
    bpy.app.timers.register(session.tick, first_interval=.05)
    phase = "connect"
    deadline = time.monotonic() + 120
    save_report()


def capture_files(label):
    ts = require_set(bpy.context)
    png = viewport.capture(bpy.context, ts, max_side=1024 if SCENE_SCOPE else 512)
    data = base64.b64decode(png.split(",", 1)[1])
    (OUT / (label + ".png")).write_bytes(data)
    # The requested material is green steel. Detect Blender's pink failed shader
    # separately from host-side graph diagnostics, which cannot see Metal errors.
    import numpy as np
    width, height = struct.unpack("!II", data[16:24])
    at, compressed = 8, b""
    while at < len(data):
        length = struct.unpack("!I", data[at:at+4])[0]
        if data[at+4:at+8] == b"IDAT": compressed += data[at+8:at+8+length]
        at += length+12
    rows = np.frombuffer(zlib.decompress(compressed), dtype=np.uint8).reshape(height, width*4+1)
    assert not rows[:,0].any(), "Expected the capture encoder's unfiltered PNG rows"
    rgba = rows[:,1:].reshape(height, width, 4)
    pink = float(((rgba[...,0]>165)&(rgba[...,1]<145)&(rgba[...,2]>165)).mean())
    if current is not None: current["pink_pixel_fraction"] = pink
    if pink > .025: raise ValueError("Actual viewport is pink: Blender failed to render the generated material")
    (OUT / (label + ".json")).write_text(json.dumps(document.encode(document.snapshot(ts)), indent=2))
    if SCENE_SCOPE:
        (OUT / (label + "-scene.json")).write_text(json.dumps({item.id: dict(name=item.name,
            document=document.encode(document.snapshot(item))) for item in scene_targets()}, indent=2))
        rotation = area.spaces.active.region_3d.view_rotation.copy()
        try:
            for side, direction in (("rear", (-1.15, 2, .7)), ("side", (2, .3, .5))):
                area.spaces.active.region_3d.view_rotation = Vector(direction).to_track_quat("Z", "Y")
                area.spaces.active.region_3d.update()
                data = viewport.capture(bpy.context, ts, max_side=1024)
                (OUT / f"{label}-{side}.png").write_bytes(base64.b64decode(data.split(",", 1)[1]))
        finally:
            area.spaces.active.region_3d.view_rotation = rotation
            area.spaces.active.region_3d.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / (label + ".blend")))
    bpy.ops.screen.screenshot(filepath=str(OUT / (label + "-screen.png")))


def preview_ready(ts):
    for layer in ts.layers:
        for mask in layer.masks:
            if not mask.enabled or mask.source != "SCENE": continue
            field = mask.influence
            if field.error and field.error != "Analysing…":
                raise ValueError(mask.name + ": " + field.error)
            if field.mode == "LIVE" and (not field.image or field.provenance != repr(scene_fields.signature(ts, field))):
                return False
    return shader_preview.current(bpy.context.scene, ts)


def scene_targets():
    return list(bpy.context.scene.open_painter.texture_sets) if SCENE_SCOPE else [require_set(bpy.context)]


def scene_state():
    return {item.id: document.canonical(document.snapshot(item)) for item in scene_targets()}


def start_case():
    global phase, deadline, current, ready_frames
    ts = require_set(bpy.context)
    name = names[case_index]
    if name in {"less_rust", "explain"}:
        paint = next((l for l in ts.layers if l.type == "FILL" and "enamel" in l.name.lower()), None)
        if paint is None: raise ValueError("Select the generated enamel layer to test paint-color protection")
        ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.id == paint.id)
        bpy.context.scene.open_painter.properties_channel = "BASE_COLOR"
        path = ["layers", paint.id, "channels", "BASE_COLOR"]
        if path not in json.loads(ts.director_locks or "[]"):
            bpy.ops.open_painter.director_protect(kind="CHANNEL")
        report["protected_channel"] = dict(layer=paint.name, path=path)
    mode, visual, prompt = CASES[name]
    settings = bpy.context.window_manager.open_painter_director
    settings.mode, settings.scope, settings.prompt, settings.send_view = mode, "SCENE" if SCENE_SCOPE else "SET", prompt, visual
    current = dict(name=name, mode=mode, prompt=prompt, image_sent=visual,
                   before_images=[img.name for img in bpy.data.images],
                   before=document.encode(document.snapshot(ts)), before_digest=document.canonical(document.snapshot(ts)),
                   old_receipt_ids=[r["id"] for r in transactions.history(ts)], started=time.monotonic(),
                   before_scene=scene_state(), geometry_digest=session.binding_digest(authoring.bindings(bpy.context, ts,
                       [ref.object for item in scene_targets() for ref in item.assignments if ref.object])))
    report["cases"].append(current)
    print("LIVE_START", name, prompt, flush=True)
    result = bpy.ops.open_painter.director_run()
    if result != {"FINISHED"}: raise ValueError(session.state["status"])
    phase, deadline, ready_frames = "running", time.monotonic() + (1860 if SCENE_SCOPE else 960), 0
    save_report()


def completed_case():
    global phase, deadline, ready_frames
    ts = require_set(bpy.context)
    current.update(seconds=round(time.monotonic() - current["started"], 2),
                   response=session.state["response"], status=session.state["status"],
                   after=document.encode(document.snapshot(ts)),
                   changes=session.state["changes"],
                   applied_receipts=[r for r in transactions.history(ts) if r["id"] not in current["old_receipt_ids"]])
    current["outcome"] = "applied" if current["applied_receipts"] else "draft" if session.request and session.request["draft"] else "response_only"
    current["generated_textures"] = [dict(name=img.name, image_id=img.get("director_image_id"),
        size=list(img.size), color_space=img.colorspace_settings.name, packed=bool(img.packed_file),
        users=img.users) for img in bpy.data.images if img.get("director_image_id")]
    if SCENE_SCOPE:
        current["scene_after"] = [dict(id=item.id, name=item.name, layers=len(item.layers),
            last_scene_operation=(transactions.history(item)[-1].get("scene_operation") if transactions.history(item) else None))
            for item in scene_targets()]
        assert current["geometry_digest"] == session.binding_digest(authoring.bindings(bpy.context, ts,
            [ref.object for item in scene_targets() for ref in item.assignments if ref.object])), "Scene geometry changed"
        if current["name"] in {"scene_create", "scene_robot"}:
            inspected = [result["result"] for result in current.get("tool_results", []) if result.get("success") and "target" in result.get("result", {})]
            assert len(scene_targets()) == 10 and all(item["layer_count"] == 0 for item in current["scene_materials_sent"])
            assert {item["target"]["id"] for item in inspected} == {item.id for item in scene_targets()}
            assert all(len(item.layers) for item in scene_targets()), "Full-scene Create left an untextured part"
            required = {"map:AO", "map:CURVATURE", "map:NORMAL", "map:WORLD_NORMAL", "map:POSITION", "map:THICKNESS", "target_view"}
            if os.environ.get("OP_DIRECTOR_LIVE_REFERENCE"):
                required.add("reference")
            assert all(required <= {v["id"] for v in item["visual_inputs"]} for item in inspected)
            assert all(v["provenance_status"] == "Ready" for item in inspected for v in item["visual_inputs"] if v["role"] == "mesh_map")
            assert current["generated_textures"] and all(item["packed"] for item in current["generated_textures"])
            assert len({item["last_scene_operation"] for item in current["scene_after"]}) == 1
            current["all_ten_materials_inspected_and_authored"] = True
    if current["name"] == "image_material":
        if not current["generated_textures"] or not all(img["packed"] for img in current["generated_textures"]):
            raise ValueError("Native image generation did not produce packed material textures")
        for img in bpy.data.images:
            if img.get("director_image_id") and img.packed_file:
                (OUT / (img.name.replace(" ", "-") + ".png")).write_bytes(bytes(img.packed_file.data))
    if current["name"] == "user_reference":
        assert current["initial_layer_count_sent"] == 0, "Fresh-model test inherited authored layers"
        assert current["visual_context_sent"]["reference_sent"] and current["visual_context_sent"]["view_sent"]
        assert {"viewport", "target_view", "reference", "map:AO", "map:CURVATURE", "map:NORMAL", "map:WORLD_NORMAL", "map:POSITION"} <= {i["id"] for i in current["visual_inputs_sent"]}
        assert all(i["provenance_status"] == "Ready" for i in current["visual_inputs_sent"] if i["role"] == "mesh_map")
        assert current["generated_textures"], "Real reference-guided generation did not produce material textures"
    if current["mode"] in {"PLAN", "EXPLAIN"}:
        current["no_mutation"] = current["before_scene"] == scene_state()
        current["no_image_allocation"] = current["before_images"] == [img.name for img in bpy.data.images]
        if not current["no_image_allocation"]:
            raise ValueError("Draft or explanation allocated Blender images before Apply")
    print("LIVE_COMPLETE", current["name"], current["outcome"], current["seconds"], current["status"], flush=True)
    save_report()
    if current["mode"] in {"CREATE", "REFINE"} and not (current["applied_receipts"] and current["changes"]):
        raise ValueError("Real Codex did not apply the requested change: " + current["status"] + ": " + current["response"])
    if current["mode"] in {"PLAN", "EXPLAIN"} and not current["no_mutation"]:
        raise ValueError(current["mode"] + " changed the material before approval")
    if current["mode"] == "PLAN" and current["outcome"] != "draft":
        raise ValueError("Real Codex did not prepare the requested draft")
    if current["mode"] == "PLAN" and session.request and session.request["draft"]:
        current["proposal"] = session.request["draft"]
        result = bpy.ops.open_painter.director_apply("EXEC_DEFAULT", True, request_id=session.request["id"])
        current["plan_apply"] = list(result)
    phase, deadline, ready_frames = "preview", time.monotonic() + (300 if SCENE_SCOPE else 120), 0


def finish(error=None):
    global phase
    phase = "finished"
    report["status"] = "failed" if error else "completed"
    if error: report["error"] = error
    try: session.disconnect()
    except Exception: pass
    save_report()
    print("LIVE_FINISH", report["status"], error or "", flush=True)
    bpy.ops.wm.quit_blender()


def step():
    global phase, case_index, ready_frames, current, deadline
    if phase == "connect":
        if session.connection is None: raise ValueError(session.state["status"])
        ts = require_set(bpy.context)
        for item in scene_targets():
            shader_preview.tick(bpy.context.scene, item)
        if all(preview_ready(item) for item in scene_targets()): ready_frames += 1
        else: ready_frames = 0
        if session.state["signed_in"] and session.state["models"] and ready_frames >= 10:
            sidebar = next(r for r in area.regions if r.type == "UI")
            with bpy.context.temp_override(region=sidebar): sidebar.active_panel_category = "Director"
            capture_files("00-before")
            start_case()
    elif phase == "running":
        if session.request is None or not session.request["running"]:
            completed_case()
    elif phase == "preview":
        ts = require_set(bpy.context)
        for item in scene_targets():
            shader_preview.tick(bpy.context.scene, item)
            diagnostic = shader_preview.diagnostics(bpy.context.scene, item)
            if diagnostic.get("error"):
                raise ValueError(item.name + ": " + diagnostic["error"])
        info = shader_preview.diagnostics(bpy.context.scene, ts)
        if info.get("error"):
            current["preview_error"] = info["error"]
            raise ValueError("Authoring committed but preview failed: " + info["error"])
        if all(preview_ready(item) for item in scene_targets()): ready_frames += 1
        else: ready_frames = 0
        if ready_frames >= 10:
            current["preview"] = info
            current["scene_previews"] = {item.name: shader_preview.diagnostics(bpy.context.scene, item) for item in scene_targets()}
            current["packed_textures_after_apply"] = [dict(name=img.name, image_id=img.get("director_image_id"),
                size=list(img.size), color_space=img.colorspace_settings.name, packed=bool(img.packed_file))
                for img in bpy.data.images if img.get("director_image_id")]
            capture_files(f"{case_index + 1:02d}-{current['name']}")
            if current["mode"] == "PLAN" and current.get("plan_apply") == ["FINISHED"]:
                bpy.ops.open_painter.director_undo("EXEC_DEFAULT", True)
                current["undo_restored"] = current["before_scene"] == scene_state()
                current["undo_changes"] = session.state["changes"]
                if not current["undo_restored"]: raise ValueError("AI Undo did not restore the material")
            save_report()
            case_index += 1
            current = None
            if case_index == len(names): finish(); return
            start_case()
    if time.monotonic() > deadline:
        raise TimeoutError("Timed out in " + phase + ": " + session.state["status"])


def timer():
    if phase == "finished":
        return None
    try:
        if phase == "setup": setup()
        else:
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=bpy.context.window, area=area, region=region): step()
    except Exception:
        finish(traceback.format_exc())
        return None
    return None if phase == "finished" else .1


open_painter.register()
if os.environ.get("OP_DIRECTOR_LIVE_BLEND"):
    bpy.ops.wm.open_mainfile(filepath=os.environ["OP_DIRECTOR_LIVE_BLEND"])
bpy.context.preferences.view.show_splash = False
bpy.app.timers.register(timer, first_interval=.5)
