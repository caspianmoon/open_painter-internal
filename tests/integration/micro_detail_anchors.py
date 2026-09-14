"""Generators wear and soil anchored micro details: a painted rivet gets a worn rim and a dirt ring."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import numpy as np
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks, _stack_signature
from open_painter.evaluation import anchors, live_masks
from open_painter.texture_engine import filters
from open_painter.utils.ids import new_id

N = 512
open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Micro details', N)
ts.preview_backend = 'CPU'
sets.assign(scene, ts, bpy.context.object)

# Flat baked maps: every mask below comes from the anchored relief alone.
for kind, value in (('CURVATURE', 0.), ('AO', 1.)):
    image = images.create('Flat ' + kind, N)
    data = np.ones((N, N, 4), np.float32)
    data[..., :3] = value
    images.write(image, data, pack=False)
    item = ts.baked_maps.add()
    item.kind, item.image = kind, image

y, x = np.indices((N, N), dtype=np.float32)
r = np.hypot(x + .5 - N / 2, y + .5 - N / 2)


def layer_by(identifier):
    # Adding layers reallocates the collection: re-resolve RNA references by id.
    return next(layer for layer in ts.layers if layer.id == identifier)


def disc_image(name, radius, inside, outside, alpha_outside=1.):
    image = images.create(name, N)
    data = np.empty((N, N, 4), np.float32)
    data[..., :3] = outside
    data[..., 3] = alpha_outside
    data[r < radius, :3] = inside
    data[r < radius, 3] = 1
    images.write(image, data, pack=False)
    return image


substrate = stack.add(ts, 'FILL', 'Substrate')
stack.channel_for(substrate, 'BASE_COLOR').color = (0, 0, 0, 1)
bump = disc_image('Rivet height', 64, 1., .5)
rivets = stack.add(ts, 'FILL', 'Rivets')
for prop in rivets.channels:
    prop.enabled = prop.channel == 'HEIGHT'
height = stack.channel_for(rivets, 'HEIGHT')
height.image = bump
anchor = height.effects.add()
anchor.kind, anchor.id, anchor.name = 'ANCHOR', new_id(), 'Rivet relief'
anchor_id = anchor.id
assert anchors.index(ts)[anchor_id].channel == 'HEIGHT'

rivets_id = rivets.id
worn = stack.add(ts, 'FILL', 'Worn'); worn_id = worn.id
rivets, height = layer_by(rivets_id), stack.channel_for(layer_by(rivets_id), 'HEIGHT')
for prop in worn.channels:
    prop.enabled = prop.channel == 'BASE_COLOR'
stack.channel_for(worn, 'BASE_COLOR').color = (1, 0, 0, 1)
wear = worn.masks.add()
wear.id, wear.source, wear.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
wear.amount, wear.contrast, wear.noise_strength, wear.detail_radius = .5, 2, 0, 3
wear.detail_height_anchor = anchor_id

rim_inside = (r > 56) & (r < 64)
rim_outside = (r > 65) & (r < 72)
coverage = evaluate_masks(ts, worn, N, 0, N, {}, {})
assert coverage[rim_inside].mean() > .5, coverage[rim_inside].mean()
assert coverage[r < 40].max() < .05 and coverage[rim_outside].max() < .05 and coverage[r > 96].max() < .05
signature = _stack_signature(ts, worn, {})
wear.detail_height_strength = 0
assert _stack_signature(ts, worn, {}) != signature
assert evaluate_masks(ts, worn, N, 0, N, {}, {}).max() == 0
wear.detail_height_strength = 1
wear.detail_height_anchor = ''
assert evaluate_masks(ts, worn, N, 0, N, {}, {}).max() == 0
wear.detail_height_anchor = anchor_id

# A painted rivet works the same: unpainted alpha-zero texels read as flat.
painted = disc_image('Painted rivet', 64, 1., 0., alpha_outside=0.)
rivets.type = 'PAINT'
height.image = painted
coverage = evaluate_masks(ts, worn, N, 0, N, {}, {})
assert coverage[rim_inside].mean() > .5 and coverage[rim_outside].max() < .05 and coverage[r > 96].max() < .05
rivets.type = 'FILL'
height.image = bump

# Dirt gathers in the concave ring outside the rivet; wear never does.
dirty = stack.add(ts, 'FILL', 'Dirty'); dirty_id = dirty.id
rivets, worn = layer_by(rivets_id), layer_by(worn_id)
height, wear = stack.channel_for(rivets, 'HEIGHT'), worn.masks[0]
for prop in dirty.channels:
    prop.enabled = prop.channel == 'BASE_COLOR'
stack.channel_for(dirty, 'BASE_COLOR').color = (0, 0, 1, 1)
dirt = dirty.masks.add()
dirt.id, dirt.source, dirt.generator = new_id(), 'GENERATOR', 'CAVITY_DIRT'
dirt.amount, dirt.contrast, dirt.noise_strength, dirt.detail_radius = .5, 1, 0, 3
dirt.detail_height_anchor = anchor_id
coverage = evaluate_masks(ts, dirty, N, 0, N, {}, {})
assert coverage[rim_outside].mean() > .5, coverage[rim_outside].mean()
assert coverage[r < 40].max() < .05 and coverage[(r > 50) & (r < 62)].max() < .05 and coverage[r > 96].max() < .05

# A tangent normal anchor drives the same rings through its slopes.
normal_image = images.create('Rivet normal', N)
normal = np.ones((N, N, 4), np.float32)
normal[..., :3] = filters.height_to_normal(images.pixels(bump)[..., 0], 24)
images.write(normal_image, normal, pack=False)
relief = stack.add(ts, 'FILL', 'Rivet normals')
rivets, worn, dirty = layer_by(rivets_id), layer_by(worn_id), layer_by(dirty_id)
height, wear, dirt = stack.channel_for(rivets, 'HEIGHT'), worn.masks[0], dirty.masks[0]
for prop in relief.channels:
    prop.enabled = prop.channel == 'NORMAL'
normal_channel = stack.channel_for(relief, 'NORMAL')
normal_channel.image = normal_image
normal_anchor = normal_channel.effects.add()
normal_anchor.kind, normal_anchor.id, normal_anchor.name = 'ANCHOR', new_id(), 'Rivet normal relief'
dirt.detail_height_anchor, dirt.detail_normal_anchor = '', normal_anchor.id
dirt.generator = 'DIRT'
coverage = evaluate_masks(ts, dirty, N, 0, N, {}, {})
assert coverage[rim_outside].mean() > coverage[r < 40].mean() + .3, (coverage[rim_outside].mean(), coverage[r < 40].mean())
dirt.generator = 'CAVITY_DIRT'
dirt.detail_height_anchor, dirt.detail_normal_anchor = anchor_id, ''

# Editing the anchored relief dirties the consumers and moves the rings.
LayerEvaluator(scene).evaluate_texture_set(ts.id)
before = images.pixels(images.output(ts, 'BASE_COLOR').image)[..., 0].copy()
assert before[rim_inside].mean() > .5 and before[r > 96].max() < .05
for out in ts.outputs:
    out.dirty = False
images.write(bump, images.pixels(disc_image('Bigger rivet', 96, 1., .5)), pack=False)
assert images.output(ts, 'BASE_COLOR').dirty
LayerEvaluator(scene).evaluate_texture_set(ts.id)
after = images.pixels(images.output(ts, 'BASE_COLOR').image)[..., 0]
assert after[(r > 88) & (r < 96)].mean() > .5 and after[rim_inside].max() < .05, after[rim_inside].max()
assert not live_masks.supports(ts, worn, N)

# The shader path uploads the encoded relief once and binds the intensities live.
from open_painter.evaluation import shader_preview
ts.preview_backend = 'LIVE'
graph = shader_preview.ensure(scene, ts)
assert graph is not None and graph.tree is not None, shader_preview.diagnostics(scene, ts)
for _ in range(400):
    if not graph.pending_resources:
        break
    graph.step_resources(budget=1.0)
assert not graph.pending_resources
micro = [binding for binding in graph.resource_bindings if binding.identifier.startswith('MicroDetails:')]
assert len(micro) == 2, [binding.identifier for binding in graph.resource_bindings]
encoded = images.pixels(micro[0].node.image)[..., 0]
assert encoded[(r > 88) & (r < 96)].mean() > .5 and encoded[(r > 97) & (r < 104)].mean() < .5
assert any(binding.field == 'detail_height_strength' for binding in graph.bindings.values())
open_painter.unregister()
print('MICRO_DETAIL_ANCHORS_PASS', flush=True)
