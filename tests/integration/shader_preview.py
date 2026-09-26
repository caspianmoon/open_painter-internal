"""Engine authoring: visible edits, no CPU composite, portable recipe.

Run in Blender background mode. Render timings are deliberately not presented as
interactive latency; shader_preview_ui.py measures completed viewport frames.
The observation hooks below call the real functions and never block raster work.
"""

from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import traceback
from unittest.mock import patch

import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.core import material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, live_preview, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id

REPORT = Path(os.environ.get("OP_SHADER_REPORT", "/private/tmp/op-shader-preview.json"))


class RasterObservation:
    """Passive instrumentation, including work submitted by normal cache timers."""

    def __enter__(self):
        self.calls = Counter()
        self.context = ExitStack()
        for owner, name, label in (
            (LayerEvaluator, "channel_steps", "cpu_channel_steps"),
            (live_preview, "steps", "legacy_gpu_composite"),
            (images, "write", "image_write"),
            (images, "pack_image", "pack_image"),
        ):
            original = getattr(owner, name)

            def observed(*args, _original=original, _label=label, **kwargs):
                self.calls[_label] += 1
                return _original(*args, **kwargs)

            self.context.enter_context(patch.object(owner, name, observed))
        self.metrics = self.context.enter_context(Metrics())
        return self

    def __exit__(self, *exc):
        return self.context.__exit__(*exc)

    def report(self):
        return {"observed_calls": dict(self.calls), **self.metrics.report()}

    def assert_no_raster(self):
        for key in ("cpu_channel_steps", "legacy_gpu_composite", "image_write"):
            assert self.calls[key] == 0, self.report()
        for phase in ("source_generation", "masking", "filters", "blending", "image_write"):
            assert self.metrics.phases[phase]["calls"] == 0, self.report()
        for key in ("shader_resource_requests", "shader_resource_publications", "shader_resource_written_bytes"):
            assert self.metrics.counts[key] == 0, self.report()


def image_hash(image):
    return hashlib.sha256(images.pixels(image).tobytes()).hexdigest()


def output_state(ts):
    """The CPU reference's publications: the engine writes the images, never a revision."""
    return {out.channel: (out.revision, tuple(out.image.size)) for out in ts.outputs}


def graph_state(material):
    """Capture actual Blender node identities and parameter sockets recursively."""
    identities, values, visited = [], {}, set()

    def visit(tree):
        pointer = tree.as_pointer()
        if pointer in visited:
            return
        visited.add(pointer)
        for node in tree.nodes:
            identities.append((pointer, node.as_pointer(), node.bl_idname))
            sockets = [("input", index, socket) for index, socket in enumerate(node.inputs)]
            sockets += [("output", index, socket) for index, socket in enumerate(node.outputs)]
            for direction, index, socket in sockets:
                if hasattr(socket, "default_value"):
                    value = socket.default_value
                    try:
                        value = tuple(value)
                    except TypeError:
                        pass
                    values[(pointer, node.as_pointer(), direction, index)] = repr(value)
            if node.type == "GROUP" and node.node_tree:
                visit(node.node_tree)
    visit(material.node_tree)
    return tuple(sorted(identities)), values


