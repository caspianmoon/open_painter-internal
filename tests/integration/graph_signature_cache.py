"""Graph digest reuse must still observe edits that bypass authoring callbacks."""
from hashlib import sha256
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.graph import mirror
from open_painter.graph.edits import link_keys
from open_painter.utils.ids import new_id

open_painter.register()
ts = sets.create(bpy.context.scene, 'Live graph fingerprints', 512)
ts.preview_backend = 'CPU'
stack.add(ts, 'FILL', 'First')
stack.add(ts, 'FILL', 'Second')
for layer in ts.layers:
    for name in ('First mask', 'Second mask'):
        mask = layer.masks.add()
        mask.id, mask.name, mask.source = new_id(), name, 'PROCEDURAL'
    for name in ('First effect', 'Second effect'):
        effect = layer.channels[0].effects.add()
        effect.id, effect.name = new_id(), name
tree = mirror.ensure_tree(ts)
area = bpy.context.screen.areas[0]
area.type = 'NODE_EDITOR'
area.spaces.active.tree_type = 'OpenPainterGraph'
area.spaces.active.node_tree = tree


def check(previous=None, serial=None):
    digest = mirror.signature(ts)
    if previous is not None:
        assert digest != previous, 'live structural mutation was hidden by memoization'
    if serial is not None:
        assert ts.edit_serial == serial, 'fixture must bypass serial callbacks'
    # Saved trees from the pre-memo implementation retain their exact digest.
    with patch.object(mirror, '_signature_digest',
                      side_effect=lambda parts: sha256(repr(list(parts)).encode()).hexdigest()):
        assert digest == mirror.signature(ts)
    mirror.sync(bpy.context.window)
    expected, _, links = mirror.project(ts)
    actual = mirror.nodes_by_id(tree)
    assert tree['op_signature'] == digest
    assert all(actual[identifier].label == spec['label'] for identifier, spec in expected.items())
    assert link_keys(tree) == set(links)
    return digest


digest = check()
hits = mirror._signature_digest.cache_info().hits
assert mirror.signature(ts) == digest
assert mirror._signature_digest.cache_info().hits > hits

# Names and stable IDs have no update callback; the visible graph must change
# immediately even when every authoring serial remains the same.
for collection, index, field in (
        (lambda: ts.layers, 0, 'name'),
        (lambda: ts.layers[0].masks, 0, 'name'),
        (lambda: ts.layers[0].channels[0].effects, 0, 'name'),
        (lambda: ts.layers[0].masks, 0, 'id'),
        (lambda: ts.layers[0].channels[0].effects, 0, 'id')):
    serial = ts.edit_serial
    setattr(collection()[index], field, new_id())
    digest = check(digest, serial)

for collection in (lambda: ts.layers, lambda: ts.layers[0].masks,
                   lambda: ts.layers[0].channels[0].effects):
    serial = ts.edit_serial
    collection().move(0, 1)
    digest = check(digest, serial)
serial = ts.edit_serial
extra = ts.layers[0].masks.add()
extra.id, extra.name = new_id(), 'Raw added mask'
digest = check(digest, serial)
ts.layers[0].masks.remove(len(ts.layers[0].masks) - 1)
digest = check(digest, serial)

# A batched replay suppresses callbacks until the context ends. The live graph
# still follows structural changes while that replay is open.
with stack.replay(ts):
    serial = ts.edit_serial
    ts.layers[0].visible = not ts.layers[0].visible
    ts.layers[0].masks[0].enabled = False
    digest = check(digest, serial)
assert mirror.signature(ts) == digest
ts.layers[0].opacity = .37
assert mirror.signature(ts) == digest, 'numeric edits must keep graph identity'

for index in range(100):
    ts.layers[0].name = f'History {index}'
    mirror.signature(ts)
assert mirror._signature_digest.cache_info().currsize == 64
area.type = 'VIEW_3D'
open_painter.unregister()
assert mirror._signature_digest.cache_info().currsize == 0
assert not mirror._owner_indexes
open_painter.register()
open_painter.unregister()
print('GRAPH_SIGNATURE_CACHE_PASS', flush=True)
