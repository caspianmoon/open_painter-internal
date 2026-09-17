"""Opt-in automatic edge map rebake: a mesh edit schedules the cooperative rebake
after a debounce; a transform does not; the default is off."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache, dependencies, field_jobs
from open_painter.baking.bake_manager import register_maps
from open_painter.baking.maps.curvature import bake_curvature_set
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.02); bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Auto rebake', 512); sets.assign(scene, ts, cube)
ts.preview_backend = 'CPU'
register_maps(ts, bake_curvature_set(ts))
layer = stack.add(ts, 'FILL', 'Wear')
mask = layer.masks.add(); mask.id, mask.source, mask.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
bpy.context.view_layer.update()
if not bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.register(cache.tick, first_interval=1000)
from open_painter.evaluation.resource_keys import key_for
dependencies.remember(scene, ts, dependencies.geometry_key(scene, ts))
assert not ts.bake.auto_edge_rebake, 'auto rebake must be opt-in'


def edit_mesh():
    cube.data.vertices[0].co.x += .05
    cube.data.update()
    bpy.context.view_layer.update()
    dependencies._mesh_epochs[cube.data.as_pointer()] = dependencies._mesh_epochs.get(cube.data.as_pointer(), 0) + 1
    dependencies._candidates.add(scene.as_pointer())
    dependencies.refresh(scene)


# Off: a mesh edit schedules nothing.
dependencies.remember(scene, ts, dependencies.geometry_key(scene, ts))
edit_mesh()
assert not field_jobs._rebakes
# On: a mesh edit schedules the rebake; a transform does not.
ts.bake.auto_edge_rebake = True
dependencies.remember(scene, ts, dependencies.geometry_key(scene, ts))
ts.dirty = False; serial = ts.edit_serial
cube.location.x += 1; bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer()); dependencies.refresh(scene)
assert not field_jobs._rebakes, 'a transform scheduled a rebake'
# Nothing on this set reads where the cube stands: a move leaves it current, a resize does not.
assert not ts.dirty and ts.edit_serial == serial, 'a move re-evaluated a set that only reads the surface'
cube.scale.x = 2; bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer()); dependencies.refresh(scene)
assert ts.dirty and ts.edit_serial != serial, 'a resize left the set current'
assert not field_jobs._rebakes, 'a transform scheduled a rebake'
edit_mesh()
assert field_jobs._rebakes, 'a mesh edit did not schedule the rebake'
# The debounce holds the rebake; in a background session there is no window, so it stays scheduled
# until a window exists (the GUI probe covers invocation). Cancel clears it.
field_jobs.REBAKE_DEBOUNCE = 0
field_jobs.tick(scene)
field_jobs.cancel_all()
assert not field_jobs._rebakes
# A mesh mask's signature carries the mesh too: an edit that changes it must not skip the rebake.
cube.vertex_groups.new(name='Worn')
mesh_mask = layer.masks.add(); mesh_mask.id, mesh_mask.source = new_id(), 'MESH'
mesh_mask.mesh.kind, mesh_mask.mesh.attribute = 'VERTEX_GROUP', 'Worn'
dependencies.remember(scene, ts, dependencies.geometry_key(scene, ts))
edit_mesh()
assert field_jobs._rebakes, 'a mesh mask on the set swallowed the rebake'
field_jobs.cancel_all()
bpy.app.timers.unregister(cache.tick)
print('AUTO_EDGE_REBAKE_PASS', flush=True)
open_painter.unregister()
