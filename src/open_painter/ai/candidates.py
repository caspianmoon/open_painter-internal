"""Disposable Eevee material candidates. No candidate is an artist transaction.

Only detached scenes own candidate objects, slots, shader graphs and outputs.
The provider receives images and diagnostics; it cannot supply scene settings.
"""

import base64
from contextlib import contextmanager
import hashlib
import json
import time

import bpy

from ..authoring import assets, commands, context as authoring, document, schema, scene as scene_authoring

MAX_PREVIEWS = 3
MAX_SECONDS = 180
MAX_SIDE = 512
CHANNELS = ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT", "NORMAL")
_STORES = tuple(name for name in ("scenes", "objects", "collections", "materials", "node_groups", "images",
           "meshes", "curves", "lights", "cameras", "worlds", "armatures", "metaballs", "lattices",
           "volumes", "hair_curves", "grease_pencils", "pointclouds") if hasattr(bpy.data, name))


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def _scene_geometry(context):
    """Read evaluated visible geometry, including untextured occluders and floors.

    Do not memoize temporary mesh pointers: Blender can reuse them after to_mesh_clear.
    This also binds pose/constraint changes that leave base mesh bytes unchanged.
    """
    import numpy as np
    graph = context.evaluated_depsgraph_get()
    result = []
    for obj in sorted(context.scene.objects, key=lambda value: value.name_full):
        evaluated = obj.evaluated_get(graph)
        entry = dict(name=obj.name_full, type=obj.type, matrix=[list(row) for row in evaluated.matrix_world],
            visible=obj.visible_get(view_layer=context.view_layer), hide_render=obj.hide_render,
            hide_viewport=obj.hide_viewport, hide=obj.hide_get(view_layer=context.view_layer),
            slots=[slot.material.name_full if slot.material else None for slot in obj.material_slots])
        if entry["visible"] and obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            try:
                mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
                if mesh is not None:
                    digest = hashlib.sha256()
                    for values, key, width, dtype in (
                        (mesh.vertices, "co", 3, np.float32),
                        (mesh.loops, "vertex_index", 1, np.int32),
                        (mesh.polygons, "loop_total", 1, np.int32),
                        (mesh.polygons, "material_index", 1, np.int32),
                        (mesh.corner_normals, "vector", 3, np.float32),
                    ):
                        buffer = np.empty(len(values) * width, dtype=dtype)
                        values.foreach_get(key, buffer)
                        digest.update(key.encode()); digest.update(buffer.tobytes())
                    for uv in mesh.uv_layers:
                        buffer = np.empty(len(uv.data) * 2, dtype=np.float32)
                        uv.data.foreach_get("uv", buffer)
                        digest.update(str((uv.name, uv.active_render)).encode()); digest.update(buffer.tobytes())
                    entry["mesh"] = digest.hexdigest()
            finally:
                evaluated.to_mesh_clear()
        result.append(entry)
    return result


def compile_proposal(scene, ts, captured, proposal):
    from ..authoring import regions
    objects = regions.bindings(authoring.resolve_bindings(captured["bindings"]), captured.setdefault("region_registry", {}))
    if captured.get("scene_scope"):
        proposal = scene_authoring.validate(proposal)
        ids = {item["texture_set_id"] for item in proposal["targets"]}
        if not ids <= captured["inspected_materials"]:
            raise schema.AuthoringError("Inspect every candidate material with get_material_context first")
        if captured["mode"] == "CREATE" and ids != set(captured["target_digests"]):
            raise schema.AuthoringError("Scene Create must include every captured material")
        compiled = scene_authoring.compile(scene, proposal, captured["id"], captured["target_digests"],
                                          objects=objects, images=captured["images"])
    else:
        proposal = schema.validate(proposal)
        compiled = [(ts, proposal, commands.compile_proposal(ts, proposal, captured["id"],
            scope=captured["scope"], objects=objects, images=captured["images"]))]
    return proposal, compiled


