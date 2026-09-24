"""Main-thread orchestration. A model turn produces a draft; one operator commits it."""

import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import bpy
from bpy.app.handlers import persistent

from ..authoring import assets, commands, context as authoring, document, schema, transactions, scene as scene_authoring
from ..core.project_manager import require_set
from .transport import Connection
from .generated_images import MAX_IMAGES
from .progress import BLOCKING_TOOLS, Progress, tool_label

connection = None
request = None
progress = Progress()   # What the panels show of the running (or last) instruction.
_pending = []           # Received events not handled yet; see announce().
_drawn = 0.
state = dict(status="Not connected", connected=False, signed_in=False, response="", changes=[],
             models=[], plan="", runtime="", login=False, auto_login=False, limits={}, visual_inputs=[], sharing=[], outcome=None)
_model_items = [("DEFAULT", "Account default", "", 0)]
_conversation = []
_conversation_target = None


def conversation(captured):
    """Ephemeral dialogue for the current material, separate from committed receipts."""
    key = (captured["scene"], "SCENE" if captured.get("scene_scope") else captured["set_id"])
    return list(_conversation) if key == _conversation_target else []


def remember(captured, outcome):
    global _conversation_target
    key = (captured["scene"], "SCENE" if captured.get("scene_scope") else captured["set_id"])
    if key != _conversation_target:
        _conversation.clear()
        _conversation_target = key
    previous = next((turn for turn in _conversation if turn["id"] == captured["id"]), None)
    if previous:
        previous["outcome"] = outcome
        return
    _conversation.append(dict(id=captured["id"], mode=captured["mode"],
                              instruction=captured["prompt"][:5000],
                              response=state["response"][:8000], outcome=outcome))
    del _conversation[:-6]


def clear_conversation():
    global _conversation_target
    _conversation.clear()
    _conversation_target = None
    state["response"] = ""


def preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    return addon.preferences if addon else None


def model_items(_self, _context):
    return _model_items


def redraw():
    global _drawn
    _drawn = time.monotonic()
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    # Session state shows in the sidebar and the Painter header, never in
                    # the 3D view itself: streaming text must not re-render the viewport.
                    for region in area.regions:
                        if region.type in {"UI", "HEADER", "TOOL_HEADER"}:
                            region.tag_redraw()


def release(captured):
    """The request is over: its textures are packed in the .blend or unwanted."""
    from . import generated_images, surface_observations
    from ..authoring import surface_field
    if captured:
        generated_images.discard(captured["images"].values())
        surface_observations.release(captured["id"])
        surface_field.forget()
        captured.pop("candidate", None)
        captured.pop("candidate_records", None)
        captured.pop("correspondences", None)
        captured.pop("correspondence_packets", None)


def ensure_online(*, share_context=False):
    if not bpy.app.online_access:
        raise schema.AuthoringError("Enable Allow Online Access in Blender Preferences > System to connect Codex")
    prefs = preferences()
    if share_context and (prefs is None or not prefs.director_share_context):
        raise schema.AuthoringError("Enable Share Material Context in the Material Assistant panel before sending a material")


def connect(context):
    global connection
    ensure_online()
    disconnect()
    prefs = preferences(context)
    package = __package__.rsplit(".", 1)[0]
    if package.startswith("bl_ext."):
        directory = bpy.utils.extension_path_user(package, path="material_director", create=True)
    else:
        directory = bpy.utils.user_resource("CONFIG", path="open_painter/material_director", create=True)
    from . import generated_images
    generated_images.prune(directory)
    connection = Connection(bpy.path.abspath(prefs.director_codex_binary) if prefs.director_codex_binary else "",
                            Path(directory), credentials=prefs.director_credentials)
    state.update(status="Preparing Material Director…", response="", changes=[], auto_login=True)
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=.05)
    redraw()


def disconnect():
    global connection, request, _model_items
    release(request)
    request = None
    _pending.clear()
    _announced.clear()
    # Keep the log: after a lost connection it is the only account of what happened.
    progress.finish("failed", "Codex connection closed")
    clear_conversation()
    if connection:
        connection.close()
        connection = None
    state.update(status="Not connected", connected=False, signed_in=False, login=False, auto_login=False,
                 models=[], plan="", runtime="", limits={}, visual_inputs=[], sharing=[])
    _model_items = [("DEFAULT", "Account default", "", 0)]
    for wm in bpy.data.window_managers:
        if hasattr(wm, "open_painter_director"):
            wm.open_painter_director.model = "DEFAULT"
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    redraw()


def stop():
    global request
    if request:
        terminal("CANCELLED", "ARTIST_CANCELLED")
        if progress.owns(request["id"]):
            progress.finish("stopped", "Stopped before Codex finished")
    if request and not request["running"] and request["draft"]:
        remember(request, "discarded")
    release(request)
    request = None
    if connection:
        connection.send(dict(action="interrupt"))
    state.update(status="Stopped", changes=[])
    redraw()


def account_action(action):
    ensure_online()
    if not connection or not state["connected"]:
        raise schema.AuthoringError("Connect Codex first")
    if action == "logout":
        stop()
    connection.send(dict(action=action))
    state.update(status={"login": "Opening ChatGPT sign-in…", "cancel_login": "Sign-in cancelled",
                         "logout": "Signing out…"}[action], login=action == "login")


def binding_digest(saved):
    from ..geometry.provenance import object_record
    value = [object_record(obj) for obj in authoring.resolve_bindings(saved).values()]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidates_observations(captured):
    """Surface observations a proposal may fuse, keyed by observation ID."""
    from .candidates import observations
    return observations(captured)


def objects_for(captured):
    from ..authoring import regions
    return regions.bindings(authoring.resolve_bindings(captured["bindings"]), captured.setdefault("region_registry", {}))


