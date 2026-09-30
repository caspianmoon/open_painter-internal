"""Mask Graphs: Open Painter's own procedural nodes behind a Custom Graph mask.

Headless (``gpu.init``). Every node type, operation, blend and ramp mode is evaluated
by the engine (the graph's own GPU pass) and by the CPU reference export uses, and
the two agree on every texel (procedural sources compare by the 99.9th percentile:
isolated cell edges may round the other way). A numeric edit in a graph is a uniform
(no rebuild, no program compiled); a structural edit rebuilds the graph's pass only.
An invalid graph reports why and recovers. Save and reload keep the graph, and the
templates and new Custom Graph masks create Mask Graphs, not Blender shader groups.
A Mask Graph is an Open Painter Graph tree (one Open Painter node editor): each kind
of graph accepts only its own nodes, and a graph saved as a tree type of its own by an
earlier 0.13 build is rebuilt as one on load, node for node.
"""
import json
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.procedural_graph import is_native, templates
from open_painter.procedural_graph.tree import MIX_BLENDS, MATH_OPERATIONS, RAMP_MODES, TREE_ID, new_tree
from open_painter.utils.ids import new_id


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


N = 512
report = {'cases': []}


def ready(scene, ts, seconds=60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def cpu_channel(scene, ts):
    steps = LayerEvaluator(scene).channel_steps(ts.id, 'BASE_COLOR', preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., 0].copy()


def compare(scene, ts, label, statistical=False):
    info = ready(scene, ts)
    engine = shader_preview.session_engine(scene, ts)
    gpu = engine.render_channel('BASE_COLOR')[..., 0]
    cpu = cpu_channel(scene, ts)
    diff = np.abs(gpu - cpu)
    record = dict(case=label, max=float(diff.max()), p999=float(np.quantile(diff, .999)),
                  large=float((diff > .05).mean()), mean=float(cpu.mean()))
    report['cases'].append(record)
    if statistical:
        assert record['p999'] < 3e-3 and record['large'] < 5e-3, record
    else:
        assert record['max'] < 2e-3, record
    print('MASK_GRAPH', json.dumps(record), flush=True)
    return info


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
runtime.PRECISE = True
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Mask graphs', N)
sets.assign(scene, ts, bpy.context.object)
u, v = np.meshgrid((np.arange(64, dtype=np.float32) + .5) / 64, (np.arange(64, dtype=np.float32) + .5) / 64)
for kind in ('CURVATURE', 'AO'):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create('Graph ' + kind, 64)
    data = np.ones((64, 64, 4), np.float32)
    data[..., :3] = (.15 + .7 * u * v)[..., None] if kind == 'CURVATURE' else (.9 - .5 * u)[..., None]
    images.write(entry.image, data, pack=False)
layer = stack.add(ts, 'FILL', 'Masked white')
layer_id = layer.id
for prop in layer.channels:
    prop.enabled = prop.channel == 'BASE_COLOR'
stack.channel_for(layer, 'BASE_COLOR').color = (1, 1, 1, 1)
mask = layer.masks.add()
mask_id = mask.id = new_id()
mask.source = 'GRAPH'
tree = new_tree('Graph under test')
mask.node_group = tree
coordinates = tree.nodes.new('OPMGCoordinates')
output = tree.nodes.new('OPMGOutput')
material_manager.repair(ts)


def the_mask():
    layer = next(item for item in ts.layers if item.id == layer_id)
    return next(item for item in layer.masks if item.id == mask_id)


def graph_key():
    return ('GRAPH', layer_id, mask_id)


def clear(keep=('OPMGCoordinates', 'OPMGOutput')):
    for node in [node for node in tree.nodes if node.bl_idname not in keep]:
        tree.nodes.remove(node)


def node(idname, **values):
    item = tree.nodes.new(idname)
    for name, value in values.items():
        setattr(item, name, value)
    return item


def link(source, target, output_name='Value', input_name='Value'):
    tree.links.new(source.outputs[output_name], target.inputs[input_name])


def to_output(source, name='Value'):
    tree.links.new(source.outputs[name], output.inputs['Coverage'])


# ---- 1. sources: every texture node type, coordinates, a value, a mesh map ----------------------------
noise = node('OPMGNoise', kind='FBM', scale=6.0, seed=3)
link(coordinates, noise, 'UV', 'Vector')
to_output(noise)
info = compare(scene, ts, 'noise FBM', statistical=True)
graph = shader_preview.session_graph(scene, ts)
assert graph_key() in graph.units and ('MASK', layer_id, '', False) in graph.units, sorted(graph.units)
assert graph.units[('MASK', layer_id, '', False)].imports[0][0] == graph_key(), 'the mask does not sample its graph'
material = ts.material.node_tree
assert len(material.nodes) <= 10 and not any(n.bl_idname == 'ShaderNodeGroup' for n in material.nodes)
for idname, kinds in (('OPMGNoise', ('VALUE', 'RIDGED', 'WARPED')), ('OPMGCells', ('VORONOI', 'WORLEY')),
                      ('OPMGPattern', ('CHECKER', 'BRICK_PATTERN', 'RADIAL_GRADIENT')),
                      ('OPMGSurface', ('GRUNGE_MEDIUM', 'SCRATCHES', 'RUST_FLAKES'))):
    clear()
    source = node(idname, kind=kinds[0], scale=5.0, seed=11)
    link(coordinates, source, 'UV', 'Vector')
    to_output(source)
    for kind in kinds:
        source.kind = kind
        compare(scene, ts, f'{idname} {kind}', statistical=True)
for name in ('Position', 'Normal'):
    clear()
    source = node('OPMGNoise', kind='FBM', scale=3.0)
    link(coordinates, source, name, 'Vector')
    to_output(source)
    compare(scene, ts, f'noise of the world {name.lower()}', statistical=True)
clear()
value = node('OPMGValue', value=.37)
to_output(value)
compare(scene, ts, 'value')
clear()
mesh_map = node('OPMGMeshMap', kind='CURVATURE')
to_output(mesh_map)
compare(scene, ts, 'mesh map curvature')
mesh_map.kind = 'AO'
compare(scene, ts, 'mesh map AO')
print('MASK_GRAPHS sources: PASS', flush=True)

# ---- 2. converters: every math operation, blend and ramp mode; levels; mapping ---------------------------
clear()
noise = node('OPMGNoise', kind='FBM', scale=4.0, seed=5)
link(coordinates, noise, 'UV', 'Vector')
pattern = node('OPMGPattern', kind='STRIPES', scale=3.0)
link(coordinates, pattern, 'UV', 'Vector')
math = node('OPMGMath')
link(noise, math, 'Value', 'A')
math.inputs['B'].default_value = .7
to_output(math)
for key, _, _, _ in MATH_OPERATIONS:
    math.operation = key
    compare(scene, ts, f'math {key}', statistical=True)
math.operation, math.use_clamp = 'SUBTRACT', True
assert math.draw_label() == 'Subtract', math.draw_label()
link(pattern, math, 'Value', 'B')   # B comes back after a unary operation hid it
compare(scene, ts, 'math SUBTRACT two sources, clamped', statistical=True)
clear()
noise = node('OPMGNoise', kind='FBM', scale=4.0, seed=5)
link(coordinates, noise, 'UV', 'Vector')
cells = node('OPMGCells', kind='WORLEY', scale=6.0)
link(coordinates, cells, 'UV', 'Vector')
mix = node('OPMGMix')
link(noise, mix, 'Value', 'A')
link(cells, mix, 'Value', 'B')
mix.inputs['Factor'].default_value = .65
to_output(mix)
for key, _, _, _ in MIX_BLENDS:
    mix.blend = key
    compare(scene, ts, f'mix {key}', statistical=True)
mix.use_clamp = True
compare(scene, ts, 'mix clamped', statistical=True)
clear()
noise = node('OPMGNoise', kind='FBM', scale=4.0, seed=5)
link(coordinates, noise, 'UV', 'Vector')
ramp = node('OPMGRamp')
extra = ramp.stops.add()
extra.position, extra.value = .45, .9     # added last, sorted by position
ramp.stops[1].position, ramp.stops[1].value = .8, .1
link(noise, ramp, 'Value', 'Fac')
to_output(ramp)
for key, _, _, _ in RAMP_MODES:
    ramp.interpolation = key
    compare(scene, ts, f'ramp {key}', statistical=True)
clear()
noise = node('OPMGNoise', kind='FBM', scale=4.0, seed=5)
link(coordinates, noise, 'UV', 'Vector')
levels = node('OPMGLevels', black=.2, white=.85, gamma=1.7, output_black=.1, output_white=.9)
link(noise, levels)
to_output(levels)
compare(scene, ts, 'levels', statistical=True)
clear()
mapping = node('OPMGMapping', offset=(.13, -.4), rotation=.6, scale=(2.5, .7))
link(coordinates, mapping, 'UV', 'Vector')
checker = node('OPMGPattern', kind='CHECKER', scale=4.0)
link(mapping, checker, 'Vector', 'Vector')
to_output(checker)
compare(scene, ts, 'mapping into a checker', statistical=True)
print('MASK_GRAPHS converters: PASS', flush=True)

# ---- 3. numeric edits are uniforms; structural edits rebuild the graph's pass only -----------------------------
clear()
noise = node('OPMGNoise', kind='FBM', scale=4.0, seed=5)
link(coordinates, noise, 'UV', 'Vector')
math = node('OPMGMath', operation='MULTIPLY')
link(noise, math, 'Value', 'A')
math.inputs['B'].default_value = .9
to_output(math)
info = compare(scene, ts, 'edit baseline', statistical=True)
builds = revisions(info)
compiles = runtime.device().compiles
with Metrics() as observed:
    noise.scale = 7.5
    noise.seed = 1238065627
    math.inputs['B'].default_value = .6
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'] and revisions(info) == builds, info
    compare(scene, ts, 'after numeric edits', statistical=True)
assert revisions(shader_preview.diagnostics(scene, ts)) == builds
assert runtime.device().compiles == compiles, 'a numeric graph edit compiled a program'
assert observed.counts['shader_resource_requests'] == 0, observed.report()
math.operation = 'MAXIMUM'
info = shader_preview.diagnostics(scene, ts)
assert info['rebuild_pending'] and info['rebuild_scope'] == [graph_key()], info
info = compare(scene, ts, 'after an operation change', statistical=True)
assert revisions(info) == builds + 1 and info['rebuilt_units'] == 1, info
cells = node('OPMGCells', kind='VORONOI', scale=5.0)
link(coordinates, cells, 'UV', 'Vector')
link(cells, math, 'Value', 'B')
info = compare(scene, ts, 'after a relink', statistical=True)
assert revisions(info) == builds + 2 and info['rebuilt_units'] == 1, info
print('MASK_GRAPHS edits: PASS', flush=True)

# ---- 4. an invalid graph reports why and recovers ---------------------------------------------------------
tree.nodes.remove(output)
shader_preview.tick(scene, ts)
info = shader_preview.diagnostics(scene, ts)
assert info['state'] == 'FAILED' and 'Mask Output' in info['error'], info
output = tree.nodes.new('OPMGOutput')
to_output(math)
compare(scene, ts, 'recovered', statistical=True)
print('MASK_GRAPHS invalid graph: PASS', flush=True)

# ---- 5. save and reload keep the graph -----------------------------------------------------------------------
path = ROOT / '.blender-test' / 'mask-graphs.blend'
path.parent.mkdir(exist_ok=True)
expected = cpu_channel(scene, ts)
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Mask graphs')
tree = the_mask().node_group
assert is_native(tree) and tree.name == 'Graph under test', tree
material_manager.repair(ts)
compare(scene, ts, 'after reload', statistical=True)
assert np.abs(cpu_channel(scene, ts) - expected).max() < 1e-6, 'the reloaded graph evaluates differently'
print('MASK_GRAPHS persistence: PASS', flush=True)

# ---- 6. templates and new masks create Mask Graphs ----------------------------------------------------------------
for kind in templates.KINDS:
    built = templates.build(kind, 'Template ' + kind)
    assert is_native(built) and any(n.bl_idname == 'OPMGOutput' for n in built.nodes), kind
    the_mask().node_group = built
    compare(scene, ts, 'template ' + kind, statistical=True)
from open_painter.graph import templates as graph_templates
from open_painter.ui.properties_panel import starter_graph
assert is_native(graph_templates.shader_group('NOISE_RAMP', 'From the graph menu'))
assert is_native(starter_graph('From the layers panel'))
assert not [g for g in bpy.data.node_groups if g.bl_idname == 'ShaderNodeTree' and 'Template' in g.name]
print('MASK_GRAPHS templates: PASS', flush=True)

# ---- 7. a removed node is never read: a node replaced by an identical one, reallocated ramp stops, a deleted graph ----
from open_painter.evaluation.shader_graph import _removed_graph_units
tree = new_tree('Graph edited')
the_mask().node_group = tree
coordinates = tree.nodes.new('OPMGCoordinates')
output = tree.nodes.new('OPMGOutput')
noise = node('OPMGNoise', kind='FBM', scale=6.0, seed=3)
link(coordinates, noise, 'UV', 'Vector')
to_output(noise)
compare(scene, ts, 'before a replacement', statistical=True)
held = list(shader_preview.session_graph(scene, ts).units[graph_key()].bindings.values())
tree.nodes.remove(noise)
assert _removed_graph_units(held) == [graph_key()], 'a removed node still reads as live'
noise = node('OPMGNoise', kind='FBM', scale=13.0, seed=3)     # the same structure, another node
link(coordinates, noise, 'UV', 'Vector')
to_output(noise)
the_mask().strength = .9                                      # a numeric edit reads the mask's bindings
compare(scene, ts, 'an identical node replaced', statistical=True)
ramp = node('OPMGRamp')
link(noise, ramp, 'Value', 'Fac')
to_output(ramp)
compare(scene, ts, 'ramp before new stops', statistical=True)
held = list(shader_preview.session_graph(scene, ts).units[graph_key()].bindings.values())
stops = {stop.as_pointer() for stop in ramp.stops}
for _ in range(12):   # the stops' storage grows: earlier stops move
    assert bpy.ops.open_painter.mask_graph_ramp_stop(tree=tree.name, node=ramp.name, action='ADD') == {'FINISHED'}
moved = not stops <= {stop.as_pointer() for stop in ramp.stops}
assert _removed_graph_units(held) == ([graph_key()] if moved else []), moved
ramp.stops[3].value = .95
compare(scene, ts, 'ramp after twelve new stops', statistical=True)
held = list(shader_preview.session_graph(scene, ts).units[graph_key()].bindings.values())
bpy.data.node_groups.remove(tree)                             # no callback reaches Open Painter
assert the_mask().node_group is None
assert _removed_graph_units(held) == [graph_key()]
the_mask().strength = .8                                      # must not read the deleted graph's nodes
for _ in range(20):
    shader_preview.tick(scene, ts)
tree = templates.build('CELLULAR_RAMP', 'Graph after a deletion')
the_mask().node_group = tree
compare(scene, ts, 'a new graph after a deletion', statistical=True)
tree = new_tree('Graph output only')
the_mask().node_group = tree
output = tree.nodes.new('OPMGOutput')
output.inputs['Coverage'].default_value = .4
compare(scene, ts, 'an unlinked Mask Output')
tree.nodes.remove(output)                                     # its Coverage uniform goes with it
output = tree.nodes.new('OPMGOutput')
output.inputs['Coverage'].default_value = .6
compare(scene, ts, 'a replaced unlinked Mask Output')
print('MASK_GRAPHS removed nodes: PASS', flush=True)

# ---- 8. one Open Painter node editor; graphs of the separate type convert on load -----------------------------------
from open_painter.graph.tree import TREE_ID as GRAPH_TREE
from open_painter.procedural_graph import program
from open_painter.procedural_graph.tree import LEGACY_ID, convert_legacy
assert TREE_ID == GRAPH_TREE and the_mask().node_group.bl_idname == GRAPH_TREE, 'a Mask Graph is an Open Painter Graph'
from open_painter.procedural_graph.tree import OP_MaskGraphTreeLegacy

assert not OP_MaskGraphTreeLegacy.poll(bpy.context), 'no second Open Painter editor type is offered'
set_graph = bpy.data.node_groups.new('A texture set graph', TREE_ID)
try:
    set_graph.nodes.new('OPMGNoise')
    raise AssertionError('a Mask Graph node was added to a texture set graph')
except RuntimeError:
    pass
try:
    tree.nodes.new('OPLayerNode')
    raise AssertionError('a layer node was added to a Mask Graph')
except RuntimeError:
    pass
bpy.data.node_groups.remove(set_graph)
legacy = bpy.data.node_groups.new('Graph from 0.13', LEGACY_ID)
built = {}
for name, idname, values in (('Coordinates', 'OPMGCoordinates', {}), ('Cells', 'OPMGCells', dict(kind='WORLEY', scale=7.0, seed=4)),
                             ('Noise', 'OPMGNoise', dict(kind='RIDGED', scale=3.0)), ('Math', 'OPMGMath', dict(operation='MAXIMUM')),
                             ('Ramp', 'OPMGRamp', dict(interpolation='EASE')), ('Output', 'OPMGOutput', {})):
    item = legacy.nodes.new(idname)
    item.name = name
    for key, value in values.items():
        setattr(item, key, value)
    built[name] = item
stop = built['Ramp'].stops.add()
stop.position, stop.value = .4, .8
built['Math'].inputs['B'].default_value = .3
for source, output_name, target, input_name in (('Coordinates', 'UV', 'Cells', 'Vector'), ('Coordinates', 'Position', 'Noise', 'Vector'),
                                               ('Cells', 'Value', 'Math', 'A'), ('Noise', 'Value', 'Math', 'B'),
                                               ('Math', 'Value', 'Ramp', 'Fac'), ('Ramp', 'Value', 'Output', 'Coverage')):
    legacy.links.new(built[source].outputs[output_name], built[target].inputs[input_name])
legacy.use_fake_user = True
expected = program.values(legacy)
the_mask().node_group = legacy          # as a file of that build saved it (the poll is not consulted on load)
assert the_mask().node_group == legacy
path = ROOT / '.blender-test' / 'mask-graphs-legacy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))   # load_post converts
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Mask graphs')
converted = the_mask().node_group
assert is_native(converted) and converted.name == 'Graph from 0.13' and converted.use_fake_user, converted
assert not any(group.bl_idname == LEGACY_ID for group in bpy.data.node_groups), 'the separate tree type is gone'
assert program.values(converted) == expected, 'the converted graph evaluates differently'
assert len(converted.nodes['Ramp'].stops) == 3 and converted.nodes['Math'].operation == 'MAXIMUM'
material_manager.repair(ts)
compare(scene, ts, 'a converted graph', statistical=True)
assert convert_legacy() == [], 'nothing left to convert'
print('MASK_GRAPHS one editor: PASS', flush=True)

report['result'] = 'PASS'
destination = ROOT / '.blender-test' / 'mask-graphs.json'
destination.write_text(json.dumps(report, indent=1))
print('MASK_GRAPHS_PASS', flush=True)
open_painter.unregister()