def identity(context, captured, compiled, *, components=False):
    """Bind review to documents, actual source pixels, geometry and viewing conditions."""
    import numpy as np
    from ..core.layer_stack import snapshot
    from ..geometry.provenance import record
    scene, space = context.scene, context.space_data
    images = {}

    def collect(value):
        if isinstance(value, bpy.types.Image):
            if value.as_pointer() in images:
                return
            pixels = np.empty(len(value.pixels), dtype=np.float32)
            value.pixels.foreach_get(pixels)
            images[value.as_pointer()] = dict(name=value.name_full, size=list(value.size),
                color_space=value.colorspace_settings.name, pixels=hashlib.sha256(pixels.tobytes()).hexdigest())
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    for item in scene.open_painter.texture_sets:
        collect(document.snapshot(item))
        collect([entry.image for entry in item.baked_maps if entry.image])
    shading = {p.identifier: getattr(space.shading, p.identifier) for p in space.shading.bl_rna.properties
               if p.type in {"BOOLEAN", "ENUM", "FLOAT", "INT", "STRING"} and not getattr(p, "is_array", False) and p.identifier != "rna_type"}
    seen = set()

    def nodes(tree):
        if tree is None or tree.as_pointer() in seen:
            return None
        seen.add(tree.as_pointer())
        values = []
        for node in tree.nodes:
            image = getattr(node, "image", None)
            collect(image)
            scalars = {p.identifier: getattr(node, p.identifier) for p in node.bl_rna.properties
                       if p.type in {"BOOLEAN", "ENUM", "FLOAT", "INT", "STRING"}
                       and p.identifier not in {"rna_type", "select", "name", "label", "width", "height"}
                       and not getattr(p, "is_array", False)}
            values.append((node.name, node.bl_idname, scalars, image.name_full if image else None,
                [(s.identifier, list(s.default_value) if hasattr(s.default_value, '__len__')
                  and not isinstance(s.default_value, str) else s.default_value)
                 for s in node.inputs if hasattr(s, "default_value")], nodes(getattr(node, "node_tree", None))))
        return dict(nodes=values, links=sorted((l.from_node.name, l.from_socket.identifier,
                                               l.to_node.name, l.to_socket.identifier) for l in tree.links))

    world = scene.world
    world_graph = nodes(world.node_tree) if world and world.use_nodes else None
    materials = {slot.material for obj in scene.objects for slot in obj.material_slots if slot.material}
    visible_materials = [(m.name_full, list(m.diffuse_color), m.use_nodes, nodes(m.node_tree))
                         for m in sorted(materials, key=lambda m: m.name_full)]
    collections = [(c.name_full, c.hide_render, c.hide_viewport, sorted(o.name_full for o in c.objects))
                   for c in scene.collection.children_recursive]
    def visibility(layer):
        return (layer.collection.name_full, layer.exclude, layer.holdout, layer.indirect_only,
                layer.hide_viewport, [visibility(child) for child in layer.children])
    value = dict(version=1, request_id=captured["id"],
        documents=[(item.id, document.encode(c["after"], digest=True)) for item, _, c in compiled],
        original_documents=[(item.id, document.digest(item)) for item in scene.open_painter.texture_sets],
        geometry=[(item.id, record(item, "SCENE_FIELD")) for item in scene.open_painter.texture_sets],
        scene_geometry=_scene_geometry(context),
        images=list(images.values()), generated=[(key, value.get("width"), value.get("height"))
                                                 for key, value in sorted(captured["images"].items())],
        view=[list(row) for row in space.region_3d.view_matrix],
        projection=[list(row) for row in space.region_3d.window_matrix], shading=shading,
        renderer=scene.render.engine, render=snapshot(scene.render), color=snapshot(scene.view_settings),
        world=(world.color[:] if world else None, world.use_nodes if world else None, world_graph),
        other_materials=visible_materials, collections=collections,
        view_layer_visibility=visibility(context.view_layer.layer_collection),
        frame=(scene.frame_current, scene.frame_subframe),
        lighting=[(o.name, [list(row) for row in o.matrix_world], snapshot(o.data), o.hide_render)
                  for o in scene.objects if o.type == "LIGHT"] )
    if components:
        return dict(geometry_digest=hash_value((value["geometry"], value["scene_geometry"], value["frame"])),
                    source_digest=hash_value((value["images"], value["generated"])),
                    renderer_digest=hash_value({k: value[k] for k in ("renderer", "render", "color", "world", "lighting", "shading", "view_layer_visibility")}),
                    view_digest=hash_value((value["view"], value["projection"])))
    return hash_value(value)


