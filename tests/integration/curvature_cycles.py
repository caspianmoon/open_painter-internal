"""Opt-in Cycles Bevel edge maps: real shader bake, method switching and flat control."""
import sys
from pathlib import Path

import bpy
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking.cycles_baker import curvature_graph
from open_painter.baking.maps.curvature import bake_curvature, METHOD
from open_painter.baking.bake_manager import register_map, stale_curvature
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.geometry.provenance import status

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Cycles reference', 512)
ts.preview_backend = 'CPU'
ts.bake.device = 'CPU'
ts.bake.samples = 8
ts.bake.mean_curvature_distance = .025
sets.assign(scene, ts, bpy.context.object)
assert ts.bake.curvature_method == 'GEOMETRIC'
ts.bake.curvature_method = 'CYCLES_BEVEL'
material = bpy.data.materials.new('Reference graph')
material.use_nodes = True
curvature_graph(material, .025)
nodes = material.node_tree.nodes
bevel = next(n for n in nodes if n.bl_idname == 'ShaderNodeBevel')
remap = next(n for n in nodes if n.bl_idname == 'ShaderNodeMapRange')
assert bevel.samples == 12 and abs(bevel.inputs['Radius'].default_value - .025) < 1e-6
assert remap.interpolation_type == 'SMOOTHERSTEP'
image = bake_curvature(ts)
values = images.pixels(image)[..., 0]
assert np.isfinite(values).all() and values.max() > .5, float(values.max())
assert np.count_nonzero(values > .01) < values.size * .2, 'flat face interiors acquired wear'
assert image['open_painter_curvature_method'] == 'CYCLES_BEVEL'
register_map(ts, 'CURVATURE', image)
entry = next(m for m in ts.baked_maps if m.kind == 'CURVATURE')
assert status(ts, entry) == 'Ready'
ts.bake.curvature_method = 'GEOMETRIC'
assert status(ts, entry).startswith('Stale') and stale_curvature(ts) is not None
assert METHOD == 'GEOMETRIC_CREASES_V2'
open_painter.unregister()
print('CURVATURE_CYCLES_PASS', flush=True)
