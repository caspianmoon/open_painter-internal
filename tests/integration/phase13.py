"""Regression coverage for automatic materials, real groups and surface presets."""

import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    material_manager,
    image_manager as images,
    surface_presets,
)
from open_painter.constants import CHANNELS, DEFAULTS
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
scene = bpy.context.scene
obj = bpy.context.object
original = obj.active_material.name if obj.active_material else None
assert bpy.ops.open_painter.texture_set(action="ADD") == {"FINISHED"}
ts = scene.open_painter.texture_sets[0]
ts.preview_backend = 'CPU'  # Explicit channel-image allocation/export contract.
LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert obj.active_material == ts.material
assert {o.channel for o in ts.outputs} == set(CHANNELS)
# The material is the Texture Set node, a Principled BSDF and a Material Output; the channel
# images are sampled inside the node's group, in the CPU reference as with the engine.
from open_painter.core import texture_set_node
kinds = sorted(n.bl_idname for n in ts.material.node_tree.nodes)
assert kinds == sorted([texture_set_node.NODE_ID, "ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial"]), kinds
roles = texture_set_node.channel_nodes(ts)
assert all(
    roles[key].image == next(o.image for o in ts.outputs if o.channel == key) for key in CHANNELS
)
assert all(roles[key].outputs["Color"].is_linked for key in CHANNELS)
for key in CHANNELS:
    np.testing.assert_allclose(images.pixels(roles[key].image)[0, 0], DEFAULTS[key], atol=1e-6)
links = [link.as_pointer() for link in ts.material.node_tree.links]
material_manager.repair(ts)
assert links == [link.as_pointer() for link in ts.material.node_tree.links], (
    "Repair rewired an unchanged shader"
)
ts.resolution = "512"
# Group selected adjacent layers without moving above unrelated top layers.
a = stack.add(ts, "FILL", "A").id
b = stack.add(ts, "FILL", "B").id
c = stack.add(ts, "FILL", "C").id
d = stack.add(ts, "FILL", "D").id
group_id = stack.group(ts, {b, c}, "Coating").id
rows = lambda: [(ts.layers[i].name, depth) for i, depth in stack.visible_rows(ts)]
assert rows() == [("D", 0), ("Coating", 0), ("C", 1), ("B", 1), ("A", 0)], rows()
group = next(item for item in ts.layers if item.id == group_id)
group.expanded = False
assert rows() == [("D", 0), ("Coating", 0), ("A", 0)]
stack.reparent_many(ts, {a}, group_id)
assert group.expanded
assert ("A", 1) in rows()
nested_id = stack.group(ts, {b}, "Nested").id
before = {item.id: item.parent_id for item in ts.layers}
try:
    stack.reparent_many(ts, {group_id}, nested_id)
    raise AssertionError("Cyclic group accepted")
except ValueError:
    pass
assert before == {item.id: item.parent_id for item in ts.layers}
stack.reparent_many(ts, {c}, "")
assert next(item for item in ts.layers if item.id == c).parent_id == ""
# All shelf surfaces evaluate without requiring missing bake maps.
for name in ["Painted Steel", "Dirty Plastic", *surface_presets.SURFACES]:
    preview = sets.create(scene, name, 512)
    surface_presets.apply(preview, name)
    LayerEvaluator(scene).evaluate_texture_set(preview.id)
    for output in preview.outputs:
        data = images.pixels(output.image)
        assert np.isfinite(data).all(), (name, output.channel)
        assert data[..., :3].min() >= 0 and data[..., :3].max() <= 1
    rough = images.pixels(next(o.image for o in preview.outputs if o.channel == "ROUGHNESS"))
    assert np.std(rough[..., 0]) > 0.001, name
    assert (root / "src/open_painter/assets/previews" / (name.replace(" ", "_") + ".png")).exists()
# Reacquire after texture-set collection additions, then prove persistence/restoration.
ts = scene.open_painter.texture_sets[0]
path = root / ".blender-test/repair_groups.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
assert any(item.id == group_id for item in ts.layers)
assert any(item.parent_id == group_id for item in ts.layers)
sets.unassign(ts, 0)
assert (
    bpy.context.object.active_material.name == original
    if original
    else bpy.context.object.active_material is None
)
# Timer work is split across channels instead of blocking for an entire set.
from time import monotonic
from open_painter.evaluation import cache

for item in bpy.context.scene.open_painter.texture_sets:
    item.dirty = False
    for output in item.outputs:
        output.dirty = False
before = {output.channel: output.revision for output in ts.outputs}
ts.layers[0].opacity = 0.4
cache._pending[cache.key_for(bpy.context.scene, ts)] = (ts.edit_serial, monotonic() - 1)
cache.tick()
assert sum(output.revision > before[output.channel] for output in ts.outputs) <= 1
assert ts.dirty
for _ in range(1000):
    cache.tick()
    if not ts.dirty:
        break
assert not ts.dirty and cache.key_for(bpy.context.scene, ts) not in cache._evaluators
# Delete a group removes its subtree; ordinary ungrouping retains its children.
ids = {item.id for item in ts.layers if item.parent_id == group_id}
stack.remove_tree(ts, group_id)
assert not any(item.id in ids | {group_id} for item in ts.layers)
open_painter.unregister()
print(
    "PHASE 13 PASS: immediate material/image wiring, stable shader links, nested/collapsible groups, atomic cycle rejection, all rendered surface presets, persistence"
)