def _settings(source, target):
    """Copy writable scalar settings; no context, scene pointers or collections."""
    for prop in source.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type":
            continue
        value = getattr(source, key)
        if prop.type == "POINTER" and value is not None and not isinstance(value, bpy.types.ID):
            other = getattr(target, key, None)
            if other is not None:
                _settings(value, other)
        elif not prop.is_readonly and prop.type not in {"POINTER", "COLLECTION"}:
            try:
                setattr(target, key, value[:] if getattr(prop, "is_array", False) else value)
            except (AttributeError, TypeError, ValueError):
                # Dynamic enums may describe another render engine; the active
                # engine and color-management values are verified explicitly.
                pass


@contextmanager
def scratch(context, compiled, images):
    """Copy scene geometry and authoring data, and always remove every owned ID."""
    from ..core.layer_stack import snapshot
    from ..core import material_manager
    from ..evaluation import cache, shader_preview
    before = {name: {v.as_pointer() for v in getattr(bpy.data, name)} for name in _STORES}
    scene = None
    try:
        source = context.scene
        if source.render.engine not in {"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}:
            raise schema.AuthoringError("Candidate review requires an Eevee scene; renderer changes are outside Director's tools")
        scene = bpy.data.scenes.new("Open Painter candidate (temporary)")
        scene["open_painter_candidate"] = True
        _settings(source.render, scene.render)
        scene.render.engine = source.render.engine
        _settings(source.view_settings, scene.view_settings)
        _settings(source.display_settings, scene.display_settings)
        _settings(source.unit_settings, scene.unit_settings)
        if hasattr(source, "eevee"):
            _settings(source.eevee, scene.eevee)
        scene.world = source.world  # Read-only input, never authored or repaired.
        scene.frame_set(source.frame_current, subframe=source.frame_subframe)
        objects, materials, collections = {}, {}, {}
        for obj in source.objects:
            duplicate = obj.copy()
            if obj.data:
                duplicate.data = obj.data.copy()
            objects[obj] = duplicate
            duplicate.hide_viewport = obj.hide_viewport or obj.hide_get()

        def copy_collection(original, parent=None):
            if original in collections:
                duplicate = collections[original]
                if parent is not None and duplicate.name not in parent.children:
                    parent.children.link(duplicate)
                return duplicate
            duplicate = scene.collection if parent is None else bpy.data.collections.new("Candidate " + original.name)
            collections[original] = duplicate
            if parent is not None:
                parent.children.link(duplicate)
            duplicate.hide_viewport, duplicate.hide_render = original.hide_viewport, original.hide_render
            for obj in original.objects:
                if obj in objects:
                    duplicate.objects.link(objects[obj])
            for child in original.children:
                copy_collection(child, duplicate)
            return duplicate

        copy_collection(source.collection)
        scene.view_layers[0].update()

        def layer_visibility(original, duplicate):
            if original is None or duplicate is None:
                raise schema.AuthoringError("Candidate view-layer hierarchy could not be initialized")
            duplicate.exclude, duplicate.holdout, duplicate.indirect_only = original.exclude, original.holdout, original.indirect_only
            duplicate.hide_viewport = original.hide_viewport
            for child in original.children:
                other = next((item for item in duplicate.children if item.collection == collections.get(child.collection)), None)
                if other is not None:
                    layer_visibility(child, other)

        layer_visibility(context.view_layer.layer_collection, scene.view_layers[0].layer_collection)
        for original, duplicate in objects.items():
            if original.parent:
                duplicate.parent = objects.get(original.parent)
            pointer_owners = list(duplicate.modifiers) + list(duplicate.constraints)
            if duplicate.pose:
                pointer_owners.extend(constraint for bone in duplicate.pose.bones for constraint in bone.constraints)
            for owner in pointer_owners:
                for prop in owner.bl_rna.properties:
                    if prop.type == "POINTER" and not prop.is_readonly:
                        value = getattr(owner, prop.identifier)
                        if isinstance(value, bpy.types.Object) and value in objects:
                            setattr(owner, prop.identifier, objects[value])
                        elif isinstance(value, bpy.types.Collection) and value in collections:
                            setattr(owner, prop.identifier, collections[value])
            if original.instance_collection in collections:
                duplicate.instance_collection = collections[original.instance_collection]
            duplicate.matrix_world = original.matrix_world
        scene.camera = objects.get(source.camera)
        for ts in source.open_painter.texture_sets:
            if ts.material and ts.material not in materials:
                material = bpy.data.materials.new("Open Painter candidate material")
                from ..constants import OWNER
                material[OWNER] = True
                material["texture_set_id"] = ts.id
                materials[ts.material] = material

        def remap(value, path=()):
            if isinstance(value, bpy.types.Object):
                return objects.get(value, value)
            if isinstance(value, bpy.types.Material):
                return materials.get(value, value)
            if isinstance(value, bpy.types.Collection):
                return collections.get(value, value)
            if isinstance(value, bpy.types.Image) and ("outputs" in path or "influence" in path):
                return None  # Disposable evaluated images must not share artist pixels.
            if isinstance(value, dict):
                return {k: remap(v, (*path, k)) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v, path) for v in value]
            return value

        saved = snapshot(source.open_painter)
        document.reconcile(scene.open_painter, remap(saved))
        desired = {ts.id: assets.prepare(c["after"], images=images) for ts, _, c in compiled}
        for ts in scene.open_painter.texture_sets:
            with shader_preview.authoring_batch(ts):
                if ts.id in desired:
                    document.apply(ts, remap(desired[ts.id]))
                ts.director_history = "[]"
                ts.preview_backend = "LIVE"
                material_manager.relink_slots(ts)
        scene.frame_set(source.frame_current, subframe=source.frame_subframe)
        scene.view_layers[0].update()
        with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            yield scene
    finally:
        # Close graph jobs before invalidating their RNA. Prune only our scene;
        # active artist sessions stay attached and retain their scheduler state.
        if scene is not None:
            from ..evaluation import field_jobs
            field_jobs.discard_scene(scene)
        live = frozenset((s.as_pointer(), ts.id) for s in bpy.data.scenes if s != scene
                         for ts in s.open_painter.texture_sets)
        cache._prune(live, time.monotonic())
        # Objects before their data; graphs before backing images. All candidates
        # are synchronous and gone before Blender can save or accept another edit.
        for name in ("objects", "scenes", "collections", "materials", "node_groups",
                     *(key for key in _STORES if key not in {"objects", "scenes", "collections", "materials", "node_groups", "images"}),
                     "images"):
            store = getattr(bpy.data, name)
            for value in list(store):
                if value.as_pointer() not in before[name]:
                    store.remove(value, do_unlink=True)
        cache._prune(live, time.monotonic() + 1.01)


