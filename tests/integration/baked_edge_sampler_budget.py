"""Baked edge/chip sources use one editable, CPU-equivalent shader boundary."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.ai import candidates
from open_painter.core import image_manager, layer_stack, texture_set_manager
from open_painter.evaluation import shader_preview
from open_painter.evaluation.mask_eval import _evaluate_masks
from open_painter.evaluation.snapshots import capture


def run():
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    bpy.ops.mesh.primitive_plane_add()
    ts = texture_set_manager.create(scene, 'Baked edge sampler regression', 512)
    texture_set_manager.assign(scene, ts, bpy.context.object, 0)
    layer = layer_stack.add(ts, 'FILL')
    mask = layer.masks.add()
    mask.id, mask.source, mask.generator = 'edge-test', 'GENERATOR', 'EDGE_WEAR'
    mask.edge_source = 'BAKED'
    y, x = np.indices((512, 512), dtype=np.float32)
    for index, kind in enumerate(('CURVATURE_THIN', 'CURVATURE', 'CURVATURE_WIDE', 'CURVATURE_XWIDE', 'AO')):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = image_manager.create(kind, 512)
        rgba = np.ones((512, 512, 4), np.float32)
        rgba[..., :3] = np.clip((x/511 + y/511 + index*.2)/2, 0, 1)[..., None]
        image_manager.write(entry.image, rgba)
    def settled():
        candidates._ready(scene, time.monotonic()+60)
        graph = shader_preview.session_graph(scene, ts)
        leaves = [b for b in graph.resource_bindings if b.node.get('op_boundary')
                  and json.loads(b.node['op_boundary']) == ['mask_source', layer.id, mask.id]]
        assert len(leaves) == 1
        assert graph.bound_sampler_count() == 1, graph.bound_sampler_count()
        actual = image_manager.pixels(leaves[0].node.image)[..., 0]
        raw = capture(mask).replaced(black=0., white=1., invert=False, strength=1., enabled=True)
        expected = _evaluate_masks(ts, capture(layer).replaced(masks=(raw,)), 512, 0, 512, {}, {})
        np.testing.assert_allclose(actual, expected, atol=2e-6)
        return actual.copy()
    first = settled()
    mask.amount = .19
    mask.edge_size = 2.4
    edited = settled()
    assert float(np.max(np.abs(first-edited))) > .01, 'Wear controls did not refresh the source'
    mask.generator = 'CHIPS'
    chips = settled()
    assert float(np.max(np.abs(chips-edited))) > .001
    # Levels are downstream uniforms, not duplicated into the cached source.
    mask.black, mask.white, mask.invert = .2, .8, True
    np.testing.assert_array_equal(settled(), chips)
    # A stack authored outside Director can exceed its detached estimate.
    # The settled graph check must stop the candidate before an Eevee draw.
    from open_painter.authoring.schema import AuthoringError
    with shader_preview.authoring_batch(ts):
        for index in range(13):
            extra = layer_stack.add(ts, 'FILL')
            extra.opacity = .5
            prop = layer_stack.channel_for(extra, 'BASE_COLOR')
            prop.image = bpy.data.images.new('Distinct sampler ' + str(index), width=8, height=8)
    try:
        candidates._ready(scene, time.monotonic()+60)
        raise AssertionError('Oversized actual graph was allowed to render')
    except AuthoringError as error:
        assert 'image inputs' in str(error), str(error)
    print('BAKED_EDGE_SAMPLER_PARITY_AND_EDITABILITY_OK', flush=True)


open_painter.register()
def main():
    try:
        run()
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(main, first_interval=1.)
