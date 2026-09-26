"""Rebaking selected maps overrides the live images: same datablock, same name, no .001."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking.bake_manager import bake_selected
from open_painter.core import texture_set_manager as sets

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Override', 512)
sets.assign(scene, ts, cube)
ts.bake.normal, ts.bake.ao, ts.bake.curvature = False, False, False
ts.bake.position = True
bake_selected(bpy.context, ts)
first = next(m.image for m in ts.baked_maps if m.kind == 'POSITION')
assert first.name == 'Override_BakedPosition', first.name
revision = first.get('open_painter_pixel_revision', 0)
bake_selected(bpy.context, ts)
second = next(m.image for m in ts.baked_maps if m.kind == 'POSITION')
assert second == first, 'rebake must write into the live image, not a new datablock'
assert second.name == 'Override_BakedPosition', second.name
assert second.get('open_painter_pixel_revision', 0) > revision, 'caches key on the revision'
assert [i.name for i in bpy.data.images if 'BakedPosition' in i.name] == [first.name], \
    [i.name for i in bpy.data.images if 'BakedPosition' in i.name]
assert next(m for m in ts.baked_maps if m.kind == 'POSITION').provenance, 'provenance re-recorded'
open_painter.unregister()
print('BAKE_OVERRIDE_PASS: rebake overwrites the live map in place')