def _ready(scene, deadline):
    from ..evaluation import shader_preview, field_jobs, layer_cache
    while True:
        ready = True
        for ts in scene.open_painter.texture_sets:
            shader_preview.tick(scene, ts)
            info = shader_preview.diagnostics(scene, ts)
            if info.get("error"):
                raise schema.AuthoringError("Candidate shader failed: " + info["error"])
            ready = ready and shader_preview.current(scene, ts)
        if ready and not field_jobs.pending(scene):
            return
        field_jobs.tick(scene, budget=.02)
        layer_cache.tick(scene, budget=.02)
        if time.monotonic() > deadline:
            raise schema.AuthoringError("Candidate preview exceeded the instruction's preview time budget")


def channel_diagnostics(scene, ids, deadline):
    import numpy as np
    from ..core import image_manager
    from ..evaluation.evaluator import LayerEvaluator
    from ..export.padding import ownership
    from .visual_context import thumbnail
    result, pictures = {}, []
    evaluator = LayerEvaluator(scene)
    for ts in scene.open_painter.texture_sets:
        if ts.id not in ids:
            continue
        valid = ownership(ts, int(ts.resolution)).covered
        entry = dict(valid_texels=int(valid.sum()), channels={})
        result[ts.id] = entry
        if not valid.any():
            raise schema.AuthoringError("Candidate has no valid UV samples")
        for channel in CHANNELS:
            for _ in evaluator.channel_steps(ts.id, channel):
                if time.monotonic() > deadline:
                    raise schema.AuthoringError("Candidate channel inspection exceeded the preview time budget")
            image = image_manager.output(ts, channel).image
            values = np.asarray(image_manager.pixels(image), dtype=np.float32)[valid, :3]
            if not np.isfinite(values).all():
                raise schema.AuthoringError("Candidate produced non-finite " + channel + " values")
            entry["channels"][channel] = dict(min=float(values.min()), max=float(values.max()),
                mean=values.mean(axis=0).tolist(), dark_fraction=float((values.max(axis=1) < .01).mean()),
                sha256=hashlib.sha256(values.tobytes()).hexdigest())
            preview = thumbnail(image, side=128, budget=20000)
            pictures.append(dict(id=ts.id + ":" + channel, label=ts.name + " — " + channel + " (UV channel)", url=preview["url"]))
    return result, pictures