def target(captured, *, check=True, require_active=True):
    scene = next((s for s in bpy.data.scenes if s.as_pointer() == captured["scene"]), None)
    ts = next((s for s in scene.open_painter.texture_sets if s.id == captured["set_id"]), None) if scene else None
    window = next((w for wm in bpy.data.window_managers for w in wm.windows
                   if w.as_pointer() == captured["window"]), None)
    if ts is None or window is None or window.scene != scene:
        raise schema.AuthoringError("The target scene or texture set changed; run the instruction again")
    if check:
        if document.digest(ts) != captured["digest"] or binding_digest(captured["bindings"]) != captured["binding_digest"]:
            raise schema.AuthoringError("The material or its geometry changed; run the instruction again")
        if captured.get("scene_scope"):
            current = {item.id: document.digest(item) for item in scene_authoring.materials(scene)}
            if current != captured["target_digests"]:
                raise schema.AuthoringError("A scene material changed; run the scene instruction again")
    if check and require_active:
        # Only applying a draft acts on the active set; tool calls resolve the
        # captured set by id, so browsing other sets during a turn is harmless.
        project = scene.open_painter
        if not 0 <= project.active_texture_set < len(project.texture_sets) or project.texture_sets[project.active_texture_set].id != ts.id:
            raise schema.AuthoringError("Return to the original texture set before applying its draft")
    return window, scene, ts


def painter_area(captured, window):
    """The Painter area the instruction was run from; maximizing moves its space to another area."""
    from ..ui import editor
    areas = [a for a in window.screen.areas if a.type == "VIEW_3D"]
    for pick in (lambda a: a.spaces.active.as_pointer() == captured.get("space"),
                 editor.is_area,
                 lambda a: a.spaces.active.shading.type in {"MATERIAL", "RENDERED"},
                 lambda a: True):
        area = next((a for a in areas if pick(a)), None)
        if area is not None:
            return area
    return None


def capture(context, *, mode, scope, prompt):
    """Also used by offline integration fixtures; has no provider side effects."""
    context.view_layer.update()
    ts = require_set(context)
    transactions.available(ts)
    if not ts.assignments:
        raise schema.AuthoringError("Assign a UV-unwrapped mesh to this texture set first")
    if not prompt.strip():
        raise schema.AuthoringError("Describe the material or the change you want")
    targets = scene_authoring.materials(context.scene) if scope == "SCENE" else [ts]
    for item in targets:
        transactions.available(item)
    bindings = authoring.bindings(context, ts, [ref.object for item in targets for ref in item.assignments if ref.object])
    root = ""
    if scope == "LAYER":
        if not 0 <= ts.active_layer < len(ts.layers):
            raise schema.AuthoringError("Select a layer or folder first")
        root = ts.layers[ts.active_layer].id
    result = dict(id=uuid.uuid4().hex, scene=context.scene.as_pointer(), set_id=ts.id,
                  window=context.window.as_pointer(), digest=document.digest(ts),
                  space=context.area.spaces.active.as_pointer() if context.area else 0,
                  bindings=bindings, binding_digest=binding_digest(bindings), scope=root,
                  mode=mode, prompt=prompt.strip(), draft=None, running=True, images={},
                  scene_scope=scope == "SCENE", target_digests={item.id: document.digest(item) for item in targets},
                  inspected_materials=set(), correction_enabled=bool(getattr(context.window_manager.open_painter_director, "correction_enabled", True)),
                  preview_count=0, preview_seconds=0., candidate=None, candidate_history=[], inability=None, region_registry={})
    result["context"] = authoring.capture(ts, bindings, root)
    result["context"]["command_schema"] = schema.SCHEMA
    if result["scene_scope"]:
        result["context"].update(scope="SCENE", scene_materials=scene_authoring.overview(targets, bindings),
                                 scene_proposal_schema=scene_authoring.SCHEMA,
                                 scene_limits=dict(materials=scene_authoring.MAX_TARGETS, commands=scene_authoring.MAX_COMMANDS))
    return result


def attachments_for(context, ts, *, send_view, send_maps, reference=None, whole_view=True, dry=False):
    """The images a request carries. ``dry`` describes them (ids, labels, meanings) without
    rendering the viewport or reading map pixels: the UI's sharing summary reads the same list
    the request is built from."""
    from . import visual_context
    attachments = []
    if send_view:
        targets = {ref.object for ref in ts.assignments if ref.object}
        isolate = (any(obj.type == "MESH" and obj not in targets and not obj.hide_get() for obj in context.view_layer.objects)
                   or any(any(face.material_index not in {r.slot_index for r in ts.assignments if r.object == obj}
                              for face in obj.data.polygons) for obj in targets))
        variants = ([False, True] if isolate else [False]) if whole_view else [True]
        for isolated in variants:
            view = None
            if not dry:
                from .viewport import capture as capture_view
                for side in (768, 512, 384, 256):
                    view = capture_view(context, ts, max_side=side, isolate=isolated, frame_target=isolated and not whole_view)
                    if len(view) <= 250000:
                        break
            attachments.append(dict(id="target_view" if isolated else "viewport", role="target_view" if isolated else "viewport",
                label=(ts.name + " — only its editable mesh parts") if isolated else "Whole model viewport — current material and lighting",
                meaning="Only this texture set's assigned geometry is shown" if isolated else "Full 3D view including neighboring parts; use target_view to identify editable geometry.", url=view))
        if whole_view:
            for label, angle, close in (("opposite", 3.141592653589793, False),
                                        ("side", 1.5707963267948966, False), ("closeup", 0., True)):
                view = None
                if not dry:
                    for side in (768, 512, 384, 256):
                        view = capture_view(context, ts, max_side=side, isolate=True, frame_target=True,
                                            orbit=angle, closeup=close)
                        if len(view) <= 200000:
                            break
                attachments.append(dict(id="target_view:" + label, role="target_view",
                    label=ts.name + " — " + label, meaning="Additional framed view of assigned geometry; closeup crops the center, not a semantic component selection", url=view))
    missing_maps = []
    if send_maps:
        if dry:
            maps = [dict(id="map:" + kind, role="mesh_map", kind=kind, label=kind + " mesh map in UV space", meaning=meaning)
                    for kind, meaning in visual_context.MAPS if any(m.kind == kind and m.image for m in ts.baked_maps)]
            missing_maps = [kind for kind, _ in visual_context.MAPS if not any(m.kind == kind and m.image for m in ts.baked_maps)]
        else:
            maps, missing_maps = visual_context.mesh_maps(ts)
        attachments.extend(maps)
    if reference:
        attachments.append(reference)
    return attachments, missing_maps


