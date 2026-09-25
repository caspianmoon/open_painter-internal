"""Actual shader readback agrees with CPU for new weathering fields and runoff."""
import json
import sys
from pathlib import Path
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph

OUT = ROOT / '.blender-test' / 'weathering-shader'
OUT.mkdir(parents=True, exist_ok=True)
open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
ts = sets.create(scene, 'Weathering shader comparison', 512)
sets.assign(scene, ts, obj)
ts.preview_backend = 'CPU'
n = 512
axis = (np.arange(n, dtype=np.float32) + .5) / n
u, v = np.meshgrid(axis, axis)
for kind in ('POSITION', 'AO'):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create(kind, n)
    rgba = np.ones((n, n, 4), np.float32)
    if kind == 'POSITION':
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = u, .2, v
    else:
        rgba[..., :3] = (.25 + .7 * v)[..., None]
    images.write(entry.image, rgba)
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.cycles.use_denoising = False
scene.render.resolution_x = scene.render.resolution_y = n
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene.render.filter_size = .01
bpy.ops.object.camera_add(location=(0, 0, 3))
scene.camera = bpy.context.object
scene.camera.data.type = 'ORTHO'
scene.camera.data.ortho_scale = 2
report = {}
for kind in ('WEATHERING_FRONT', 'CORROSION_GRAIN', 'RUNOFF_TRAILS', 'RUNOFF'):
    ts.layers.clear()
    item = stack.add(ts, 'FILL', kind)
    color = stack.channel_for(item, 'BASE_COLOR')
    color.color = (.3, .2, .1, 1)
    color.procedural = 'CORROSION_GRAIN'
    color.procedural_scale = 7
    color.procedural_detail = 1
    mask = item.masks.add()
    mask.id = kind
    mask.source = 'GENERATOR' if kind == 'RUNOFF' else 'PROCEDURAL'
    if kind == 'RUNOFF':
        mask.generator = kind
    else:
        mask.procedural = kind
    mask.noise_scale, mask.seed = 4, 42
    steps = LayerEvaluator(scene).channel_steps(ts.id, 'BASE_COLOR', preview_resolution=n)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            expected = images.pixels(done.value)[..., :3].copy()
            break
    mat = ts.material
    mat.node_tree.nodes.clear()
    graph = Graph(scene, ts, mat).build()
    while graph.pending_resources:
        graph.step_resources(.1)
    emission = mat.node_tree.nodes.new('ShaderNodeEmission')
    output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
    mat.node_tree.links.new(graph.outputs['BASE_COLOR'], emission.inputs['Color'])
    mat.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    # Emission bake reads the graph without the render-pre preview handler
    # replacing this diagnostic output with the production surface shader.
    image = images.create('Shader readback', n)
    node = mat.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    mat.node_tree.nodes.active = node
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene.render.bake.margin = 0
    scene.render.bake.use_selected_to_active = False
    bpy.ops.object.bake(type='EMIT')
    actual = images.pixels(image)[..., :3]
    delta = np.abs(expected[2:-2, 2:-2] - actual[2:-2, 2:-2])
    report[kind] = {'mean_error': float(delta.mean()), 'max_error': float(delta.max())}
    # The Cycles emission bake nudges samples slightly inside triangles;
    # interpolation affects very sharp trail edges.
    assert delta.mean() < .00005 and delta.max() < .002, report[kind]
    bpy.data.images.remove(image)
(OUT / 'report.json').write_text(json.dumps(report, indent=2))
open_painter.unregister()
print('WEATHERING_SHADER_PASS', report, flush=True)
