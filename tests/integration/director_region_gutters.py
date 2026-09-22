"""Native region boundaries filter without leaking the atlas background.

Uses exact face membership, the CPU sampler and the actual published shader
leaf. No provider, scene-field generation or changes to artist source images.
"""
from pathlib import Path
import json
import sys
import tempfile

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.authoring import document, regions
from open_painter.core import image_manager, layer_stack, texture_set_manager as sets
from open_painter.evaluation import cache, mesh_masks
from open_painter.evaluation.blend_modes import sample_uv
from open_painter.evaluation.shader_graph import Graph
from open_painter.export.padding import ownership

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.data.scenes.new('Native region sampling regression')
bpy.context.window.scene = scene
scene.render.engine = 'BLENDER_EEVEE'
mesh = bpy.data.meshes.new('Two disconnected exact regions')
mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                 (2,0,0),(3,0,0),(3,1,0),(2,1,0)], [], [(0,1,2,3),(4,5,6,7)])
uv = mesh.uv_layers.new()
for face, (lo, hi) in zip(mesh.polygons, ((.251,.499),(.701,.949))):
    for loop, point in zip(face.loop_indices, ((lo,.25),(hi,.25),(hi,.75),(lo,.75))):
        uv.data[loop].uv = point
obj = bpy.data.objects.new('Exact regions', mesh)
scene.collection.objects.link(obj)
ts = sets.create(scene, 'Region coverage', 512)
sets.assign(scene, ts, obj)
layer = layer_stack.add(ts, 'FILL', 'Selected component')
mask = layer.masks.add(); mask.id = 'region-boundary'; mask.source = 'MESH'
bpy.context.view_layer.update()
components = [item for item in regions.capture(ts, {'object:0':obj}) if item['kind']=='CONNECTED_COMPONENT']
selected = next(item for item in components if item['bounds_world']['maximum'][0] == 1.)
for name, value in regions.selector(ts, selected['id'], {'object:0':obj}).items():
    setattr(mask.mesh, name, value)
# A persistent artist image is deliberately present to prove no padding writes.
artist_image = image_manager.create('Unrelated artist pixels',512)
artist_pixels = np.full((512,512,4), .237, np.float32)
image_manager.write(artist_image, artist_pixels)
before = document.canonical(document.snapshot(ts))
geometry = (tuple(tuple(v.co) for v in mesh.vertices), tuple(tuple(p.vertices) for p in mesh.polygons),
            tuple(tuple(v.uv) for v in uv.data), tuple(p.material_index for p in mesh.polygons))
image_ids = {image.as_pointer() for image in bpy.data.images}
n = 512
raw = mesh_masks.raster(ts, mask, n)
store = {}
sampled = mesh_masks.evaluate(ts, mask, n, 0, n, store)
owned = ownership(ts,n).covered
np.testing.assert_array_equal(sampled[owned], raw[owned])
np.testing.assert_array_equal(mesh_masks.raster(ts,mask,n), raw)
assert any('region-gutters-v1' in repr(key) for key in store)
u = np.array([.2511,.2514,.4988], np.float32)
v = np.full_like(u, .5)
old = sample_uv(raw[...,None],u,v)[...,0]
new = sample_uv(sampled[...,None],u,v)[...,0]
assert old.min() < .8, old
np.testing.assert_allclose(new,1.,atol=1e-6)
# Excluded owned faces remain zero, including their own chart boundary.
excluded = sample_uv(sampled[...,None],np.array([.7011,.8,.9488]),v)[...,0]
np.testing.assert_allclose(excluded,0.,atol=0.)
np.testing.assert_allclose(1-new,0.,atol=1e-6)
assert document.canonical(document.snapshot(ts))==before
np.testing.assert_array_equal(image_manager.pixels(artist_image), artist_pixels)
assert {image.as_pointer() for image in bpy.data.images}==image_ids
assert geometry==(tuple(tuple(v.co) for v in mesh.vertices), tuple(tuple(p.vertices) for p in mesh.polygons),
                  tuple(tuple(v.uv) for v in uv.data), tuple(p.material_index for p in mesh.polygons))

def shader_leaf(scene,ts):
    graph = Graph(scene,ts,ts.material).build()
    while graph.pending_resources:
        graph.step_resources(.1)
    node = next(node for node in graph.nodes()
                if node.get('op_boundary') and json.loads(node['op_boundary'])==['mask_source',layer.id,mask.id])
    assert node.interpolation=='Linear'
    binding = next(binding for binding in graph.resource_bindings if binding.node==node)
    assert 'region-gutters-v1' in repr(binding.signature), binding.signature
    pixels = image_manager.pixels(node.image).copy()
    np.testing.assert_array_equal(pixels[...,0][owned], raw[owned])
    np.testing.assert_allclose(sample_uv(pixels,u,v)[...,0],1.,atol=1e-6)
    return graph