def compose(context, settings, captured, attachments, missing_maps, *, recent=(), models=()):
    """The outgoing request, assembled the same way for a real run and for the sharing summary."""
    from . import visual_context
    selected_model = "" if settings.model == "DEFAULT" else settings.model
    generate_textures = settings.generate_textures and settings.mode != "EXPLAIN"
    if generate_textures:
        captured["correction_enabled"] = True
    if captured["correction_enabled"] and settings.mode != "EXPLAIN" and not settings.send_view:
        raise schema.AuthoringError("Candidate review sends model images. Enable Send Current 3D View or disable Review Candidate Appearance")
    captured["context"]["image_generation"] = dict(enabled=generate_textures, max_images=MAX_IMAGES)
    from . import surface_observations
    from ..authoring import surface_field
    captured["context"]["generated_surface_workflow"] = dict(enabled=generate_textures,
        requires_surface_plan=generate_textures, requires_assigned_generated_source=generate_textures,
        requires_visual_critique=generate_textures, initial_correction_required=generate_textures,
        max_corrections=2, diagnostics_are_artistic_scores=False,
        primary_path='SURFACE_OBSERVATION_REPROJECTION',
        surface_space=dict(
            flow='plan_surface -> capture_surface_observations -> generate one image per observation '
                 '-> generate_surface_field -> preview_proposal -> inspect_surface_coverage / '
                 'inspect_texture_correspondence -> corrective observation or source correction '
                 '-> critique_candidate -> propose_changes',
            capture_tool='capture_surface_observations', packet_tool='inspect_surface_observation',
            coverage_tool='inspect_surface_coverage', correction_tool='inspect_surface_area',
            command='generate_surface_field',
            max_observations=surface_observations.MAX_OBSERVATIONS,
            max_observations_per_field=surface_field.MAX_OBSERVATIONS,
            projection='ORTHOGRAPHIC', image_origin='TOP_LEFT',
            correspondence='generated pixel -> observation pixel -> visible triangle -> barycentric point -> UV',
            fusion='facing quality, visibility confidence, projected resolution and silhouette confidence; '
                   'occluded observations contribute zero',
            rejected='pixels with no valid mesh hit, hits belonging to another texture set, occluded '
                     'surface, and anything outside the valid surface mask',
            derived_channel_fallback='Roughness and Height fall back to a luminance approximation only '
                                     'when no generated map is supplied for the observation',
            correction_types=['TEXTURE_SOURCE', 'SURFACE_OBSERVATION', 'MAPPING_FUSION'],
            projected_path='assign_projected_surface remains for intentionally decal-like or planar content'))
    from . import candidates
    captured["context"]["candidate_review"] = dict(enabled=captured["correction_enabled"],
        max_previews=candidates.MAX_PREVIEWS, max_corrections=candidates.MAX_PREVIEWS - 1,
        max_preview_seconds=candidates.MAX_SECONDS, total_generated_image_limit=MAX_IMAGES,
        commit_requires_matching_reviewed_candidate=captured["correction_enabled"],
        required_views=['front', 'back', 'left', 'right', 'top', 'bottom', 'close'],
        correspondence_tool='inspect_texture_correspondence',
        correspondence_coordinates='Normalized x/y with top-left origin in the selected candidate view',
        correction_flow='Inspect visible defect → classify its correction_type and LOCAL/GLOBAL scope → '
                        'for TEXTURE_SOURCE edit the existing source and use replace_surface_source; for '
                        'SURFACE_OBSERVATION capture a corrective observation with inspect_surface_area and '
                        'refuse the field; for MAPPING_FUSION change which observations the field fuses → '
                        'preview all views → critique → submit')
    captured["context"]["visual_inputs"] = visual_context.manifest(attachments)
    captured["context"]["visual_context"] = dict(view_sent=settings.send_view, maps_sent=settings.send_maps,
        missing_maps=missing_maps, reference_sent=bool(settings.reference_path.strip()),
        target_digest=captured["digest"])
    return dict(action="run", request_id=captured["id"], model=selected_model,
                generate_textures=generate_textures, scene_scope=captured["scene_scope"],
                images=[{k: item.get(k) for k in ("id", "label", "url")} for item in attachments],
                instructions=authoring.INSTRUCTIONS, tools=authoring.tools(captured["scene_scope"]),
                input=json.dumps(dict(mode=settings.mode, instruction=captured["prompt"],
                                      current_context=captured["context"], recent_changes=list(recent),
                                      recent_conversation=conversation(captured)), allow_nan=False))


