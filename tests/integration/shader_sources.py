"""Native shader source parity and all-catalog compilation on assigned geometry.

An explicit emission bake reads shader values only for regression comparison.
Authoring graph tests use a 32-pixel proxy without altering saved resolution.
The fifty catalog cases compile their real recipes without beauty renders.
"""
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.constants import DEFAULTS
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import channel_eval, mask_eval, shader_preview
from open_painter.evaluation.blend_modes import blend
from open_painter.evaluation.shader_graph import Graph
from open_painter.smart_materials import composer, generated_sources
from open_painter.smart_materials.catalog import CATALOG
from open_painter.texture_engine import filters
from open_painter.utils.ids import new_id

N = 32


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def add_mask(layer, source="GENERATOR", **fields):
    mask = layer.masks.add()
    mask.id = new_id()
    mask.source = source
    mask.operation = "NORMAL"
    for name, value in fields.items():
        setattr(mask, name, value)
    return mask


def run():
    open_painter.register()
    scene = bpy.context.scene
    ts = sets.create(scene, "Shader source parity", 512)
    ts.preview_backend = "CPU"
    shader_preview.clear()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    # A rotated plane exercises all three triplanar weights and world position.
    obj.rotation_euler = (.43, -.36, .19)
    obj.location = (.18, -.11, .27)
    sets.assign(scene, ts, obj)
    bpy.context.view_layer.update()
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    small = SampledSet(ts)
    target = bpy.data.images.new("Shader source test readback", width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = "Non-Color"
    uv = (np.arange(N, dtype=np.float32) + .5) / N
    u, v = np.meshgrid(uv, uv)

    # Cycles' emission bake deliberately nudges barycentric samples inside the
    # triangle. Measure that offset instead of mistaking it for source mismatch
    # near repeat seams or high-frequency generated textures. The assigned test
    # plane has affine UV/world coordinates, so both offsets are exact constants.
    tree = ts.material.node_tree
    output = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    calibration_uv = tree.nodes.new("ShaderNodeTexCoord")
    calibration_emission = tree.nodes.new("ShaderNodeEmission")
    calibration_target = tree.nodes.new("ShaderNodeTexImage")
    calibration_target.image = target
    tree.nodes.active = calibration_target
    tree.links.new(calibration_uv.outputs["UV"], calibration_emission.inputs["Color"])
    tree.links.new(calibration_emission.outputs[0], output.inputs["Surface"])
    bpy.ops.object.bake(type="EMIT")
    baked_uv = images.pixels(target)[..., :2]
    offsets = baked_uv - np.stack((u, v), axis=-1)
    bias = np.median(offsets.reshape(-1, 2), axis=0)
    assert np.max(np.abs(offsets-bias)) < 1e-6
    from mathutils import Vector
    world_bias = tuple(obj.matrix_world.to_3x3() @ Vector((2*bias[0], 2*bias[1], 0)))
    for node in (calibration_uv, calibration_emission, calibration_target):
        tree.nodes.remove(node)

    def create_input(name, alpha=False, phase=0.0):
        image = images.create(name, N)
        data = np.ones((N, N, 4), np.float32)
        data[..., 0] = .2 + .5 * u * (1 - phase) + phase * (1 - v) ** 2
        data[..., 1] = .1 + .7 * v
        data[..., 2] = .3 + .35 * np.sin(u * np.pi + phase) * np.cos(v * np.pi)
        if alpha:
            data[..., 3] = .2 + .7 * v
        images.write(image, data, pack=False)
        return image

    rgba = create_input("RGBA source", alpha=True)
    detail = create_input("RGB scalar detail")
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL", "THICKNESS",
                 "MATERIAL_ID", "OBJECT_ID", "UV_ISLAND", "UV_BORDER",
                 "CURVATURE_THIN", "CURVATURE_WIDE", "CURVATURE_XWIDE"):
        item = ts.baked_maps.add()
        item.kind = kind
        # Distinct edge-map reaches, so Edge Size blends between different content.
        item.image = create_input("Map " + kind, phase={"CURVATURE_THIN": .8, "CURVATURE_WIDE": .4, "CURVATURE_XWIDE": .95}.get(kind, 0.0))

    report = {"source_cases": [], "catalog": [], "failures": [],
              "readback_uv_bias": bias.tolist()}

    def check(label, configure, channel="BASE_COLOR", kind="FILL"):
        ts.layers.clear()
        layer = stack.add(ts, kind, label)
        for p in layer.channels:
            p.enabled = p.channel == channel
        prop = stack.channel_for(layer, channel)
        prop.color = (.23, .52, .76, .81)
        prop.value = .37
        identifier = layer.id
        configure(layer, prop)
        layer = next(item for item in ts.layers if item.id == identifier)
        prop = stack.channel_for(layer, channel)
        graph = Graph(scene, small, ts.material)
        try:
            graph.build()
            while graph.pending_resources:
                graph.step_resources(.1)
            # Test readback calibration only; production graph construction and
            # property binding are unmodified. Correct the probe's displaced
            # evaluation location to the CPU reference's authored texel center.
            for source, delta in ((graph.uv, (*bias, 0.)),
                                  (graph.geometry.outputs["Position"], world_bias)):
                destinations = [link.to_socket for link in source.links]
                corrected = graph.vec("SUBTRACT", source, delta)
                for socket in destinations:
                    graph.link(corrected, socket)
            tree = ts.material.node_tree
            output = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
            emission = tree.nodes.new("ShaderNodeEmission")
            tree.links.new(graph.outputs[channel], emission.inputs["Color"])
            tree.links.new(emission.outputs[0], output.inputs["Surface"])
            bake_node = tree.nodes.new("ShaderNodeTexImage")
            bake_node.image = target
            tree.nodes.active = bake_node
            bpy.ops.object.bake(type="EMIT")
            actual = images.pixels(target)[..., :3]
            source = channel_eval.source(prop, kind, N, 0, N, {}, small, {})
            mask = mask_eval.evaluate_masks(small, layer, N, 0, N, {}, {})
            alpha = np.clip(source[..., 3:4] * layer.opacity * prop.opacity * mask[..., None], 0, 1)
            expected = np.clip(blend(np.array(DEFAULTS[channel][:3], np.float32), source[..., :3], alpha,
                                     layer.blend_mode), 0, 1)
            if channel == "NORMAL":
                vector = expected * 2 - 1
                expected = vector / np.maximum(np.linalg.norm(vector, axis=-1, keepdims=True), 1e-8) * .5 + .5
            difference = np.abs(actual - expected)
            record = {"name": label, "max_error": float(difference.max()), "mean_error": float(difference.mean()),
                      "nodes": len(graph.tree.nodes), "resources": len(graph.resource_bindings)}
            report["source_cases"].append(record)
            if not np.allclose(actual, expected, atol=3e-5, rtol=2e-5):
                report["failures"].append(record)
            print("SHADER_SOURCE", json.dumps(record), flush=True)
            tree.nodes.remove(bake_node)
            tree.nodes.remove(emission)
        except Exception as error:
            report["failures"].append({"name": label, "error": repr(error)})
            print("SHADER_SOURCE_ERROR", label, repr(error), flush=True)
        finally:
            graph.close()

    def image_source(layer, prop, projection="UV", tiling="REPEAT", filtering="BILINEAR"):
        prop.image = rgba
        prop.projection = projection
        prop.scale = (1.23, .84)
        prop.offset = (-.13, .19)
        prop.rotation = .17
        prop.tiling = tiling
        prop.filtering = filtering

    check("uv_rgba", image_source)
    check("uv_rgba_border", lambda l,p: image_source(l,p,tiling="NONE"))
    check("uv_rgba_nearest", lambda l,p: image_source(l,p,filtering="NEAREST"))
    check("triplanar_rgba", lambda l,p: image_source(l,p,projection="TRIPLANAR"))
    check("paint_float", lambda l,p: setattr(p, "image", rgba), kind="PAINT")

    def scalar_detail(layer, prop, projection="UV", generated=False):
        prop.detail_image = generated_sources.load("fine_wood") if generated else detail
        prop.procedural_detail = .67
        prop.procedural_height = .17
        prop.projection = projection
        prop.scale = (1.27, .91)
        prop.rotation = .21
        prop.offset = (.08, -.17)

    check("uv_scalar_detail", scalar_detail)
    check("triplanar_scalar_detail", lambda l,p: scalar_detail(l,p,projection="TRIPLANAR"))
    check("generated_scalar_detail", lambda l,p: scalar_detail(l,p,generated=True))
    check("scalar_detail_height", scalar_detail, channel="HEIGHT")
    check("scalar_detail_normal", scalar_detail, channel="NORMAL")
    for procedural in ("CHECKER", "GRADIENT", "FBM"):
        def configure(layer, prop, kind=procedural):
            prop.procedural = kind
            prop.procedural_scale = 4.25
            prop.procedural_detail = .71
            prop.offset = (-.4, .17)
        check("channel_procedural_" + procedural, configure)
    for generator in ("EDGE_WEAR", "DIRT", "CHIPS", "DUST", "DRIPS", "UV_BORDER"):
        def configure(layer, prop, kind=generator):
            add_mask(layer, generator=kind, amount=.64, contrast=1.3, noise_strength=.38,
                     black=.11, white=.83, strength=.76, invert=True)
        check("generator_" + generator, configure)
    # Edge Width narrows the baked band identically on both evaluators.
    check("generator_EDGE_WEAR_width",
          lambda l, p: add_mask(l, generator="EDGE_WEAR", amount=.64, contrast=1.3, noise_strength=.38, edge_width=.45))
    # Edge Size blends the four baked edge maps identically on both evaluators.
    for size in (0.0, .35, 1.6, 2.7, 3.0):
        check(f"generator_EDGE_WEAR_size_{size}",
              lambda l, p, size=size: add_mask(l, generator="EDGE_WEAR", amount=.64, contrast=1.3,
                                               noise_strength=.38, edge_width=.7, edge_size=size))
    check("generator_CHIPS_size",
          lambda l, p: add_mask(l, generator="CHIPS", amount=.5, contrast=1.1, noise_strength=.3, edge_size=2.2))
    for procedural in ("CHECKER", "FBM"):
        check("mask_procedural_" + procedural,
              lambda l,p,kind=procedural: add_mask(l, "PROCEDURAL", procedural=kind, noise_scale=4.25,
                                                  projection="TRIPLANAR", strength=.73))

    def mask_filters(layer, prop):
        add_mask(layer, generator="EDGE_WEAR", amount=.64, noise_strength=.31)
        mask = add_mask(layer, "FILTER", black=.09, white=.81, strength=.64)
        mask.effect.kind = "HISTOGRAM_SCAN"
        mask.effect.amount = .38
        mask.effect.softness = .12
        mask.effect.strength = .73
    check("native_mask_filter", mask_filters)

    def boundary_filter(layer, prop):
        scalar_detail(layer, prop)
        effect = prop.effects.add()
        effect.kind = "GAUSSIAN_BLUR"
        effect.radius = 1.2
        effect.strength = .67
        effect = prop.effects.add()
        effect.kind = "LEVELS"
        effect.black = .08
        effect.white = .87
        effect.gamma = 1.7
    check("isolated_filter_prefix", boundary_filter)

    def published_scalar(layer, prop):
        identifier = layer.id
        add_mask(layer, "REFERENCE", reference_channel="ROUGHNESS")
        published = stack.add(ts, "FILL", "Published RGB roughness")
        published.visible = False
        channel = stack.channel_for(published, "ROUGHNESS")
        channel.image = detail
        source_id = published.id
        consumer = next(item for item in ts.layers if item.id == identifier)
        consumer.masks[0].reference_layer = source_id
    check("published_scalar_uses_red", published_scalar)

    # Anchor points: prefixes of another layer's stacks instantiate identically.
    def hidden_layer(name, channel, image, effects=()):
        layer = stack.add(ts, "FILL", name)
        layer.visible = False
        for p in layer.channels:
            p.enabled = p.channel == channel
        prop = stack.channel_for(layer, channel)
        prop.image = image
        anchor_id = ""
        for kind, fields in effects:
            effect = prop.effects.add()
            effect.kind = kind
            if kind == "ANCHOR":
                effect.id = anchor_id = new_id()
            for key, value in fields.items():
                setattr(effect, key, value)
        return layer, anchor_id

    def anchor_mask_mid_stack(layer, prop):
        identifier = layer.id
        add_mask(layer, "REFERENCE")
        published = stack.add(ts, "FILL", "Published mask stack")
        published.visible = False
        add_mask(published, "FILL", image=detail, strength=.9)
        anchor = add_mask(published, "ANCHOR")
        add_mask(published, "FILTER", black=.2, white=.7, invert=True)
        consumer = next(item for item in ts.layers if item.id == identifier)
        consumer.masks[0].reference_anchor = anchor.id
    check("anchor_mask_mid_stack", anchor_mask_mid_stack)

    def anchor_channel_prefix(layer, prop):
        identifier = layer.id
        _, anchor_id = hidden_layer("Published color", "BASE_COLOR", rgba,
                                    (("HSL", {"hue": .2, "saturation": 1.4}), ("ANCHOR", {}), ("INVERT", {})))
        stack.channel_for(next(item for item in ts.layers if item.id == identifier), "BASE_COLOR").anchor = anchor_id
    check("anchor_channel_prefix", anchor_channel_prefix)

    def anchor_channel_after_boundary(layer, prop):
        identifier = layer.id
        _, anchor_id = hidden_layer("Published blurred roughness", "ROUGHNESS", detail,
                                    (("GAUSSIAN_BLUR", {"radius": 1.5}), ("ANCHOR", {}),
                                     ("LEVELS", {"black": .2, "white": .8})))
        consumer = stack.channel_for(next(item for item in ts.layers if item.id == identifier), "ROUGHNESS")
        consumer.anchor = anchor_id
        effect = consumer.effects.add()
        effect.kind = "GAMMA"
        effect.gamma = 1.6
    check("anchor_channel_after_boundary", anchor_channel_after_boundary, channel="ROUGHNESS")
    assert report["source_cases"][-1]["resources"] == 1, report["source_cases"][-1]

    def anchor_reference_mask_from_channel(layer, prop):
        identifier = layer.id
        add_mask(layer, "REFERENCE", black=.1, white=.9)
        _, anchor_id = hidden_layer("Published luminance", "BASE_COLOR", rgba, (("ANCHOR", {}),))
        next(item for item in ts.layers if item.id == identifier).masks[0].reference_anchor = anchor_id
    check("anchor_reference_mask_from_channel", anchor_reference_mask_from_channel)

    def anchor_same_layer_cross_channel(layer, prop):
        height = stack.channel_for(layer, "HEIGHT")
        height.enabled = True
        height.image = detail
        effect = height.effects.add()
        effect.kind, effect.id = "ANCHOR", new_id()
        prop.anchor = effect.id
    check("anchor_same_layer_cross_channel", anchor_same_layer_cross_channel)

    def anchor_consumer_modulated(layer, prop):
        identifier = layer.id
        _, anchor_id = hidden_layer("Published base", "BASE_COLOR", rgba, (("ANCHOR", {}),))
        consumer = stack.channel_for(next(item for item in ts.layers if item.id == identifier), "BASE_COLOR")
        consumer.anchor = anchor_id
        consumer.procedural, consumer.procedural_scale, consumer.procedural_detail = "CHECKER", 3.5, .8
        effect = consumer.effects.add()
        effect.kind = "LEVELS"
        effect.black, effect.white = .15, .85
    check("anchor_consumer_modulated", anchor_consumer_modulated)

    def anchor_missing_neutral(layer, prop):
        prop.anchor = "nowhere"
        add_mask(layer, "REFERENCE", reference_anchor="nowhere")
    check("anchor_missing_neutral", anchor_missing_neutral)

    # An artist's sRGB-tagged byte image (image.open's default for a PNG) feeds
    # both a Normal fill and its Normal Combine detail. The fill decodes sRGB
    # like the Image Texture node, the detail is read as stored by both
    # evaluators, and the datablock keeps its tag: neither read retags it.
    opened = bpy.data.images.new("Opened normal map", width=N, height=N)
    opened.colorspace_settings.name = "sRGB"
    authored = np.ones((N, N, 4), np.float32)
    authored[..., 0] = .3 + .4 * u
    authored[..., 1] = .35 + .3 * v
    authored[..., 2] = .7 + .25 * np.sin(u * np.pi) * np.cos(v * np.pi)
    opened.pixels.foreach_set(authored.ravel())
    opened.update()
    stored = np.empty(N * N * 4, np.float32)
    opened.pixels.foreach_get(stored)
    stored = stored.reshape(N, N, 4)
    assert np.abs(stored - authored).max() <= .5 / 255 + 1e-6   # the byte buffer holds authored values, quantised

    def shared_srgb_detail(layer, prop):
        prop.image = opened
        effect = prop.effects.add()
        effect.kind = "NORMAL_COMBINE"
        effect.image = opened
        effect.amount = 1.3
    check("srgb_detail_shared_fill", shared_srgb_detail, channel="NORMAL")
    assert opened.colorspace_settings.name == "sRGB", opened.colorspace_settings.name
    assert not report["failures"], report["failures"]
    shared = stack.channel_for(ts.layers[-1], "NORMAL")
    from open_painter.evaluation.image_snapshots import read
    detail_reference = channel_eval.source(shared, "FILL", N, 0, N, {}, small, {})
    shared.effects[0].strength = 0.
    fill = channel_eval.source(shared, "FILL", N, 0, N, {}, small, {})
    decoded = read({}, opened, linear=True)
    np.testing.assert_allclose(fill[..., :3], decoded[..., :3], atol=1e-6)   # the fill still decodes sRGB
    assert np.abs(decoded[..., :3] - stored[..., :3]).max() > .05
    # The detail combined with the decoded fill was the stored map, not the decoded one.
    combined = filters.apply(decoded[..., :3], "NORMAL_COMBINE",
                             detail=filters.apply(stored[..., :3], "NORMAL_STRENGTH", strength=1.3))
    np.testing.assert_allclose(detail_reference[..., :3], combined, atol=1e-6)
    wrong = filters.apply(decoded[..., :3], "NORMAL_COMBINE",
                          detail=filters.apply(decoded[..., :3], "NORMAL_STRENGTH", strength=1.3))
    assert np.abs(detail_reference[..., :3] - wrong).max() > .02

    bump = images.create("Bump disc", N)
    disc = np.full((N, N, 4), .5, np.float32)
    disc[..., 3] = 1
    disc[(u - .5) ** 2 + (v - .5) ** 2 < .3 ** 2, :3] = 1
    images.write(bump, disc, pack=False)
    bump_normal = images.create("Bump normal", N)
    normal = np.ones((N, N, 4), np.float32)
    normal[..., :3] = filters.height_to_normal(disc[..., 0], 8)
    images.write(bump_normal, normal, pack=False)

    def micro(layer, prop, generator="EDGE_WEAR", slot="detail_height_anchor", channel="HEIGHT"):
        identifier = layer.id
        add_mask(layer, generator=generator, amount=.64, contrast=1.3, noise_strength=.38, detail_radius=1.5)
        _, anchor_id = hidden_layer("Published relief", channel, bump if channel == "HEIGHT" else bump_normal,
                                    (("ANCHOR", {}),))
        setattr(next(item for item in ts.layers if item.id == identifier).masks[0], slot, anchor_id)
    check("generator_EDGE_WEAR_micro_height", micro)
    check("generator_CAVITY_DIRT_micro_height", lambda l, p: micro(l, p, "CAVITY_DIRT"))
    check("generator_DIRT_micro_normal", lambda l, p: micro(l, p, "DIRT", "detail_normal_anchor", "NORMAL"))
    assert report["source_cases"][-1]["resources"] >= 1, report["source_cases"][-1]

    for name in (() if os.environ.get("OP_SHADER_SOURCE_ONLY") else CATALOG):
        ts.layers.clear()
        graph = None
        started = perf_counter()
        try:
            composer.apply(ts, name)
            graph = Graph(scene, small, ts.material)
            graph.build()
            report["catalog"].append({"name": name, "nodes": len(graph.tree.nodes),
                                      "resources": len(graph.resource_bindings),
                                      "compile_ms": (perf_counter()-started)*1000})
            print("SHADER_CATALOG_COMPILE_PASS", name, len(graph.tree.nodes), flush=True)
        except Exception as error:
            report["failures"].append({"name": "catalog:" + name, "error": repr(error)})
            print("SHADER_CATALOG_COMPILE_FAIL", name, repr(error), flush=True)
        finally:
            if graph:
                graph.close()
    destination = ROOT / ".blender-test" / ("shader-sources-only.json" if os.environ.get("OP_SHADER_SOURCE_ONLY")
                                             else "shader-sources.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2))
    assert os.environ.get("OP_SHADER_SOURCE_ONLY") or len(report["catalog"]) == 50
    assert not report["failures"], report["failures"]
    print("SHADER_SOURCES_PASS", len(report["source_cases"]), len(report["catalog"]), flush=True)


run()
