"""Mask architecture (0.9.3): every entry is a source; groups nest so masks feed masks;
custom shader node groups are sources too. CPU model, structure operators, shader parity."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, mask_stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, custom_graph, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.shader_graph import Graph
from open_painter.utils.ids import new_id

N = 32


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def gradient(name, axis, n=512):
    image = images.create(name, n)
    u, v = np.meshgrid((np.arange(n) + .5) / n, (np.arange(n) + .5) / n)
    data = np.ones((n, n, 4), np.float32)
    data[..., 0] = data[..., 1] = data[..., 2] = (u if axis == 0 else v).astype(np.float32)
    images.write(image, data, pack=False)
    return image


def levels(mask, value):
    value = np.clip((value - mask.black) / max(1e-6, mask.white - mask.black), 0, 1)
    return 1 - value if mask.invert else value


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Mask groups', 512)
sets.assign(scene, ts, plane)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.1, .1, .1, 1.)
layer = stack.add(ts, 'FILL', 'Rust')
layer_id = layer.id
stack.channel_for(layer, 'BASE_COLOR').color = (.9, .4, .1, 1.)
layer_of = lambda: next(l for l in ts.layers if l.id == layer_id)
u_image, v_image = gradient('U', 0), gradient('V', 1)


def add(source, name, **fields):
    mask = layer_of().masks.add()
    mask.id, mask.name, mask.source = new_id(), name, source
    for key, value in fields.items():
        setattr(mask, key, value)
    return mask.id


def by_id(identifier):
    return next(m for m in layer_of().masks if m.id == identifier)


# Curvature ─┐ Multiply → Levels → MASK   (with gradients standing in for curvature/noise)
#  Noise   ──┘
group_id = add('GROUP', 'Wear', operation='MAX', strength=.8)
a_id = add('FILL', 'Curvature', image=u_image, parent_id=group_id)
b_id = add('FILL', 'Noise', image=v_image, operation='MULTIPLY', parent_id=group_id)
lv_id = add('FILTER', 'Levels', parent_id=group_id)
by_id(lv_id).effect.kind, by_id(lv_id).effect.black, by_id(lv_id).effect.white = 'LEVELS', .1, .9
c_id = add('FILL', 'Base coverage', image=v_image, strength=.5)
by_id(group_id).black = .05
masks = list(layer_of().masks)
assert mask_stack.rows(masks) == [(0, 0), (1, 1), (2, 1), (3, 1), (4, 0)]
assert mask_stack.members(masks, 0) == [1, 2, 3] and mask_stack.block(masks, 0) == (0, 4)

# CPU model: the group's members combine first, then the group folds into the stack.
n = 512
u, v = np.meshgrid((np.arange(n) + .5) / n, (np.arange(n) + .5) / n)
u, v = u.astype(np.float32), v.astype(np.float32)
inner = 1 - 1 + u * 1                                   # A: first entry, strength 1
inner = inner * (1 - 1 + v * 1)                         # B: multiply, strength 1
lv = np.clip((inner - .1) / .8, 0, 1)                   # Levels filter (effect black/white)
inner = inner * 0 + lv                                  # NORMAL fold at strength 1
g = levels(by_id(group_id), inner)
result = 1 - .8 + g * .8                                # group is the first outer entry: 1-s+v*s
result = np.clip(result + 0, 0, 1)
c = v                                                   # C: v gradient, operation MULTIPLY default
result = result * (1 - .5 + c * .5)
actual = np.concatenate([evaluate_masks(ts, layer_of(), n, y0, min(n, y0 + 128), {}, {}) for y0 in range(0, n, 128)])
assert actual.shape == (n, n) and np.abs(actual - result).max() < 2e-3, float(np.abs(actual - result).max())
# Disabling the group removes its whole run from the stack.
by_id(group_id).enabled = False
actual = evaluate_masks(ts, layer_of(), n, 0, n, {}, {})
assert np.abs(actual - (1 - .5 + v * .5)).max() < 2e-3
by_id(group_id).enabled = True
print('MASK_GROUPS_MODEL_OK', flush=True)

# Structure operators keep member runs contiguous.
masks = layer_of().masks
c_index = next(i for i, m in enumerate(masks) if m.id == c_id)
assert mask_stack.into_group_above(layer_of(), c_index) == 0 and by_id(c_id).parent_id == group_id
assert mask_stack.block(list(layer_of().masks), 0) == (0, 5)
out = mask_stack.out_of_group(layer_of(), 4)
assert out == 4 and by_id(c_id).parent_id == '' and mask_stack.block(list(layer_of().masks), 0) == (0, 4)
moved = mask_stack.move(layer_of(), 0, 1)               # the group block passes C
assert [m.id for m in layer_of().masks] == [c_id, group_id, a_id, b_id, lv_id] and moved == 1
moved = mask_stack.move(layer_of(), 1, -1)
assert [m.id for m in layer_of().masks] == [group_id, a_id, b_id, lv_id, c_id] and moved == 0
assert mask_stack.move(layer_of(), 2, -1) == 1          # B above A inside the group
assert [m.id for m in layer_of().masks][:3] == [group_id, b_id, a_id]
assert mask_stack.move(layer_of(), 1, 1) == 2
assert mask_stack.move(layer_of(), 3, 1) == 3            # Levels is the last member: stays
# Adding while a member is active joins its group at the end of the run (mask_stack.adopt).
nested_id = add('GROUP', 'Inner')
assert mask_stack.adopt(layer_of(), 5, 3) == 4 and by_id(nested_id).parent_id == group_id
d_id = add('FILL', 'D', image=u_image)
assert mask_stack.adopt(layer_of(), 6, 4) == 5 and by_id(d_id).parent_id == nested_id
assert [m.id for m in layer_of().masks] == [group_id, a_id, b_id, lv_id, nested_id, d_id, c_id]
assert mask_stack.rows(list(layer_of().masks))[4:6] == [(4, 1), (5, 2)]
assert mask_stack.block(list(layer_of().masks), 0) == (0, 6)
ts.active_layer = stack.index_of(ts, layer_id)
layer_of().active_mask = 5
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.mask(action='OUT_OF_GROUP') == {'FINISHED'}
assert by_id(d_id).parent_id == group_id and mask_stack.block(list(layer_of().masks), 0) == (0, 6)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.mask(action='ADD', source='PROCEDURAL') == {'FINISHED'}
added = layer_of().masks[layer_of().active_mask]
assert added.parent_id == group_id and mask_stack.block(list(layer_of().masks), 0) == (0, 7)
layer_of().active_mask = next(i for i, m in enumerate(layer_of().masks) if m.id == added.id)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.mask(action='DELETE') == {'FINISHED'}
layer_of().active_mask = next(i for i, m in enumerate(layer_of().masks) if m.id == nested_id)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.mask(action='DISSOLVE') == {'FINISHED'}
assert not any(m.id == nested_id for m in layer_of().masks) and by_id(d_id).parent_id == group_id
layer_of().active_mask = next(i for i, m in enumerate(layer_of().masks) if m.id == d_id)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.mask(action='DELETE') == {'FINISHED'}
assert [m.id for m in layer_of().masks] == [group_id, a_id, b_id, lv_id, c_id]
print('MASK_GROUPS_STRUCTURE_OK', flush=True)

# Shader parity of the nested stack against the CPU reference (smooth sources).
ts.preview_backend = 'CPU'
shader_preview.clear()
bpy.context.view_layer.update()
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.bake.margin = 0; scene.render.bake.use_clear = True
target = bpy.data.images.new('Mask groups readback', width=N, height=N, float_buffer=True)
target.colorspace_settings.name = 'Non-Color'
small = SampledSet(ts)


def bake_channel(graph):
    tree = ts.material.node_tree
    output = next(node for node in tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    bake_node = tree.nodes.new('ShaderNodeTexImage')
    bake_node.image = target
    tree.nodes.active = bake_node
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    tree.links.new(graph.outputs['BASE_COLOR'], emission.inputs['Color'])
    bpy.ops.object.bake(type='EMIT')
    result = images.pixels(target)[..., :3].copy()
    tree.nodes.remove(bake_node); tree.nodes.remove(emission)
    return result


def cpu_channel():
    steps = LayerEvaluator(scene).channel_steps(ts.id, 'BASE_COLOR', preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., :3].copy()


graph = Graph(scene, small, ts.material)
graph.build()
while graph.pending_resources:
    graph.step_resources(.1)
error = float(np.abs(bake_channel(graph) - cpu_channel()).max())
assert error < 3e-4, error
nodes = graph.node_count
graph.close()
material_manager.repair(ts)
print('MASK_GROUPS_PARITY', error, flush=True)


def parity(label):
    """The baked Rust coverage against mask_eval at the sampled size (the CPU
    channel harness scales filter radii with the preview size; the shader's
    boundary rasters the authored radius at its own size)."""
    graph = Graph(scene, small, ts.material)
    graph.build()
    while graph.pending_resources:
        graph.step_resources(.1)
    coverage = np.concatenate([evaluate_masks(ts, layer_of(), N, y0, min(N, y0 + 16), {}, {}) for y0 in range(0, N, 16)])
    expected = .1 + (.9 - .1) * coverage
    error = float(np.abs(bake_channel(graph)[..., 0] - expected).max())
    assert error < 3e-4, (label, error)
    print('MASK_GROUPS_PARITY', label, error, flush=True)
    return graph


# A spatial filter inside a group rasters the group's own run, not the outer stack
# folded through the group again (0.9.7).
blur_id = add('FILTER', 'Blur')
by_id(blur_id).effect.kind, by_id(blur_id).effect.radius, by_id(blur_id).effect.space = 'GAUSSIAN_BLUR', 3., 'UV'
assert mask_stack.adopt(layer_of(), 5, 3) == 4 and by_id(blur_id).parent_id == group_id
assert [m.id for m in layer_of().masks] == [group_id, a_id, b_id, lv_id, blur_id, c_id]
graph = parity('blur_in_group')
prefix_node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["mask_prefix"'))
graph.close(); material_manager.repair(ts)
# Undo rehydration and continuations rebuild the prefix with the same group walk:
# the members of a disabled group stay out of a later top-level filter.
assert mask_stack.out_of_group(layer_of(), 4) == 4 and by_id(blur_id).parent_id == ''
by_id(group_id).enabled = False
graph = parity('blur_after_disabled_group')
built = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterMaskPrefix:'))
from open_painter.constants import OWNER
for stale in [n for n in ts.material.node_tree.nodes if n.get(OWNER) and n.get('role') == 'Procedural Graph'
              and n != graph.instance]:
    ts.material.node_tree.nodes.remove(stale)   # the detached empty-set graph from sets.create
probe = Graph(scene, small, ts.material)
assert probe.rehydrate(), probe._last
rehydrated = next(b for b in probe.resource_bindings if b.identifier.startswith('RasterMaskPrefix:'))
assert rehydrated.signature == built.signature, 'the rehydrated prefix includes the disabled group\'s member'
assert probe._last['raster_boundaries_queued'] == 0 and rehydrated.node.image == built.node.image
probe.detach()
graph.close(); material_manager.repair(ts)
by_id(group_id).enabled = True
layer_of().masks.remove(next(i for i, m in enumerate(layer_of().masks) if m.id == blur_id))
assert [m.id for m in layer_of().masks] == [group_id, a_id, b_id, lv_id, c_id]
print('MASK_GROUPS_FILTER_PREFIX_OK', flush=True)

# Custom Graph: native in the shader, baked with Cycles for the CPU reference.
graph_id = add('GRAPH', 'Custom', operation='MULTIPLY')
tree = bpy.data.node_groups.new('Mask ramp', 'ShaderNodeTree')
tree.interface.new_socket(name='UV', in_out='INPUT', socket_type='NodeSocketVector')
tree.interface.new_socket(name='Coverage', in_out='OUTPUT', socket_type='NodeSocketFloat')
group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
separate = tree.nodes.new('ShaderNodeSeparateXYZ')
tree.links.new(group_in.outputs['UV'], separate.inputs[0])
tree.links.new(separate.outputs['X'], group_out.inputs['Coverage'])
by_id(graph_id).node_group = tree
before = custom_graph.signature(tree)
raster = custom_graph.raster(ts, by_id(graph_id), 64)
uu = (np.arange(64) + .5) / 64
assert np.abs(raster - uu[None, :]).max() < .03, float(np.abs(raster - uu[None, :]).max())
graph = Graph(scene, small, ts.material)
graph.build()
while graph.pending_resources:
    graph.step_resources(.1)
assert any(node.get('op_custom_graph') == 'Mask ramp' for node in graph.nodes())
assert graph.node_count > nodes
shader_result = bake_channel(graph)
# The custom ramp multiplies the rust coverage by u: the right side is rustier than the left.
assert shader_result[:, -4:, 0].mean() > shader_result[:, :4, 0].mean() + .1
graph.close()
material_manager.repair(ts)
separate.outputs['X'].default_value = 0.0
tree.links.remove(tree.links[-1])
tree.links.new(separate.outputs['Y'], group_out.inputs['Coverage'])
assert custom_graph.signature(tree) != before, 'editing the group changes its signature'
raster = custom_graph.raster(ts, by_id(graph_id), 64)
assert np.abs(raster - uu[:, None]).max() < .03
# The identity also covers what the property walk misses (0.9.7): interface defaults (what a
# fresh instance reads for an unlinked input), ramp stops, curve points, node and link mutes.
before = custom_graph.signature(tree)
scale = tree.interface.new_socket(name='Scale', in_out='INPUT', socket_type='NodeSocketFloat')
assert custom_graph.signature(tree) != before
before = custom_graph.signature(tree)
scale.default_value = 20.
assert custom_graph.signature(tree) != before, 'an interface default'
ramp = tree.nodes.new('ShaderNodeValToRGB')
before = custom_graph.signature(tree)
ramp.color_ramp.elements[0].position = .6
assert custom_graph.signature(tree) != before, 'a ramp stop'
before = custom_graph.signature(tree)
ramp.color_ramp.interpolation = 'CONSTANT'
assert custom_graph.signature(tree) != before, 'ramp interpolation'
before = custom_graph.signature(tree)
ramp.mute = True
assert custom_graph.signature(tree) != before, 'a node mute'
curve = tree.nodes.new('ShaderNodeRGBCurve')
before = custom_graph.signature(tree)
curve.mapping.curves[3].points[0].location = (0., .3)
curve.mapping.update()
assert custom_graph.signature(tree) != before, 'a curve point'
before = custom_graph.signature(tree)
tree.links[-1].is_muted = True
assert custom_graph.signature(tree) != before, 'a muted link'
tree.links[-1].is_muted = False
volume = tree.nodes.new('ShaderNodeVolumePrincipled')   # its attribute inputs are String sockets
assert any(s.type == 'STRING' and s.default_value for s in volume.inputs)
assert custom_graph.signature(tree) == custom_graph.signature(tree), 'string sockets sign as text'
for node in (ramp, curve, volume):
    tree.nodes.remove(node)
tree.interface.remove(scale)
# The CPU raster is baked over the assigned meshes, so it follows the geometry stamp (0.9.7):
# a group reading Position is placement-aware, and the export path (no force) re-bakes after
# the plane moves instead of serving the cached raster.
from open_painter.evaluation import dependencies
assert not dependencies.uses_placement(ts)
height = bpy.data.node_groups.new('Mask height', 'ShaderNodeTree')
height.interface.new_socket(name='Position', in_out='INPUT', socket_type='NodeSocketVector')
height.interface.new_socket(name='Coverage', in_out='OUTPUT', socket_type='NodeSocketFloat')
height_in, height_out = height.nodes.new('NodeGroupInput'), height.nodes.new('NodeGroupOutput')
height_xyz = height.nodes.new('ShaderNodeSeparateXYZ')
height.links.new(height_in.outputs['Position'], height_xyz.inputs[0])
height.links.new(height_xyz.outputs['Z'], height_out.inputs['Coverage'])
by_id(graph_id).node_group = height
assert dependencies.uses_geometry(ts) and dependencies.uses_placement(ts)
output = lambda: images.pixels(next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR'))[..., 0]
evaluator = LayerEvaluator(scene)
evaluator.evaluate_texture_set(ts.id)
low = output()
assert abs(float(low.mean()) - .1) < .02, float(low.mean())   # z = 0: the graph masks the rust out
plane.location.z = 1.
bpy.context.view_layer.update()
dependencies.refresh(scene)
assert ts.dirty, 'moving the plane invalidates a set whose only geometry consumer is the graph'
evaluator.evaluate_texture_set(ts.id)
high = output()
assert float(high.mean()) > float(low.mean()) + .1, (float(low.mean()), float(high.mean()))
plane.location.z = 0.
bpy.context.view_layer.update()
by_id(graph_id).node_group = tree
# The live session adopts the graph and reports it in the structural signature.
ts.preview_backend = 'LIVE'
material_manager.repair(ts)
deadline = monotonic() + 60
while monotonic() < deadline and not shader_preview.current(scene, ts):
    shader_preview.tick(scene, ts)
    assert not shader_preview.diagnostics(scene, ts)['error'], shader_preview.diagnostics(scene, ts)
assert shader_preview.current(scene, ts)
builds = shader_preview.diagnostics(scene, ts)['graph_builds']
by_id(graph_id).node_group = None
assert shader_preview.diagnostics(scene, ts)['error'] or shader_preview.diagnostics(scene, ts)['rebuild_pending']
by_id(graph_id).node_group = tree
# Group membership follows the fresh mask ids of a duplicated layer and of a loaded material.
from open_painter.smart_materials.loader import apply
from open_painter.smart_materials.serializer import save
copy = stack.duplicate(ts, stack.index_of(ts, layer_id))
copy_masks = list(copy.masks)
assert copy_masks[0].source == 'GROUP' and copy_masks[0].id != group_id
assert all(m.parent_id == copy_masks[0].id for m in copy_masks[1:4]), [m.parent_id for m in copy_masks]
assert mask_stack.block(copy_masks, 0) == (0, 4)
assert not any(m.parent_id == group_id for m in copy_masks)
stack.remove(ts, stack.index_of(ts, copy.id))
by_id(graph_id).node_group = None
copy_masks = [m for m in layer_of().masks if m.id != graph_id]
layer_of().masks.remove(next(i for i, m in enumerate(layer_of().masks) if m.id == graph_id))
archive = ROOT / '.blender-test' / 'mask-groups-material.zip'
archive.unlink(missing_ok=True)
start = len(ts.layers)
save(ts, archive)
loaded_ids = [l.id for l in apply(ts, archive)]
loaded = [l for l in ts.layers[start:] if l.name == 'Rust']
assert len(loaded) == 1, [l.name for l in ts.layers]
loaded_masks = list(loaded[0].masks)
assert loaded_masks[0].source == 'GROUP' and loaded_masks[0].id != group_id
assert all(m.parent_id == loaded_masks[0].id for m in loaded_masks[1:4]), [m.parent_id for m in loaded_masks]
assert mask_stack.block(loaded_masks, 0) == (0, 4)
while len(ts.layers) > start:
    stack.remove(ts, len(ts.layers) - 1)
graph_id = add('GRAPH', 'Custom', operation='MULTIPLY', node_group=tree)
# Portable materials refuse custom graphs explicitly.
archive.unlink(missing_ok=True)
try:
    save(ts, archive)
except ValueError as exc:
    assert 'Custom Graph' in str(exc)
else:
    raise AssertionError('a custom graph must be refused by the serializer')
print('MASK_GROUPS_PASS', flush=True)
open_painter.unregister()