def views(context, scene, compiled):
    import gpu
    import numpy as np
    from mathutils import Matrix, Vector
    from .viewport import png, framing_matrices, capture
    space, region = context.space_data, context.region
    if space.type != "VIEW_3D" or space.shading.type not in {"MATERIAL", "RENDERED"}:
        raise schema.AuthoringError("Open a Material Preview or Eevee rendered 3D view for candidate inspection")
    if region.type != "WINDOW":
        raise schema.AuthoringError("Candidate review needs a visible 3D viewport")
    points = [ref.object.matrix_world @ Vector(corner) for ts in scene.open_painter.texture_sets
              for ref in ts.assignments if ref.object for corner in ref.object.bound_box]
    center = sum(points, Vector()) / len(points)
    view, projection = framing_matrices(space.region_3d, points, 1.)
    matrices = [("front", view), ("back", view @ Matrix.Translation(center) @ Matrix.Rotation(3.141592653589793, 4, 'Z') @ Matrix.Translation(-center))]
    frames = []
    old_overlays = space.overlay.show_overlays
    try:
        space.overlay.show_overlays = False
        for label, matrix in matrices:
            offscreen = gpu.types.GPUOffScreen(MAX_SIDE, MAX_SIDE, format="RGBA8")
            try:
                offscreen.draw_view3d(scene, scene.view_layers[0], space, region, matrix, projection,
                                     do_color_management=True, draw_background=True)
                with offscreen.bind():
                    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, MAX_SIDE, MAX_SIDE, 4, 0, "UBYTE")
                    rgba = np.asarray(pixels, dtype=np.uint8).reshape(MAX_SIDE, MAX_SIDE, 4)[::-1]
                side = MAX_SIDE
                while True:
                    indices = np.minimum(MAX_SIDE - 1, ((np.arange(side) + .5) * MAX_SIDE / side).astype(int))
                    data = rgba[indices[:, None], indices].tobytes()
                    url = "data:image/png;base64," + base64.b64encode(png(side, side, data)).decode()
                    if len(url) <= 150000 or side <= 128:
                        break
                    side = max(128, int(side * .75))
                frames.append(dict(id="candidate:" + label, label="Candidate Eevee " + label + " — uncommitted materials on actual geometry",
                    url=url))
            finally:
                offscreen.free()
    finally:
        space.overlay.show_overlays = old_overlays
    for ts in scene.open_painter.texture_sets:
        if ts.id not in {part.id for part, _, _ in compiled}:
            continue
        # A whole-object view cannot expose a small material slot. Use the same
        # exact assigned-face isolation as normal material inspection.
        url = capture(context, ts, max_side=192, isolate=True, frame_target=True)
        frames.append(dict(id="candidate:target:" + ts.id,
            label=ts.name + " — candidate on only its assigned faces", url=url))
    return frames