def sharing_summary(payload):
    """What an outgoing request contains, read from the payload itself."""
    body = json.loads(payload["input"])
    context = body.get("current_context", {})
    lines = [f"Instruction ({len(body.get('instruction', ''))} characters), mode {body.get('mode', '').title()}"]
    summary_keys = [key for key in ("material", "materials", "mesh", "meshes", "scene_materials", "history")
                    if key in context]
    if summary_keys:
        lines.append("Material and mesh summaries: " + ", ".join(summary_keys))
    else:
        lines.append("Material summary: " + ", ".join(sorted(k for k in context if k not in
                     {"command_schema", "visual_inputs", "visual_context", "image_generation", "scope",
                      "scene_proposal_schema", "scene_limits"}))[:80])
    images = payload.get("images", [])
    views = [item["id"] for item in images if item["id"] in {"viewport", "target_view"}]
    maps = [item["id"].split(":", 1)[1] for item in images if item["id"].startswith("map:")]
    reference = any(item["id"] == "reference" for item in images)
    lines.append("Model views: " + (", ".join(views) if views else "none"))
    lines.append("Mesh maps: " + (", ".join(maps) if maps else "none"))
    lines.append("Reference image: " + ("yes" if reference else "no"))
    lines.append("Texture generation: " + ("allowed" if payload.get("generate_textures") else "off"))
    if body.get("recent_changes"):
        lines.append(f"Recent changes: {len(body['recent_changes'])} receipt(s)")
    return lines


def dry_payload(context):
    """The request the Run button would send now, without rendering or contacting anything."""
    settings = context.window_manager.open_painter_director
    ts = require_set(context)
    captured = capture(context, mode=settings.mode, scope=settings.scope, prompt=settings.prompt or "(instruction)")
    reference = (dict(id="reference", role="reference", label="Artist reference") if settings.reference_path.strip() else None)
    attachments, missing = attachments_for(context, ts, send_view=settings.send_view, send_maps=settings.send_maps,
                                           reference=reference, dry=True)
    recent = [dict(summary=r["summary"], changes=r["changes"][:30], reverted=r.get("reverted", False))
              for r in transactions.history(ts)[-3:]]
    payload = compose(context, settings, captured, attachments, missing, recent=recent)
    release(captured)
    return payload


def run(context):
    global request
    ensure_online(share_context=True)
    if not connection or not state["signed_in"]:
        raise schema.AuthoringError("Sign in with ChatGPT first")
    if request and request["running"]:
        raise schema.AuthoringError("Stop the current instruction before starting another")
    settings = context.window_manager.open_painter_director
    if settings.mode != "EXPLAIN":
        from .visual_context import require_geometry_maps
        targets = scene_authoring.materials(context.scene) if settings.scope == "SCENE" else [require_set(context)]
        require_geometry_maps(targets, send_maps=settings.send_maps, send_view=settings.send_view)
    captured = capture(context, mode=settings.mode, scope=settings.scope, prompt=settings.prompt)
    recent = [dict(summary=r["summary"], changes=r["changes"][:30], reverted=r.get("reverted", False))
              for r in transactions.history(require_set(context))[-3:]]
    selected_model = "" if settings.model == "DEFAULT" else settings.model
    from . import visual_context
    # Keep the appearance reference last so the native image tool can select
    # the latest image without accidentally treating a normal map as its style.
    reference = visual_context.reference(settings.reference_path) if settings.reference_path.strip() else None
    captured["visual_settings"] = dict(send_view=settings.send_view, send_maps=settings.send_maps, reference=reference)
    if captured["scene_scope"] and settings.send_view:
        from ..evaluation import shader_preview
        if any(not shader_preview.current(context.scene, item) for item in scene_authoring.materials(context.scene)):
            raise schema.AuthoringError("Wait for all scene material previews to finish before sending the scene view")
    attachments, missing_maps = attachments_for(context, require_set(context), **captured["visual_settings"])
    if attachments:
        model = next((m for m in state["models"] if m["id"] == selected_model or
                      (not selected_model and m["default"])), None)
        if model and "image" not in model["input_modalities"]:
            raise schema.AuthoringError("Select a model that accepts images to send the reference, model view or mesh maps")
    payload = compose(context, settings, captured, attachments, missing_maps, recent=recent)
    connection.send(payload)
    if request and not request["running"] and request["draft"]:
        remember(request, "discarded")
    release(request)
    request = captured
    state.update(status="Directing material…", response="", changes=[], outcome=None)
    progress.begin(captured["id"])
    images = f" with {len(attachments)} image{'s' if len(attachments) != 1 else ''}" if attachments else ""
    progress.step("Sent the instruction" + images, "SEND", busy=True)
    state["visual_inputs"] = visual_context.manifest(attachments)
    state["sharing"] = sharing_summary(payload)   # what actually went out, for the panel
    redraw()


