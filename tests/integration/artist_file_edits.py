"""Destructive scene edits on the real asset file: what the artist does to the scene around a
texture set must never break the session, and every message must name the current datablocks.

Companion of ``artist_file_workflow.py`` (same file, same skip rule). Renames, a deleted decal
Empty, hidden objects, edit mode, a material slot taken over by another material, the assigned
object deleted, sets duplicated without assignments and removed again — the scheduler keeps
ticking, evaluation either works or says what to fix, and portable materials carry the names
the datablocks have now.
"""
import os
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
FILE = Path(os.environ.get('OP_ARTIST_FILE', ROOT / 'open painter test.blend'))
if not FILE.exists():
    print(f'ARTIST_FILE_SKIPPED {FILE}', flush=True)
    print('ARTIST_FILE_EDITS_PASS', flush=True)
    sys.exit(0)

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.core.validation import validate
from open_painter.evaluation import cache, dependencies, physical, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import decals, paint_session
from open_painter.utils.ids import new_id

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(FILE))
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
ts = project.texture_sets[0]
set_id = ts.id
low = next(r.object for r in ts.assignments if r.object)


def ticks(count=40):
    for _ in range(count):
        cache.tick()   # any exception here is a scheduler crash the artist would see in the console


def settle():
    bpy.context.view_layer.update()
    dependencies._candidates.add(scene.as_pointer())
    dependencies.refresh(scene)
    ticks()


def cpu(channel='BASE_COLOR', n=128):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=n)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value).copy()


def set_by_id(identifier):
    return next(t for t in scene.open_painter.texture_sets if t.id == identifier)


material_manager.repair(ts)
ticks()

# ---- renames: every name mirror follows the datablock -------------------------------------------
screws = bpy.data.collections.new('Screws')
scene.collection.children.link(screws)
screws.objects.link(low)
rust = stack.add(ts, 'FILL', 'Rust on screws')
rust_id = rust.id
mask = rust.masks.add()
mask.id, mask.source, mask.mesh.kind, mask.mesh.collection = new_id(), 'MESH', 'COLLECTION', screws
scene.cursor.location = (0., 0., 0.)
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    assert bpy.ops.open_painter.decal(action='ADD', name='Label', size=.1) == {'FINISHED'}
decal_id = ts.layers[ts.active_layer].id
empty = decals.resolve(ts.layers[ts.active_layer])
low.name, screws.name, empty.name = 'Wheel', 'Bolts', 'OP_Decal_Warning'
settle()
ts = set_by_id(set_id)
assert ts.assignments[0].object_name == 'Wheel', ts.assignments[0].object_name
layer_of = lambda identifier: next(l for l in ts.layers if l.id == identifier)
assert layer_of(rust_id).masks[0].mesh.collection_name == 'Bolts'
assert layer_of(decal_id).decal_object_name == 'OP_Decal_Warning'
from open_painter.smart_materials.serializer import save
import json
import zipfile
archive = ROOT / '.blender-test' / 'artist-file-renamed.zip'
archive.unlink(missing_ok=True)
save(ts, archive)
with zipfile.ZipFile(archive) as bundle:
    data = json.loads(bundle.read('material.json'))
stored = next(m for layer in data['layers'] for m in layer.get('masks', []) if m.get('source') == 'MESH')
assert stored['mesh']['collection_name'] == 'Bolts', stored['mesh']
stored_decal = next(layer for layer in data['layers'] if layer.get('decal_object_name'))
assert stored_decal['decal_object_name'] == 'OP_Decal_Warning'
print('ARTIST_FILE_RENAMES_OK', flush=True)

# ---- the decal Empty deleted: the layer goes transparent, nothing else changes -------------------
before = cpu()
bpy.data.objects.remove(empty)
settle()
ts = set_by_id(set_id)
assert layer_of(decal_id).decal_object is None and decals.resolve(layer_of(decal_id)) is None
assert np.array_equal(cpu(), before), 'an empty-less decal layer contributes nothing'
material_manager.repair(ts)
ticks(200)
assert not shader_preview.diagnostics(scene, ts).get('error'), shader_preview.diagnostics(scene, ts)
print('ARTIST_FILE_DECAL_GONE_OK', flush=True)

# ---- hidden object, edit mode: the scheduler keeps going --------------------------------------
low.hide_viewport = True
settle()
low.hide_viewport = False
low.hide_set(True)
settle()
grime = stack.add(ts, 'PAINT', 'Grime')
try:
    ts.active_layer = stack.index_of(ts, grime.id)
    with bpy.context.temp_override(scene=scene, object=low, active_object=low):
        paint_session.begin(bpy.context, ts, 'BASE_COLOR')
    raise AssertionError('painting a hidden object must be refused')
except ValueError as exc:
    assert 'Show' in str(exc), exc
low.hide_set(False)
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    bpy.ops.object.mode_set(mode='EDIT')
settle()
assert np.isfinite(cpu()).all()
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    bpy.ops.object.mode_set(mode='OBJECT')
print('ARTIST_FILE_HIDDEN_EDIT_OK', flush=True)

# ---- the artist drops another material onto the slot; Repair takes it back --------------------
other = bpy.data.materials.new('Hand-made material')
low.material_slots[0].material = other
settle()
issues = validate(ts)
assert any('Material assignment changed on Wheel' in issue for issue in issues), issues
material_manager.repair(ts)   # handlers repair the graph but leave a swapped slot alone
assert low.material_slots[0].material == other
project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == set_id)
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    assert bpy.ops.open_painter.texture_set(action='REPAIR') == {'FINISHED'}   # the button takes it back
assert low.material_slots[0].material == ts.material
assert not validate(ts), validate(ts)
print('ARTIST_FILE_SLOT_OK', flush=True)

# ---- the assigned object deleted: clear messages, no crash; a set without meshes is inert ------
bpy.data.objects.remove(low)
settle()
ts = set_by_id(set_id)
assert ts.assignments[0].object is None and ts.assignments[0].object_name == 'Wheel'
issues = validate(ts)
assert any('Missing object: Wheel' in issue for issue in issues), issues
try:
    cpu()
    raise AssertionError('evaluation without meshes must say so')
except ValueError as exc:
    assert 'Assign' in str(exc), exc
assert physical.density(ts) is None and 'assign a mesh' in physical.describe(ts)
copy = sets.duplicate(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == set_id))
copy_id = copy.id
ts = set_by_id(set_id)
assert any('No mesh slots assigned' in issue for issue in validate(set_by_id(copy_id)))
ticks(100)
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == copy_id))
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == set_id))
assert not len(project.texture_sets)
ticks(100)
print('ARTIST_FILE_EDITS_PASS', flush=True)
open_painter.unregister()
