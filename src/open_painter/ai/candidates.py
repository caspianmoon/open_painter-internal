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
from ..utils.digest import fingerprint, sha256 as fast_sha256

MAX_PREVIEWS = 3
MAX_SECONDS = 360
MAX_SIDE = 512
PREVIEW_RESOLUTION = 1024
CHANNELS = ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT", "NORMAL")
_STORES = tuple(name for name in ("scenes", "objects", "collections", "materials", "node_groups", "images",
           "meshes", "curves", "lights", "cameras", "worlds", "armatures", "metaballs", "lattices",
           "volumes", "hair_curves", "grease_pencils", "pointclouds") if hasattr(bpy.data, name))


def _pixel_digests(images, *, workers=4, in_flight=256 << 20):
    """BLAKE2b of each image's float pixels, keyed by image pointer.

    A fingerprint covers dozens of 2K float maps, so SHA-256 in Blender's Python took 20 s
    per fingerprint. Pixels are read here, on Blender's thread; hashing releases the GIL
    and runs on worker threads with at most ``in_flight`` bytes of copies alive."""
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    digests, pending, held = {}, [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for image in images:
            pixels = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(pixels)
            pending.append((image.as_pointer(), pixels.nbytes, pool.submit(lambda data: fingerprint(data).hexdigest(), pixels)))
            held += pixels.nbytes
            while pending and held > in_flight:
                pointer, size, future = pending.pop(0)
                digests[pointer] = future.result()
                held -= size
        for pointer, _, future in pending:
            digests[pointer] = future.result()
    return digests


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
                    digest = fingerprint()
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


def observations(captured):
    """Surface observations registered for this instruction, keyed by observation ID."""
    from .surface_observations import registry
    return {key: dict(value, observation_id=key) for key, value in registry(captured["id"]).items()}


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
                                          objects=objects, images=captured["images"], correspondences=captured.get("correspondences"),
                                          observations=observations(captured))
    else:
        proposal = schema.validate(proposal)
        compiled = [(ts, proposal, commands.compile_proposal(ts, proposal, captured["id"],
            scope=captured["scope"], objects=objects, images=captured["images"], correspondences=captured.get("correspondences"),
            observations=observations(captured)))]
    return proposal, compiled