def tool_call(event):
    if not request or not request["running"] or event.get("request_id") != request["id"]:
        raise schema.AuthoringError("This instruction is no longer active")
    window, scene, ts = target(request, require_active=False)
    name = event["tool"]
    spec = next((t["inputSchema"] for t in authoring.tools(request.get("scene_scope", False)) if t["name"] == name), None)
    if spec is None:
        raise schema.AuthoringError("Unknown Painter tool")
    arguments = event.get("arguments", {})
    if name in {"propose_changes", "propose_scene_changes"}:
        # An invalid replacement never leaves a previous draft ready to commit.
        request["draft"] = None
        state["changes"] = []
    schema.check(arguments, spec, name)
    if name in {"plan_surface", "critique_candidate"}:
        from ..authoring import surface_workflow
        if request["mode"] == "EXPLAIN":
            raise schema.AuthoringError("Explain mode cannot plan an authoring workflow")
        if name == "plan_surface":
            from ..authoring import regions
            for component in arguments["components"]:
                if component.get("region_id"):
                    part = next((item for item in scene.open_painter.texture_sets
                                 if item.id == component["texture_set_id"]), None)
                    if part is None:
                        raise schema.AuthoringError("Surface-plan material is unavailable")
                    regions.selector(part, component["region_id"], objects_for(request))
            return surface_workflow.store_plan(request, arguments)
        from . import candidates
        candidate = request.get("candidate")
        if not candidate:
            raise schema.AuthoringError("Preview a candidate before critiquing it")
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open the candidate's 3D View")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            candidates.reviewed(bpy.context, ts, request, candidate["proposal"], arguments["candidate_digest"])
        return surface_workflow.critique(request, arguments)
    if name == "get_context":
        if request.get("scene_scope"):
            return request["context"]
        return dict(authoring.capture(ts, request["bindings"], request["scope"], region_registry=request["region_registry"]), command_schema=schema.SCHEMA,
                    image_generation=request["context"].get("image_generation", dict(enabled=False, max_images=MAX_IMAGES)),
                    generated_surface_workflow=request["context"].get("generated_surface_workflow", {}),
                    candidate_review=request["context"].get("candidate_review", {}),
                    surface_plan=request.get("surface_plan"),
                    visual_inputs=request["context"].get("visual_inputs", []),
                    visual_context=request["context"].get("visual_context", {}))
    if name == "get_material_context":
        identifier = arguments["texture_set_id"]
        if identifier not in request["target_digests"]:
            raise schema.AuthoringError("Material is outside this instruction's scene scope")
        part = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open a 3D View to inspect scene materials")
        region = next(r for r in area.regions if r.type == "WINDOW")
        from . import visual_context
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            attachments, missing = attachments_for(bpy.context, part, whole_view=False, **request["visual_settings"])
        result = authoring.capture(part, request["bindings"], region_registry=request["region_registry"])
        result.update(visual_inputs=visual_context.manifest(attachments), missing_maps=missing,
                      _images=[{k: item[k] for k in ("id", "label", "url")} for item in attachments])
        request["inspected_materials"].add(identifier)
        state["status"] = f"Inspected {len(request['inspected_materials'])}/{len(request['target_digests'])} scene materials"
        return result
    if name == "search_library":
        return assets.search(arguments["query"])
    if name == "describe_asset":
        return assets.describe(arguments["asset_id"])
    if name == "list_generated_images":
        return dict(images=[{k: v for k, v in item.items() if k != "state_directory"}
                            for item in request["images"].values()])
    if name == 'inspect_texture_correspondence':
        if arguments['texture_set_id'] not in request['target_digests']:
            raise schema.AuthoringError('Material is outside this instruction scope')
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError('Open the candidate\'s 3D View')
        from . import correspondence
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            return correspondence.inspect(bpy.context, ts, request, arguments)
    if name == "inspect_surface_observation":
        from . import surface_observations
        return surface_observations.packet(request, arguments)
    if name in {"capture_surface_observations", "inspect_surface_coverage", "inspect_surface_area"}:
        if arguments["texture_set_id"] not in request["target_digests"]:
            raise schema.AuthoringError("Material is outside this instruction's scope")
        part = next(item for item in scene.open_painter.texture_sets if item.id == arguments["texture_set_id"])
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open a 3D View to observe the surface")
        region = next(r for r in area.regions if r.type == "WINDOW")
        from . import surface_observations
        handler = dict(capture_surface_observations=surface_observations.capture,
                       inspect_surface_coverage=surface_observations.surface_coverage,
                       inspect_surface_area=surface_observations.corrective)[name]
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            return handler(bpy.context, part, request, arguments, objects_for(request))
    if name in {"list_regions", "inspect_region", "inspect_surface", "inspect_surface_source", "query_region", "inspect_material_channels", "inspect_mask_coverage"}:
        identifier = arguments["texture_set_id"]
        if identifier not in request["target_digests"]:
            raise schema.AuthoringError("Material is outside this instruction's scope")
        part = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
        from ..authoring import regions
        if name == 'inspect_surface_source':
            from ..authoring import surface_maps
            allowed = commands.scope_ids(document.snapshot(part), request.get('scope', ''))
            if arguments['layer_id'] not in allowed:
                raise schema.AuthoringError('Surface layer is outside this instruction scope')
            return surface_maps.inspect(part, arguments['layer_id'])
        if name == 'inspect_surface':
            from ..authoring import surface_projection
            if request.get('surface_inspections', 0) >= 8:
                raise schema.AuthoringError('Surface template limit reached; reuse an inspected frame')
            result = surface_projection.inspect(part, arguments['region_id'], objects_for(request),
                arguments['normal_direction'], arguments['up_direction'])
            request['surface_inspections'] = request.get('surface_inspections', 0) + 1
            return result
        if name == "query_region":
            spec = {key: value for key, value in arguments.items() if key != "texture_set_id"}
            return regions.query(part, objects_for(request), spec, request["region_registry"])
        records = regions.capture(part, objects_for(request))
        if name == "list_regions":
            return dict(regions=records)
        if name == "inspect_region":
            found = next((record for record in records if record["id"] == arguments["region_id"]), None)
            if found is None:
                raise schema.AuthoringError("Region is outside the current material or its geometry changed")
            if request.get("visual_settings", {}).get("send_view", False):
                area = painter_area(request, window)
                if area is None:
                    raise schema.AuthoringError("Open a 3D View to inspect a region")
                from . import viewport
                selected = regions.selector(part, arguments["region_id"], objects_for(request))
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
                    url = viewport.capture(bpy.context, part, max_side=384, isolate=True, frame_target=True,
                        region_faces={selected["object"]: set(json.loads(selected["region_faces"]))},
                        region_topologies={selected["object"]: selected["region_topology"]})
                return dict(found, _images=[dict(id=found["id"], label="Exact assigned region — " + found["id"], url=url)])
            return found
        from . import candidates
        import time
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open a 3D View for material inspection")
        region = next(r for r in area.regions if r.type == "WINDOW")
        # No authored change: scratch channel outputs are discarded immediately.
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            with candidates.scratch(bpy.context, [], request["images"]) as temporary:
                deadline = time.monotonic() + 60
                candidates._ready(temporary, deadline)
                if name == "inspect_mask_coverage":
                    from ..authoring import coverage
                    copy = next(item for item in temporary.open_painter.texture_sets if item.id == identifier)
                    return coverage.inspect(copy, arguments["layer_id"])
                diagnostics, images = candidates.channel_diagnostics(temporary, {identifier}, deadline)
        return dict(channels=diagnostics, _images=images)
    if name == "report_inability":
        request["draft"] = None
        request["inability"] = arguments
        return dict(recorded=True, applied=False, **arguments)
    if name in {"inspect_candidate", "compare_candidates"}:
        from . import candidates
        candidate = request.get("candidate")
        if not candidate:
            raise schema.AuthoringError("There is no candidate for this instruction")
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open the candidate's 3D View")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            candidates.reviewed(bpy.context, ts, request, candidate["proposal"], arguments.get("candidate_digest", arguments.get("current_digest")))
        if name == "compare_candidates":
            previous = request.get("candidate_records", {}).get(arguments["previous_digest"])
            if previous is None or previous["digest"] == candidate["digest"]:
                raise schema.AuthoringError("Choose a different earlier candidate from this instruction")
            images = [dict(frame, id="candidate:compare:" + label + ":" + frame["id"], label=label + " — " + frame["label"])
                      for label, record in (("PREVIOUS", previous), ("CURRENT", candidate))
                      for frame in record["images"] if frame["id"] in {"candidate:front", "candidate:back"}]
            return dict(previous=previous["result"], current=candidate["result"], _images=images)
        return dict(candidate["result"], _images=candidate["images"])
    if request["mode"] == "EXPLAIN":
        raise schema.AuthoringError("Explain mode cannot propose changes")
    if name == "preview_proposal":
        from . import candidates
        if not request.get("correction_enabled", True):
            raise schema.AuthoringError("Candidate review is disabled for this instruction")
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open a 3D View for candidate review")
        region = next(r for r in area.regions if r.type == "WINDOW")
        request["draft"] = None
        state["status"] = f"Rendering candidate {request.get('preview_count', 0) + 1}/{candidates.MAX_PREVIEWS} in Eevee…"
        redraw()
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            result = candidates.preview(bpy.context, ts, request, arguments["proposal_json"])
        state["status"] = "Candidate ready — Codex is inspecting the material views and channels"
        return result
    if request.get("correction_enabled", True):
        from . import candidates
        from ..authoring.surface_workflow import require_review
        require_review(request)
        if not arguments.get("visual_review", "").strip():
            raise schema.AuthoringError("Inspect the candidate Eevee images and report concrete visual findings before submitting")
        area = painter_area(request, window)
        if area is None:
            raise schema.AuthoringError("Open the candidate's 3D View")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
            candidates.reviewed(bpy.context, ts, request, arguments["proposal_json"], arguments.get("candidate_digest"))
        request["reviewed_digest"] = arguments["candidate_digest"]
        request["visual_review"] = arguments["visual_review"]
    request["inability"] = None
    if name == "propose_scene_changes":
        proposal = scene_authoring.validate(arguments["proposal_json"])
        ids = {item["texture_set_id"] for item in proposal["targets"]}
        if not ids <= request["inspected_materials"]:
            raise schema.AuthoringError("Inspect every proposed material with get_material_context first")
        if request["mode"] == "CREATE" and ids != set(request["target_digests"]):
            raise schema.AuthoringError("Scene Create must cover every captured material; include all scene_materials in the coordinated proposal")
        compiled = scene_authoring.compile(scene, proposal, request["id"], request["target_digests"],
            objects=objects_for(request), images=request["images"], correspondences=request.get("correspondences"),
            observations=candidates_observations(request))
        request["draft"] = proposal
        state["changes"] = [part.name + ": " + change for part, _, c in compiled for change in c["changes"]]
        return dict(valid=True, applied=False, changes=state["changes"],
                    targets=[dict(texture_set_id=part.id, aliases=c["aliases"]) for part, _, c in compiled])
    arguments = schema.validate(arguments["proposal_json"])
    compiled = commands.compile_proposal(ts, arguments, request["id"], scope=request["scope"],
                                        objects=objects_for(request), images=request["images"],
                                        correspondences=request.get("correspondences"),
                                        observations=candidates_observations(request))
    request["draft"] = arguments
    state["changes"] = compiled["changes"]
    return dict(valid=True, applied=False, changes=compiled["changes"], aliases=compiled["aliases"])


