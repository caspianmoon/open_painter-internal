"""Damaged Variable Bevel as texture effects: Cycles-only shader nodes become baked maps.

The reference script drives a Bevel node, an AO node and a Bump node inside the
material, which only Cycles evaluates. Here the Bevel Mask (distance between the
Bevel normal and the geometry normal) and the Bevel Normal are baked once through
the owned Cycles staging scene; the effects then read ordinary textures, so the
result is identical for EEVEE, Cycles and export. This test covers the maps, the
recipes, the evaluated channels, persistence and the shader graph; the EEVEE
viewport itself is exercised by scripts/validate_damaged_bevel_eevee.py.
"""
import json
import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.baking.bake_manager import bake_selected, register_map
from open_painter.baking.cycles_baker import bake
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.smart_materials import composer, serializer, loader
from open_painter.smart_materials.composer import EFFECTS, MissingMeshMaps, requirements

open_painter.register()
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"  # the user's engine is never touched by the bakes
cube = bpy.context.object
ts = sets.create(scene, "Damaged bevel", 512)
sets.assign(scene, ts, cube)
n = 512

# Requirements: the damage effect needs only the bevel mask; AO is optional.
assert requirements(EFFECTS["Damaged Bevel"]) == {"BEVEL_MASK"}
assert requirements(EFFECTS["Rounded Edges"]) == {"BEVEL_NORMAL"}
try:
    composer.apply_effect(ts, "Damaged Bevel")
    raise AssertionError("Effect applied without its bevel mask")
except MissingMeshMaps as exc:
    assert exc.missing == ("BEVEL_MASK",)
assert not ts.layers

# Bake through the queue with the user's engine untouched and no leaked data.
ts.bake.normal = ts.bake.ao = ts.bake.curvature = False
ts.bake.selected_to_active = False
ts.bake.bevel_normal = ts.bake.bevel_mask = True
ts.bake.bevel_radius, ts.bake.bevel_samples, ts.bake.samples = .15, 8, 4
worlds, scenes, materials = len(bpy.data.worlds), len(bpy.data.scenes), len(bpy.data.materials)
bake_selected(bpy.context, ts)
assert scene.render.engine == "BLENDER_EEVEE"
assert (len(bpy.data.worlds), len(bpy.data.scenes), len(bpy.data.materials)) == (worlds, scenes, materials)
kinds = {m.kind: m.image for m in ts.baked_maps}
assert set(kinds) == {"BEVEL_NORMAL", "BEVEL_MASK"}
mask_px = images.pixels(kinds["BEVEL_MASK"])[..., 0]
normal_px = images.pixels(kinds["BEVEL_NORMAL"])[..., :3] * 2 - 1
assert abs(kinds["BEVEL_MASK"]["bevel_radius"] - .15) < 1e-6 and kinds["BEVEL_MASK"]["open_painter_mesh_map"] == "BEVEL_MASK"
edge = mask_px > .2
assert .03 < edge.mean() < .3, edge.mean()                      # bands along the twelve edges only
tilt = np.degrees(np.arccos(np.clip(normal_px[..., 2], -1, 1)))
assert tilt[edge].mean() > 8 and tilt[~edge].mean() < 5, (tilt[edge].mean(), tilt[~edge].mean())  # Bevel sampling noise stays small off the edges
assert .9 < np.median(np.linalg.norm(normal_px, axis=-1)) < 1.05  # sampled Bevel normals are renormalized by the Normal Map node

# Damage: recessed height only where the bevel mask and the noise coincide.
damage = composer.apply_effect(ts, "Damaged Bevel")
assert damage.type == "FILL" and [m.source for m in damage.masks] == ["BAKED", "PROCEDURAL"]
assert damage.masks[0].map_type == "BEVEL_MASK" and damage.masks[1].operation == "MULTIPLY"
assert stack.channel_for(damage, "HEIGHT").enabled and stack.channel_for(damage, "METALLIC").value == 1
coverage = np.asarray(evaluate_masks(ts, damage, n, 0, n, {}, {}))
coverage = coverage[..., 0] if coverage.ndim == 3 else coverage
# The mask levels (black .04) discard the Bevel sampling noise on flat faces.
assert coverage[mask_px <= .04].max() < 1e-6, "Damage escaped the edge bands"
assert 0 < coverage[edge].mean() < .8, coverage[edge].mean()   # noise leaves parts of every edge intact
evaluator = LayerEvaluator(scene)
height = images.pixels(evaluator.evaluate_channel(ts.id, "HEIGHT"))[..., 0]
assert np.allclose(height[mask_px <= .04], .5, atol=1e-6)
assert height[edge].min() < .35 and (height <= .5 + 1e-6).all()

# With an AO map baked, the optional cavity term joins the same effect.
ao = images.create("flat ao", n, (1, 1, 1, 1))
register_map(ts, "AO", ao)
with_ao = composer.apply_effect(ts, "Damaged Bevel")
assert [m.source for m in with_ao.masks] == ["BAKED", "PROCEDURAL", "BAKED"] and with_ao.masks[2].map_type == "AO"
stack.remove(ts, next(i for i, l in enumerate(ts.layers) if l.id == with_ao.id))

# Rounded edges: the bevel normal enters the Normal channel with RNM blending.
rounded = composer.apply_effect(ts, "Rounded Edges")
assert rounded.blend_mode == "RNM"
normal_prop = stack.channel_for(rounded, "NORMAL")
assert normal_prop.enabled and normal_prop.image == kinds["BEVEL_NORMAL"]
assert not any(stack.channel_for(rounded, c).enabled for c in ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"))
composed = images.pixels(evaluator.evaluate_channel(ts.id, "NORMAL"))[..., :3] * 2 - 1
composed_tilt = np.degrees(np.arccos(np.clip(composed[..., 2], -1, 1)))
assert composed_tilt[edge].mean() > 8 and composed_tilt[~edge].mean() < 5

# The persistent shader graph accepts both layers (baked masks and normal images).
try:
    from open_painter.evaluation import shader_preview
except ImportError:
    shader_preview = None
if shader_preview is not None:
    graph = shader_preview.ensure(scene, ts)
    assert graph is not None and graph.tree is not None
    for _ in range(200):  # the FBM noise mask is a cached source boundary the timer normally fills
        if not graph.pending_resources:
            break
        graph.step_resources(budget=1.0)
    assert not graph.pending_resources
    assert any(node.bl_idname == "ShaderNodeTexImage" and node.image == kinds["BEVEL_MASK"] for node in graph.nodes())

# Portable material round trip keeps the baked masks as images.
path = root / ".blender-test" / "damaged-bevel.openpaintmaterial"
path.unlink(missing_ok=True)
serializer.save(ts, path)
other = sets.create(scene, "Import target", 512)
loader.apply(other, path)
imported = next(l for l in other.layers if l.name == "Damaged Bevel")
assert imported.masks[0].source == "BAKED" and imported.masks[0].map_type == "BEVEL_MASK"
imported_rounded = next(l for l in other.layers if l.name == "Rounded Edges")
assert imported_rounded.blend_mode == "RNM" and stack.channel_for(imported_rounded, "NORMAL").image is not None
open_painter.unregister()
print("DAMAGED_BEVEL_PASS: bevel mask/normal bakes, engine untouched, edge-only recessed damage, optional AO, RNM rounded edges, shader graph, portable round trip")
