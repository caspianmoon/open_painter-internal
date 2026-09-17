"""Live edge exposure: edge wear from the evaluated mesh without curvature maps, on
CPU and in the shader, that follows a Bevel modifier; convexity/concavity kinds
read a cylinder as flat and a box edge as a band."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images, material_manager
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.generators import scene_fields
from open_painter.utils.ids import new_id

N = 64
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.subdivide(number_cuts=9)   # per-vertex curvature needs vertices between the edges
bpy.ops.uv.smart_project(island_margin=.02); bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Live edges', 512); sets.assign(scene, ts, cube)
ts.preview_backend = 'CPU'
bpy.context.view_layer.update()


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


# 1. Convexity/concavity fields: a box has convex edges, no concave ones; a cylinder body is flat.
probe = stack.add(ts, 'FILL', 'Probe')
mask = probe.masks.add(); mask.id, mask.source = new_id(), 'SCENE'
field = mask.influence
field.kind, field.mode, field.resolution, field.distance = 'CONVEXITY', 'MANUAL', '128', .1
convex = scene_fields.compute(ts, field)
from open_painter.painting.projection import geometry
covered = geometry(ts, 128, coverage=True)[2]
assert convex.max() > .5 and (convex[covered] > .3).mean() < .6, (convex.max(), (convex[covered] > .3).mean())
assert np.percentile(convex[covered], 30) < .05, 'face interiors must read flat'
field.kind = 'CONCAVITY'
concave = scene_fields.compute(ts, field)
assert concave.max() < .2, concave.max()
bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=.5, depth=2)
cylinder = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.subdivide(number_cuts=3)
bpy.ops.uv.smart_project(island_margin=.02); bpy.ops.object.mode_set(mode='OBJECT')
other = sets.create(scene, 'Cylinder', 512); sets.assign(scene, other, cylinder)
other.preview_backend = 'CPU'
layer2 = stack.add(other, 'FILL', 'Probe')
mask2 = layer2.masks.add(); mask2.id, mask2.source = new_id(), 'SCENE'
mask2.influence.kind, mask2.influence.mode, mask2.influence.resolution, mask2.influence.distance = 'CONVEXITY', 'MANUAL', '128', .05
bpy.context.view_layer.update()
cyl = scene_fields.compute(other, mask2.influence)
cyl_covered = geometry(other, 128, coverage=True)[2]
# The body is the baseline (flat); only the two rims register.
assert cyl.max() > .2 and np.median(cyl[cyl_covered]) < .05, (cyl.max(), np.median(cyl[cyl_covered]))
print('LIVE_EDGE fields: PASS', flush=True)

# 2. Edge Wear with live edges and no curvature maps at all, on CPU.
ts.layers.remove(0)
base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
wear = stack.add(ts, 'FILL', 'Wear'); stack.channel_for(wear, 'ROUGHNESS').value = .9
edges = wear.masks.add(); edges.id, edges.source, edges.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
edges.amount, edges.contrast, edges.noise_strength = .6, 2., 0.
assert not ts.baked_maps
edges.edge_source = 'LIVE'
assert edges.influence.kind == 'EDGE' and edges.influence.mode == 'LIVE'
edges.influence.distance = .1
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
sharp = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()
assert sharp.max() > .5 and (sharp > .5).mean() < .5, 'edge wear without curvature maps'
assert not edges.influence.error
# A bevel modifier widens and softens the band: the wear follows the evaluated mesh.
from open_painter.evaluation import dependencies
modifier = cube.modifiers.new('Bevel', 'BEVEL'); modifier.width, modifier.segments = .08, 6
bpy.context.view_layer.update(); dependencies.refresh(scene)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
bevelled = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()
assert not np.allclose(sharp, bevelled), 'a bevel did not change live edge wear'
assert (bevelled > .5).mean() > (sharp > .5).mean() * .8, ((bevelled > .5).mean(), (sharp > .5).mean())
print('LIVE_EDGE cpu: PASS', flush=True)

# 2b. The cooperative engine owns live edges as it owns LIVE scene masks: with the scheduler
# timer registered an evaluation requests the analysis instead of running it (the masked layer
# reads 0 meanwhile) and the job publishes it — a generator mask must not be dropped as 'not SCENE'.
from open_painter.evaluation import field_jobs
from open_painter.evaluation.metrics import Metrics
bpy.app.timers.register(cache.tick, first_interval=1000)   # registered → the engine is active; driven by hand
assert field_jobs.active()
edges_id = edges.id
chips = wear.masks.add(); chips.id, chips.source, chips.generator = new_id(), 'GENERATOR', 'CHIPS'
chips.amount, chips.contrast, chips.noise_strength = .6, 2., 0.
chips.edge_source = 'LIVE'
chips.influence.distance = .1
dependencies.refresh(scene)
with Metrics() as counters:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert counters.phases.get('scene_analysis', {}).get('calls', 0) == 0, 'the evaluation analysed synchronously'
assert chips.influence.image is None and chips.influence.error == 'Analysing…'
assert field_jobs.pending(scene), 'the live edge did not request its analysis'
hidden = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0]
assert hidden.max() < .5, 'an unanalysed live edge must read 0 (the wear layer waits)'
started = monotonic()
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
    assert monotonic() - started < 60, 'the live-edge analysis never published'
assert chips.influence.image is not None and not chips.influence.error, chips.influence.error
assert chips.influence.provenance == repr(scene_fields.signature(ts, chips.influence))
assert images.pixels(chips.influence.image)[..., 0].max() > .5, 'the published edge field is empty'
assert ts.dirty, 'publication did not invalidate the set'
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
chipped = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0]
assert chipped.max() > .5 and not field_jobs.pending(scene), 'the wear layer did not return once analysed'
# A flush publishes a pending live edge too (a save or export runs every request to completion).
chips.influence.distance = .12
dependencies.refresh(scene)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert field_jobs.pending(scene)
field_jobs.flush(scene)
assert not field_jobs.pending(scene) and chips.influence.provenance == repr(scene_fields.signature(ts, chips.influence))
analysis = chips.influence.image
wear.masks.remove(next(i for i, m in enumerate(wear.masks) if m.id == chips.id))
if analysis is not None and analysis.users <= 1:
    bpy.data.images.remove(analysis)
edges = next(m for m in wear.masks if m.id == edges_id)   # masks.add() re-allocated the collection
bpy.app.timers.unregister(cache.tick)
field_jobs.cancel_all()
print('LIVE_EDGE job engine: PASS', flush=True)

# 3. Shader parity: the live edge is a boundary raster; CPU and shader agree.
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.bake.margin = 0; scene.render.bake.use_clear = True
target = bpy.data.images.new('Live edge readback', width=N, height=N, float_buffer=True)
target.colorspace_settings.name = 'Non-Color'
small = SampledSet(ts)
graph = Graph(scene, small, ts.material)
graph.build()
while graph.pending_resources:
    graph.step_resources(.1)
assert any(node.get('op_boundary', '').startswith('["scene_edge"') for node in graph.nodes()), 'no live-edge boundary compiled'
tree = ts.material.node_tree
output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
emission = tree.nodes.new('ShaderNodeEmission')
bake_node = tree.nodes.new('ShaderNodeTexImage'); bake_node.image = target; tree.nodes.active = bake_node
tree.links.new(emission.outputs[0], output.inputs['Surface'])
tree.links.new(graph.outputs['ROUGHNESS'], emission.inputs['Color'])
bpy.ops.object.select_all(action='DESELECT'); cube.select_set(True); bpy.context.view_layer.objects.active = cube
bpy.ops.object.bake(type='EMIT')
actual = images.pixels(target)[..., 0]
steps = LayerEvaluator(scene).channel_steps(ts.id, 'ROUGHNESS', preview_resolution=N)
try:
    while True:
        next(steps)
except StopIteration as done:
    expected = images.pixels(done.value)[..., 0]
# The bake only writes covered texels of the cube's islands: compare inside them, away from seams.
coverage = geometry(ts, N, coverage=True)[2]
interior = coverage.copy()
interior[1:] &= coverage[:-1]; interior[:-1] &= coverage[1:]; interior[:, 1:] &= coverage[:, :-1]; interior[:, :-1] &= coverage[:, 1:]
error = float(np.abs(actual - expected)[interior].max())
assert interior.sum() > N * N // 4
assert error <= 1 / 255 + 1e-3, error
assert expected.std() > 1e-3
graph.close(); material_manager.repair(ts)
print('LIVE_EDGE shader parity: PASS', json_error := round(error, 5), flush=True)

# 4. Structural fields: edge_source is structural; the field's reach re-analyses but does not rebuild.
ts.preview_backend = 'LIVE'
material_manager.repair(ts)
deadline = monotonic() + 60
while monotonic() < deadline:
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert not info['error'], info
    if shader_preview.current(scene, ts):
        break
builds = info['graph_builds']
edges_rna = next(m for layer in ts.layers for m in layer.masks if m.id == edges.id)
edges_rna.influence.distance = .07
assert shader_preview.current(scene, ts) or shader_preview.diagnostics(scene, ts)['graph_builds'] == builds
while monotonic() < deadline:
    shader_preview.tick(scene, ts)
    if shader_preview.current(scene, ts):
        break
assert shader_preview.diagnostics(scene, ts)['graph_builds'] == builds, 'a reach edit rebuilt the graph'
edges_rna.edge_source = 'BAKED'
assert not shader_preview.current(scene, ts), 'edge_source is structural'
print('LIVE_EDGE structural: PASS', flush=True)

# 5. A lowered analysis grid survives copies: restore replays edge_source after
# influence, and the callback's 64 -> 512 default belongs to the first switch only.
edges_rna.edge_source = 'LIVE'
edges_rna.influence.resolution = '64'
edges_rna.edge_source = 'BAKED'; edges_rna.edge_source = 'LIVE'
assert edges_rna.influence.resolution == '64', 'a second switch to live edges overrode the chosen grid'
probe = wear.masks.add(); probe.id, probe.source, probe.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
probe.edge_source = 'LIVE'
assert probe.influence.resolution == '512', 'a fresh live edge keeps the generic 64 grid'
wear.masks.remove(len(wear.masks) - 1)
wear_index = next(i for i, layer in enumerate(ts.layers) if layer.name == 'Wear')
stack.duplicate(ts, wear_index)
copy = next(m for layer in ts.layers if layer.name == 'Wear Copy' for m in layer.masks)
assert (copy.edge_source, copy.influence.kind, copy.influence.mode, copy.influence.resolution) == \
    ('LIVE', 'EDGE', 'LIVE', '64'), (copy.edge_source, copy.influence.kind, copy.influence.mode, copy.influence.resolution)
set_index = next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == ts.id)
sets.duplicate(scene, set_index)
twin = next(item for item in scene.open_painter.texture_sets if item.name == ts.name + ' Copy')
grids = {layer.name: m.influence.resolution for layer in twin.layers for m in layer.masks if m.edge_source == 'LIVE'}
assert grids == {'Wear': '64', 'Wear Copy': '64'}, grids
print('LIVE_EDGE copies keep the grid: PASS', flush=True)
print('LIVE_EDGE_EXPOSURE_PASS', flush=True)
open_painter.unregister()