def apply_draft(identifier):
    global request
    if not request or request["id"] != identifier or request["running"] or not request["draft"]:
        raise schema.AuthoringError("There is no completed draft to apply")
    _, scene, ts = target(request)
    from ..authoring.surface_workflow import require_review
    require_review(request)
    if request.get("correction_enabled", True):
        from . import candidates
        candidates.reviewed(bpy.context, ts, request, request["draft"], request.get("reviewed_digest"))
    if request.get("scene_scope"):
        receipt = scene_authoring.apply(scene, request["draft"], operation_id=request["id"],
            digests=request["target_digests"], objects=objects_for(request), images=request["images"], correspondences=request.get("correspondences"))
    else:
        receipt = transactions.apply(ts, request["draft"], operation_id=request["id"],
                                     expected_digest=request["digest"], scope=request["scope"],
                                     objects=objects_for(request), images=request["images"], correspondences=request.get("correspondences"))
    state.update(status="Applied: " + receipt["summary"], changes=receipt["changes"])
    terminal("APPLIED", "MATERIAL_COMMITTED", receipt_ids=[r["id"] for r in receipt.get("receipts", [receipt])])
    remember(request, "applied")
    release(request)
    request = None
    redraw()
    return receipt


def terminal(execution, cause_code, **details):
    """Stable machine-readable outcome survives request cleanup and UI messages."""
    state["outcome"] = dict(version=1, execution=execution, cause_code=cause_code,
        request_id=request["id"] if request else None,
        candidate_history=list(request.get("candidate_history", [])) if request else [],
        validation_errors=list(request.get("validation_errors", [])) if request else [],
        visual_review=request.get("visual_review") if request else None, **details)
    if request:
        state["outcome"].update(surface_plan=request.get("surface_plan"),
                                visual_critiques=request.get("visual_critiques", []))