def identity(context, captured, compiled, *, components=False, details=False):
    """Bind review to documents, actual source pixels, geometry and viewing conditions."""
    from ..core.layer_stack import snapshot
    from ..geometry.provenance import record
    from .viewport import framing_view, view_identity
    scene, space = context.scene, context.space_data
    review_view = framing_view(context)
    images = {}
    used_generated = set()

    def generated_references(value):
        if isinstance(value, str) and value.startswith('generated:'):
            used_generated.add(value)
        elif isinstance(value, bpy.types.Image):
            generated_references(value.get('director_image_id', ''))
        elif isinstance(value, dict):
            for child in value.values():
                generated_references(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                generated_references(child)

    for _, _, change in compiled:
        generated_references(change['after'])

    def collect(value):
        if isinstance(value, bpy.types.Image):
            images.setdefault(value.as_pointer(), value)
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
    digests = _pixel_digests(images.values())
    value = dict(version=1, request_id=captured["id"],
        documents=[(item.id, document.encode(c["after"], digest=True)) for item, _, c in compiled],
        original_documents=[(item.id, document.digest(item)) for item in scene.open_painter.texture_sets],
        geometry=[(item.id, record(item, "SCENE_FIELD")) for item in scene.open_painter.texture_sets],
        scene_geometry=_scene_geometry(context),
        images=[dict(name=image.name_full, size=list(image.size), color_space=image.colorspace_settings.name,
                     pixels=digests[pointer]) for pointer, image in images.items()],
        generated=[(key, value.get("width"), value.get("height"))
                                                 for key, value in sorted(captured["images"].items())
                                                 if key in used_generated],
        **view_identity(review_view), shading=shading,
        renderer=scene.render.engine, render=snapshot(scene.render), color=snapshot(scene.view_settings),
        world=(world.color[:] if world else None, world.use_nodes if world else None, world_graph),
        other_materials=visible_materials, collections=collections,
        view_layer_visibility=visibility(context.view_layer.layer_collection),
        frame=(scene.frame_current, scene.frame_subframe),
        lighting=[(o.name, [list(row) for row in o.matrix_world], snapshot(o.data), o.hide_render)
                  for o in scene.objects if o.type == "LIGHT"] )
    if components or details:
        summary = dict(geometry_digest=hash_value((value["geometry"], value["scene_geometry"], value["frame"])),
                    source_digest=hash_value((value["images"], value["generated"])),
                    renderer_digest=hash_value({k: value[k] for k in ("renderer", "render", "color", "world", "lighting", "shading", "view_layer_visibility")}),
                    view_digest=hash_value((value["view"], value["projection"])))
        if details:
            return dict(digest=hash_value(value), fields={key: hash_value(item) for key, item in value.items()},
                        components=summary)
        return summary
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
def scratch(context, compiled, images, *, preview_resolution=None):
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
            duplicate['open_painter_candidate_source'] = obj.name_full
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
                if preview_resolution is not None:
                    ts.resolution = str(min(int(ts.resolution), preview_resolution))
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
            # The detached proposal estimate cannot see every derived binding.
            # Check the constructed graph as well, before any Eevee draw/commit.
            from ..authoring.resource_budget import LIMIT
            graph = shader_preview.session_graph(scene, ts)
            actual = graph.bound_sampler_count() if graph else None
            if actual is not None and actual > LIMIT:
                raise schema.AuthoringError(f"Candidate shader binds {actual} image inputs; safe material budget is {LIMIT}. Reduce independent inputs before rendering.")
            ready = ready and shader_preview.current(scene, ts)
        if ready and not field_jobs.pending(scene) and not layer_cache.pending(scene):
            return
        field_jobs.tick(scene, budget=.02)
        layer_cache.tick(scene, budget=.02)
        if time.monotonic() > deadline:
            raise schema.AuthoringError("Candidate preview exceeded the instruction's preview time budget")


def channel_diagnostics(scene, ids, deadline, *, buffers=None):
    import numpy as np
    from ..core import image_manager
    from ..evaluation.evaluator import LayerEvaluator
    from ..export.padding import ownership
    from .visual_context import thumbnail
    from .surface_diagnostics import variation
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
        stride = max(1, int(np.ceil(int(ts.resolution) / 512)))
        if buffers is not None:
            buffers[ts.id] = dict(valid=valid[::stride, ::stride].copy(), channels={}, regions={})
        height_pixels = None
        for channel in CHANNELS:
            for _ in evaluator.channel_steps(ts.id, channel):
                if time.monotonic() > deadline:
                    raise schema.AuthoringError("Candidate channel inspection exceeded the preview time budget")
            image = image_manager.output(ts, channel).image
            pixels = np.asarray(image_manager.pixels(image), dtype=np.float32)
            if channel == 'HEIGHT':
                height_pixels = pixels.copy()
            if channel == 'NORMAL':
                from ..export.surface_normal import combined
                pixels = combined(ts, pixels, height_pixels)
            values = pixels[valid, :3]
            if not np.isfinite(values).all():
                raise schema.AuthoringError("Candidate produced non-finite " + channel + " values")
            entry["channels"][channel] = dict(min=float(values.min()), max=float(values.max()),
                mean=values.mean(axis=0).tolist(), dark_fraction=float((values.max(axis=1) < .01).mean()),
                sha256=fast_sha256(np.ascontiguousarray(values)).hexdigest())
            entry["channels"][channel]["surface_variation"] = variation(pixels, valid)
            if buffers is not None:
                buffers[ts.id]["channels"][channel] = pixels[::stride, ::stride].copy()
            preview_image = image
            if channel == 'NORMAL':
                # Inspect the relief that Eevee actually uses, not the flat authored
                # NORMAL input before Height Bump. Never overwrite that input.
                preview_image = bpy.data.images.new('Candidate combined normal', width=pixels.shape[1],
                                                     height=pixels.shape[0], float_buffer=True)
                preview_image.colorspace_settings.name = 'Non-Color'
                preview_image.pixels.foreach_set(pixels.ravel())
            try:
                preview = thumbnail(preview_image, side=512, budget=max(1024, min(90000, 600000 // (len(ids) * len(CHANNELS)))))
            finally:
                if preview_image != image:
                    bpy.data.images.remove(preview_image)
            label = 'NORMAL including height relief' if channel == 'NORMAL' else channel
            pictures.append(dict(id=ts.id + ":" + channel, label=ts.name + " — " + label + " (UV channel)", url=preview["url"]))
        if buffers is not None:
            from ..authoring.coverage import _buffer
            entry["regions"] = {}
            for layer in ts.layers:
                if not any(mask.enabled and mask.source == "MESH" for mask in layer.masks):
                    continue
                if len(entry["regions"]) >= 8:
                    break
                region = (_buffer(ts, layer)[::stride, ::stride] > .5) & buffers[ts.id]["valid"]
                if not region.any():
                    continue
                buffers[ts.id]["regions"][layer.id] = region
                entry["regions"][layer.id] = dict(name=layer.name,
                    covered_texel_fraction=float(region.sum() / max(1, buffers[ts.id]["valid"].sum())),
                    meaning="Authored mesh-region mask; names do not prove semantic recognition; UV-weighted coverage",
                    channels={key: variation(value, region) for key, value in buffers[ts.id]["channels"].items()})
    return result, pictures


def views(context, scene, compiled, *, focus=(), pixels_out=None, bypass=False, correspondence_out=None):
    import gpu
    import numpy as np
    from mathutils import Matrix, Vector
    from .viewport import png, framing_matrices, framing_view, capture, draw_view, principal_directions
    space, region = context.space_data, context.region
    if space.type != "VIEW_3D" or space.shading.type not in {"MATERIAL", "RENDERED"}:
        raise schema.AuthoringError("Open a Material Preview or Eevee rendered 3D view for candidate inspection")
    if region.type != "WINDOW":
        raise schema.AuthoringError("Candidate review needs a visible 3D viewport")
    points = [ref.object.matrix_world @ Vector(corner) for ts in scene.open_painter.texture_sets
              for ref in ts.assignments if ref.object for corner in ref.object.bound_box]
    review_view = framing_view(context)
    from types import SimpleNamespace
    # Six independent directions even when the user's viewport looks down from above.
    # Surface observations label their cameras from the same frame.
    matrices = []
    for label, direction in principal_directions(context):
        orientation = direction.to_track_quat('Z', 'Y').inverted().to_matrix().to_4x4()
        matrix, project = framing_matrices(SimpleNamespace(view_matrix=orientation,
            window_matrix=review_view.window_matrix), points, 1.)
        matrices.append((label, matrix, project, 512))
    view, projection = matrices[0][1:3]
    close = projection.copy()
    close[0][0] *= 1.8
    close[1][1] *= 1.8
    matrices.append(("close", view, close, 1024))
    if bypass:
        matrices = [item for item in matrices if item[0] in {"front", "close"}]
    else:
        for index, item in enumerate(focus[:3]):
            bounds = item["bounds_world"]
            corners = [Vector((x, y, z)) for x in (bounds["minimum"][0], bounds["maximum"][0])
                       for y in (bounds["minimum"][1], bounds["maximum"][1])
                       for z in (bounds["minimum"][2], bounds["maximum"][2])]
            region_view, region_projection = framing_matrices(review_view, corners, 1.)
            matrices.append(("component_" + str(index + 1), region_view, region_projection, 768))
    frames = []
    old_overlays = space.overlay.show_overlays
    try:
        space.overlay.show_overlays = False
        for label, matrix, project, render_side in matrices:
            offscreen = gpu.types.GPUOffScreen(render_side, render_side, format="RGBA8")
            try:
                draw_view(offscreen, scene, scene.view_layers[0], space, region, matrix, project)
                with offscreen.bind():
                    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, render_side, render_side, 4, 0, "UBYTE")
                    rgba = np.asarray(pixels, dtype=np.uint8).reshape(render_side, render_side, 4)[::-1]
                    depth = None
                    if correspondence_out is not None and not bypass:
                        depth = np.asarray(gpu.state.active_framebuffer_get().read_depth(
                            0, 0, render_side, render_side), dtype=np.float32).reshape(render_side, render_side)[::-1].copy()
                        # Eevee can composite a color-only quad into the offscreen
                        # target, leaving uniform quad depth (0.5 on Metal). That
                        # buffer is not surface evidence; retain geometric rays.
                        if not np.isfinite(depth).all() or np.ptp(depth) < 1e-7:
                            depth = None
                if pixels_out is not None:
                    pixels_out[label] = rgba.copy()
                side = render_side
                while True:
                    indices = np.minimum(render_side - 1, ((np.arange(side) + .5) * render_side / side).astype(int))
                    data = rgba[indices[:, None], indices].tobytes()
                    url = "data:image/png;base64," + base64.b64encode(png(side, side, data)).decode()
                    if len(url) <= (250000 if label == "close" else 150000) or side <= 128:
                        break
                    side = max(128, int(side * .75))
                frames.append(dict(id=("candidate:bypass:" if bypass else "candidate:") + label,
                    label=("Generated sources replaced by their means — " if bypass else "Candidate Eevee ") + label + " — uncommitted materials on actual geometry",
                    url=url))
                if correspondence_out is not None and not bypass:
                    correspondence_out[label] = dict(matrix=[list(row) for row in matrix],
                        projection=[list(row) for row in project], render_size=[render_side, render_side],
                        image_size=[side, side], origin='TOP_LEFT', depth=depth)
            finally:
                offscreen.free()
    finally:
        space.overlay.show_overlays = old_overlays
    targets = [ts for ts in scene.open_painter.texture_sets if ts.id in {part.id for part, _, _ in compiled}]
    for ts in ([] if bypass else targets[:8]):
        if ts.id not in {part.id for part, _, _ in compiled}:
            continue
        # A whole-object view cannot expose a small material slot. Use the same
        # exact assigned-face isolation as normal material inspection.
        url = capture(context, ts, max_side=192, isolate=True, frame_target=True)
        frames.append(dict(id="candidate:target:" + ts.id,
            label=ts.name + " — candidate on only its assigned faces", url=url))
    return frames


def bypass_generated(scene):
    """Remove generated spatial signals on the disposable copy, preserving means."""
    import numpy as np
    from ..evaluation.image_snapshots import read
    from ..evaluation.shader_preview import authoring_batch
    records = []
    for ts in scene.open_painter.texture_sets:
        with authoring_batch(ts):
            for layer in ts.layers:
                for prop in layer.channels:
                    for field in ("image", "detail_image"):
                        image = getattr(prop, field)
                        if image is None or not image.get("director_image_id"):
                            continue
                        mean = np.asarray(read({}, image, linear=True), np.float32).reshape(-1, 4).mean(axis=0)
                        records.append(dict(texture_set_id=ts.id, layer=layer.name, channel=prop.channel,
                                            source_id=image["director_image_id"], field=field))
                        setattr(prop, field, None)
                        if field == "image":
                            prop.color = tuple(float(v) for v in mean)
                            prop.value = float(mean[:3].mean())
                        prop.procedural = ""
                for mask in layer.masks:
                    image = mask.image
                    if image is None or not image.get("director_image_id"):
                        continue
                    if image.get("director_surface_field_channel") == "COVERAGE":
                        # Where a fused field has valid surface is geometry, not generated
                        # appearance. Averaging it would leak the layer onto unobserved
                        # surface and overstate the measured contribution.
                        continue
                    mean = float(np.asarray(read({}, image, linear=True), np.float32)[..., 0].mean())
                    replacement = bpy.data.images.new("Candidate generated mask mean", width=1, height=1, float_buffer=True)
                    replacement.colorspace_settings.name = "Non-Color"
                    replacement.pixels[:] = [mean, mean, mean, 1]
                    mask.image = replacement
                    records.append(dict(texture_set_id=ts.id, layer=layer.name, mask=mask.name,
                                        source_id=image["director_image_id"], field="mask"))
    return records


def preview(context, ts, captured, proposal):
    from ..authoring import surface_workflow
    from .surface_diagnostics import contribution
    generated_workflow = surface_workflow.enabled(captured)
    if generated_workflow and not captured.get("surface_plan"):
        raise schema.AuthoringError("Call plan_surface before previewing generated materials: include component histories and macro/meso/micro surface detail")
    if captured.get("preview_count", 0) >= MAX_PREVIEWS:
        raise schema.AuthoringError("Preview budget exhausted: one initial candidate and two corrections per instruction")
    elapsed = captured.get("preview_seconds", 0.)
    if elapsed >= MAX_SECONDS:
        raise schema.AuthoringError("Instruction preview time budget exhausted")
    proposal, compiled = compile_proposal(context.scene, ts, captured, proposal)
    if generated_workflow:
        if (captured.get("candidate") and not captured["candidate"].get("invalidated")
                and not captured["candidate"].get("critique")):
            raise schema.AuthoringError("Call critique_candidate on the current images before authoring a correction")
        def generated(value):
            return (isinstance(value, dict) and ("$generated" in value or "$surface_field" in value)) or (
                isinstance(value, bpy.types.Image) and bool(value.get("director_image_id")))
        if not any(generated(prop.get(field)) for _, _, c in compiled for layer in c["after"]["layers"]
                   for prop in [*layer["channels"], *layer["masks"]] for field in ("image", "detail_image")):
            raise schema.AuthoringError("Generate and assign actual surface sources before preview. Generate Textures cannot be satisfied by procedural layers alone")
    material_digest = hash_value([document.encode(c["after"], digest=True) for _, _, c in compiled])
    if (generated_workflow and not (captured.get("candidate") or {}).get("invalidated")
            and (captured.get("candidate") or {}).get("result", {}).get("material_digest") == material_digest):
        raise schema.AuthoringError("The correction did not change the material; address the visual critique before spending another preview")
    fingerprint_state = identity(context, captured, compiled, details=True)
    fingerprint = fingerprint_state["digest"]
    from ..authoring import regions
    bound = regions.bindings(authoring.resolve_bindings(captured["bindings"]), captured.get("region_registry", {}))
    planned = captured.get("surface_plan", {}).get("components", [])
    focus = []
    for part, _, _ in compiled:
        wanted = {component.get("region_id") for component in planned if component["texture_set_id"] == part.id}
        if wanted - {None}:
            focus.extend(item for item in regions.capture(part, bound) if item["id"] in wanted)
    # Failed rendering must not discard the last reviewable candidate or spend
    # a correction slot. Elapsed work still counts against the bounded budget.
    preview_number = captured.get("preview_count", 0) + 1
    started = time.monotonic()
    try:
        with scratch(context, compiled, captured["images"], preview_resolution=PREVIEW_RESOLUTION) as scene:
            deadline = started + MAX_SECONDS - elapsed
            _ready(scene, deadline)
            normal_buffers, normal_pixels = {}, {}
            channel_info, pictures = channel_diagnostics(scene, {part.id for part, _, _ in compiled}, deadline,
                                                         buffers=normal_buffers if generated_workflow else None)
            if time.monotonic() >= started + MAX_SECONDS - elapsed:
                raise schema.AuthoringError("Instruction preview time budget exhausted before Eevee capture")
            view_records = {}
            frames = views(context, scene, compiled, focus=focus, pixels_out=normal_pixels,
                           correspondence_out=view_records)
            from ..geometry import texture_correspondence
            geometry_records = texture_correspondence.capture(context, scene, captured['bindings'])
            generated_evidence = dict(status="NOT_REQUESTED")
            if generated_workflow:
                sources = bypass_generated(scene)
                if not sources:
                    raise schema.AuthoringError("Generate Textures requires an assigned generated surface source. Generate and assign real surface imagery; procedural layers alone cannot satisfy this setting")
                _ready(scene, deadline)
                bypass_buffers, bypass_pixels = {}, {}
                channel_diagnostics(scene, {part.id for part, _, _ in compiled}, deadline, buffers=bypass_buffers)
                frames += views(context, scene, compiled, bypass=True, pixels_out=bypass_pixels)
                generated_evidence = dict(status="MEASURED", sources=sources,
                    unique_source_count=len({item["source_id"] for item in sources}), materials={}, renders={},
                    bypass_method="Replace generated color/mask content with its source mean; remove generated detail variation. Preserve geometry, lights, mapping and remaining layers.",
                    artistic_score=None, limitations="UV-texel weighting and display-space render differences are evidence, not surface-area or aesthetic scores. Derived relief is approximate.")
                for identifier, original in normal_buffers.items():
                    other = bypass_buffers[identifier]
                    generated_evidence["materials"][identifier] = dict(
                        channels={key: contribution(value, other["channels"][key], original["valid"])
                                  for key, value in original["channels"].items()},
                        regions={region_id: dict(name=channel_info[identifier]["regions"][region_id]["name"],
                            channels={key: contribution(value, other["channels"][key], valid)
                                      for key, value in original["channels"].items()})
                            for region_id, valid in original["regions"].items()})
                import numpy as np
                for label, values in bypass_pixels.items():
                    original = normal_pixels[label]
                    # Includes background by design, disclosed to avoid treating
                    # empty viewport area as weak surface texture contribution.
                    measured = contribution(original.astype(np.float32)/255, values.astype(np.float32)/255,
                                            np.ones(values.shape[:2], bool))
                    measured["denominator"] = "Whole rendered frame including unchanged background"
                    generated_evidence["renders"][label] = measured
            if time.monotonic() >= started + MAX_SECONDS - elapsed:
                raise schema.AuthoringError("Eevee capture exceeded the instruction preview time budget")
        if identity(context, captured, compiled) != fingerprint:
            raise schema.AuthoringError("Artist state or visual inputs changed during candidate inspection; request a fresh candidate")
        captured["preview_count"] = preview_number
        result = dict(candidate_digest=fingerprint, applied=False, preview_number=preview_number,
            remaining_corrections=MAX_PREVIEWS - preview_number, renderer=context.scene.render.engine,
            preview_resolution_limit=PREVIEW_RESOLUTION,
            resolution_note="Disposable material evaluation capped at 1024 pixels for review. Authored texture-set resolution and source images are unchanged; inspect closeups before accepting fine detail.",
            material_digest=material_digest, generated_contribution=generated_evidence,
            correspondence_available=True, review_views=['front', 'back', 'left', 'right', 'top', 'bottom', 'close'],
            correspondence_coordinates='Normalized x/y, top-left origin in the named candidate image. Inspect visible seams, stretching or direction errors with inspect_texture_correspondence before correcting.',
            channels=channel_info, changes=[change for _, _, c in compiled for change in c["changes"]],
            aliases=[dict(texture_set_id=part.id, aliases=c["aliases"]) for part, _, c in compiled],
            review_required="Inspect all seven review_views against the request. Check seams, pattern continuity, feature endings, scale, direction, mirrored detail, stretching, side-dependent appearance, roughness/relief discontinuities and material leakage, as well as flat/procedural surfaces. Call inspect_texture_correspondence on visible correspondence defects before correction. Critique with reviewed_views, evidence IDs, category, LOCAL/GLOBAL scope and correspondence_id for those defects. Do not praise. Edit the existing source for local failures; rebuild and review all views before committing.",
            images=[dict(id=frame["id"], label=frame["label"], sha256=hashlib.sha256(frame["url"].encode()).hexdigest()) for frame in frames + pictures])
        result.update(fingerprint_state["components"])
        captured["candidate"] = dict(digest=fingerprint, identity_fields=fingerprint_state["fields"],
                                     proposal=proposal, result=result, images=frames + pictures,
                                     correspondence=dict(views=view_records, geometry=geometry_records))
        # Historic images remain comparable; only the current candidate needs a BVH/mesh snapshot.
        for previous in captured.get('candidate_records', {}).values():
            previous.pop('correspondence', None)
        captured.setdefault("candidate_records", {})[fingerprint] = captured["candidate"]
        captured.setdefault("candidate_history", []).append(result)
        return dict(result, _images=frames + pictures)
    finally:
        captured["preview_seconds"] = elapsed + time.monotonic() - started


def reviewed(context, ts, captured, proposal, digest):
    candidate = captured.get("candidate")
    if not candidate:
        raise schema.AuthoringError("No current candidate exists; call preview_proposal before reviewing or committing")
    if digest != candidate["digest"]:
        raise schema.AuthoringError("Candidate ID is superseded or incorrect; use the current candidate_digest: " + candidate["digest"])
    proposal, compiled = compile_proposal(context.scene, ts, captured, proposal)
    current = identity(context, captured, compiled, details=True)
    if digest != current["digest"]:
        fields = candidate.get("identity_fields", {})
        changed = [key for key, value in current["fields"].items() if fields.get(key) != value]
        if "documents" in changed and proposal != candidate["proposal"]:
            raise schema.AuthoringError("Proposal differs from the rendered material; call preview_proposal on the changed proposal before committing")
        candidate["invalidated"] = changed
        raise schema.AuthoringError("Candidate inputs changed: " + ", ".join(changed) +
            ". Call preview_proposal to render fresh evidence; the stale candidate does not require a critique. "
            "The existing preview budget still applies.")
    candidate.pop("invalidated", None)
    return proposal, compiled
