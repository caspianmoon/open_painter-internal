"""Main-thread orchestration. A model turn produces a draft; one operator commits it."""

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

import bpy
from bpy.app.handlers import persistent

from ..authoring import assets, commands, context as authoring, document, schema, transactions, scene as scene_authoring
from ..core.project_manager import require_set
from .transport import Connection

connection = None
request = None
state = dict(status="Not connected", connected=False, signed_in=False, response="", changes=[],
             models=[], plan="", runtime="", login=False, limits={}, visual_inputs=[], sharing=[], outcome=None)
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
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def release(captured):
    """The request is over: its textures are packed in the .blend or unwanted."""
    from . import generated_images
    if captured:
        generated_images.discard(captured["images"].values())
        captured.pop("candidate", None)
        captured.pop("candidate_records", None)


def ensure_online():
    if not bpy.app.online_access:
        raise schema.AuthoringError("Enable Allow Online Access in Blender Preferences > System to connect Codex")
    prefs = preferences()
    if prefs is None or not prefs.director_share_context:
        raise schema.AuthoringError("Enable Share Material Context in Material Director connection settings first")


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
    connection = Connection(bpy.path.abspath(prefs.director_codex_binary) if prefs.director_codex_binary else "codex",
                            Path(directory), credentials=prefs.director_credentials)
    state.update(status="Connecting Codex…", response="", changes=[])
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=.05)
    redraw()


def disconnect():
    global connection, request, _model_items
    release(request)
    request = None
    clear_conversation()
    if connection:
        connection.close()
        connection = None
    state.update(status="Not connected", connected=False, signed_in=False, login=False,
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
    if captured["correction_enabled"] and settings.mode != "EXPLAIN" and not settings.send_view:
        raise schema.AuthoringError("Candidate review sends model images. Enable Send Current 3D View or disable Review Candidate Appearance")
    captured["context"]["image_generation"] = dict(enabled=generate_textures, max_images=4)
    from . import candidates
    captured["context"]["candidate_review"] = dict(enabled=captured["correction_enabled"],
        max_previews=candidates.MAX_PREVIEWS, max_corrections=candidates.MAX_PREVIEWS - 1,
        max_preview_seconds=candidates.MAX_SECONDS, total_generated_image_limit=4,
        commit_requires_matching_reviewed_candidate=captured["correction_enabled"])
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
    ensure_online()
    if not connection or not state["signed_in"]:
        raise schema.AuthoringError("Sign in with ChatGPT first")
    if request and request["running"]:
        raise schema.AuthoringError("Stop the current instruction before starting another")
    settings = context.window_manager.open_painter_director
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
    if name == "get_context":
        if request.get("scene_scope"):
            return request["context"]
        return dict(authoring.capture(ts, request["bindings"], request["scope"], region_registry=request["region_registry"]), command_schema=schema.SCHEMA,
                    image_generation=request["context"].get("image_generation", dict(enabled=False, max_images=4)),
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
    if name in {"list_regions", "inspect_region", "query_region", "inspect_material_channels", "inspect_mask_coverage"}:
        identifier = arguments["texture_set_id"]
        if identifier not in request["target_digests"]:
            raise schema.AuthoringError("Material is outside this instruction's scope")
        part = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
        from ..authoring import regions
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
            objects=objects_for(request), images=request["images"])
        request["draft"] = proposal
        state["changes"] = [part.name + ": " + change for part, _, c in compiled for change in c["changes"]]
        return dict(valid=True, applied=False, changes=state["changes"],
                    targets=[dict(texture_set_id=part.id, aliases=c["aliases"]) for part, _, c in compiled])
    arguments = schema.validate(arguments["proposal_json"])
    compiled = commands.compile_proposal(ts, arguments, request["id"], scope=request["scope"],
                                        objects=objects_for(request), images=request["images"])
    request["draft"] = arguments
    state["changes"] = compiled["changes"]
    return dict(valid=True, applied=False, changes=compiled["changes"], aliases=compiled["aliases"])


def apply_draft(identifier):
    global request
    if not request or request["id"] != identifier or request["running"] or not request["draft"]:
        raise schema.AuthoringError("There is no completed draft to apply")
    _, scene, ts = target(request)
    if request.get("correction_enabled", True):
        from . import candidates
        candidates.reviewed(bpy.context, ts, request, request["draft"], request.get("reviewed_digest"))
    if request.get("scene_scope"):
        receipt = scene_authoring.apply(scene, request["draft"], operation_id=request["id"],
            digests=request["target_digests"], objects=objects_for(request), images=request["images"])
    else:
        receipt = transactions.apply(ts, request["draft"], operation_id=request["id"],
                                     expected_digest=request["digest"], scope=request["scope"],
                                     objects=objects_for(request), images=request["images"])
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


def completed(event):
    global request
    if not request or event.get("request_id") != request["id"]:
        return
    request["running"] = False
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


def handle(event):
    global _model_items, request
    kind = event.get("event")
    if kind == "connected":
        state.update(connected=True, runtime=event["runtime"], status="Connected — sign in with ChatGPT")
    elif kind == "account":
        state.update(signed_in=event["connected"], plan=event.get("plan", ""), login=False,
                     status="Ready" if event["connected"] else "Connected — sign in with ChatGPT")
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
        try:
            result = tool_call(event)
            success = True
        except Exception as exc:
            result, success = dict(error=str(exc)[:1000]), False
            if request:
                request.setdefault("validation_errors", []).append(dict(tool=event.get("tool"), message=str(exc)[:1000]))
        images = result.pop("_images", []) if isinstance(result, dict) else []
        connection.send(dict(action="tool_result", call_id=event["call_id"], result=result, success=success, images=images))
    elif kind == "generated_image" and request and request["running"] and event.get("request_id") == request["id"]:
        from . import generated_images
        if len(request["images"]) >= generated_images.MAX_IMAGES:
            # The companion already stops a fifth generation; never drop the session over it.
            state["status"] = "Ignored an extra generated texture (four per instruction)"
            return
        metadata = {k: event[k] for k in ("image_id", "width", "height", "prompt")}
        generated_images.read(connection.state_directory, metadata)
        request["images"][metadata["image_id"]] = dict(metadata, state_directory=connection.state_directory)
        state["status"] = f"Texture ready ({metadata['width']} × {metadata['height']}); building editable layers…"
    elif kind == "image_status" and request and event.get("request_id") == request["id"]:
        state["status"] = event["message"]
    elif kind == "text" and request and event.get("request_id") == request["id"]:
        state["response"] = (state["response"] + event.get("text", ""))[-16000:]
    elif kind == "completed":
        completed(event)
    elif kind == "error":
        ends_request = bool(event.get("request_id"))
        if ends_request and (not request or event["request_id"] != request["id"]):
            return
        if ends_request:
            terminal("PROVIDER_FAILED", "PROVIDER_TRANSPORT_ERROR", reason=event.get("message", "Codex error"))
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
    try:
        ensure_online()
        events = connection.poll()
        for event in events:
            if connection is None:
                break  # A fatal event in this batch already disconnected.
            handle(event)
    except Exception as exc:
        disconnect()
        state["status"] = str(exc)[:500]
        return None
    # The panel only reflects session state, which changes inside handle();
    # an idle connection must not keep every viewport redrawing.
    if events:
        redraw()
    return .05 if request and request["running"] else .25


@persistent
def on_load(_):
    disconnect()
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