def completed(event):
    global request
    if not request or event.get("request_id") != request["id"]:
        return
    request["running"] = False
    if not state["response"].strip() and progress.owns(request["id"]) and progress.notes:
        # Progress notes are kept out of the reply; a turn that only narrated still answers.
        state["response"] = "\n\n".join(note["text"].strip() for note in progress.notes)[-16000:]
    if event.get("status") != "completed":
        cancelled = event.get("status") == "interrupted"
        terminal("CANCELLED" if cancelled else "PROVIDER_FAILED",
                 "PROVIDER_INTERRUPTED" if cancelled else "PROVIDER_TERMINAL_FAILED", provider_error=event.get("error"))
        release(request)
        request = None
        reason = (event.get("error") or {}).get("message", "")
        state.update(status="Instruction ended without applying changes" + (": " + reason if reason else ""), changes=[])
        return
    if not request["draft"]:
        inability = request.get("inability")
        if inability:
            terminal("NEEDS_INPUT" if inability["code"] == "NEEDS_INPUT" else "VALIDATION_FAILED",
                     inability["code"], reason=inability["reason"])
        else:
            if request.get("validation_errors") and request["mode"] != "EXPLAIN":
                terminal("VALIDATION_FAILED", "NO_VALID_PROPOSAL")
            else:
                terminal("RESPONSE_ONLY", "EXPLANATION" if request["mode"] == "EXPLAIN" else "NO_PROPOSAL")
        remember(request, "explained" if request["mode"] == "EXPLAIN" else "response_only")
        state["status"] = "Textures generated; no material changes proposed" if request["images"] else "Ready"
        return
    remember(request, "draft_not_applied")
    if request["mode"] == "PLAN":
        terminal("RESPONSE_ONLY", "PLAN_DRAFT_READY")
        state["status"] = "Draft ready — review changes, then Apply Draft"
        return
    try:
        # A material edited during the turn is a routine stale draft, not a
        # connection fault: report it without dropping the Codex session.
        window, scene, _ = target(request)
    except schema.AuthoringError as exc:
        terminal("VALIDATION_FAILED", "STALE_INPUT", reason=str(exc))
        state["status"] = f"Draft not applied: {exc}"
        return
    area = painter_area(request, window)
    if area is None:
        terminal("NEEDS_INPUT", "VIEWPORT_UNAVAILABLE")
        state["status"] = "Draft ready — open a 3D View and Apply Draft"
        return
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
        result = bpy.ops.open_painter.director_apply("EXEC_DEFAULT", True, request_id=request["id"])
    if "FINISHED" not in result:
        # The operator stored the actual error in the status; keep it visible.
        state["status"] = "Draft not applied: " + (state["status"] or "review the reported error")


def step_label(event):
    name = event.get("tool", "")
    if name == "preview_proposal" and request:
        from .candidates import MAX_PREVIEWS
        return f"Rendering candidate {request.get('preview_count', 0) + 1}/{MAX_PREVIEWS} in Eevee"
    return tool_label(name)


_announced = {}   # call_id -> the progress step shown before a blocking tool ran


def announce(event):
    """Hold a blocking tool call for one tick so Blender draws its step first.

    Renders and material evaluation keep Blender's main thread busy for up to a few
    minutes; naming the step beforehand shows the artist why the interface paused.
    True while the call waits; tick() handles it next time."""
    if (event.get("event") != "tool" or event.get("tool") not in BLOCKING_TOOLS
            or event.get("call_id") in _announced or not progress.owns(event.get("request_id"))):
        return False
    _announced[event.get("call_id")] = progress.step(step_label(event), "WAIT", busy=True, tool=event.get("tool"))
    return True


