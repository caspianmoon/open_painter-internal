"""A multi-set session on the real asset file: more parts, shared materials, painting across
sets, undo, and a game-engine bundle.

Companion of ``artist_file_workflow.py`` (same file, same skip rule). The artist texture-sets two
more parts of the prop — one of them with two material slots so two sets share the object —
exports the authored Worn Painted Metal folder as a module and applies it to a part, bakes that
part's maps so the generators wake up, drops a catalog material on the third set, paints one
stroke across the two sets of the shared object, undoes a few edits, exports every set and
builds the bundle.
"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
FILE = Path(os.environ.get('OP_ARTIST_FILE', ROOT / 'open painter test.blend'))
if not FILE.exists():
    print(f'ARTIST_FILE_SKIPPED {FILE}', flush=True)
    print('ARTIST_FILE_MULTISET_PASS', flush=True)
    sys.exit(0)

import open_painter
from open_painter.baking.bake_manager import bake_selected
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues, warnings
from open_painter.painting import paint_session
from open_painter.smart_materials import composer
from open_painter.smart_materials.loader import apply as load_material
from open_painter.smart_materials.serializer import save as save_material

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(FILE))
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
timings = {}


def timed(name, fn, limit):
    started = monotonic()
    result = fn()
    timings[name] = monotonic() - started
    assert timings[name] < limit, f'{name} took {timings[name]:.1f}s (limit {limit}s)'
    return result


def ready(ts, limit=180):
    deadline = monotonic() + limit
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info.get('error'), info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def cpu(ts, channel, n):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=n)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value).copy()


def set_by_id(identifier):
    return next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == identifier)


# ---- 1. two more parts; one of them split over two material slots -----------------------------
main = project.texture_sets[0]
main_id = main.id
wheel = next(r.object for r in main.assignments if r.object)
parts = sorted((o for o in scene.objects if o.type == 'MESH' and o != wheel), key=lambda o: len(o.data.polygons))
small, medium = parts[0], parts[len(parts) // 2]
assert not small.material_slots
small.data.materials.append(None)
small.data.materials.append(None)
flags = np.arange(len(small.data.polygons)) % 2
small.data.polygons.foreach_set('material_index', flags.astype(np.int32))
small.data.update()
front = sets.create(scene, 'Small front', 512)
back = sets.create(scene, 'Small back', 512)
third = sets.create(scene, 'Medium part', 512)
front_id, back_id, third_id = front.id, back.id, third.id
sets.assign(scene, front, small, 0)
sets.assign(scene, back, small, 1)
sets.assign(scene, third, medium)
try:
    sets.assign(scene, third, small, 0)
    raise AssertionError('a slot cannot belong to two sets')
except ValueError as exc:
    assert 'already belongs' in str(exc)
assert small.material_slots[0].material == front.material and small.material_slots[1].material == back.material
print('ARTIST_FILE_SETS_OK', [len(o.data.polygons) for o in (small, medium)], flush=True)

# ---- 2. the authored folder travels as a module to another part ------------------------------
main = set_by_id(main_id)   # texture_sets grew: resolve by id again
folder = main.layers[0]
folder_id = folder.id
with TemporaryDirectory(prefix='op-artist-module-') as directory:
    archive = Path(directory) / 'worn-painted-metal.openpaintmaterial'
    timed('module save', lambda: save_material(main, archive, root_id=folder_id,
                                               module=dict(id='artist.worn', name='Worn Painted Metal', version=1)), 120)
    assert archive.exists() and archive.stat().st_size > 1000
    roots = timed('module load', lambda: load_material(set_by_id(third_id), archive), 120)
assert len(roots) == 1 and roots[0].type == 'FOLDER'
third = set_by_id(third_id)
assert not issues(third), issues(third)
generator_masks = [m for l in third.layers for m in l.masks if m.source == 'GENERATOR']
assert len(generator_masks) >= 3
from open_painter.core.validation import validate
missing = [issue for issue in validate(third) if 'requires a baked' in issue]
assert missing, 'without baked maps the generators report what they need'
assert not any(m.mesh.object for l in third.layers for m in l.masks), 'nothing points at the original part'
try:
    cpu(third, 'BASE_COLOR', 128)
    raise AssertionError('evaluation without the maps must say what to bake')
except ValueError as exc:
    assert 'Bake mesh maps first' in str(exc), exc
material_manager.repair(third)
for _ in range(50):
    shader_preview.tick(scene, third)
    if shader_preview.diagnostics(scene, third).get('error'):
        break
assert 'Bake mesh maps first' in shader_preview.diagnostics(scene, third)['error'], 'the shader session says so too'
settings = third.bake
settings.selected_to_active = False
settings.normal = settings.ao = settings.curvature = True
settings.samples, settings.margin = 2, 2
scene.render.engine, scene.cycles.device = 'CYCLES', 'CPU'
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=medium, active_object=medium):
    timed('bake medium', lambda: bake_selected(bpy.context, third), 900)
third = set_by_id(third_id)
assert not [issue for issue in validate(third) if 'requires a baked' in issue]
baked_colour = cpu(third, 'BASE_COLOR', 128)
assert np.isfinite(baked_colour).all() and baked_colour[..., :3].std() > .02
material_manager.repair(third)
info = ready(third)
assert not info['warnings'], info['warnings']
print('ARTIST_FILE_MODULE_OK', flush=True)

# ---- 3. a catalog material on the two-slot part; the sets stay independent ------------------
from open_painter.smart_materials.composer import MissingMeshMaps
front = set_by_id(front_id)
try:
    composer.apply(front, 'Heavy Rust')
    raise AssertionError('catalog materials need their mesh maps first')
except MissingMeshMaps:
    pass
for identifier in (front_id, back_id):
    ts = set_by_id(identifier)
    ts.bake.selected_to_active = False
    ts.bake.normal = ts.bake.ao = ts.bake.curvature = True
    ts.bake.samples, ts.bake.margin = 2, 2
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=small, active_object=small):
        timed(f'bake {ts.name}', lambda: bake_selected(bpy.context, ts), 600)
front = set_by_id(front_id)
composer.apply(front, 'Heavy Rust')
front = set_by_id(front_id)
assert any(l.type == 'FOLDER' and l.name == 'Heavy Rust' for l in front.layers)
back = set_by_id(back_id)
composer.apply(back, 'Polished Chrome')
back = set_by_id(back_id)
assert {l.name for l in back.layers if l.type == 'FOLDER'} == {'Polished Chrome'}
assert {l.name for l in set_by_id(front_id).layers if l.type == 'FOLDER'} == {'Heavy Rust'}
for identifier in (front_id, back_id):
    ts = set_by_id(identifier)
    material_manager.repair(ts)
    info = ready(ts)
    assert not info['warnings'], (ts.name, info['warnings'])
rust = cpu(set_by_id(front_id), 'BASE_COLOR', 128)
chrome = cpu(set_by_id(back_id), 'BASE_COLOR', 128)
assert rust[..., :3].mean() < chrome[..., :3].mean(), 'rust is darker than chrome'
print('ARTIST_FILE_CATALOG_OK', flush=True)

# ---- 4. one stroke paints both sets of the shared object ---------------------------------------
front = set_by_id(front_id)
grime = stack.add(front, 'PAINT', 'Shared grime')
front.active_layer = stack.index_of(front, grime.id)
project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == front_id)
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=small, active_object=small):
    canvas = paint_session.begin_multi(bpy.context, front, 'BASE_COLOR')
back = set_by_id(back_id)
linked = next((l for l in back.layers if l.type == 'PAINT' and l.paint_link and l.paint_link == grime.paint_link), None)
assert linked is not None, 'the other set of the object gets a linked paint layer'
assert len(project.paint_canvases) == 2
partner = next(entry.image for entry in project.paint_canvases if entry.image != canvas)
for image in (canvas, partner):
    data = images.pixels(image)
    data[100:160, 100:160] = (.9, .1, .1, 1.)
    image.pixels.foreach_set(data.ravel())
    image.update()
paint_session.detect(scene, force=True)
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=small, active_object=small):
    paint_session.finish(bpy.context)
assert project.paint_image is None and not len(project.paint_canvases)
for identifier in (front_id, back_id):
    painted = cpu(set_by_id(identifier), 'BASE_COLOR', 512)
    patch = painted[100:160, 100:160, :3].mean(axis=(0, 1))
    assert patch[0] > .6 and patch[1] < .3, (identifier, patch)
print('ARTIST_FILE_MULTI_PAINT_OK', flush=True)

# ---- 5. undo steps through authoring edits without breaking the sessions ------------------------
main = set_by_id(main_id)
material_manager.repair(main)
ready(main)
bpy.ops.ed.undo_push(message='before opacity')
oxide = next(l for l in main.layers if l.name == 'Oxide beneath paint')
oxide_id = oxide.id
before = oxide.opacity
oxide.opacity = .2
bpy.ops.ed.undo_push(message='opacity')
stack.remove(main, stack.index_of(main, oxide_id))
bpy.ops.ed.undo_push(message='removed')
main = set_by_id(main_id)
assert not any(l.id == oxide_id for l in main.layers)
bpy.ops.ed.undo()
scene, project = bpy.context.scene, bpy.context.scene.open_painter   # undo reloads the database
main = set_by_id(main_id)
restored = next(l for l in main.layers if l.id == oxide_id)
assert abs(restored.opacity - .2) < 1e-6
bpy.ops.ed.undo()
scene, project = bpy.context.scene, bpy.context.scene.open_painter
main = set_by_id(main_id)
assert abs(next(l for l in main.layers if l.id == oxide_id).opacity - before) < 1e-6
material_manager.repair(main)
info = ready(main)
assert not info['warnings'] and not issues(main)
print('ARTIST_FILE_UNDO_OK', flush=True)

# ---- 6. every set exports; the bundle carries the parts ----------------------------------------
from open_painter.export.exporter import export
from open_painter.export.mesh_bundle import export_bundle
with TemporaryDirectory(prefix='op-artist-export-') as directory:
    written = []
    for ts in project.texture_sets:
        ts.export.preset, ts.export.file_format, ts.export.resolution = 'UNITY', 'PNG', '512'
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            written += timed(f'export {ts.name}', lambda: export(bpy.context, ts, ts.export, directory=directory), 600)
    assert len({p.name for p in written}) == len(written) and all(p.exists() for p in written)
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        bundle = timed('bundle', lambda: export_bundle(bpy.context, list(project.texture_sets), Path(directory) / 'bundle'), 600)
    files = sorted(p.name for p in Path(directory).rglob('*') if p.is_file())
assert bundle is not None and len(files) > len(written), files
print('ARTIST_FILE_BUNDLE_OK', len(files), flush=True)
print('ARTIST_FILE_TIMINGS', {k: round(v, 1) for k, v in timings.items()}, flush=True)
print('ARTIST_FILE_MULTISET_PASS', flush=True)
open_painter.unregister()
