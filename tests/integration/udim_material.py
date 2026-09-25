"""UDIM material brush, real shader pixels, atlas remapping and region rebakes."""
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import udim, layer_stack as stack, image_manager as images, texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.painting import gesture
from open_painter.baking import corrections
from open_painter.export import atlas

open_painter.register()
scene = bpy.context.scene
project = scene.open_painter
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
mesh = bpy.data.meshes.new('UDIM material')
mesh.from_pydata([(-1, 0, 0), (0, 0, 0), (1, 0, 0), (-1, 1, 0), (0, 1, 0), (1, 1, 0)],
                 [], [(0, 1, 4, 3), (1, 2, 5, 4)])
obj = bpy.data.objects.new('UDIM material', mesh)
scene.collection.objects.link(obj)
source = mesh.uv_layers.new(name='UVMap')
original = np.array([(0, 0), (1, 0), (1, 1), (0, 1), (1, 0), (2, 0), (2, 1), (1, 1)], np.float32)
source.data.foreach_set('uv', original.ravel())
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
members = udim.create(scene, [obj], 'Material', 512)
ids = [ts.id for ts in members]
checks = []


def check(label):
    checks.append(label)
    print('UDIM_MATERIAL PASS:', label, flush=True)


# Nonrepeating inputs make an uncorrected tile 1002 observably wrong.
graphs, readbacks = [], []
for index, ts in enumerate(members):
    base = stack.add(ts, 'FILL', 'Local gradient')
    prop = stack.channel_for(base, 'BASE_COLOR')
    prop.source, prop.tiling = 'IMAGE', 'NONE'
    prop.image = images.create('Tile gradient', 512, color_data=True)
    values = np.ones((512, 512, 4), np.float32)
    values[..., :3] = .02
    values[..., index * 2] = (np.arange(512, dtype=np.float32)[None, :] + .5) / 512
    images.write(prop.image, values)
    ts.preview_backend = 'CPU'
    shader_preview.clear()
    graph = Graph(scene, ts, ts.material).build()
    while graph.pending_resources:
        graph.step_resources(.1)
    tree = ts.material.node_tree
    emission = tree.nodes.new('ShaderNodeEmission')
    output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    tree.links.new(graph.outputs['BASE_COLOR'], emission.inputs['Color'])
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    readback = images.create('Shader readback', 512)
    node = tree.nodes.new('ShaderNodeTexImage')
    node.image = readback
    tree.nodes.active = node
    readbacks.append(readback)
    graphs.append(graph)
# Read back each material into a local bake UV map; the shader explicitly reads
# the untouched source map. A render would rewire our emission probe to the PBR
# material in the normal render-pre handler, so use the shader suite's EMIT path.
readback_uv = mesh.uv_layers.new(name='Readback UV')
local = original.copy()
local[4:, 0] -= 1
readback_uv.data.foreach_set('uv', local.ravel())
mesh.uv_layers.active = source
source.active_render = True
scene.render.engine = 'CYCLES'
scene.cycles.device, scene.cycles.samples = 'CPU', 1
scene.render.bake.use_selected_to_active = False
scene.render.bake.margin = 0
bpy.context.view_layer.update()
bpy.ops.object.bake(type='EMIT', uv_layer=readback_uv.name)
for index, readback in enumerate(readbacks):
    pixels = images.pixels(readback)
    for x, expected in ((128, .25), (384, .75)):
        np.testing.assert_allclose(pixels[256, x, index * 2], expected, atol=.01)
mesh.uv_layers.remove(readback_uv)
check('Cycles shader preview samples nonrepeating gradients in local coordinates on both tiles')

for ts in members:
    ts.layers.clear()
    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.01, .01, .01, 1)
    stack.channel_for(base, 'ROUGHNESS').value = .1
first = members[0]
layer = stack.add(first, 'PAINT', 'Material stroke')
first.active_layer = stack.index_of(first, layer.id)
project.active_texture_set = 0
settings = project.vector_brush
settings.radius_mode, settings.projection, settings.blend = 'WORLD', 'SURFACE', 'PAINT'
settings.radius, settings.spacing, settings.hardness = .1, .2, .9
settings.opacity, settings.flow, settings.accumulate = 1, 1, 'MAX'
settings.jitter, settings.alpha_image, settings.universal = 0, None, True
settings.depth_limit, settings.pressure_size, settings.pressure_flow = .5, False, False
settings.reach = 'SURFACE'
settings.channels = {'BASE_COLOR', 'ROUGHNESS'}
settings.base_color, settings.roughness = (.1, .7, .1, 1), .85
bpy.context.view_layer.update()


class View:
    def ray(self, xy):
        return Vector((xy[0] / 200, xy[1] / 200, 10)), Vector((0, 0, -1))

    def world_per_pixel(self, xy, point):
        return .005


scope = gesture.Scope.of(project, first, across_sets=True)
g = gesture.Gesture(scene, first, layer, settings, scope, View(), preview=False)
attributes = (1., 0., 0., 0.)
start = (-160, 100)
assert g.start(start, attributes, g.hit(start), (0, 0, 1))
for x in np.linspace(-140, 160, 20):
    g.advance((float(x), 100), attributes)
assert g.end(commit=True) is not None, g.reason
for ts in members:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    color = images.pixels(images.output(ts, 'BASE_COLOR').image)
    roughness = images.pixels(images.output(ts, 'ROUGHNESS').image)
    assert color[240:270, 80:430, 1].max() > .6
    assert roughness[240:270, 80:430, 0].max() > .8
    assert color[400:, :, 1].max() < .02
check('one material gesture paints color and roughness on both tiles at the correct coordinates')

faces, count = corrections.face_map(bpy.context, members[1])[obj]
assert count == 2 and faces[256, 256] == 1 and not np.any(faces == 0)
check('regional rebake maps tile 1002 texels to its own face')

from open_painter.geometry_effects import displace, manager as effect_manager
height = images.create('UDIM height ramp', 512)
values = np.ones((512, 512, 4), np.float32)
values[..., :3] = (np.arange(512, dtype=np.float32)[None, :, None] + .5) / 512
images.write(height, values)
helper = displace.create(members[1], obj, height, levels=0, amplitude=1, midlevel=0)
bpy.context.view_layer.update()
evaluated = helper.evaluated_get(bpy.context.evaluated_depsgraph_get())
for vertex in evaluated.data.vertices:
    x, _, z = vertex.co
    np.testing.assert_allclose(z, max(0, x), atol=.01)
effect_manager.remove(helper)
check('geometry displacement samples the tile-local height ramp')

frozen, copies = atlas.build(bpy.context, members, 512, 8)
members = [sets._set_by_id(project, identifier) for identifier in ids]
assert len(copies) == 1
uv = copies[0].data.uv_layers.active
for polygon in copies[0].data.polygons:
    values = np.asarray([uv.data[i].uv[:] for i in polygon.loop_indices])
    low, high = (8 / 512, 248 / 512) if polygon.index == 0 else (264 / 512, 504 / 512)
    np.testing.assert_allclose((values[:, 0].min(), values[:, 0].max()), (low, high), atol=1e-6)
original_after = np.empty_like(original)
source.data.foreach_get('uv', original_after.ravel())
assert np.array_equal(original_after, original)
check('atlas places both tiles into separate cells on a copy and preserves source UVs')

output = ROOT / '.blender-test/udim-material.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({'blender': bpy.app.version_string, 'checks': checks}, indent=2))
open_painter.unregister()
print('UDIM_MATERIAL_PASS', len(checks), flush=True)