def ready(scene, ts, timeout=30):
    """Initial/structural compatibility resources may legitimately take work."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        shader_preview.ensure(scene, ts)
        cache.tick()
        diagnostic = shader_preview.diagnostics(scene, ts)
        assert not diagnostic.get("error"), diagnostic
        if shader_preview.current(scene, ts) and not diagnostic.get("pending_resources"):
            return diagnostic
        time.sleep(.01)
    raise AssertionError(f"Shader resources never became ready: {diagnostic}")


def fixture(scene, resolution=512):
    """Deterministic editable images are fixtures, not generated production art."""
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    plane.name = "Shader acceptance surface"
    ts = sets.create(scene, "Shader acceptance", resolution)
    sets.assign(scene, ts, plane)
    assert ts.preview_backend == "LIVE"
    base = stack.add(ts, "FILL", "Blue substrate")
    stack.channel_for(base, "BASE_COLOR").color = (.025, .09, .24, 1)
    stack.channel_for(base, "ROUGHNESS").value = .65
    coat = stack.add(ts, "FILL", "Red masked coating")
    stack.channel_for(coat, "BASE_COLOR").color = (.58, .08, .025, 1)
    stack.channel_for(coat, "ROUGHNESS").value = .4
    coat.opacity = .68
    n = 64
    y, x = np.indices((n, n), dtype=np.float32)
    source = images.create("Acceptance seeded scalar", n)
    random = np.random.default_rng(19).uniform(.15, .85, (8, 8)).astype(np.float32)
    scalar = np.repeat(np.repeat(random, 8, axis=0), 8, axis=1)
    data = np.ones((n, n, 4), np.float32)
    data[..., :3] = scalar[..., None]
    images.write(source, data)
    mask = coat.masks.add()
    mask.id, mask.source, mask.image = new_id(), "FILL", source
    mask.black, mask.white = .08, .92
    filtered = coat.masks.add()
    filtered.id, filtered.source = new_id(), "FILTER"
    filtered.effect.kind = "LEVELS"
    filtered.effect.gamma = .9
    height = stack.channel_for(coat, "HEIGHT")
    height.enabled, height.image = True, source
    levels = height.effects.add()
    levels.kind, levels.output_black, levels.output_white = "LEVELS", .51, .59
    wear = stack.add(ts, "FILL", "Curvature wear")
    stack.channel_for(wear, "BASE_COLOR").color = (.63, .49, .19, 1)
    stack.channel_for(wear, "ROUGHNESS").value = .52
    curvature = images.create("Acceptance curvature", n)
    data[..., :3] = np.clip(1 - np.abs(x - 32) / 14, 0, 1)[..., None]
    images.write(curvature, data)
    mesh_map = ts.baked_maps.add()
    mesh_map.kind, mesh_map.image = "CURVATURE", curvature
    wear_mask = wear.masks.add()
    wear_mask.id, wear_mask.source, wear_mask.generator = new_id(), "GENERATOR", "EDGE_WEAR"
    wear_mask.amount, wear_mask.contrast = .35, 1.3
    wear_mask.noise_strength, wear_mask.seed = .35, 7
    ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == coat.id)
    stack.invalidate(ts)
    return ts, plane, {"coat": coat.id, "wear": wear.id}, [source, curvature]


def render_setup(scene):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.seed = 0
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
    world = bpy.data.worlds.new("Acceptance fixed world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.22, .22, .22, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .5
    scene.world = world
    camera = bpy.data.objects.new("Acceptance camera", bpy.data.cameras.new("Acceptance camera"))
    scene.collection.objects.link(camera)
    camera.location = (0, 0, 4)
    camera.data.type, camera.data.ortho_scale = "ORTHO", 2.3
    scene.camera = camera
    light = bpy.data.objects.new("Acceptance light", bpy.data.lights.new("Acceptance light", "AREA"))
    scene.collection.objects.link(light)
    light.location = (-1.4, 1.2, 3)
    light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat("-Z", "Y").to_euler()
    light.data.energy, light.data.shape, light.data.size = 350, "DISK", 4


def rendered(scene):
    bpy.ops.render.render()
    # Render Result pixels are not exposed on every Blender build. Read a linear
    # float EXR from the real render, without changing the material or source data.
    with TemporaryDirectory(prefix="op-shader-frame-") as directory:
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.color_depth = "32"
        path = Path(directory) / "frame.exr"
        bpy.data.images["Render Result"].save_render(str(path), scene=scene)
        result = bpy.data.images.load(str(path), check_existing=False)
        result.colorspace_settings.name = "Non-Color"
        try:
            # Exclude plane silhouette and background; changes must be surface pixels.
            return images.pixels(result)[20:-20, 20:-20, :3].copy()
        finally:
            bpy.data.images.remove(result)


def main():
    open_painter.register()
    scene = bpy.context.scene
    ts, plane, ids, sources = fixture(scene)
    render_setup(scene)
    initial = ready(scene, ts)
    material_manager.repair(ts)
    assert shader_preview.current(scene, ts)
    assert plane.active_material == ts.material
    source_hashes = {image.name: image_hash(image) for image in sources}
    outputs = output_state(ts)
    baseline = rendered(scene)
    assert output_state(ts) == outputs, "Rendering ran the CPU evaluator"
    assert ts.dirty
    checks = []

    def layer(key):
        return next(item for item in ts.layers if item.id == ids[key])

    def check_edit(name, owner, key, value, minimum=2e-4):
        original = getattr(owner, key)
        before_graph, before_sockets = graph_state(ts.material)
        before_builds = shader_preview.diagnostics(scene, ts)["graph_builds"]
        with RasterObservation() as observed:
            setattr(owner, key, value)
            # Do not call ensure/notify here: the RNA callback must do the work.
            diagnostic = shader_preview.diagnostics(scene, ts)
            assert diagnostic["serial"] == ts.edit_serial and not diagnostic["rebuild_pending"], diagnostic
            assert not diagnostic.get("texture_bake_triggered"), diagnostic
            assert diagnostic.get("graph_builds") == before_builds, diagnostic
            cache.tick()   # the engine renders the edit into the set's images
            assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
            assert shader_preview.diagnostics(scene, ts)["engine"]["channels"], name + " re-rendered nothing"
            after_graph, after_sockets = graph_state(ts.material)
            assert after_graph == before_graph and after_sockets == before_sockets, name + " changed the material"
            for _ in range(4):
                cache.tick()
            changed = rendered(scene)
            observed.assert_no_raster()
        difference = float(np.mean(np.abs(changed - baseline)))
        assert difference > minimum, (name, difference, diagnostic)
        assert output_state(ts) == outputs and ts.dirty
        setattr(owner, key, original)
        cache.tick()
        assert shader_preview.current(scene, ts)
        restored_graph, restored_sockets = graph_state(ts.material)
        assert restored_graph == before_graph and restored_sockets == before_sockets, (
            name, "restoring RNA did not restore the shader sockets",
            {key: (before_sockets.get(key), value) for key, value in restored_sockets.items()
             if before_sockets.get(key) != value})
        restored = rendered(scene)
        np.testing.assert_allclose(restored, baseline, atol=2e-5, rtol=2e-5, err_msg=name)
        checks.append(dict(name=name, mean_visible_change=difference,
                           restored_max_error=float(np.max(abs(restored - baseline))),
                           diagnostic=diagnostic, instrumentation=observed.report()))

    check_edit("layer-opacity", layer("coat"), "opacity", .24)
    check_edit("image-mask-level", layer("coat").masks[0], "black", .38)
    check_edit("mask-levels-gamma", layer("coat").masks[1].effect, "gamma", 2.2)
    check_edit("edge-wear-amount", layer("wear").masks[0], "amount", .13)
    check_edit("edge-wear-breakup", layer("wear").masks[0], "noise_strength", .9)
    check_edit("channel-opacity", stack.channel_for(layer("coat"), "BASE_COLOR"), "opacity", .3)
    check_edit("height-levels", stack.channel_for(layer("coat"), "HEIGHT").effects[0],
               "output_white", .75, minimum=1e-5)

    # The wear breakup's noise domain is a uniform of its GPU program (the material graph
    # rastered it on the CPU as an isolated input): no source job, the frame still changes.
    wear_mask = layer("wear").masks[0]
    old_scale = wear_mask.noise_scale
    with RasterObservation() as boundary_observation:
        wear_mask.noise_scale = old_scale * 1.7
        queued = shader_preview.diagnostics(scene, ts)
        assert not queued["pending_resources"] and not queued["raster_boundaries_queued"], queued
        ready(scene, ts)
        boundary_frame = rendered(scene)
        assert boundary_observation.calls["cpu_channel_steps"] == 0
        assert boundary_observation.calls["legacy_gpu_composite"] == 0
        assert boundary_observation.calls["image_write"] == 0
        assert boundary_observation.metrics.counts["shader_resource_publications"] == 0
    assert np.mean(abs(boundary_frame - baseline)) > .001
    assert output_state(ts) == outputs and ts.dirty
    wear_mask.noise_scale = old_scale
    ready(scene, ts)
    np.testing.assert_allclose(rendered(scene), baseline, atol=2e-5, rtol=2e-5)

    # Structural changes may rebuild a graph; verify their actual visual result.
    layer("wear").visible = False
    ready(scene, ts)
    hidden = rendered(scene)
    assert np.mean(abs(hidden - baseline)) > .005
    layer("wear").visible = True
    ready(scene, ts)
    index = next(i for i, item in enumerate(ts.layers) if item.id == ids["wear"])
    stack.move(ts, index, -1)
    ready(scene, ts)
    reordered = rendered(scene)
    assert np.mean(abs(reordered - baseline)) > .002
    index = next(i for i, item in enumerate(ts.layers) if item.id == ids["wear"])
    stack.move(ts, index, 1)
    ready(scene, ts)
    np.testing.assert_allclose(rendered(scene), baseline, atol=2e-5, rtol=2e-5)

    with TemporaryDirectory(prefix="op-shader-acceptance-") as directory:
        identifier = ts.id
        with RasterObservation() as save_observation:
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / "authoring.blend"))
            save_observation.assert_no_raster()
        assert output_state(ts) == outputs and ts.dirty, "Saving ran the CPU evaluator"
        bpy.ops.wm.open_mainfile(filepath=str(Path(directory) / "authoring.blend"))
        scene = bpy.context.scene
        ts = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
        ready(scene, ts)
        material_manager.repair(ts)
        assert shader_preview.current(scene, ts)
        np.testing.assert_allclose(rendered(scene), baseline, atol=2e-5, rtol=2e-5)
        assert {name: image_hash(bpy.data.images[name]) for name in source_hashes} == source_hashes
        assert all(bpy.data.images[name].packed_file for name in source_hashes)
        assert output_state(ts) == outputs, "Reload consumed stale CPU outputs"

    shader_preview.clear()
    assert not shader_preview.current(scene, ts)
    ready(scene, ts)
    np.testing.assert_allclose(rendered(scene), baseline, atol=2e-5, rtol=2e-5)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(result="PASS", blender=bpy.app.version_string,
        initial=initial, checks=checks, source_hashes=source_hashes,
        legacy_noise_scale=dict(classification="a uniform of the wear program",
                                diagnostic=queued, instrumentation=boundary_observation.report()),
        structure="visibility and reorder change the rendered frame; restoration matches",
        persistence="save/reload preserves the CPU state and packed sources; the engine re-renders",
        timing_scope="No UI/frame latency is inferred from these correctness renders"), indent=2) + "\n")
    open_painter.unregister()
    print("SHADER_PREVIEW_PASS", str(REPORT), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(dict(result="FAIL", error=traceback.format_exc()), indent=2) + "\n")
        raise
