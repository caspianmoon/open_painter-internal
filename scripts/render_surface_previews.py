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
for name in ['Painted Steel','Dirty Plastic',*surface_presets.SURFACES,'Fine Wood','Weathered Paint']:
 ts=sets.create(scene,name,512)
 # Each preview uses its own material without altering the sphere topology/UVs.
 for previous in scene.open_painter.texture_sets:
  while previous.assignments:
   sets.unassign(previous, 0)
 sphere.data.materials.clear()
 sets.assign(scene, ts, sphere)
 surface_presets.apply(ts,name)
 LayerEvaluator(scene).evaluate_texture_set(ts.id)
 scene.render.filepath=str(path/(name.replace(' ','_')+'.png'))
 bpy.ops.render.render(write_still=True)
 print('RENDERED',name,flush=True)
open_painter.unregister()
