"""Shared UDIM ownership, frozen Brush Graphs, particle contacts and v5 materials."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, shared_stacks
from open_painter.evaluation import cache
from open_painter.painting import brush_graph, particles, strokes, stroke_raster, payload
from open_painter.smart_materials import serializer, loader

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
a, b = sets.create(scene, 'Tile A', 512), sets.create(scene, 'Tile B', 1024)
a, b = scene.open_painter.texture_sets[-2:]
for tile, number in ((a, 1001), (b, 1002)):
    tile.udim_group, tile.udim_tile = 'test-group', number
    stack.add(tile, 'PAINT', 'Shared')
images = []
for tile in (a, b):
    image = bpy.data.images.new(tile.name, width=8, height=8, float_buffer=True)
    tile.layers[-1].channels[0].image = image
    images.append(image)
shared_stacks.link([a, b])
assert shared_stacks.layer_owner(b) == a
assert a.shared_stack_version == b.shared_stack_version == 1
b.layers[-1].opacity = .37
assert abs(a.layers[-1].opacity - .37) < 1e-6
assert [t.layers[-1].channels[0].image for t in (a, b)] == images
created = stack.add(b, 'FILL', 'From B')
assert [l.id for l in a.layers] == [l.id for l in b.layers]
created.channels[0].color = (.7, .2, .1, 1)
assert np.allclose(a.layers[-1].channels[0].color, created.channels[0].color)
# Both tiles share a scoped recorded stroke; unlink gives each its own editable copy.
samples = np.asarray([[0, 0, 0, 0, 0, 1, 1, .1, 0, 0, 1, 0, 0]], np.float32)
record = strokes.record(scene.open_painter, a, a.layers[0], samples, scene.open_painter.vector_brush,
                        universal=False, dabs=[{'donor': [0, 0, 0]}])
record.scope = a.id + ',' + b.id
saved_payload = record.dab_payload
shared_stacks.make_independent(b)
assert not a.shared_stack_id and not b.shared_stack_id
assert a.layers[0].stroke_key != b.layers[0].stroke_key
for tile in (a, b):
    copied = strokes.strokes_for(scene.open_painter, tile.layers[0].stroke_key, tile)
    assert len(copied) == 1 and copied[0].dab_payload == saved_payload
    assert copied[0].scope == tile.id
b.layers[-1].opacity = .13
before = stack.snapshot(a.layers[-1])
try:
    shared_stacks.link([a, b])
    raise AssertionError('Conflicting stacks linked')
except ValueError:
    pass
assert stack.snapshot(a.layers[-1]) == before
print('SHARED_UDIM_PASS', flush=True)

tree = bpy.data.node_groups.new('Deterministic Brush', 'OpenPainterGraph')
tree[brush_graph.MARKER] = True
inputs, random, output = [tree.nodes.new(name) for name in ('OPBrushInputs', 'OPBrushRandom', 'OPBrushOutput')]
tree.links.new(inputs.outputs['Pressure'], output.inputs['Flow'])
tree.links.new(random.outputs['Value'], output.inputs['Roughness'])
program = brush_graph.compile(tree)
context = dict(brush_graph.INPUTS, Pressure=.27, **{'Sample Index': 19})
first = brush_graph.evaluate(program, context, 33)
random.seed = 91
second = brush_graph.evaluate(program, context, 33)
assert all(np.array_equal(first[key], second[key]) for key in first)
math = tree.nodes.new('OPBrushMath')
tree.links.new(math.outputs['Value'], math.inputs['A'])
tree.links.new(math.outputs['Value'], output.inputs['Radius'])
try:
    brush_graph.compile(tree)
    raise AssertionError('Cyclic graph accepted')
except ValueError:
    pass
print('BRUSH_GRAPH_PASS', flush=True)

settings = scene.open_painter.vector_brush
def collide(start, delta):
    if start[2] > 0 and start[2] + delta[2] <= 0:
        return start + delta * (-start[2] / delta[2]), (0, 0, 1), a.id, 0, 1
    return None
left = particles.contacts((0, 0, 0), (0, 0, 1), .1, settings, 19, collide)
right = particles.contacts((0, 0, 0), (0, 0, 1), .1, settings, 19, collide)
assert len(left) == settings.particle_count
assert all(np.array_equal(x[2], y[2]) and x[0] == y[0] for x, y in zip(left, right))
print('PARTICLE_CONTACTS_PASS', flush=True)

# Native text graph and resolved stroke material assets round-trip together.
from open_painter.procedural_graph.tree import new_tree
mask = a.layers[-1].masks.add()
from open_painter.utils.ids import new_id
mask.id, mask.source = new_id(), 'GRAPH'
graph = new_tree('Portable Text')
text, out = graph.nodes.new('OPMGText'), graph.nodes.new('OPMGOutput')
text.text.content = 'Saved text'
graph.links.new(text.outputs['Value'], out.inputs['Coverage'])
mask.node_group = graph
temporary = tempfile.TemporaryDirectory(prefix='op-authoring-v5-')
path = Path(temporary.name) / 'material.opmat'
serializer.save(a, path)
from open_painter.smart_materials.format import read
document, assets = read(path)
assert document['format_version'] == 5
destination = sets.create(scene, 'Imported v5', 512)
loader.apply(destination, path)
restored = destination.layers[-1].masks[-1].node_group
assert next(node for node in restored.nodes if node.bl_idname == 'OPMGText').text.content == 'Saved text'
print('AUTHORING_RECORDS_PASS', flush=True)
temporary.cleanup()
open_painter.unregister()
