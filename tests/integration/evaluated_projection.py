"""Triplanar position/normal fields agree with evaluated utility mesh maps."""
from contextlib import nullcontext
import sys
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.painting.projection import geometry
from open_painter.baking.maps import utility
from open_painter.baking.maps.utility import bake_utility
from open_painter.geometry import snapshots

open_painter.register()
scene = bpy.context.scene
obj = bpy.context.object
ts = sets.create(scene, 'Evaluated projection', 512)
sets.assign(scene, ts, obj)
modifier = obj.modifiers.new('Projection bevel', 'BEVEL')
modifier.width, modifier.segments = .18, 3
original = np.array([v.co[:] for v in obj.data.vertices])
for scale in ((1, 2, .7), (-1, 2, .7)):
    obj.scale = scale
    obj.location = (2, -3, 1)
    bpy.context.view_layer.update()
    # Both read one evaluation. Blender's bevel can round a UV differently from one evaluation
    # to the next (one ulp, seen on Windows), which moves edge texels between triangles: across
    # evaluations positions then differ by up to 1.2e-6, on one by 6e-7 (the bake's encoding).
    with snapshots.targets(ts) as entries:
        entries = list(entries)
        same = lambda _ts: nullcontext(entries)
        with patch.object(snapshots, 'targets', same), patch.object(utility, '_targets', same):
            positions, normals = geometry(ts, 512)
            position_image = bake_utility(ts, 'POSITION')
            normal_image = bake_utility(ts, 'WORLD_NORMAL')
    baked_position = images.pixels(position_image)
    baked_normal = images.pixels(normal_image)
    coverage = baked_position[..., 3] > 0
    low, high = np.array(position_image['world_min']), np.array(position_image['world_max'])
    decoded = baked_position[..., :3] * (high - low) + low
    np.testing.assert_allclose(positions[coverage], decoded[coverage], atol=1e-6)
    normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-12)
    np.testing.assert_allclose(normals[coverage], baked_normal[coverage, :3] * 2 - 1, atol=3e-7)
    assert modifier in obj.modifiers[:]
    np.testing.assert_array_equal(np.array([v.co[:] for v in obj.data.vertices]), original)
open_painter.unregister()
print('EVALUATED_PROJECTION_PASS')