graph = shader_leaf(scene,ts)
assert document.canonical(document.snapshot(ts))==before
sid, lid, mid = ts.id, layer.id, mask.id
path = Path(tempfile.gettempdir())/'open-painter-region-gutter-persistence.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
graph.close()
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.id==sid)
layer = next(item for item in ts.layers if item.id==lid)
mask = next(item for item in layer.masks if item.id==mid)
assert document.canonical(document.snapshot(ts))==before
np.testing.assert_array_equal(mesh_masks.raster(ts,mask,n),raw)
graph = shader_leaf(scene,ts)
graph.close()

# Reproduce a genuinely stale saved filter leaf, including a published-mask
# consumer. The old evaluator is emulated only for this fixture by disabling
# its gutter buffer and returning its pre-version sampling identity.
from open_painter.evaluation import uv_gutters, mask_eval
blur = layer.masks.add(); blur.id='blur-region'; blur.source='FILTER'
blur.effect.kind='GAUSSIAN_BLUR'; blur.effect.radius=1.
reader = layer_stack.add(ts,'FILL','Published region consumer')
reader_id = reader.id
reference = reader.masks.add(); reference.id='published-region'; reference.source='REFERENCE'
reference.reference_layer=lid; reference.reference_channel='MASK'
reader_blur = reader.masks.add(); reader_blur.id='blur-published'; reader_blur.source='FILTER'
reader_blur.effect.kind='GAUSSIAN_BLUR'; reader_blur.effect.radius=1.
folder = layer_stack.add(ts,'FOLDER','Cache dependency owner')
folder_id = folder.id
layer,folder,reader = [next(item for item in ts.layers if item.id==identifier)
                       for identifier in (lid,folder_id,reader_id)]
layer.parent_id=folder.id
saved_sampling, saved_padding = mesh_masks.sampling_signature, uv_gutters.padded
mesh_masks.sampling_signature = mesh_masks.signature
uv_gutters.padded = lambda _ts, values: values

def prefix_leaves(graph):
    result = {}
    for binding in graph.resource_bindings:
        descriptor = json.loads(binding.node.get('op_boundary','[]'))
        if descriptor and descriptor[0]=='mask_prefix':
            result[descriptor[1]] = (binding.signature, image_manager.pixels(binding.node.image).copy())
    return result

try:
    old_graph = Graph(scene,ts,ts.material).build()
    while old_graph.pending_resources:old_graph.step_resources(.1)
    old_prefixes = prefix_leaves(old_graph)
    assert set(old_prefixes)=={layer.id,reader.id}
    old_cache_keys = [old_graph._cache_identity(item,'BASE_COLOR')[1] for item in (layer,folder,reader)]
    old_stack = mask_eval._stack_signature(ts,layer,{})
    for _,pixels in old_prefixes.values():
        assert sample_uv(pixels,u,v)[...,0].min()<.8
    persisted = document.canonical(document.snapshot(ts))
    identifiers = [item.id for item in (layer,folder,reader)]
    legacy_path = Path(tempfile.gettempdir())/'open-painter-region-gutter-legacy-prefix.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(legacy_path))
    old_graph.close()
finally:
    mesh_masks.sampling_signature, uv_gutters.padded = saved_sampling, saved_padding
bpy.ops.wm.open_mainfile(filepath=str(legacy_path))
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.id==sid)
layer,folder,reader = [next(item for item in ts.layers if item.id==identifier) for identifier in identifiers]
assert document.canonical(document.snapshot(ts))==persisted
fixed_graph = Graph(scene,ts,ts.material).build()
while fixed_graph.pending_resources:fixed_graph.step_resources(.1)
new_prefixes = prefix_leaves(fixed_graph)
assert set(new_prefixes)==set(old_prefixes)
for identifier,(signature,pixels) in new_prefixes.items():
    assert signature!=old_prefixes[identifier][0] and 'region-gutters-v1' in repr(signature)
    np.testing.assert_allclose(sample_uv(pixels,u,v)[...,0],1.,atol=1e-6)
    np.testing.assert_allclose(sample_uv(pixels,np.array([.7011,.8,.9488]),v)[...,0],0.,atol=1e-6)
for old_key,item in zip(old_cache_keys,(layer,folder,reader)):
    new_key = fixed_graph._cache_identity(item,'BASE_COLOR')[1]
    assert new_key!=old_key and 'region-gutters-v1' in repr(new_key)
assert mask_eval._stack_signature(ts,layer,{})!=old_stack
assert document.canonical(document.snapshot(ts))==persisted
fixed_graph.close()
print('DIRECTOR_REGION_GUTTERS_FILTERED_REFERENCE_CACHE_UPGRADE_OK',flush=True)
print('DIRECTOR_REGION_GUTTERS_CPU_SHADER_PERSISTENCE_OK',old.tolist(),new.tolist(),flush=True)
