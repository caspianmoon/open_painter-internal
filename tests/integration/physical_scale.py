"""Physical scale awareness (0.9.5): sizes in metres resolve per asset and follow the mesh."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies, physical, shader_preview
from open_painter.utils.ids import new_id


def cube(name, size):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
mug_object, truck_object = cube('Mug', 1.0), cube('Truck', 4.0)
truck_object.location.x = 6
mug, truck = sets.create(scene, 'Mug', 512), sets.create(scene, 'Truck', 512)
sets.assign(scene, mug, mug_object)
sets.assign(scene, truck, truck_object)
mug_density, truck_density = physical.density(mug), physical.density(truck)
assert mug_density and truck_density and abs(truck_density / mug_density - 4) < 1e-3, (mug_density, truck_density)
assert 'Texel' in physical.describe(mug) and 'tile' in physical.describe(mug)


def build(ts):
    layer = stack.add(ts, 'FILL', 'Scratches')
    prop = stack.channel_for(layer, 'BASE_COLOR')
    prop.procedural = 'FBM'
    prop.units, prop.feature_size = 'PHYSICAL', .1          # 10 cm cells
    blur = prop.effects.add()
    blur.kind, blur.units, blur.physical_radius = 'GAUSSIAN_BLUR', 'PHYSICAL', .02   # 2 cm blur
    warp = prop.effects.add()
    warp.kind, warp.units, warp.physical_radius, warp.feature_size = 'WARP', 'PHYSICAL', .01, .25
    mask = layer.masks.add()
    mask.id, mask.source, mask.generator = new_id(), 'GENERATOR', 'SCRATCHES'
    mask.units, mask.feature_size = 'PHYSICAL', .002        # 2 mm scratch cells
    return layer.id


mug_layer, truck_layer = build(mug), build(truck)


def values(ts, layer_id):
    layer = next(l for l in ts.layers if l.id == layer_id)
    prop = stack.channel_for(layer, 'BASE_COLOR')
    return dict(scale=prop.procedural_scale, blur=prop.effects[0].radius, warp=prop.effects[1].radius,
                warp_noise=prop.effects[1].scale, mask=layer.masks[0].noise_scale)


m, t = values(mug, mug_layer), values(truck, truck_layer)
print('PHYSICAL_SCALE_VALUES', {'mug': m, 'truck': t}, flush=True)
assert abs(m['scale'] - mug_density / .1) < 1e-3 and abs(t['scale'] - truck_density / .1) < 1e-3
assert abs(t['scale'] / m['scale'] - 4) < 1e-3, 'four times the tile size: four times the repeats'
assert abs(m['blur'] / t['blur'] - 4) < 1e-3, 'the same 2 cm covers four times fewer texels on the truck'
assert abs(m['blur'] - .02 / physical.texel_size(mug)) < 1e-3
assert abs(m['warp'] - .01 / physical.texel_size(mug)) < 1e-3 and abs(m['warp_noise'] - mug_density / .25) < 1e-3
assert abs(t['mask'] / m['mask'] - 4) < 1e-3 or (m['mask'] >= 512 or t['mask'] >= 512), 'generator noise follows too'
print('PHYSICAL_SCALE_RESOLVE_OK', flush=True)

# Triplanar cells are world-sized already: repeats per world unit, independent of density.
prop = stack.channel_for(next(l for l in mug.layers if l.id == mug_layer), 'BASE_COLOR')
prop.projection = 'TRIPLANAR'
assert abs(prop.procedural_scale - 1 / .1) < 1e-6
prop.projection = 'UV'
assert abs(prop.procedural_scale - mug_density / .1) < 1e-3

# Resolution moves the texel size: the 2 cm blur becomes twice the texels at 1024.
before = m['blur']
mug.resolution = '1024'
assert abs(values(mug, mug_layer)['blur'] / before - 2) < 1e-3
mug.resolution = '512'
assert abs(values(mug, mug_layer)['blur'] - before) < 1e-3
print('PHYSICAL_SCALE_RESOLUTION_OK', flush=True)

# The surface changes: a scaled object re-resolves through the geometry dependency.
truck.preview_backend = 'LIVE'
material_manager.repair(truck)
ready(scene, truck)
assert dependencies.uses_geometry(truck)
before = values(truck, truck_layer)['scale']
truck_object.scale = (2., 2., 2.)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert abs(values(truck, truck_layer)['scale'] / before - 2) < 1e-3, 'twice the size: twice the repeats per tile'
ready(scene, truck)
truck_object.scale = (1., 1., 1.)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert abs(values(truck, truck_layer)['scale'] - before) < 1e-3
print('PHYSICAL_SCALE_GEOMETRY_OK', flush=True)

# Lock: an existing texture-unit material keeps its look and gains metre sizes.
plain = stack.add(mug, 'FILL', 'Rust')
plain_prop = stack.channel_for(plain, 'BASE_COLOR')
plain_prop.procedural, plain_prop.procedural_scale = 'VORONOI', 24.
dilate = plain_prop.effects.add()
dilate.kind, dilate.radius = 'DILATE', 6.
count = physical.lock(mug)
assert count >= 2 and plain_prop.units == 'PHYSICAL' and dilate.units == 'PHYSICAL'
assert abs(plain_prop.procedural_scale - 24.) < 1e-3 and abs(dilate.radius - 6.) < 1e-3, 'locking keeps the look'
assert abs(plain_prop.feature_size - mug_density / 24.) < 1e-6 and abs(dilate.physical_radius - 6 * physical.texel_size(mug)) < 1e-6
physical.unlock(mug)
assert plain_prop.units == 'TEXTURE' and abs(plain_prop.procedural_scale - 24.) < 1e-3
with bpy.context.temp_override(scene=scene):
    scene.open_painter.active_texture_set = 0
    assert bpy.ops.open_painter.physical_scale(action='LOCK') == {'FINISHED'}
assert plain_prop.units == 'PHYSICAL'
print('PHYSICAL_SCALE_LOCK_OK', flush=True)

# An unresolvable set is remembered too (0.9.7): a set landing on a material slot without faces
# used to copy the evaluated mesh on every panel redraw and refresh until the artist fixed it.
from open_painter.geometry import snapshots
crate_object = cube('Crate', 2.0)
crate_object.data.materials.append(None); crate_object.data.materials.append(None)
crate = sets.create(scene, 'Crate', 512)
crate.assignments.add().object, crate.assignments[0].slot_index = crate_object, 1
measured = []
original_targets = snapshots.targets
snapshots.targets = lambda ts: measured.append(ts.id) or original_targets(ts)
try:
    assert physical.density(crate) is None and physical.describe(crate) == 'Physical scale: assign a mesh'
    assert physical.expected(stack.channel_for(stack.add(crate, 'FILL', 'Paint'), 'BASE_COLOR'), crate) == {}
    physical.sync(crate)
    assert measured == [crate.id], f'an empty slot is measured once, not per call: {measured}'
    crate_object.data.polygons.foreach_set('material_index', np.ones(len(crate_object.data.polygons), np.int32))
    crate_object.data.update()
    bpy.context.view_layer.update()
    crate_density = physical.density(crate)
    assert crate_density and abs(crate_density / mug_density - 2) < 1e-3, (crate_density, mug_density, measured)
    assert measured == [crate.id, crate.id], 'faces moved onto the slot: measured again, once'
    assert 'Texel' in physical.describe(crate) and measured == [crate.id, crate.id]
finally:
    snapshots.targets = original_targets
print('PHYSICAL_SCALE_NEGATIVE_CACHE_OK', flush=True)

# A material authored on the mug re-normalizes on the truck when loaded.
from open_painter.smart_materials.serializer import save
from open_painter.smart_materials.loader import apply as load
archive = ROOT / '.blender-test' / 'physical-scale-material.zip'
if archive.exists():
    archive.unlink()
save(mug, archive)
start = len(truck.layers)
load(truck, archive)
rust = next(l for l in list(truck.layers)[start:] if l.name == 'Rust')
rust_prop = stack.channel_for(rust, 'BASE_COLOR')
assert rust_prop.units == 'PHYSICAL' and abs(rust_prop.feature_size - mug_density / 24.) < 1e-6
assert abs(rust_prop.procedural_scale - truck_density / rust_prop.feature_size) < 1e-2, \
    (rust_prop.procedural_scale, truck_density / rust_prop.feature_size)
assert abs(rust_prop.procedural_scale / 24. - 4) < 1e-2, 'the same rust spots are four times as many across the truck'
print('PHYSICAL_SCALE_PASS', flush=True)
open_painter.unregister()
