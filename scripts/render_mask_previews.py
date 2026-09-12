"""Render catalog thumbnails from the actual Open Painter material graphs in Cycles."""
import sys
from pathlib import Path
import bpy
from mathutils import Vector
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, surface_presets
from open_painter.evaluation.evaluator import LayerEvaluator
open_painter.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
scene.render.engine='CYCLES'
scene.cycles.device='CPU'
scene.cycles.samples=32
scene.cycles.use_denoising=True
scene.render.resolution_x=scene.render.resolution_y=256
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.world.color=(0.15,0.15,0.15)
bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=64, radius=1, location=(0,0,0))
sphere=bpy.context.object
bpy.ops.object.shade_smooth()
bpy.ops.object.camera_add(location=(2.9,-4.5,2.0))
camera=bpy.context.object
camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO'
camera.data.ortho_scale=2.5
scene.camera=camera
for location,energy,size,color in [((-3,-4,4),700,3.5,(1,0.95,0.9)),((4,-1,2),500,2,(0.8,0.9,1)),((0,3,3),900,2,(1,1,1))]:
 bpy.ops.object.light_add(type='AREA', location=location)
 light=bpy.context.object
 light.data.energy=energy
 light.data.shape='DISK'
 light.data.size=size
 light.data.color=color
 light.rotation_euler=(-light.location).to_track_quat('-Z','Y').to_euler()
path=root/'src/open_painter/assets/previews'
path.mkdir(parents=True,exist_ok=True)

import numpy as np
from open_painter.smart_masks.library import PRESETS, apply
from open_painter.core import layer_stack, image_manager
from open_painter.baking.bake_manager import bake_selected
from open_painter.evaluation.mask_eval import evaluate_masks
high = sphere.copy(); high.data = sphere.data.copy(); scene.collection.objects.link(high)
for vertex in high.data.vertices:
 vertex.co *= 1 + .06 * np.sin(vertex.co.z * 32)
ts = sets.create(scene, 'Mask preview', 512)
sets.assign(scene, ts, sphere)
ts.bake.high_objects.add().object = high
ts.bake.ray_distance = .2
bpy.context.view_layer.update()
bake_selected(bpy.context, ts)
high.hide_render = True
layer = layer_stack.add(ts, 'FILL')
preview_material = bpy.data.materials.new('Mask preview emission')
preview_material.use_nodes = True
nodes = preview_material.node_tree.nodes; nodes.clear()
tex = nodes.new('ShaderNodeTexImage'); emission = nodes.new('ShaderNodeEmission'); output = nodes.new('ShaderNodeOutputMaterial')
preview_material.node_tree.links.new(tex.outputs['Color'], emission.inputs['Color'])
preview_material.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
sphere.data.materials[0] = preview_material
scene.view_settings.view_transform = 'Standard'
for preset in PRESETS:
 layer.masks.clear()
 apply(ts, preset[0])
 value = evaluate_masks(ts, layer, 512, 0, 512, {})
 rgba = np.ones((512,512,4), dtype=np.float32); rgba[...,:3] = value[...,None]
 img = image_manager.create('Mask thumbnail', 512); image_manager.write(img, rgba)
 tex.image = img
 scene.render.filepath = str(path / (preset[0].replace(' ', '_') + '.png'))
 bpy.ops.render.render(write_still=True)
 print('MASK_PREVIEW', preset[0], float(value.std()), flush=True)
open_painter.unregister()
