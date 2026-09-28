"""Runtime resource identities never stand on a datablock address (0.9.7): an
image carries a persistent token, so a replacement datablock allocated at the
freed address re-rasters the boundaries that read it, and the keys persisted on
boundary nodes match after a reload instead of re-rastering every isolated input."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager, layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.resource_keys import UID, datablock_key, image_key
from open_painter.evaluation.shader_resources import RESOURCE

N = 512


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def solid(name, rgb):
    image = bpy.data.images.new(name, N, N)
    image.pixels.foreach_set(np.tile(np.array([*rgb, 1.], np.float32), (N * N,)))
    image.update()
    return image


def mean_rgb(image):
    return image_manager.pixels(image)[..., :3].mean(axis=(0, 1))


def resource_images():
    return [image for image in bpy.data.images if image.get(RESOURCE)]


# These fixtures verify CPU boundary caching and publication explicitly.
# Supported neighborhood effects now run on the GPU by default; force the
# established fallback here. engine_filters.py covers the native GPU path.
from unittest.mock import patch
_cpu_boundaries = patch('open_painter.evaluation.engine.filters.supported', return_value=False)
_cpu_boundaries.start()
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Resource keys', N)
sets.assign(scene, ts, plane)
grunge = solid('grunge_a', (1., 0., 0.))
blurred = stack.add(ts, 'FILL', 'Blurred'); blurred_id = blurred.id
prop = stack.channel_for(blurred, 'BASE_COLOR')
prop.image = grunge
blur = prop.effects.add()
blur.kind, blur.radius = 'GAUSSIAN_BLUR', 2.
plain = stack.add(ts, 'FILL', 'Plain'); plain_id = plain.id
stack.channel_for(plain, 'ROUGHNESS').image = grunge   # an unfiltered sampler of the same image
material_manager.repair(ts)
ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
boundary = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterChannelPrefix:' + blurred_id))
assert np.allclose(mean_rgb(boundary.node.image), (1., 0., 0.), atol=1e-3)
token = grunge.get(UID)
assert token and image_key(grunge)[0] == token, 'the key is not the image token'
assert isinstance(datablock_key(plane), str) and datablock_key(plane) == plane.name_full
sampler = next(node for _, _, _, node, _ in graph.image_bindings if node.image == grunge)

# ---- The token outlives a rename; a copy is told apart from its original.
grunge.name = 'grunge renamed'
assert boundary.identity() == boundary.signature, 'a rename re-identified the boundary'
copy = grunge.copy()
assert copy.get(UID) == token, 'Image.copy() no longer duplicates ID properties'
assert image_key(copy)[0] != token and image_key(grunge)[0] == token, 'a copy shares its original token'
assert copy[UID] != token
bpy.data.images.remove(copy)
print('RESOURCE_KEYS_TOKEN_OK', flush=True)

# ---- Outliner > Blender File > delete, then Image > Open a same-sized texture:
# the replacement lands on the freed address, so an address-keyed identity
# would keep serving the raster of the removed image.
old_key = image_key(grunge)
address = grunge.as_pointer()
bpy.data.images.remove(grunge)   # the channel pointers go None without a callback
replacement = solid('grunge_b', (0., 0., 1.))
reused = replacement.as_pointer() == address
assert image_key(replacement) != old_key, 'the replacement datablock produced the identity of the removed one'
prop = stack.channel_for(next(l for l in ts.layers if l.id == blurred_id), 'BASE_COLOR')
prop.image = replacement
stack.channel_for(next(l for l in ts.layers if l.id == plain_id), 'ROUGHNESS').image = replacement
with Metrics() as observed:
    ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
boundary = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterChannelPrefix:' + blurred_id))
assert boundary.signature == boundary.identity() and observed.counts['shader_resource_requests'] >= 1, observed.report()
assert np.allclose(mean_rgb(boundary.node.image), (0., 0., 1.), atol=1e-3), 'the boundary still samples the removed image'
assert sampler.image == replacement, 'the plain sampler was not re-pointed at the replacement'
print('RESOURCE_KEYS_REPLACEMENT_OK', 'address reused' if reused else 'fresh address', flush=True)

# ---- An undo to a step older than the mint drops the property: the same token comes back.
token = replacement[UID]
del replacement[UID]
assert image_key(replacement)[0] == token and replacement[UID] == token
assert boundary.identity() == boundary.signature

# ---- Reopening the file adopts every persisted raster: nothing is rastered again (the save
# wrote the leaves the engine bound, packed; the reopened session compiles and finds them).
with TemporaryDirectory(prefix='op-resource-keys-') as directory:
    path = str(Path(directory) / 'keys.blend')
    identifier = ts.id
    saved = boundary.node.image.get(RESOURCE)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
    persisted = next(image for image in resource_images() if image.get(RESOURCE) == saved)
    assert persisted.packed_file is not None and not persisted.has_data   # packed pixels load on first use
    with Metrics() as observed:
        info = ready(scene, ts)
    assert info['graph_builds'] == 1, info
    assert observed.counts['shader_resource_requests'] == 0, observed.report()
    assert observed.counts['shader_resource_cache_hits'] >= 1, observed.report()
    graph = shader_preview.ensure(scene, ts)
    boundary = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterChannelPrefix:' + blurred_id))
    assert boundary.node.image == persisted and boundary.pending_image is None
    assert saved in graph.resources.entries and [i.name for i in resource_images()] == [persisted.name]
    assert bpy.data.images['grunge_b'][UID] == token
    print('RESOURCE_KEYS_REOPEN_OK', flush=True)

    # ---- An identity edited before the first tick after a reload: the persisted
    # leaf it no longer names is no longer bound, and the next save does not write it again.
    shader_preview.clear()
    bpy.ops.wm.open_mainfile(filepath=path)
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
    prop = stack.channel_for(next(l for l in ts.layers if l.id == blurred_id), 'BASE_COLOR')
    prop.effects[0].radius = 3.
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    boundary = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterChannelPrefix:' + blurred_id))
    assert boundary.node.image.get(RESOURCE) != saved and boundary.pending_image is None
    orphan = next(image for image in resource_images() if image.get(RESOURCE) == saved)
    from open_painter.evaluation.engine.headless import bound_count
    from open_painter.evaluation.shader_resources import persist_bound_leaves
    assert not bound_count(orphan), 'the superseded persisted leaf is still bound'
    persist_bound_leaves()   # what the next save does before packing
    assert not orphan.use_fake_user and not orphan.users, 'the superseded persisted leaf would be saved again'
    assert boundary.node.image.use_fake_user, 'the bound leaf would not be saved'
    print('RESOURCE_KEYS_SUPERSEDED_OK', flush=True)
open_painter.unregister()
print('RESOURCE_KEYS_PASS', flush=True)