def preview(context, ts, captured, proposal):
    if captured.get("preview_count", 0) >= MAX_PREVIEWS:
        raise schema.AuthoringError("Preview budget exhausted: one initial candidate and two corrections per instruction")
    elapsed = captured.get("preview_seconds", 0.)
    if elapsed >= MAX_SECONDS:
        raise schema.AuthoringError("Instruction preview time budget exhausted")
    proposal, compiled = compile_proposal(context.scene, ts, captured, proposal)
    fingerprint = identity(context, captured, compiled)
    captured["preview_count"] = captured.get("preview_count", 0) + 1
    captured["candidate"] = None
    started = time.monotonic()
    try:
        with scratch(context, compiled, captured["images"]) as scene:
            _ready(scene, started + MAX_SECONDS - elapsed)
            channel_info, pictures = channel_diagnostics(scene, {part.id for part, _, _ in compiled}, started + MAX_SECONDS - elapsed)
            if time.monotonic() >= started + MAX_SECONDS - elapsed:
                raise schema.AuthoringError("Instruction preview time budget exhausted before Eevee capture")
            frames = views(context, scene, compiled)
            if time.monotonic() >= started + MAX_SECONDS - elapsed:
                raise schema.AuthoringError("Eevee capture exceeded the instruction preview time budget")
        if identity(context, captured, compiled) != fingerprint:
            raise schema.AuthoringError("Artist state or visual inputs changed during candidate inspection; request a fresh candidate")
        result = dict(candidate_digest=fingerprint, applied=False, preview_number=captured["preview_count"],
            remaining_corrections=MAX_PREVIEWS - captured["preview_count"], renderer=context.scene.render.engine,
            channels=channel_info, changes=[change for _, _, c in compiled for change in c["changes"]],
            aliases=[dict(texture_set_id=part.id, aliases=c["aliases"]) for part, _, c in compiled],
            review_required="Inspect the returned Eevee images AND channels against the instruction; correct failures with another complete proposal, then submit this exact candidate digest.",
            images=[dict(id=frame["id"], label=frame["label"], sha256=hashlib.sha256(frame["url"].encode()).hexdigest()) for frame in frames + pictures])
        result.update(identity(context, captured, compiled, components=True))
        captured["candidate"] = dict(digest=fingerprint, proposal=proposal, result=result, images=frames + pictures)
        captured.setdefault("candidate_records", {})[fingerprint] = captured["candidate"]
        captured.setdefault("candidate_history", []).append(result)
        return dict(result, _images=frames + pictures)
    finally:
        captured["preview_seconds"] = elapsed + time.monotonic() - started


def reviewed(context, ts, captured, proposal, digest):
    proposal, compiled = compile_proposal(context.scene, ts, captured, proposal)
    candidate = captured.get("candidate")
    if not candidate or digest != candidate["digest"] or digest != identity(context, captured, compiled):
        raise schema.AuthoringError("This proposal has no matching current candidate preview; preview and inspect it before committing")
    return proposal, compiled