def handle(event):
    global _model_items, request
    kind = event.get("event")
    if progress.owns(event.get("request_id")):
        progress.touch()   # Any sign of life, token usage included, counts as progress.
    if kind == "connected":
        state.update(connected=True, runtime=event["runtime"], status="Connected — sign in with ChatGPT")
    elif kind == "account":
        state.update(signed_in=event["connected"], plan=event.get("plan", ""), login=False,
                     status="Ready" if event["connected"] else "Connected — sign in with ChatGPT")
        if state.get("auto_login"):
            state["auto_login"] = False
            if not event["connected"] and connection:
                connection.send(dict(action="login"))
                state.update(status="Opening ChatGPT sign-in…", login=True)
    elif kind == "runtime_status":
        state["status"] = event.get("message", "Preparing Material Director…")
    elif kind == "models":
        state["models"] = event["models"]
        _model_items = [("DEFAULT", "Account default", "", 0)] + [(m["id"], m["name"], "", i + 1) for i, m in enumerate(event["models"]) if m["id"]]
        for wm in bpy.data.window_managers:
            if hasattr(wm, "open_painter_director"):
                wm.open_painter_director.model = "DEFAULT"
    elif kind == "limits":
        state["limits"] = event.get("limits", {})
    elif kind == "login":
        url = event.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "auth0.openai.com", "chatgpt.com"}:
            raise schema.AuthoringError("Codex returned an unsupported sign-in address")
        bpy.ops.wm.url_open(url=url)
        state.update(status="Finish signing in in your browser", login=True)
    elif kind == "tool":
        entry = _announced.pop(event.get("call_id"), None)
        if entry is None and progress.owns(event.get("request_id")):
            entry = progress.step(step_label(event), "TOOL", busy=True, tool=event.get("tool"))
        began = time.monotonic()
        try:
            result = tool_call(event)
            success = True
        except Exception as exc:
            result, success = dict(error=str(exc)[:1000]), False
            if request:
                request.setdefault("validation_errors", []).append(dict(tool=event.get("tool"), message=str(exc)[:1000]))
        progress.end(entry, time.monotonic() - began, "" if success else result["error"])
        images = result.pop("_images", []) if isinstance(result, dict) else []
        connection.send(dict(action="tool_result", call_id=event["call_id"], result=result, success=success, images=images))
    elif kind == "thinking" and progress.owns(event.get("request_id")):
        progress.think(event.get("item_id", ""), event.get("part", 0), event.get("text", ""))
    elif kind == "commentary" and progress.owns(event.get("request_id")):
        progress.note(event.get("item_id", ""), event.get("text", ""))
    elif kind == "turn_metadata" and progress.owns(event.get("request_id")):
        progress.time_limit = event.get("time_limit") or 0
        model = f" on {event['model']}" if event.get("model") else ""
        effort = f" · {event['reasoning_effort']} reasoning" if event.get("reasoning_effort") else ""
        progress.step("Codex started" + model + effort)
    elif kind == "model_rerouted" and progress.owns(event.get("request_id")):
        progress.step(f"Codex switched to {event.get('to_model') or 'another model'}"
                      + (f" ({event['reason']})" if event.get("reason") else ""))
    elif kind == "generated_image" and request and request["running"] and event.get("request_id") == request["id"]:
        from . import generated_images
        if len(request["images"]) >= generated_images.MAX_IMAGES:
            # The companion already enforces the generation limit; never drop the session over it.
            state["status"] = f"Ignored an extra generated texture ({MAX_IMAGES} per instruction)"
            return
        metadata = {k: event[k] for k in ("image_id", "width", "height", "prompt")}
        generated_images.read(connection.state_directory, metadata)
        request["images"][metadata["image_id"]] = dict(metadata, state_directory=connection.state_directory)
        state["status"] = f"Texture ready ({metadata['width']} × {metadata['height']}); building editable layers…"
        prompt = " ".join(str(metadata.get("prompt") or "").split())
        progress.step(f"Texture {len(request['images'])} ready ({metadata['width']} × {metadata['height']})"
                      + (": " + (prompt[:90] + "…" if len(prompt) > 90 else prompt) if prompt else ""), "IMAGE")
    elif kind == "image_status" and request and event.get("request_id") == request["id"]:
        state["status"] = event["message"]
        if progress.owns(event.get("request_id")):
            progress.step(event["message"], "IMAGE", busy=event["message"].startswith("Generating"))
    elif kind == "text" and request and event.get("request_id") == request["id"]:
        state["response"] = (state["response"] + event.get("text", ""))[-16000:]
    elif kind == "completed":
        completed(event)
        if progress.owns(event.get("request_id")):
            ending = {"completed": "completed", "interrupted": "stopped"}.get(event.get("status"), "failed")
            progress.finish(ending, state["status"])
    elif kind == "error":
        ends_request = bool(event.get("request_id"))
        if ends_request and (not request or event["request_id"] != request["id"]):
            return
        if ends_request:
            terminal("PROVIDER_FAILED", "PROVIDER_TRANSPORT_ERROR", reason=event.get("message", "Codex error"))
        if ends_request or event.get("fatal"):
            progress.finish("failed", event.get("message", "Codex error"))
        if event.get("fatal"):
            disconnect()
        elif ends_request:
            release(request)
            request = None
        if ends_request or event.get("fatal"):
            state["changes"] = []
        # An account or model request failing on its own leaves a running
        # turn and a reviewed draft alone; the companion still owns that turn.
        state["status"] = event.get("message", "Codex error")


def tick():
    if connection is None:
        return None
    changed = False
    try:
        ensure_online(share_context=bool(request and request["running"]))
        _pending.extend(connection.poll())
        while _pending and connection is not None:  # A fatal event in a batch disconnects.
            changed = True
            if announce(_pending[0]):
                break  # Draw the step now; the blocking call runs on the next tick.
            handle(_pending.pop(0))
    except Exception as exc:
        progress.finish("failed", str(exc)[:300])
        disconnect()
        state["status"] = str(exc)[:500]
        return None
    # The panel only reflects session state, which changes inside handle();
    # an idle connection must not keep every viewport redrawing. A running
    # instruction still redraws once a second so its elapsed time moves.
    if changed or (progress.running and time.monotonic() - _drawn >= 1):
        redraw()
    return .05 if request and request["running"] else .25


@persistent
def on_load(_):
    disconnect()
    progress.reset()
    state.update(response="", changes=[], limits={})


@persistent
def on_undo(_):
    stop()
    clear_conversation()
    state["status"] = "Scene history changed — run the next instruction with fresh context"


def register():
    for handlers, callback in ((bpy.app.handlers.load_pre, on_load),
                               (bpy.app.handlers.undo_pre, on_undo), (bpy.app.handlers.redo_pre, on_undo)):
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    disconnect()
    state.update(response="", changes=[], models=[], limits={})
    for handlers, callback in ((bpy.app.handlers.load_pre, on_load),
                               (bpy.app.handlers.undo_pre, on_undo), (bpy.app.handlers.redo_pre, on_undo)):
        if callback in handlers:
            handlers.remove(callback)
