"""Projection respects render UVs and is independent of island placement."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images, surface_presets
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.painting.projection import geometry
open_painter.register()
mesh = bpy.data.meshes.new('Duplicated surface')
mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)] * 2, [], [(0,1,2,3),(4,5,6,7)])
obj = bpy.data.objects.new('UV regression', mesh)
bpy.context.collection.objects.link(obj)
render_uv = mesh.uv_layers.new(name='Render UV')
coords = [(0,0),(.5,0),(.5,1),(0,1),(.5,0),(1,0),(1,1),(.5,1)]
for item, uv in zip(render_uv.data, coords): item.uv = uv
mesh.uv_layers.new(name='Editing UV')
mesh.uv_layers.active_index = 1
for item in mesh.uv_layers.active.data: item.uv = (0,0)
mesh.uv_layers['Render UV'].active_render = True
ts = sets.create(bpy.context.scene, 'Projection', 512)
sets.assign(bpy.context.scene, ts, obj)
layer = stack.add(ts, 'FILL')
mask = layer.masks.add(); mask.source = 'FILL'; mask.projection = 'TRIPLANAR'
mask.image = images.create('Asymmetric mask', 32)
data = np.ones((32,32,4), dtype=np.float32)
data[..., :3] = np.arange(32, dtype=np.float32)[None,:,None] / 31
images.write(mask.image, data)
p, normal = geometry(ts, 512)
assert p[...,0].max() > .99, 'Collapsed editing UV was used instead of render UV'
np.testing.assert_allclose(p[:,:256], p[:,256:], atol=1e-6)
cache = {}; geometry_cache = {}
value = evaluate_masks(ts, layer, 512, 0, 512, cache, geometry_cache)
assert 'geometry' in geometry_cache
assert np.std(value) > .2
np.testing.assert_allclose(value[:,:256], value[:,256:], atol=1e-6)
# UV projection intentionally follows atlas coordinates; it does not fit each island.
mask.projection = 'UV'
uv_value = evaluate_masks(ts, layer, 512, 0, 512, {}, {})
assert np.mean(abs(uv_value[:,:256] - uv_value[:,256:])) > .4
# Existing hand-painted masks remain UV data even if projection fields are changed.
mask.source = 'PAINT'; mask.projection = 'TRIPLANAR'
# Fill coordinates pass through a centered UV transform; paint samples the UV
# grid directly. Both now retain float32 interpolation weights, so allow two
# float32 ULPs from the otherwise identical coordinate paths.
np.testing.assert_allclose(evaluate_masks(ts, layer, 512, 0, 512, {}, {}), uv_value, atol=2e-7)
group = surface_presets.apply(ts, 'Painted Steel')
coat = next(l for l in ts.layers if l.parent_id == group.id and l.masks)
assert coat.masks[0].source == 'FILL' and coat.masks[0].projection == 'TRIPLANAR'
assert [p.name for p in mesh.uv_layers] == ['Render UV', 'Editing UV']
assert mesh.uv_layers.active_index == 1
open_painter.unregister()
print('PROJECTED_MASKS_PASS', flush=True)
